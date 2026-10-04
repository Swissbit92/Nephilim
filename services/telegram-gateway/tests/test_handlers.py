"""Tests for PTB handlers using lightweight fakes (no network, no real PTB app).

Focus: the security-critical guards (allowlist silence, forwarded refusal) and
the error-mapping contract (never leak internals), plus happy-path relaying.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from eeva_telegram import handlers, relay
from eeva_telegram.config import TelegramConfig
from eeva_telegram.handlers import Gateway
from eeva_telegram.nephilim_client import NephilimSessionNotFoundError, NephilimUnavailableError
from eeva_telegram.session_store import SessionStore


class FakeBot:
    def __init__(self):
        self.sent = []
        self.actions = []

    async def send_message(self, chat_id, text, link_preview_options=None):
        self.sent.append((chat_id, text))

    async def send_chat_action(self, chat_id, action):
        self.actions.append((chat_id, action))

    @property
    def texts(self):
        return [t for _, t in self.sent]


class FakeClient:
    def __init__(self):
        self.chat_calls = []
        self.cleared = []
        self.greeted = []
        self._n = 0
        self.chat_response = {"answer": "hello from persona", "message_flow": "single"}
        self.greet_response = {"answer": "Greetings."}
        self.raise_on_chat: Exception | None = None

    async def create_session(self, persona_key, title="Telegram"):
        self._n += 1
        return f"sess-{self._n}"

    async def chat(self, session_id, message):
        if self.raise_on_chat is not None:
            raise self.raise_on_chat
        self.chat_calls.append((session_id, message))
        return self.chat_response

    async def greet(self, session_id):
        self.greeted.append(session_id)
        return self.greet_response

    async def clear_messages(self, session_id):
        self.cleared.append(session_id)


@pytest.fixture
def gateway(cfg: TelegramConfig, store: SessionStore):
    return Gateway(config=cfg, client=FakeClient(), store=store, llm_lock=asyncio.Lock())


def make_update(chat_id, text=None, forwarded=False):
    message = SimpleNamespace(text=text, forward_origin=(object() if forwarded else None))
    return SimpleNamespace(effective_chat=SimpleNamespace(id=chat_id), message=message)


def make_context(gateway, bot):
    app = SimpleNamespace(bot_data={"gateway": gateway})
    return SimpleNamespace(application=app, bot=bot, error=None)


# ─── is_forwarded ────────────────────────────────────────────────────────────


def test_is_forwarded_detects_forward():
    assert handlers.is_forwarded(SimpleNamespace(forward_origin=object())) is True


def test_is_forwarded_false_for_normal():
    assert handlers.is_forwarded(SimpleNamespace(forward_origin=None)) is False
    assert handlers.is_forwarded(None) is False


# ─── allowlist gate ──────────────────────────────────────────────────────────


async def test_non_allowlisted_is_silent(gateway):
    bot = FakeBot()
    ctx = make_context(gateway, bot)
    await handlers.text_message(make_update(999, text="hi"), ctx)
    assert bot.sent == []  # no reply at all
    assert gateway.client.chat_calls == []  # nephilim never called


async def test_allowlisted_happy_path_relays(gateway):
    bot = FakeBot()
    ctx = make_context(gateway, bot)
    await handlers.text_message(make_update(111, text="hi"), ctx)
    assert gateway.client.chat_calls  # backend called
    assert bot.texts == ["hello from persona"]


# ─── forwarded refusal ───────────────────────────────────────────────────────


async def test_forwarded_message_refused_not_relayed(gateway):
    bot = FakeBot()
    ctx = make_context(gateway, bot)
    await handlers.text_message(make_update(111, text="malicious instructions", forwarded=True), ctx)
    assert bot.texts == [handlers.MSG_FORWARD_REFUSED]
    assert gateway.client.chat_calls == []  # never reached the LLM


# ─── error mapping (no leakage) ──────────────────────────────────────────────


async def test_unavailable_maps_to_generic(gateway):
    gateway.client.raise_on_chat = NephilimUnavailableError("http://127.0.0.1:8000 refused")
    bot = FakeBot()
    ctx = make_context(gateway, bot)
    await handlers.text_message(make_update(111, text="hi"), ctx)
    assert bot.texts == [handlers.MSG_UNAVAILABLE]
    # internal detail (URL) must not appear anywhere in the reply
    assert all("127.0.0.1" not in t for t in bot.texts)


async def test_unexpected_error_maps_to_generic(gateway):
    gateway.client.raise_on_chat = NephilimSessionNotFoundError("secret-session-uuid")
    bot = FakeBot()
    ctx = make_context(gateway, bot)
    await handlers.text_message(make_update(111, text="hi"), ctx)
    # SessionNotFound recreate path will retry once; make recreate also fail:
    # here chat always raises, so after recreate it raises again -> generic error.
    assert bot.texts == [handlers.MSG_ERROR]
    assert all("secret-session-uuid" not in t for t in bot.texts)


async def test_unexpected_non_nephilim_error_maps_to_generic(gateway):
    # A bug unrelated to nephilim (e.g. sqlite) must still map to the fixed
    # MSG_ERROR string, never leaking the exception text.
    gateway.client.raise_on_chat = RuntimeError("sqlite disk image is malformed at /secret/path")
    bot = FakeBot()
    ctx = make_context(gateway, bot)
    await handlers.text_message(make_update(111, text="hi"), ctx)
    assert bot.texts == [handlers.MSG_ERROR]
    assert all("secret" not in t for t in bot.texts)


async def test_empty_reply_gets_placeholder(gateway):
    gateway.client.chat_response = {"answer": "", "message_flow": "single"}
    bot = FakeBot()
    ctx = make_context(gateway, bot)
    await handlers.text_message(make_update(111, text="hi"), ctx)
    assert bot.texts == [handlers.MSG_EMPTY]


# ─── /start and /reset ───────────────────────────────────────────────────────


async def test_start_greets_new_session(gateway):
    bot = FakeBot()
    ctx = make_context(gateway, bot)
    await handlers.start_command(make_update(111), ctx)
    assert bot.texts == ["Greetings."]


async def test_start_acks_existing_session(gateway):
    gateway.store.set(111, "nephilim_eeva", "sess-existing")
    bot = FakeBot()
    ctx = make_context(gateway, bot)
    await handlers.start_command(make_update(111), ctx)
    assert bot.texts == [handlers.MSG_START_ACK]
    assert gateway.client.greeted == []


async def test_reset_clears_and_confirms(gateway):
    gateway.store.set(111, "nephilim_eeva", "sess-existing")
    bot = FakeBot()
    ctx = make_context(gateway, bot)
    await handlers.reset_command(make_update(111), ctx)
    assert gateway.client.cleared == ["sess-existing"]
    # The confirmation now carries an image line too — a reset that says
    # nothing about images cannot be distinguished from one that missed them.
    assert len(bot.texts) == 1
    assert bot.texts[0].startswith(handlers.MSG_RESET_DONE)
    assert "No images to remove." in bot.texts[0]


async def test_reset_non_allowlisted_silent(gateway):
    bot = FakeBot()
    ctx = make_context(gateway, bot)
    await handlers.reset_command(make_update(999), ctx)
    assert bot.sent == []
    assert gateway.client.cleared == []


async def test_persona_override_routes_to_nyx(gateway):
    # cfg fixture maps chat 222 -> nephilim_nyx
    bot = FakeBot()
    ctx = make_context(gateway, bot)
    await handlers.text_message(make_update(222, text="hi"), ctx)
    # session created under the nyx persona key
    assert gateway.store.get(222, "nephilim_nyx") is not None
    assert gateway.store.get(222, "nephilim_eeva") is None


# ─── /tools command (ADR-009 W3) ─────────────────────────────────────────────

_TOOLKIT = {
    "persona_key": "nephilim_eeva",
    "display_name": "E.E.V.A.",
    "nsfw": True,
    "toolsets": ["web", "wallet"],
    "tools": {
        "web": [{"name": "web_search", "description": "Search the web", "requires_hitl": False}],
        "wallet": [{"name": "solana_propose_swap", "description": "Propose a swap", "requires_hitl": True}],
    },
}


def test_format_toolkit_lists_tools_and_nsfw():
    out = handlers.format_toolkit(_TOOLKIT)
    assert "E.E.V.A." in out
    assert "web_search" in out and "solana_propose_swap" in out
    assert "🔞" in out  # nsfw banner
    assert "(asks first)" in out  # requires_hitl surfaced


def test_format_toolkit_empty():
    assert handlers.format_toolkit({"tools": {}}) == handlers.MSG_TOOLKIT_EMPTY


async def test_tools_command_happy_path(gateway):
    async def _toolkit(persona_key):
        return _TOOLKIT
    gateway.client.get_toolkit = _toolkit
    bot = FakeBot()
    await handlers.tools_command(make_update(111), make_context(gateway, bot))
    assert any("web_search" in t for t in bot.texts)


async def test_tools_command_non_allowlisted_silent(gateway):
    async def _toolkit(persona_key):
        raise AssertionError("must not be called for non-allowlisted chat")
    gateway.client.get_toolkit = _toolkit
    bot = FakeBot()
    await handlers.tools_command(make_update(999), make_context(gateway, bot))
    assert bot.sent == []


async def test_tools_command_unavailable_maps_to_fixed_string(gateway):
    async def _toolkit(persona_key):
        raise NephilimUnavailableError("down")
    gateway.client.get_toolkit = _toolkit
    bot = FakeBot()
    await handlers.tools_command(make_update(111), make_context(gateway, bot))
    assert bot.texts == [handlers.MSG_UNAVAILABLE]


# ---------- media delivery (M4) ----------


class _MediaBot:
    """Records sends in ORDER, so a test can assert the turn reads as one turn."""

    def __init__(self):
        self.sent: list[tuple[str, object]] = []

    async def send_message(self, chat_id, text, link_preview_options=None):
        self.sent.append(("text", text))

    async def send_document(self, chat_id, document, filename=None, caption=None, protect_content=None):
        self.sent.append(("doc", document))

    async def send_chat_action(self, *a, **k):
        pass


def _media_gateway(cfg, tmp_path, store):
    """A gateway whose media root is a throwaway directory."""
    import dataclasses

    from eeva_telegram.handlers import Gateway

    root = tmp_path / "media"
    (root / "img").mkdir(parents=True)
    return Gateway(
        config=dataclasses.replace(cfg, media_enabled=True, media_root=root),
        client=object(),
        store=store,
        llm_lock=asyncio.Lock(),
    )


async def test_media_is_sent_after_the_text(cfg, tmp_path, store):
    """Text first: the reply gives the image its context, and a persona reply
    can exceed the 1024-char caption limit so it cannot ride along as one."""
    gw = _media_gateway(cfg, tmp_path, store)
    good = gw.config.media_root / "img" / "a.png"
    good.write_bytes(b"x" * 10)
    bot = _MediaBot()

    await handlers._deliver_media(bot, 111, gw, [])
    await bot.send_message(111, "she speaks")
    await handlers._deliver_media(
        bot, 111, gw, [relay.MediaRef(path=str(good), filename="a.png")]
    )
    assert [kind for kind, _ in bot.sent] == ["text", "doc"]


async def test_a_rejected_item_reports_once_and_never_leaks_the_path(cfg, tmp_path, store):
    gw = _media_gateway(cfg, tmp_path, store)
    bot = _MediaBot()
    secret = tmp_path / "outside" / "very-secret.png"
    secret.parent.mkdir()
    secret.write_bytes(b"x")

    await handlers._deliver_media(
        bot, 111, gw, [relay.MediaRef(path=str(secret), filename="x.png")]
    )

    assert [kind for kind, _ in bot.sent] == ["text"]
    body = bot.sent[0][1]
    assert body == handlers.MSG_MEDIA_UNAVAILABLE
    assert "very-secret" not in body


async def test_one_bad_item_does_not_cost_the_good_one(cfg, tmp_path, store):
    """Send what you can. N failures still report ONCE, because N messages is
    the confusion that makes partial delivery worse than none."""
    gw = _media_gateway(cfg, tmp_path, store)
    good = gw.config.media_root / "img" / "good.png"
    good.write_bytes(b"x" * 10)
    bot = _MediaBot()

    await handlers._deliver_media(
        bot,
        111,
        gw,
        [
            relay.MediaRef(path="/nope/missing.png", filename="a.png"),
            relay.MediaRef(path=str(good), filename="good.png"),
            relay.MediaRef(path="/nope/also-missing.png", filename="b.png"),
        ],
    )

    kinds = [kind for kind, _ in bot.sent]
    assert kinds.count("doc") == 1
    assert kinds.count("text") == 1  # two failures, one report


async def test_no_media_sends_nothing_extra(cfg, tmp_path, store):
    gw = _media_gateway(cfg, tmp_path, store)
    bot = _MediaBot()
    await handlers._deliver_media(bot, 111, gw, [])
    assert bot.sent == []
