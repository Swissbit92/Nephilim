# tests/backend/coordinator/test_notifications_route.py
"""The out-of-band notification surface.

What each load-bearing test would otherwise let through:

- `test_the_payload_is_chat_shaped` — the gateway parses this with
  `relay.extract_media`, the SAME extractor it uses on the chat path. A
  different shape here means a second parser, and the transport would then be
  proven on a path the real one does not use.
- `test_claiming_twice_returns_it_once` — the image delivered twice.
- `test_a_succeeded_job_whose_file_vanished_does_not_claim_success` — the
  silent-failure-as-success case this whole feature exists to prevent.
- `test_the_error_reason_is_not_in_the_user_facing_text` — operator detail
  must not describe the machine's internals in a chat.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.coordinator.repositories.image_job_repository import (
    ImageJobRepository,
    JobStatus,
)
from src.coordinator.server import app


@pytest.fixture(autouse=True)
def _deterministic_lines(monkeypatch):
    """Stub the in-voice line to a sentinel naming the SITUATION.

    ⚠️ These tests used to assert on the canned fallback text ("stopped",
    "didn't work out", "can't find it"). Those strings live in
    `persona_lines._FALLBACK` and are used ONLY when the model is
    unreachable — so the tests passed headless, failed the moment a model
    was available, and, worse, asserted the exact behaviour `persona_lines`
    exists to AVOID. They would have stayed green with in-voice generation
    completely broken.

    Asserting the situation instead tests the wiring that actually matters:
    a cancelled job must produce the CANCELLED line and not the failed one,
    which the old substring check could not tell apart.
    """
    from src.coordinator.services import persona_lines
    monkeypatch.setattr(persona_lines, "line",
                        lambda persona_key, situation: f"<<{situation}>>")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    r = ImageJobRepository(str(tmp_path / "jobs.db"))
    from src.coordinator import startup
    monkeypatch.setattr(startup, "get_image_job_repo", lambda: r)
    return r


@pytest.fixture
def client():
    # Deliberately NOT `with TestClient(app)`: the house convention, which
    # skips the lifespan. These endpoints need no startup state beyond the
    # patched repo, and running the real lifespan here would boot the whole app.
    return TestClient(app)


def _finished(repo, tmp_path, *, status=JobStatus.SUCCEEDED, png=True, error=None):
    job = repo.create(session_id="sess-1", persona_key="gwen", prompt="a fox")
    repo.claim_next()
    media_path = None
    if png:
        p = tmp_path / f"{job.id}.png"
        p.write_bytes(_PNG)
        media_path = str(p)
    repo.finish(job.id, status=status, media_path=media_path, error=error)
    return repo.get(job.id)


# ---------- the shape the gateway already knows how to read ----------


def test_the_payload_is_chat_shaped(repo, client, tmp_path):
    """`metadata.media` with path/filename/caption — exactly what
    `relay.extract_media` reads on the chat path."""
    job = _finished(repo, tmp_path)
    body = client.post("/notifications/claim").json()

    assert len(body["notifications"]) == 1
    note = body["notifications"][0]
    assert note["job_id"] == job.id
    assert note["session_id"] == "sess-1"

    media = note["metadata"]["media"]
    assert len(media) == 1
    item = media[0]
    assert item["path"] == job.media_path
    assert item["filename"].endswith(".png")
    assert item["caption"]
    assert item["bytes"] == len(_PNG)
    assert item["protect_content"] is True
    # The same envelope keys /chat returns, so one parser covers both.
    for key in ("answer", "message_flow", "message_count", "metadata"):
        assert key in note


def test_claiming_twice_returns_it_once(repo, client, tmp_path):
    _finished(repo, tmp_path)
    first = client.post("/notifications/claim").json()["notifications"]
    second = client.post("/notifications/claim").json()["notifications"]
    assert len(first) == 1
    assert second == [], "a second claim would deliver the image twice"


def test_unfinished_jobs_are_not_offered(repo, client):
    repo.create(session_id="sess-1", persona_key="gwen", prompt="a fox")
    repo.claim_next()  # running, not finished
    assert client.post("/notifications/claim").json()["notifications"] == []


def test_limit_is_bounded(repo, client):
    assert client.post("/notifications/claim?limit=0").status_code == 400
    assert client.post("/notifications/claim?limit=999").status_code == 400


# ---------- failures must be reported, never dressed as success ----------


def test_a_failed_job_is_offered_with_an_honest_message(repo, client, tmp_path):
    _finished(repo, tmp_path, status=JobStatus.FAILED, png=False,
              error="killed: the machine ran out of memory")
    note = client.post("/notifications/claim").json()["notifications"][0]
    assert note["status"] == "failed"
    assert note["metadata"]["media"] == []
    assert note["answer"] == "<<image_failed>>"


def test_the_error_reason_is_not_in_the_user_facing_text(repo, client, tmp_path):
    """Six distinct failure reasons collapse to one message: a failure must
    not describe the machine's internals in a chat."""
    _finished(repo, tmp_path, status=JobStatus.FAILED, png=False,
              error="killed: no progress for 180s in /media/jobs/abc123")
    note = client.post("/notifications/claim").json()["notifications"][0]
    assert "180s" not in note["answer"]
    assert "/media/jobs" not in note["answer"]
    # ...but the operator still gets it, out of band.
    assert "180s" in note["error"]


def test_a_succeeded_job_whose_file_vanished_does_not_claim_success(
    repo, client, tmp_path
):
    """rc 0 with nothing on disk is the exact shape this feature exists to
    stop rendering as success."""
    job = _finished(repo, tmp_path)
    import os
    os.remove(job.media_path)

    note = client.post("/notifications/claim").json()["notifications"][0]
    assert note["metadata"]["media"] == [], "offered a path that no longer exists"
    assert note["answer"] == "<<image_missing>>", (
        "a vanished file must say MISSING, not failed — the old substring "
        "check could not tell those two situations apart"
    )


def test_a_cancelled_job_says_so(repo, client, tmp_path):
    _finished(repo, tmp_path, status=JobStatus.CANCELLED, png=False)
    note = client.post("/notifications/claim").json()["notifications"][0]
    assert note["answer"] == "<<image_cancelled>>"


# ---------- handing one back ----------


def test_nack_makes_it_claimable_again(repo, client, tmp_path):
    """Without this a transient Telegram outage permanently swallows the
    result of a five-and-a-half-minute generation."""
    job = _finished(repo, tmp_path)
    client.post("/notifications/claim")
    assert client.post("/notifications/claim").json()["notifications"] == []

    assert client.post(f"/notifications/{job.id}/nack").status_code == 200
    again = client.post("/notifications/claim").json()["notifications"]
    assert len(again) == 1 and again[0]["job_id"] == job.id


def test_nack_on_an_unknown_job_is_404(repo, client):
    assert client.post("/notifications/deadbeef/nack").status_code == 404


# ---------- status is read-only ----------


def test_status_does_not_consume_the_notification(repo, client, tmp_path):
    """A status check must never eat a pending delivery."""
    job = _finished(repo, tmp_path)
    assert client.get(f"/notifications/jobs/{job.id}").status_code == 200
    assert len(client.post("/notifications/claim").json()["notifications"]) == 1


def test_status_on_an_unknown_job_is_404(repo, client):
    assert client.get("/notifications/jobs/nope").status_code == 404


_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x08\x00\x00\x00\x08"
    b"\x08\x02\x00\x00\x00K\x6d\x29\x5c\x00\x00\x00\nIDATx\x9cc\x60\x00\x00"
    b"\x00\x02\x00\x01\xe2!\xbc\x33\x00\x00\x00\x00IEND\xaeB`\x82"
)


# ---------- the cross-repo contract ----------


def test_the_payload_matches_the_frozen_contract(repo, client, tmp_path):
    """PRODUCER half of a two-sided contract.

    The gateway runs in its own venv and the backend venv has no `telegram`
    installed, so neither suite can call the other's code. The ecosystem
    already solved this once — CRA freezes its maker-grid model into JSON that
    eeva-sol asserts against, for the same reason — so the agreed payload
    lives in `tests/fixtures/notification_payload.json` and both sides assert
    against it.

    This half proves the coordinator still EMITS that shape. The gateway's
    `test_the_frozen_contract_is_parseable` proves it can still READ it. A
    change that breaks the gateway fails here first.
    """
    import json
    from pathlib import Path

    fixture = json.loads(
        (Path(__file__).resolve().parents[2] / "fixtures"
         / "notification_payload.json").read_text()
    )

    job = _finished(repo, tmp_path)
    note = client.post("/notifications/claim").json()["notifications"][0]

    # Normalise the parts that legitimately vary per run. `answer` and
    # `caption` are generated IN THE PERSONA'S VOICE, so they differ per
    # persona and per call by design — the contract is about SHAPE, and
    # pinning the wording here would make every voice change a cross-repo
    # contract break.
    note["job_id"] = "JOB_ID"
    note["answer"] = "<IN-VOICE>"
    if note["metadata"]["media"]:
        m = note["metadata"]["media"][0]
        m["path"], m["media_id"], m["sha256"] = "MEDIA_PATH", "MEDIA_ID", "SHA256"
        m["filename"] = "nephilim_MEDIA_ID.png"
        m["caption"] = "<IN-VOICE>"

    assert note == fixture["succeeded"], (
        "the notification payload changed shape. The Telegram gateway parses "
        "this with relay.extract_media and CANNOT be imported from here to "
        "check. If the change is intended, update "
        "tests/fixtures/notification_payload.json AND confirm the gateway's "
        "test_the_frozen_contract_is_parseable still passes."
    )
    assert job.id  # the fixture is about shape, not this id


# ---------- the cross-bot delivery bug, measured live 2026-10-02 ----------


def test_the_claim_can_be_filtered_to_one_persona(repo, client, tmp_path):
    """THE live bug: an image requested in the eeva chat arrived in the gwen
    chat.

    Two Telegram bots serve the same person, so `chat.id` is IDENTICAL for
    both (it is the user's id). Both pollers claim from this one endpoint, and
    whichever won sent the file with ITS OWN bot token. Filtering here rather
    than gateway-side is deliberate: a gateway check would have to hand the
    job back, and two pollers nacking each other's jobs ping-pong at the poll
    interval.
    """
    gwen = repo.create(session_id="s-gwen", persona_key="gwen", prompt="a fox")
    repo.claim_next()
    p = tmp_path / "g.png"; p.write_bytes(_PNG)
    repo.finish(gwen.id, status=JobStatus.SUCCEEDED, media_path=str(p))

    eeva = repo.create(session_id="s-eeva", persona_key="nephilim_eeva", prompt="a chart")
    repo.claim_next()
    q = tmp_path / "e.png"; q.write_bytes(_PNG)
    repo.finish(eeva.id, status=JobStatus.SUCCEEDED, media_path=str(q))

    got = client.post("/notifications/claim?personas=gwen").json()["notifications"]
    assert [n["job_id"] for n in got] == [gwen.id], "a bot claimed another bot's image"

    # ...and eeva's is STILL AVAILABLE, not consumed by the gwen poller.
    rest = client.post(
        "/notifications/claim?personas=nephilim_eeva"
    ).json()["notifications"]
    assert [n["job_id"] for n in rest] == [eeva.id]


def test_an_unfiltered_claim_still_takes_everything(repo, client, tmp_path):
    """Back-compat: a single-bot deployment passes no filter and is unchanged."""
    _finished(repo, tmp_path)
    assert len(client.post("/notifications/claim").json()["notifications"]) == 1


def test_a_filter_matching_nothing_takes_nothing(repo, client, tmp_path):
    job = _finished(repo, tmp_path)
    assert client.post("/notifications/claim?personas=nobody").json()["notifications"] == []
    # and the job is still claimable by its real owner
    again = client.post("/notifications/claim?personas=gwen").json()["notifications"]
    assert [n["job_id"] for n in again] == [job.id]


def test_an_empty_persona_filter_is_rejected(repo, client):
    """`?personas=` is almost certainly a bug in the caller, not a request to
    claim everything — refuse rather than silently widening the filter."""
    assert client.post("/notifications/claim?personas=").status_code == 400
    assert client.post("/notifications/claim?personas=,,").status_code == 400
