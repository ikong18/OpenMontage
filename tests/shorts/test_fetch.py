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
