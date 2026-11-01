"""A forbidden destination must receive nothing, proved against a real socket.

The earlier check read the connected address from the response, by which time the
request had already landed. These tests count requests at a real server.
"""
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from src.coordinator import startup  # noqa: F401
from src.coordinator.services import web_fetch_service as wf


class _Server:
    def __enter__(self):
        outer = self
        outer.hits = []
        outer.redirect_to = None

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.hits.append(self.path)
                if outer.redirect_to and self.path == "/start":
                    self.send_response(302)
                    self.send_header("Location", outer.redirect_to)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<p>reachable body text here for extraction</p>")

            def log_message(self, *args):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *args):
        self.httpd.shutdown()
        self.httpd.server_close()


def _dns(mapping):
    """A resolver answering from `mapping`, whose values may be per-call sequences."""
    real = socket.getaddrinfo
    calls = {}

    def fake(host, port=None, *args, **kwargs):
        if host not in mapping:
            return real(host, port, *args, **kwargs)
        answers = mapping[host]
        n = calls.get(host, 0)
        calls[host] = n + 1
        ip = answers[min(n, len(answers) - 1)]
        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        address = (ip, port or 0, 0, 0) if ":" in ip else (ip, port or 0)
        return [(family, socket.SOCK_STREAM, 6, "", address)]

    return fake


def test_control_a_permitted_address_is_really_reached(monkeypatch):
    """Without this, every 'received nothing' below could be a dead server."""
    with _Server() as server:
        monkeypatch.setattr(socket, "getaddrinfo", _dns({"public.test": ["127.0.0.1"]}))
        monkeypatch.setattr(wf, "_is_private_ip", lambda ip: False)
        out = wf.fetch_url(f"http://public.test:{server.port}/page", "raw")
    assert server.hits == ["/page"] and "reachable" in out


def test_rebinding_public_then_private_sends_nothing(monkeypatch):
    with _Server() as server:
        monkeypatch.setattr(
            socket, "getaddrinfo", _dns({"rebind.test": ["93.184.216.34", "127.0.0.1"]})
        )
        out = wf.fetch_url(f"http://rebind.test:{server.port}/secret", "raw")
    assert out.startswith("ERROR") and server.hits == []


def test_private_address_with_the_precheck_bypassed_sends_nothing(monkeypatch):
    with _Server() as server:
        monkeypatch.setattr(wf, "_is_private_host", lambda host: False)
        out = wf.fetch_url(f"http://127.0.0.1:{server.port}/secret", "raw")
    assert out.startswith("ERROR") and server.hits == []


def test_redirect_to_a_blocked_host_sends_nothing_there(monkeypatch):
    with _Server() as public, _Server() as internal:
        public.redirect_to = f"http://internal.test:{internal.port}/secret"
        monkeypatch.setattr(socket, "getaddrinfo", _dns({
            # The mapped form reaches this machine too, so with no guard the hop
            # lands. The test would otherwise pass against an unreachable address.
            "public.test": ["127.0.0.1"], "internal.test": ["::ffff:127.0.0.1"],
        }))
        monkeypatch.setattr(wf, "_is_private_ip", lambda ip: ip != "127.0.0.1")
        out = wf.fetch_url(f"http://public.test:{public.port}/start", "raw")
    assert public.hits == ["/start"] and internal.hits == []
    assert out.startswith("ERROR")


def test_a_hostname_with_any_private_answer_is_refused(monkeypatch):
    with _Server() as server:
        real = socket.getaddrinfo

        def both(host, port=None, *a, **k):
            if host != "mixed.test":
                return real(host, port, *a, **k)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 0))
                    for ip in ("93.184.216.34", "127.0.0.1")]

        monkeypatch.setattr(socket, "getaddrinfo", both)
        monkeypatch.setattr(wf, "_is_private_host", lambda host: False)
        assert wf.fetch_url(f"http://mixed.test:{server.port}/x", "raw").startswith("ERROR")
    assert server.hits == []


def test_an_unreachable_first_address_falls_through_to_the_next(monkeypatch):
    with _Server() as server:
        real = socket.getaddrinfo

        def both(host, port=None, *a, **k):
            if host != "dual.test":
                return real(host, port, *a, **k)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 0))
                    for ip in ("127.0.0.2", "127.0.0.1")]

        monkeypatch.setattr(socket, "getaddrinfo", both)
        monkeypatch.setattr(wf, "_is_private_ip", lambda ip: False)
        monkeypatch.setattr(wf, "_is_private_host", lambda host: False)
        out = wf.fetch_url(f"http://dual.test:{server.port}/page", "raw")
    assert server.hits == ["/page"] and "reachable" in out


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "::1", "::ffff:127.0.0.1", "10.0.0.1", "192.168.1.1", "172.16.0.1",
    "169.254.169.254", "fe80::1", "fc00::1", "0.0.0.0", "100.64.0.1", "100.127.255.254",
    "::ffff:100.64.0.1", "::ffff:10.0.0.1",
])
def test_blocked_addresses(ip):
    assert wf._is_private_ip(ip) is True


@pytest.mark.parametrize("ip", ["93.184.216.34", "1.1.1.1", "100.63.255.255", "100.128.0.1"])
def test_public_addresses(ip):
    assert wf._is_private_ip(ip) is False
