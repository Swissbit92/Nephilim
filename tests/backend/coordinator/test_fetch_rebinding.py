"""DNS rebinding: refuse on the connected address, not only the checked name."""

from unittest.mock import MagicMock, patch

from src.coordinator.services import web_fetch_service as wf


def _fetch(peer):
    stream = MagicMock()
    stream.get_extra_info.return_value = (peer, 443)
    resp = MagicMock()
    resp.extensions = {"network_stream": stream}
    resp.url.host = "public.example"
    resp.encoding = "utf-8"
    resp.iter_bytes.return_value = [b"<p>secret from inside the network</p>"]
    client = MagicMock()
    client.__enter__.return_value = client
    client.stream.return_value.__enter__.return_value = resp
    with (
        patch.object(wf, "_is_private_host", return_value=False),
        patch("httpx.Client", return_value=client),
    ):
        return wf.fetch_url("https://public.example/page", "raw"), resp


def test_private_peer_is_refused_before_any_body_is_read():
    out, resp = _fetch("127.0.0.1")
    assert out.startswith("ERROR") and "secret" not in out
    resp.iter_bytes.assert_not_called()


def test_public_peer_reads_normally():
    out, _ = _fetch("93.184.216.34")
    assert "secret" in out
