"""Application factory: wires config -> handlers -> nephilim client/session store."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from telegram import BotCommand
from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

from . import handlers, notifications
from .config import TelegramConfig
from .handlers import Gateway
from .nephilim_client import NephilimClient
from .session_store import SessionStore

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DB_PATH = _PROJECT_ROOT / "data" / "sessions.sqlite3"


async def _post_shutdown(application: Application) -> None:
    """Stop the notification poller, then release the client and the store.

    ORDER MATTERS: the poller is cancelled BEFORE the HTTP client closes.
    Reversed, an in-flight claim would hit a closed client and the loop's
    catch-all would hand the notification back — against a client that can no
    longer send the hand-back, losing it.
    """
    await notifications.stop(application)

    gateway: Gateway = application.bot_data.get("gateway")
    if gateway is not None:
        await gateway.client.aclose()
        gateway.store.close()


# Native slash menu — EVERY command is surfaced, ordered most-used first.
#
# An earlier cut showed only 6, citing the "5-8 sweet spot / dilution past 8"
# command-menu research. That research measures tap-through in a multi-user
# conversion funnel; this is a single-operator personal bot, where discoverability
# strictly beats funnel optimisation (the hidden commands were simply invisible).
# Keep the high-value verbs at the top — the first entries are the ones reached for.
_MENU_COMMANDS = [
    BotCommand("regen", "Reroll my last reply"),
    BotCommand("continue", "Continue my last reply"),
    BotCommand("undo", "Delete the last exchange"),
    BotCommand("sys", "Set a scene beat, e.g. /sys it's late"),
    BotCommand("note", "Standing direction: [text | clear]"),
    BotCommand("impersonate", "Draft a reply as you: [hint]"),
    BotCommand("whoami", "Who you're talking to"),
    BotCommand("tools", "Show my available tools"),
    BotCommand("start", "Begin or resume our chat"),
    BotCommand("reset", "Wipe our history and start fresh"),
    BotCommand("help", "List everything I can do"),
]


async def _post_init(application: Application) -> None:
    """Register the command menu, then start the notification poller.

    The poller only runs when media delivery is on: with TG_MEDIA_ENABLED
    false there is no media root, so every image it fetched would be rejected
    by the path guard anyway — polling would be pure noise against the
    coordinator.
    """
    await application.bot.set_my_commands(_MENU_COMMANDS)

    gateway: Gateway = application.bot_data.get("gateway")
    if gateway is not None and gateway.config.media_enabled:
        notifications.start(application)


def build_application(config: TelegramConfig, db_path: Path | None = None) -> Application:
    """Build a fully-wired PTB Application ready for run_polling()."""
    application = (
        ApplicationBuilder()
        .token(config.bot_token)
        # PTB's read_timeout stays at 5s even for media — it swaps only the
        # WRITE timeout for uploads. After the bytes are up Telegram still
        # builds its thumbnail ladder before answering, so the default is the
        # most likely cause of a phantom "the image never arrived".
        .connect_timeout(20)
        .read_timeout(60)
        .media_write_timeout(180)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )

    gateway = Gateway(
        config=config,
        client=NephilimClient(config.nephilim_base_url, config.request_timeout_seconds),
        store=SessionStore(db_path or _DEFAULT_DB_PATH),
        llm_lock=asyncio.Lock(),
    )
    application.bot_data["gateway"] = gateway

    application.add_handler(CommandHandler("start", handlers.start_command))
    application.add_handler(CommandHandler("reset", handlers.reset_command))
    application.add_handler(CommandHandler("tools", handlers.tools_command))
    # ADR-011 conversation-control commands
    application.add_handler(CommandHandler("help", handlers.help_command))
    application.add_handler(CommandHandler("whoami", handlers.whoami_command))
    application.add_handler(CommandHandler("regen", handlers.regen_command))
    application.add_handler(CommandHandler("continue", handlers.continue_command))
    application.add_handler(CommandHandler("undo", handlers.undo_command))
    application.add_handler(CommandHandler("sys", handlers.sys_command))
    application.add_handler(CommandHandler("note", handlers.note_command))
    application.add_handler(CommandHandler("impersonate", handlers.impersonate_command))
    if config.media_enabled:
        # Dev probe. Deliberately absent from _MENU_COMMANDS and MSG_HELP:
        # flag-gated registration is a cleaner mechanism than an undocumented
        # always-on command. With the flag off it is a silent no-op, since
        # both message handlers filter out commands.
        application.add_handler(CommandHandler("testimage", handlers.testimage_command))
        application.add_handler(CommandHandler("testgen", handlers.testgen_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.text_message))
    # Any non-text, non-command content (media, voice, stickers, docs).
    application.add_handler(MessageHandler((filters.ALL & ~filters.TEXT) & ~filters.COMMAND, handlers.non_text_message))
    application.add_error_handler(_typed_error_handler)

    return application


async def _typed_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    await handlers.on_error(update, context)
