"""The API key must never enter the docker argv.

`subprocess.TimeoutExpired.__str__` embeds the full command unconditionally and
CPython offers no redaction hook, so any secret in argv reaches the log on the
first container timeout. That is not hypothetical here: it happened, and the live
BRAVE_API_KEY ended up in a world-readable backend log.
"""
import subprocess
from unittest.mock import patch

from src.coordinator.mcp_client_stdio import BraveMCPClientStdio

SECRET = "sk-test-do-not-leak-me-123456"


def _captured_cmd():
    c = BraveMCPClientStdio(api_key=SECRET, timeout=1)
    seen = {}

    class FakeProc:
        returncode = 0
        def communicate(self, input=None, timeout=None):
            return ('{"result":{}}', "")
        def kill(self): pass
        def wait(self, timeout=None): pass

    def fake_popen(cmd, **kw):
        seen["cmd"] = cmd
        seen["env"] = kw.get("env")
        return FakeProc()

    with patch.object(subprocess, "Popen", side_effect=fake_popen):
        try:
            c._spawn_mcp_container({"jsonrpc": "2.0", "method": "x", "id": 1})
        except Exception:
            pass
    return seen


def test_the_secret_is_not_in_argv():
    seen = _captured_cmd()
    joined = " ".join(seen["cmd"])
    assert SECRET not in joined, f"secret leaked into argv: {joined}"


def test_the_bare_env_flag_is_used():
    assert "BRAVE_API_KEY" in _captured_cmd()["cmd"]


def test_the_secret_travels_in_the_child_environment():
    assert _captured_cmd()["env"]["BRAVE_API_KEY"] == SECRET


def test_a_timeout_exception_cannot_carry_the_secret():
    """The actual failure mode, reproduced: TimeoutExpired stringifies the cmd."""
    cmd = _captured_cmd()["cmd"]
    exc = subprocess.TimeoutExpired(cmd, 10)
    assert SECRET not in str(exc)
