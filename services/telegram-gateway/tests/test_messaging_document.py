"""send_document — the call contract, and the wire.

Two kinds of test, deliberately:

(i) ``create_autospec(telegram.Bot)`` rather than a fourth hand-rolled FakeBot.
    There are already three partial ones in this suite, and a duck-typed fake
    cannot catch a signature mismatch — a typo'd kwarg is accepted silently and
    the suite stays green while production raises TypeError. Autospec enforces
    the real signature.

(ii) ONE respx test that lets the REAL ``Bot.send_document`` run and asserts on
     the bytes that reach the wire. The autospec tests prove we called PTB
     correctly; only this one proves PTB does what we think with a ``Path``.
     That distinction is not academic — ``InputMediaDocument`` given a Path
     silently converts it to a ``file://`` string and uploads nothing, so
     "it was called with the right argument" and "the file was uploaded" are
     genuinely different claims here.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import create_autospec

import httpx
import pytest
import respx
import telegram
from telegram.constants import MessageLimit

from eeva_telegram import messaging

PNG = b"\x89PNG\r\n\x1a\n" + b"payload-bytes-that-must-survive" * 50


@pytest.fixture
def png(tmp_path) -> Path:
    p = tmp_path / "art.png"
    p.write_bytes(PNG)
    return p


@pytest.fixture
def bot():
    return create_autospec(telegram.Bot, instance=True)


# ---------- (i) the call contract ----------


async def test_passes_a_path_not_bytes_or_a_handle(bot, png):
    """PTB re-opens a Path inside each call, which is what makes a retry safe.
    An open handle would be at EOF on attempt 2 and upload an empty body that
    returns ok:true — silent corruption."""
    await messaging.send_document(bot, 111, png, filename="art.png")
    assert bot.send_document.await_args.kwargs["document"] is png


async def test_never_sets_parse_mode(bot, png):
    """A caption is the same surface as a message body: messaging.py's
    anti-markup-injection rule extends to it."""
    await messaging.send_document(bot, 111, png, filename="art.png", caption="hi *there*")
    assert "parse_mode" not in bot.send_document.await_args.kwargs


async def test_protect_content_defaults_on(bot, png):
    await messaging.send_document(bot, 111, png, filename="art.png")
    assert bot.send_document.await_args.kwargs["protect_content"] is True


async def test_protect_content_can_be_turned_off_explicitly(bot, png):
    await messaging.send_document(bot, 111, png, filename="a.png", protect_content=False)
    assert bot.send_document.await_args.kwargs["protect_content"] is False


async def test_filename_is_forwarded(bot, png):
    await messaging.send_document(bot, 111, png, filename="eeva_2026.png")
    assert bot.send_document.await_args.kwargs["filename"] == "eeva_2026.png"


async def test_long_caption_is_truncated_at_the_api_limit(bot, png, caplog):
    await messaging.send_document(bot, 111, png, filename="a.png", caption="x" * 2000)
    sent = bot.send_document.await_args.kwargs["caption"]
    assert len(sent) == MessageLimit.CAPTION_LENGTH
    assert "truncated" in caplog.text  # loud, not silent


async def test_a_short_caption_is_untouched(bot, png):
    await messaging.send_document(bot, 111, png, filename="a.png", caption="hello")
    assert bot.send_document.await_args.kwargs["caption"] == "hello"


async def test_autospec_rejects_a_mistyped_kwarg(bot, png):
    """Proves the fake can actually catch a signature drift — the thing a
    hand-rolled duck-typed FakeBot cannot do."""
    with pytest.raises(TypeError):
        await bot.send_document(chat_id=1, document=png, bogus_kwarg=True)


# ---------- (ii) the wire ----------


@respx.mock
async def test_the_file_bytes_reach_the_wire_verbatim(png):
    """The strongest offline statement of 'losslessly' available.

    Falsifiable three ways: the bytes could be absent (the Path-to-file:// trap),
    respx could fail to intercept PTB's own httpx client, or parse_mode could
    leak in from a default.
    """
    # Bot.__aenter__ calls initialize() -> getMe before anything else.
    respx.post("https://api.telegram.org/bottoken/getMe").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "id": 1,
                    "is_bot": True,
                    "first_name": "test",
                    "username": "testbot",
                },
            },
        )
    )
    route = respx.post("https://api.telegram.org/bottoken/sendDocument").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "message_id": 1,
                    "date": 0,
                    "chat": {"id": 111, "type": "private"},
                    "document": {"file_id": "f", "file_unique_id": "u"},
                },
            },
        )
    )

    real_bot = telegram.Bot("token")
    async with real_bot:
        await messaging.send_document(real_bot, 111, png, filename="art.png")

    body = route.calls[0].request.content
    assert b"multipart/form-data" in route.calls[0].request.headers["content-type"].encode()
    assert PNG in body, "the file's bytes did not reach the request body"
    assert b'filename="art.png"' in body
    assert b"protect_content" in body
    assert b"parse_mode" not in body
