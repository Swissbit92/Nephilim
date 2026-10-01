"""python-telegram-bot handlers: the thin adapter over relay + messaging.

Security posture enforced here:
  - Allowlist gate on EVERY update: non-allowlisted chats are silently ignored
    (no reply, content never logged, nephilim never called).
  - Forwarded messages are refused, never relayed to the LLM (injection guard).
  - Only fixed, hardcoded user-facing strings — exception text/URLs/internals are
    NEVER interpolated into an outbound message. Full detail is logged locally.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from telegram import Message, Update
from telegram.ext import ContextTypes

from . import media as media_guard
from . import media_cleanup, messaging, relay
from .config import TelegramConfig
from .nephilim_client import (
    NephilimBadRequestError,
    NephilimClient,
    NephilimError,
    NephilimUnavailableError,
)
from .session_store import SessionStore
from .typing_indicator import TypingIndicator

logger = logging.getLogger(__name__)

# Fixed user-facing strings. Persona-neutral (a chat may be routed to a persona
# other than E.E.V.A.). NEVER interpolate exception detail into these.
MSG_UNAVAILABLE = "I'm having trouble connecting right now — give it a moment and try again."
MSG_ERROR = "Something went wrong on my end. Try again in a moment."
MSG_EMPTY = "Hm — nothing came through. Try rephrasing?"
MSG_FORWARD_REFUSED = "I only read messages you write to me directly — forwarded messages are ignored for safety."
MSG_MEDIA_UNAVAILABLE = "I made something for you, but I couldn't attach it."
MSG_TEXT_ONLY = "I can only handle text messages right now."
MSG_START_ACK = (
    "We're already mid-conversation. Say anything to continue, or /reset to wipe our history and start fresh."
)
# "wiped" was unconditional and, once images exist, often false — the user
# finds out by scrolling up, and a bot that overstates one privacy action
# loses credibility on every later one. "cleared" is the specific verb for
# the specific thing, and the image detail follows as counts.
MSG_RESET_DONE = "Done — our conversation history is cleared. We're starting fresh."
MSG_TOOLKIT_EMPTY = "I don't have any tools available right now — just conversation."

# ── ADR-011 conversation-control command strings (fixed; never interpolate errors) ──
MSG_HELP = (
    "Here's what I can do:\n\n"
    "/start — begin or resume our chat\n"
    "/regen — reroll my last reply\n"
    "/continue — have me continue my last reply\n"
    "/undo — delete the last exchange\n"
    "/sys <text> — set a scene beat (e.g. /sys it's late and quiet)\n"
    "/note [text | clear] — standing direction; no text shows it, 'clear' removes it\n"
    "/impersonate [hint] — draft a reply as you\n"
    "/whoami — who you're talking to\n"
    "/tools — my available tools\n"
    "/reset — wipe our history and start fresh"
)
MSG_NOTHING_TO_REGEN = "Nothing to reroll yet — say something first."
MSG_NOTHING_TO_CONTINUE = "Nothing to continue yet."
MSG_UNDO_DONE = "Done — dropped the last exchange."
MSG_SYS_USAGE = "Give me a scene to set, like: /sys it's raining outside"
MSG_NOTE_SET = "Got it — I'll keep that in mind from now on."
MSG_NOTE_CLEARED = "Cleared — no standing direction now."
MSG_NOTE_NONE = "No standing direction set. Set one with /note <text>."
MSG_IMPERSONATE_EMPTY = "I couldn't think of anything to say for you."

# Human-friendly toolset labels for the /tools listing.
_TOOLSET_LABELS = {
    "web": "🔎 Web",
    "wallet": "💰 Wallet",
    "memory": "🧠 Memory",
    "terminal": "⌨️ Terminal",
}


def format_toolkit(toolkit: dict) -> str:
    """Render a persona toolkit dict (from GET /personas/{key}/toolkit) into a
    Telegram message. Only structured fields from our OWN backend are used —
    tool names/descriptions are developer-authored, not user/LLM content."""
    name = toolkit.get("display_name") or toolkit.get("persona_key") or "This persona"
    tools_by_set = toolkit.get("tools") or {}
    if not tools_by_set:
        return MSG_TOOLKIT_EMPTY

    lines = [f"🧰 {name} — available tools:"]
    if toolkit.get("nsfw"):
        lines.append("🔞 Unrestricted (adult) mode is on.")
    for toolset, tools in tools_by_set.items():
        label = _TOOLSET_LABELS.get(toolset, toolset.capitalize())
        lines.append(f"\n{label}")
        for t in tools:
            desc = t.get("description") or ""
            hitl = " (asks first)" if t.get("requires_hitl") else ""
            lines.append(f"• {t.get('name')} — {desc}{hitl}")
    return "\n".join(lines)


async def tools_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List the tools/skills available to the persona for this chat (ADR-009 W3).

    Generic: uses the chat's configured persona, so it works for any persona,
    not just E.E.V.A.
    """
    gateway = get_gateway(context)
    chat_id = _allowed_chat_id(update, gateway)
    if chat_id is None:
        return
    persona = gateway.config.persona_for_chat(chat_id)
    bot = context.bot
    limit = gateway.config.message_char_limit
    try:
        toolkit = await gateway.client.get_toolkit(persona)
    except NephilimUnavailableError:
        await messaging.send_text(bot, chat_id, MSG_UNAVAILABLE, limit)
        return
    except NephilimError:
        logger.exception("get_toolkit failed for chat_id=%s persona=%s", chat_id, persona)
        await messaging.send_text(bot, chat_id, MSG_ERROR, limit)
        return
    except Exception:
        logger.exception("Unexpected error in /tools for chat_id=%s", chat_id)
        await messaging.send_text(bot, chat_id, MSG_ERROR, limit)
        return
    await messaging.send_text(bot, chat_id, format_toolkit(toolkit), limit)


@dataclass
class Gateway:
    """Shared per-process state, stashed in Application.bot_data['gateway']."""

    config: TelegramConfig
    client: NephilimClient
    store: SessionStore
    llm_lock: object  # asyncio.Lock — typed loosely to avoid import at module load


def is_forwarded(message: Message | None) -> bool:
    """True if the message was forwarded from elsewhere (Bot API 7.0 forward_origin)."""
    if message is None:
        return False
    return getattr(message, "forward_origin", None) is not None


def get_gateway(context: ContextTypes.DEFAULT_TYPE) -> Gateway:
    return context.application.bot_data["gateway"]


def _allowed_chat_id(update: Update, gateway: Gateway) -> int | None:
    """Return chat_id iff this update is from an allowlisted chat, else None.

    Silent by design: a rejected update produces no reply and logs only the id.
    """
    chat = update.effective_chat
    if chat is None:
        return None
    if not gateway.config.is_allowed(chat.id):
        logger.info("Ignoring update from non-allowlisted chat_id=%s", chat.id)
        return None
    return chat.id


def _log_content(gateway: Gateway, chat_id: int, text: str) -> None:
    if gateway.config.log_content:
        logger.debug("chat_id=%s text=%r", chat_id, text)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    gateway = get_gateway(context)
    chat_id = _allowed_chat_id(update, gateway)
    if chat_id is None:
        return
    persona = gateway.config.persona_for_chat(chat_id)
    bot = context.bot
    limit = gateway.config.message_char_limit
    try:
        async with gateway.llm_lock, TypingIndicator(bot, chat_id, gateway.config.typing_interval_seconds):
            messages, greeted = await relay.start_session(gateway.client, gateway.store, chat_id, persona)
    except NephilimUnavailableError:
        await messaging.send_text(bot, chat_id, MSG_UNAVAILABLE, limit)
        return
    except NephilimError:
        logger.exception("start_session failed for chat_id=%s", chat_id)
        await messaging.send_text(bot, chat_id, MSG_ERROR, limit)
        return
    except Exception:
        # Unexpected (e.g. sqlite) — never leak detail; reply with the fixed string.
        logger.exception("Unexpected error in /start for chat_id=%s", chat_id)
        await messaging.send_text(bot, chat_id, MSG_ERROR, limit)
        return
    if greeted and messages:
        await messaging.send_messages(bot, chat_id, messages, limit)
    else:
        await messaging.send_text(bot, chat_id, MSG_START_ACK, limit)


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    gateway = get_gateway(context)
    chat_id = _allowed_chat_id(update, gateway)
    if chat_id is None:
        return
    persona = gateway.config.persona_for_chat(chat_id)
    bot = context.bot
    limit = gateway.config.message_char_limit
    try:
        async with gateway.llm_lock:
            quarantined = await relay.reset_session(
                gateway.client, gateway.store, chat_id, persona
            )
    except NephilimUnavailableError:
        await messaging.send_text(bot, chat_id, MSG_UNAVAILABLE, limit)
        return
    except NephilimError:
        logger.exception("reset_session failed for chat_id=%s", chat_id)
        await messaging.send_text(bot, chat_id, MSG_ERROR, limit)
        return
    except Exception:
        logger.exception("Unexpected error in /reset for chat_id=%s", chat_id)
        await messaging.send_text(bot, chat_id, MSG_ERROR, limit)
        return
    # The chat half is best-effort and must never fail the reset: the history
    # is already gone server-side by this point, so raising here would report
    # failure for an action that largely succeeded.
    try:
        records = gateway.store.media_for_chat(chat_id)
        outcome = await media_cleanup.remove_sent_media(bot, chat_id, records)
        gateway.store.forget_media(chat_id)
    except Exception:
        logger.exception("[Media] chat cleanup failed for chat_id=%s", chat_id)
        outcome = media_cleanup.CleanupResult()

    detail = media_cleanup.describe(outcome, quarantined)
    await messaging.send_text(bot, chat_id, f"{MSG_RESET_DONE}\n\n{detail}", limit)


# ── ADR-011 conversation-control commands ───────────────────────────────────


async def _reply_or_error(update, context, *, action, empty_msg: str) -> None:
    """Run an LLM-producing conversation-control action under the lock and send its messages.

    ``action(client, store, chat_id, persona) -> list[str]``. Maps the standard
    errors to fixed strings; a 400 (``NephilimBadRequestError`` — "nothing to act
    on") shows the friendlier ``empty_msg``.
    """
    gateway = get_gateway(context)
    chat_id = _allowed_chat_id(update, gateway)
    if chat_id is None:
        return
    persona = gateway.config.persona_for_chat(chat_id)
    bot = context.bot
    limit = gateway.config.message_char_limit
    try:
        async with gateway.llm_lock, TypingIndicator(bot, chat_id, gateway.config.typing_interval_seconds):
            messages = await action(gateway.client, gateway.store, chat_id, persona)
    except NephilimBadRequestError:
        await messaging.send_text(bot, chat_id, empty_msg, limit)
        return
    except NephilimUnavailableError:
        await messaging.send_text(bot, chat_id, MSG_UNAVAILABLE, limit)
        return
    except NephilimError:
        logger.exception("conversation-control verb failed for chat_id=%s", chat_id)
        await messaging.send_text(bot, chat_id, MSG_ERROR, limit)
        return
    except Exception:
        logger.exception("Unexpected error in conversation-control verb for chat_id=%s", chat_id)
        await messaging.send_text(bot, chat_id, MSG_ERROR, limit)
        return
    if not messages:
        messages = [MSG_EMPTY]
    await messaging.send_messages(bot, chat_id, messages, limit)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/help — list available commands (client-local, no backend call)."""
    gateway = get_gateway(context)
    chat_id = _allowed_chat_id(update, gateway)
    if chat_id is None:
        return
    await messaging.send_text(context.bot, chat_id, MSG_HELP, gateway.config.message_char_limit)


async def whoami_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/whoami — show the bound persona + session counts (read-only)."""
    gateway = get_gateway(context)
    chat_id = _allowed_chat_id(update, gateway)
    if chat_id is None:
        return
    persona = gateway.config.persona_for_chat(chat_id)
    bot = context.bot
    limit = gateway.config.message_char_limit
    try:
        meta = await relay.whoami(gateway.client, gateway.store, chat_id, persona)
    except NephilimUnavailableError:
        await messaging.send_text(bot, chat_id, MSG_UNAVAILABLE, limit)
        return
    except NephilimError:
        logger.exception("whoami failed for chat_id=%s", chat_id)
        await messaging.send_text(bot, chat_id, MSG_ERROR, limit)
        return
    except Exception:
        logger.exception("Unexpected error in /whoami for chat_id=%s", chat_id)
        await messaging.send_text(bot, chat_id, MSG_ERROR, limit)
        return
    # Structured fields from OUR backend only — safe to render.
    name = meta.get("display_name") or meta.get("persona_key") or "someone"
    nsfw = " · 🔞 adult mode" if meta.get("nsfw") else ""
    count = meta.get("message_count", 0)
    await messaging.send_text(bot, chat_id, f"You're talking to {name}{nsfw}.\n{count} messages so far.", limit)


async def regen_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/regen — reroll the last reply."""
    await _reply_or_error(update, context, action=relay.regenerate_reply, empty_msg=MSG_NOTHING_TO_REGEN)


async def continue_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/continue — extend the last reply."""
    await _reply_or_error(update, context, action=relay.continue_reply, empty_msg=MSG_NOTHING_TO_CONTINUE)


async def undo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/undo — delete the last exchange (no LLM)."""
    gateway = get_gateway(context)
    chat_id = _allowed_chat_id(update, gateway)
    if chat_id is None:
        return
    persona = gateway.config.persona_for_chat(chat_id)
    bot = context.bot
    limit = gateway.config.message_char_limit
    try:
        await relay.undo_last(gateway.client, gateway.store, chat_id, persona)
    except NephilimUnavailableError:
        await messaging.send_text(bot, chat_id, MSG_UNAVAILABLE, limit)
        return
    except NephilimError:
        logger.exception("undo failed for chat_id=%s", chat_id)
        await messaging.send_text(bot, chat_id, MSG_ERROR, limit)
        return
    except Exception:
        logger.exception("Unexpected error in /undo for chat_id=%s", chat_id)
        await messaging.send_text(bot, chat_id, MSG_ERROR, limit)
        return
    await messaging.send_text(bot, chat_id, MSG_UNDO_DONE, limit)


async def sys_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/sys <text> — inject a narrator/scene beat and get the persona's reaction."""
    gateway = get_gateway(context)
    chat_id = _allowed_chat_id(update, gateway)
    if chat_id is None:
        return
    text = " ".join(context.args).strip() if context.args else ""
    if not text:
        await messaging.send_text(context.bot, chat_id, MSG_SYS_USAGE, gateway.config.message_char_limit)
        return
    await _reply_or_error(
        update,
        context,
        action=lambda c, s, cid, p: relay.narrate(c, s, cid, p, text),
        empty_msg=MSG_ERROR,
    )


async def note_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/note [text | clear] — show / set / clear the standing author's note (no LLM)."""
    gateway = get_gateway(context)
    chat_id = _allowed_chat_id(update, gateway)
    if chat_id is None:
        return
    persona = gateway.config.persona_for_chat(chat_id)
    bot = context.bot
    limit = gateway.config.message_char_limit
    arg = " ".join(context.args).strip() if context.args else ""
    try:
        if not arg:
            note = await relay.get_note(gateway.client, gateway.store, chat_id, persona)
            msg = f"📝 Current direction:\n{note}" if note else MSG_NOTE_NONE
        elif arg.lower() == "clear":
            await relay.clear_note(gateway.client, gateway.store, chat_id, persona)
            msg = MSG_NOTE_CLEARED
        else:
            await relay.set_note(gateway.client, gateway.store, chat_id, persona, arg)
            msg = MSG_NOTE_SET
    except NephilimUnavailableError:
        await messaging.send_text(bot, chat_id, MSG_UNAVAILABLE, limit)
        return
    except NephilimError:
        logger.exception("note command failed for chat_id=%s", chat_id)
        await messaging.send_text(bot, chat_id, MSG_ERROR, limit)
        return
    except Exception:
        logger.exception("Unexpected error in /note for chat_id=%s", chat_id)
        await messaging.send_text(bot, chat_id, MSG_ERROR, limit)
        return
    await messaging.send_text(bot, chat_id, msg, limit)


async def impersonate_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/impersonate [hint] — draft the user's next line (returned as a suggestion)."""
    gateway = get_gateway(context)
    chat_id = _allowed_chat_id(update, gateway)
    if chat_id is None:
        return
    persona = gateway.config.persona_for_chat(chat_id)
    bot = context.bot
    limit = gateway.config.message_char_limit
    hint = " ".join(context.args).strip() if context.args else None
    try:
        async with gateway.llm_lock, TypingIndicator(bot, chat_id, gateway.config.typing_interval_seconds):
            draft = await relay.impersonate(gateway.client, gateway.store, chat_id, persona, hint)
    except NephilimUnavailableError:
        await messaging.send_text(bot, chat_id, MSG_UNAVAILABLE, limit)
        return
    except NephilimError:
        logger.exception("impersonate failed for chat_id=%s", chat_id)
        await messaging.send_text(bot, chat_id, MSG_ERROR, limit)
        return
    except Exception:
        logger.exception("Unexpected error in /impersonate for chat_id=%s", chat_id)
        await messaging.send_text(bot, chat_id, MSG_ERROR, limit)
        return
    await messaging.send_text(
        bot, chat_id, f"✍️ Draft (yours to send or edit):\n{draft}" if draft else MSG_IMPERSONATE_EMPTY, limit
    )


async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    gateway = get_gateway(context)
    chat_id = _allowed_chat_id(update, gateway)
    if chat_id is None:
        return
    message = update.message
    if message is None or not message.text:
        return
    bot = context.bot
    limit = gateway.config.message_char_limit

    # Injection guard: never relay forwarded content to the LLM.
    if is_forwarded(message):
        logger.info("Refusing forwarded message from chat_id=%s", chat_id)
        await messaging.send_text(bot, chat_id, MSG_FORWARD_REFUSED, limit)
        return

    _log_content(gateway, chat_id, message.text)
    persona = gateway.config.persona_for_chat(chat_id)
    try:
        # Single global lock: OLLAMA_NUM_PARALLEL=1 means one LLM call at a time.
        async with gateway.llm_lock, TypingIndicator(bot, chat_id, gateway.config.typing_interval_seconds):
            reply = await relay.handle_user_message(gateway.client, gateway.store, chat_id, persona, message.text)
    except NephilimUnavailableError:
        await messaging.send_text(bot, chat_id, MSG_UNAVAILABLE, limit)
        return
    except NephilimError:
        logger.exception("handle_user_message failed for chat_id=%s", chat_id)
        await messaging.send_text(bot, chat_id, MSG_ERROR, limit)
        return
    except Exception:
        logger.exception("Unexpected error handling message for chat_id=%s", chat_id)
        await messaging.send_text(bot, chat_id, MSG_ERROR, limit)
        return

    messages = reply.messages or ([MSG_EMPTY] if not reply.media else [])
    if messages:
        await messaging.send_messages(bot, chat_id, messages, limit)
    # Text first, then the attachment: the reply is what gives the image its
    # context, and a persona reply can exceed the 1024-char caption limit
    # anyway, so it structurally cannot ride along as one.
    await _deliver_media(bot, chat_id, gateway, reply.media)


async def _deliver_media(bot, chat_id: int, gateway, items) -> None:
    """Send each media item, sequentially, reporting failures ONCE.

    Sequential awaits, deliberately: ordering is guaranteed only by causality —
    a send is not issued until the previous one's response has arrived, so a
    slow upload cannot be overtaken by a later message. asyncio.gather would
    race on PTB's 256-connection pool and reorder the turn.

    One bad item must not swallow a good one, and N failures must not become N
    messages — that is the confusion that makes partial delivery worse than
    none. Note this deliberately differs from Telegram's own sendMediaGroup,
    which is atomic: that is the right call for a single wire request and the
    wrong one for an application-level batch of independent sends.
    """
    failures = 0
    for item in items:
        try:
            path = media_guard.resolve_media_path(gateway.config, item.path)
        except media_guard.MediaRejectedError as exc:
            # Logged with the reason; the user sees a fixed string. Six distinct
            # rejection reasons collapse to one message so a probing sender
            # cannot map what exists on disk.
            logger.warning("[Media] rejected for chat_id=%s: %s", chat_id, exc)
            failures += 1
            continue
        try:
            message_id = await messaging.send_document(
                bot,
                chat_id,
                path,
                filename=item.filename,
                caption=item.caption,
                protect_content=item.protect_content,
            )
            if message_id is not None:
                # Remembered so /reset can try to remove it from the chat.
                # Best-effort: failing to record must not fail a delivered image.
                try:
                    gateway.store.record_media(chat_id, message_id)
                except Exception:  # noqa: BLE001
                    logger.warning("[Media] could not record message_id %s", message_id)
        except messaging.MediaSendFailedError as exc:
            # Already retried and already logged with the cause by retry.py.
            logger.warning("[Media] giving up on an item for chat_id=%s: %s", chat_id, exc)
            failures += 1
        except Exception:
            logger.exception("[Media] unexpected send failure for chat_id=%s", chat_id)
            failures += 1

    if failures:
        await messaging.send_text(
            bot, chat_id, MSG_MEDIA_UNAVAILABLE, gateway.config.message_char_limit
        )


async def testimage_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Dev probe: ask the coordinator for a fixture image and deliver it.

    Registered only when media is enabled (see bot.build_application), kept out
    of the slash menu and out of /help. With the flag off the command is a
    silent no-op, because both message handlers exclude commands.
    """
    gateway = get_gateway(context)
    chat_id = _allowed_chat_id(update, gateway)
    if chat_id is None:
        return
    bot, limit = context.bot, gateway.config.message_char_limit
    persona = gateway.config.persona_for_chat(chat_id)
    try:
        reply = await relay.request_fixture_media(gateway.client, gateway.store, chat_id, persona)
    except NephilimUnavailableError:
        await messaging.send_text(bot, chat_id, MSG_UNAVAILABLE, limit)
        return
    except NephilimError:
        logger.exception("fixture media failed for chat_id=%s", chat_id)
        await messaging.send_text(bot, chat_id, MSG_ERROR, limit)
        return

    if reply.messages:
        await messaging.send_messages(bot, chat_id, reply.messages, limit)
    await _deliver_media(bot, chat_id, gateway, reply.media)


async def non_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Photos/voice/docs/etc.: text-only in v1 — acknowledge, don't process."""
    gateway = get_gateway(context)
    chat_id = _allowed_chat_id(update, gateway)
    if chat_id is None:
        return
    await messaging.send_text(context.bot, chat_id, MSG_TEXT_ONLY, gateway.config.message_char_limit)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Catch-all for exceptions escaping handlers (bugs, not expected failures)."""
    logger.exception("Unhandled error in handler", exc_info=context.error)
