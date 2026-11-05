"""Size-capped clip fetching (no network: head/download are injected)."""

from types import SimpleNamespace

import pytest

from lib.shorts.fetch import SizeCapError, check_size, fetch_clip

GB = 1024 * 1024 * 1024
MB = 1024 * 1024


def test_check_size_rejects_over_cap():
    with pytest.raises(SizeCapError) as exc:
        check_size(5 * GB, max_mb=400)
    assert "400" in str(exc.value)


def test_check_size_allows_under_cap():
    check_size(100 * MB, max_mb=400)


def test_check_size_rejects_unknown_by_default():
    with pytest.raises(SizeCapError) as exc:
        check_size(None)
    assert "unknown" in str(exc.value)


def test_check_size_allows_unknown_when_opted_in():
    check_size(None, allow_unknown_size=True)


def test_fetch_does_not_download_when_over_cap(tmp_path):
    def download(_cand, _dest):
        raise AssertionError("download must not run when the file is over the cap")

    cand = SimpleNamespace(download_url="https://example.com/big.mp4")
    with pytest.raises(SizeCapError):
        fetch_clip(cand, tmp_path / "big.mp4", max_mb=400, head=lambda _u: 5 * GB, download=download)


def test_fetch_downloads_when_under_cap(tmp_path):
    dest = tmp_path / "small.mp4"

    def download(_cand, out):
        out.write_bytes(b"x")
        return out

    cand = SimpleNamespace(download_url="https://example.com/small.mp4")
    result = fetch_clip(cand, dest, max_mb=400, head=lambda _u: 10 * MB, download=download)
    assert result == dest and dest.read_bytes() == b"x"


def test_fetch_unknown_size_refused_without_opt_in(tmp_path):
    cand = SimpleNamespace(download_url="https://example.com/x.mp4")
    with pytest.raises(SizeCapError):
        fetch_clip(cand, tmp_path / "x.mp4", head=lambda _u: None, download=lambda c, d: d)


# --- review fixes ---


def test_head_size_treats_error_status_as_unknown(monkeypatch):
    from lib.shorts import fetch as fetch_module

    fake = SimpleNamespace(status_code=403, headers={"content-length": "243"})
    monkeypatch.setattr("requests.head", lambda *a, **k: fake)
    assert fetch_module._head_size("https://example.com/x.mp4") is None


def test_head_size_reads_content_length_on_success(monkeypatch):
    from lib.shorts import fetch as fetch_module

    fake = SimpleNamespace(status_code=200, headers={"content-length": "1234"})
    monkeypatch.setattr("requests.head", lambda *a, **k: fake)
    assert fetch_module._head_size("https://example.com/x.mp4") == 1234


# ---- the size check against a real (local) HTTP server that behaves like Wikimedia ------------------------------

import threading  # noqa: E402
from http.server import BaseHTTPRequestHandler, HTTPServer  # noqa: E402

from lib.shorts.fetch import _head_size  # noqa: E402

TOTAL = 12345


def _serve(handler_cls):
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}/clip.webm"


class _NeedsAgent(BaseHTTPRequestHandler):
    """Refuses requests without a descriptive User-Agent, like Wikimedia does."""
    def _ok(self):
        return "OpenMontage" in (self.headers.get("User-Agent") or "")

    def do_HEAD(self):
        self.send_response(200 if self._ok() else 403)
        if self._ok():
            self.send_header("Content-Length", str(TOTAL))
        self.end_headers()

    def log_message(self, *a):
        pass


class _NoHead(BaseHTTPRequestHandler):
    """HEAD gives no length; a one-byte range request reports the total."""
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        if self.headers.get("Range"):
            self.send_response(206)
            self.send_header("Content-Range", f"bytes 0-0/{TOTAL}")
            self.send_header("Content-Length", "1")
            self.end_headers()
            self.wfile.write(b"x")
        else:
            self.send_response(200)
            self.send_header("Content-Length", str(TOTAL))
            self.end_headers()
            self.wfile.write(b"x" * TOTAL)

    def log_message(self, *a):
        pass


class _IgnoresRange(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(405)
        self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", str(TOTAL))
        self.end_headers()
        self.wfile.write(b"x" * TOTAL)

    def log_message(self, *a):
        pass


class _Nothing(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(404)
        self.end_headers()

    do_GET = do_HEAD

    def log_message(self, *a):
        pass


def test_size_check_sends_a_descriptive_user_agent():
    server, url = _serve(_NeedsAgent)
    try:
        assert _head_size(url) == TOTAL
    finally:
        server.shutdown()


def test_size_check_falls_back_to_a_one_byte_range_request():
    server, url = _serve(_NoHead)
    try:
        assert _head_size(url) == TOTAL
    finally:
        server.shutdown()


def test_size_check_reads_the_length_even_when_the_server_ignores_ranges():
    server, url = _serve(_IgnoresRange)
    try:
        assert _head_size(url) == TOTAL
    finally:
        server.shutdown()


def test_size_check_still_says_unknown_for_a_missing_file():
    server, url = _serve(_Nothing)
    try:
        assert _head_size(url) is None
    finally:
        server.shutdown()
