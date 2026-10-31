"""SearXNG watch: alerts once, recovers once, and treats 'up but empty' as down."""

import importlib.util
import json
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

_SPEC = importlib.util.spec_from_file_location(
    "searxng_watch", Path(__file__).parents[3] / "scripts" / "searxng_watch.py"
)
watch = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(watch)


def _fresh():
    return {"failures": 0, "alerted": False}


def _response(payload):
    r = MagicMock()
    r.__enter__.return_value = r
    r.read.return_value = json.dumps(payload).encode()
    return r


class TestProbe:
    def test_results_are_healthy(self):
        with patch("urllib.request.urlopen", return_value=_response({"results": [{}]})):
            assert watch.probe() is None

    def test_up_but_empty_is_a_problem_and_names_the_blocked_engines(self):
        payload = {"results": [], "unresponsive_engines": [["bing images", "CAPTCHA"]]}
        with patch("urllib.request.urlopen", return_value=_response(payload)):
            assert "bing images" in watch.probe()

    def test_connection_refused_is_a_problem(self):
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            assert "not answering" in watch.probe()

    def test_garbage_response_is_a_problem(self):
        r = _response({})
        r.read.return_value = b"<html>"
        with patch("urllib.request.urlopen", return_value=r):
            assert "not answering" in watch.probe()


class TestStep:
    def test_a_single_failure_stays_quiet(self):
        send = MagicMock(return_value=True)
        state = watch.step(_fresh(), "down", send)
        send.assert_not_called()
        assert state["failures"] == 1

    def test_alerts_once_on_the_second_failure_then_stays_quiet(self):
        send = MagicMock(return_value=True)
        state = watch.step(watch.step(_fresh(), "down", send), "down", send)
        assert state["alerted"] is True
        watch.step(state, "down", send)
        send.assert_called_once()

    def test_a_failed_send_retries_on_the_next_run(self):
        state = watch.step({"failures": 1, "alerted": False}, "down", MagicMock(return_value=False))
        assert state["alerted"] is False
        send = MagicMock(return_value=True)
        assert watch.step(state, "down", send)["alerted"] is True

    def test_recovery_is_announced_once_and_resets(self):
        send = MagicMock(return_value=True)
        state = watch.step({"failures": 5, "alerted": True}, None, send)
        send.assert_called_once()
        assert state == _fresh()

    def test_a_failed_recovery_send_is_retried(self):
        before = {"failures": 5, "alerted": True}
        assert watch.step(before, None, MagicMock(return_value=False)) == before

    def test_healthy_with_nothing_owed_sends_nothing(self):
        send = MagicMock()
        watch.step(_fresh(), None, send)
        send.assert_not_called()


def test_send_retries_then_succeeds_and_targets_only_the_first_chat(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# c\nTG_BOT_TOKEN=abc\nTG_ALLOWED_CHAT_IDS=111, 222\n")
    calls = []

    def fake(url, data=None, timeout=None):
        calls.append(data)
        if len(calls) == 1:
            raise urllib.error.URLError("x")
        return _response({})

    with patch("urllib.request.urlopen", side_effect=fake), patch.object(watch.time, "sleep"):
        assert watch.send_telegram("hi", env) is True
    assert len(calls) == 2
    assert b"chat_id=111" in calls[1] and b"222" not in calls[1]


def test_missing_credentials_is_a_failed_send(tmp_path):
    env = tmp_path / ".env"
    env.write_text("TG_BOT_TOKEN=\n")
    assert watch.send_telegram("hi", env) is False


def test_a_missing_env_file_is_a_failed_send(tmp_path):
    assert watch.send_telegram("hi", tmp_path / "absent") is False


def test_a_corrupt_state_file_starts_fresh(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("{nope")
    assert watch.load_state(path) == _fresh()
