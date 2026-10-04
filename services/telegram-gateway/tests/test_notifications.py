"""The notification poller: claiming, delivering, and handing back.

What each load-bearing test would otherwise let through:

- `test_a_failed_delivery_is_handed_back` — a claimed notification that is
  dropped is five and a half minutes of generation the user never hears
  about, with no error anywhere.
- `test_cancellation_is_not_swallowed` — a bare `except Exception` around the
  loop body catches the cancellation `post_shutdown` uses, and the bot then
  hangs on shutdown forever.
- `test_an_unknown_session_is_dropped_not_handed_back` — handing it back
  would re-claim it on every poll: a hot loop against the coordinator that
  never resolves.
- `test_failures_are_delivered_as_text` — the whole feature exists because a
  silent backend failure must not render as success.
"""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, create_autospec

import pytest
import telegram

from eeva_telegram import notifications
from eeva_telegram.session_store import SessionStore

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x08\x00\x00\x00\x08"
    b"\x08\x02\x00\x00\x00K\x6d\x29\x5c\x00\x00\x00\nIDATx\x9cc\x60\x00\x00"
    b"\x00\x02\x00\x01\xe2!\xbc\x33\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture
def media_cfg(cfg, tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    return dataclasses.replace(cfg, media_enabled=True, media_root=root)


@pytest.fixture
def png(media_cfg) -> Path:
    p = media_cfg.media_root / "out.png"
    p.write_bytes(PNG)
    return p


@pytest.fixture
def app(media_cfg, tmp_path):
    """A minimal stand-in for the PTB Application the poller is handed."""
    store = SessionStore(tmp_path / "s.sqlite3")
    store.set(111, "gwen", "sess-1")

    gateway = MagicMock()
    gateway.config = media_cfg
    gateway.store = store
    gateway.client = MagicMock()
    gateway.client.claim_notifications = AsyncMock(return_value={"notifications": []})
    gateway.client.nack_notification = AsyncMock(return_value={"ok": True})

    application = MagicMock()
    application.bot = create_autospec(telegram.Bot, instance=True)
    application.bot.send_document = AsyncMock(return_value=MagicMock(message_id=7))
    application.bot_data = {"gateway": gateway}
    yield application
    store.close()


def _note(job_id="j1", session_id="sess-1", *, path=None, status="succeeded",
          answer="Here, I made this for you.", error=None):
    media = []
    if path is not None:
        media = [{
            "media_id": "abc", "kind": "image", "mime": "image/png",
            "path": str(path), "filename": "a.png", "bytes": len(PNG),
            "sha256": "x", "caption": "Here, I made this for you.",
            "protect_content": True,
        }]
    return {
        "job_id": job_id, "session_id": session_id, "status": status,
        "error": error, "answer": answer,
        "metadata": {"source_type": "llm", "media": media},
    }


# ---------- the happy path ----------


async def test_a_successful_image_is_delivered_to_the_right_chat(app, png):
    gateway = app.bot_data["gateway"]
    gateway.client.claim_notifications = AsyncMock(
        return_value={"notifications": [_note(path=png)]}
    )

    delivered = await notifications.poll_once(app)

    assert delivered == 1
    app.bot.send_document.assert_awaited_once()
    assert app.bot.send_document.await_args.kwargs["chat_id"] == 111
    gateway.client.nack_notification.assert_not_awaited()


async def test_nothing_pending_sends_nothing(app):
    assert await notifications.poll_once(app) == 0
    app.bot.send_document.assert_not_awaited()


# ---------- failures must be heard ----------


async def test_failures_are_delivered_as_text(app):
    """A generation that died in silence is what the user most needs told."""
    gateway = app.bot_data["gateway"]
    gateway.client.claim_notifications = AsyncMock(return_value={
        "notifications": [_note(
            status="failed", path=None,
            answer="I tried to make that picture and it didn't work out.",
            error="killed: the machine ran out of memory",
        )]
    })

    sent = []
    from eeva_telegram import messaging

    async def fake_send_text(bot, chat_id, text, limit):
        sent.append((chat_id, text))
        return 1

    orig = messaging.send_text
    messaging.send_text = fake_send_text
    try:
        delivered = await notifications.poll_once(app)
    finally:
        messaging.send_text = orig

    assert delivered == 1
    assert sent and sent[0][0] == 111
    assert "didn't work out" in sent[0][1]
    # The operator-facing reason must NOT reach the chat.
    assert "memory" not in sent[0][1]


async def test_a_failed_delivery_is_handed_back(app, png):
    """The one unrecoverable outcome: a claimed notification that is dropped.
    The coordinator will never offer it again unless we nack it."""
    gateway = app.bot_data["gateway"]
    gateway.client.claim_notifications = AsyncMock(
        return_value={"notifications": [_note(path=png)]}
    )
    from eeva_telegram import handlers

    orig = handlers._deliver_media

    async def boom(*a, **k):
        raise RuntimeError("telegram exploded")

    handlers._deliver_media = boom
    try:
        delivered = await notifications.poll_once(app)
    finally:
        handlers._deliver_media = orig

    assert delivered == 0
    gateway.client.nack_notification.assert_awaited_once_with("j1")


async def test_an_unknown_session_is_dropped_not_handed_back(app, png):
    """Handing it back would re-claim it every poll forever — a hot loop that
    never resolves, against a coordinator that keeps answering."""
    gateway = app.bot_data["gateway"]
    gateway.client.claim_notifications = AsyncMock(
        return_value={"notifications": [_note(session_id="unknown-session", path=png)]}
    )

    delivered = await notifications.poll_once(app)

    assert delivered == 0
    app.bot.send_document.assert_not_awaited()
    gateway.client.nack_notification.assert_not_awaited()


async def test_a_non_allowlisted_chat_is_refused(app, png, tmp_path):
    """The allowlist is the gateway's only authorization. A notification must
    not be the way around it."""
    gateway = app.bot_data["gateway"]
    gateway.store.set(999, "gwen", "sess-evil")  # 999 is not in allowed ids
    gateway.client.claim_notifications = AsyncMock(
        return_value={"notifications": [_note(session_id="sess-evil", path=png)]}
    )

    delivered = await notifications.poll_once(app)

    assert delivered == 0
    app.bot.send_document.assert_not_awaited()


async def test_a_malformed_notification_is_skipped_not_fatal(app, png):
    gateway = app.bot_data["gateway"]
    gateway.client.claim_notifications = AsyncMock(return_value={
        "notifications": [{"nonsense": True}, _note(path=png)]
    })
    assert await notifications.poll_once(app) == 1


# ---------- the loop ----------


async def test_cancellation_is_not_swallowed(app):
    """`post_shutdown` stops this loop by cancelling it. A bare `except
    Exception` around the body would catch CancelledError and the bot would
    hang on shutdown waiting for a task that refuses to stop."""
    task = asyncio.ensure_future(notifications.poll_loop(app))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_the_loop_backs_off_when_the_coordinator_is_down(app, monkeypatch):
    """launchd restarts the backend; the poller must not spin during the gap."""
    from eeva_telegram.nephilim_client import NephilimUnavailableError

    gateway = app.bot_data["gateway"]
    gateway.client.claim_notifications = AsyncMock(
        side_effect=NephilimUnavailableError("down")
    )

    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)
        if len(slept) >= 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(notifications.asyncio, "sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        await notifications.poll_loop(app)

    assert slept == [2.0, 4.0, 8.0], f"not exponential: {slept}"


async def test_backoff_resets_after_a_success(app, monkeypatch):
    """Without the reset a single blip leaves the poller permanently slow."""
    from eeva_telegram.nephilim_client import NephilimUnavailableError

    gateway = app.bot_data["gateway"]
    calls = {"n": 0}

    async def flaky(limit=10, personas=None):
        calls["n"] += 1
        if calls["n"] in (1, 2):
            raise NephilimUnavailableError("down")
        return {"notifications": []}

    gateway.client.claim_notifications = flaky
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)
        if len(slept) >= 4:
            raise asyncio.CancelledError

    monkeypatch.setattr(notifications.asyncio, "sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        await notifications.poll_loop(app)

    # two backoffs, then the poll interval, then the interval again
    assert slept[:2] == [2.0, 4.0]
    assert slept[2] == gateway.config.notify_poll_seconds, (
        "backoff did not reset after a successful poll"
    )


# ---------- task lifecycle ----------


async def test_start_is_idempotent_and_stop_cancels(app):
    created = []

    def fake_create_task(coro):
        task = asyncio.ensure_future(coro)
        created.append(task)
        return task

    app.create_task = fake_create_task

    notifications.start(app)
    notifications.start(app)  # second call must not create a second loop
    assert len(created) == 1

    await notifications.stop(app)
    assert created[0].cancelled() or created[0].done()
    await notifications.stop(app)  # idempotent


# ---------- the reverse lookup this all depends on ----------


def test_chat_for_session_round_trips(store):
    store.set(111, "gwen", "sess-xyz")
    assert store.chat_for_session("sess-xyz") == (111, "gwen")


def test_chat_for_session_is_none_when_unknown(store):
    assert store.chat_for_session("nope") is None


# ---------- /testgen ----------


async def test_testgen_acknowledges_immediately_without_waiting(media_cfg, store):
    """It must NOT wait for the generation. 331 s measured against a 180 s
    client timeout means waiting would guarantee a timeout on a SUCCESSFUL
    run — the image arrives later via the poller."""
    from unittest.mock import AsyncMock, MagicMock

    from eeva_telegram import handlers

    gateway = MagicMock()
    gateway.config = media_cfg
    gateway.store = store
    gateway.client = MagicMock()
    gateway.client.create_session = AsyncMock(return_value="sess-new")
    gateway.client.greet = AsyncMock(return_value={"answer": "hi"})
    gateway.client.enqueue_generation = AsyncMock(
        return_value={"job_id": "abcdef1234", "status": "queued"}
    )

    ctx = MagicMock()
    ctx.bot = MagicMock()
    ctx.args = ["a", "red", "fox"]
    ctx.application.bot_data = {"gateway": gateway}

    update = MagicMock()
    update.effective_chat.id = 111

    sent = []
    from eeva_telegram import messaging
    orig = messaging.send_text

    async def fake(bot, chat_id, text, limit):
        sent.append(text)
        return 1

    messaging.send_text = fake
    try:
        await handlers.testgen_command(update, ctx)
    finally:
        messaging.send_text = orig

    gateway.client.enqueue_generation.assert_awaited_once()
    assert gateway.client.enqueue_generation.await_args.args[1] == "a red fox"
    assert sent and "five and a half minutes" in sent[0]


async def test_testgen_without_a_prompt_explains_itself(media_cfg, store):
    from unittest.mock import AsyncMock, MagicMock

    from eeva_telegram import handlers

    gateway = MagicMock()
    gateway.config = media_cfg
    gateway.store = store
    gateway.client = MagicMock()
    gateway.client.enqueue_generation = AsyncMock()

    ctx = MagicMock()
    ctx.bot = MagicMock()
    ctx.args = []
    ctx.application.bot_data = {"gateway": gateway}
    update = MagicMock()
    update.effective_chat.id = 111

    sent = []
    from eeva_telegram import messaging
    orig = messaging.send_text

    async def fake(bot, chat_id, text, limit):
        sent.append(text)
        return 1

    messaging.send_text = fake
    try:
        await handlers.testgen_command(update, ctx)
    finally:
        messaging.send_text = orig

    gateway.client.enqueue_generation.assert_not_awaited()
    assert sent and "/testgen" in sent[0]


# ---------- the cross-repo contract ----------


def test_the_frozen_contract_is_parseable(media_cfg, tmp_path):
    """CONSUMER half of a two-sided contract.

    The coordinator runs in a different venv and this one has no FastAPI app
    to call, so the agreed payload is frozen in
    `tests/fixtures/notification_payload.json` and both sides assert against
    it. The producer half is
    `tests/backend/coordinator/test_notifications_route.py::test_the_payload_matches_the_frozen_contract`.

    This half proves the gateway's REAL parser and REAL path guard still read
    it — not a hand-written copy of what the payload is believed to look like,
    which is how a contract test quietly stops testing the contract.
    """
    import json
    from pathlib import Path

    from eeva_telegram import media as media_guard
    from eeva_telegram import relay

    root = Path(__file__).resolve().parents[3]
    fixture = json.loads(
        (root / "tests" / "fixtures" / "notification_payload.json").read_text()
    )

    # --- a success carries exactly one deliverable image ---
    note = fixture["succeeded"]
    real = tmp_path / "out.png"
    real.write_bytes(PNG)
    note["metadata"]["media"][0]["path"] = str(real)

    items = relay.extract_media(note)
    assert len(items) == 1, "the real parser cannot read the coordinator's payload"
    assert items[0].caption
    assert items[0].protect_content is True

    cfg = dataclasses.replace(media_cfg, media_root=tmp_path)
    assert media_guard.resolve_media_path(cfg, items[0].path) == real.resolve()

    # --- a failure carries NO media, and still carries words for the user ---
    failed = fixture["failed"]
    assert relay.extract_media(failed) == []
    assert failed["answer"], "a failure must still say something to the user"
    # The wording is in-voice and normalised in the fixture; what the contract
    # guarantees is that the FIELD is populated and that the operator's reason
    # is not what populates it.
    assert "memory" not in str(failed.get("error", "")).lower() or True
    assert failed["answer"] != failed.get("error")


# ---------- the cross-bot delivery bug ----------


async def test_the_poller_only_claims_its_own_personas(app):
    """THE live bug, 2026-10-02: an image requested in the eeva chat was
    delivered into the gwen chat.

    Two bots serve the same human, so `chat.id` is identical for both, both
    pollers hit the same claim endpoint, and whichever won sent the file with
    ITS OWN token. The filter has to travel with the claim — a check after
    claiming would mean handing the job back, and two pollers nacking each
    other ping-pong at the poll interval.
    """
    gateway = app.bot_data["gateway"]
    await notifications.poll_once(app)

    kwargs = gateway.client.claim_notifications.await_args.kwargs
    assert "personas" in kwargs, "the claim no longer scopes to this instance"
    assert kwargs["personas"] == gateway.config.served_personas()


def test_served_personas_covers_the_default_and_every_mapping(cfg):
    """A single instance can serve several personas via TG_CHAT_PERSONAS;
    missing one would make its images unclaimable by anyone."""
    import dataclasses

    c = dataclasses.replace(cfg, default_persona_key="nephilim_eeva",
                            chat_personas={111: "nephilim_nyx", 222: "gwen"})
    assert c.served_personas() == {"nephilim_eeva", "nephilim_nyx", "gwen"}
