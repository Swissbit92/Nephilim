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
    assert "didn't work out" in note["answer"]


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
    assert "can't find it" in note["answer"]


def test_a_cancelled_job_says_so(repo, client, tmp_path):
    _finished(repo, tmp_path, status=JobStatus.CANCELLED, png=False)
    note = client.post("/notifications/claim").json()["notifications"][0]
    assert "stopped" in note["answer"]


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
