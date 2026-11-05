"""Size-capped clip download.

direct_clip_search downloads NASA originals before it knows their size (one
search pulled 5 GB for a 30-second Short). fetch_clip checks the size first and
refuses over the cap, so a single mistake cannot fill the disk.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

DEFAULT_MAX_MB = 400.0


class SizeCapError(RuntimeError):
    """The file is larger than the cap, or its size cannot be determined."""


USER_AGENT = "OpenMontageShorts/1.0 (personal, non-commercial use; https://github.com/ikong18/OpenMontage)"


def _head_size(url: str) -> Optional[int]:
    """File size in bytes, or None when it cannot be learned. Some hosts (Wikimedia) refuse requests without a descriptive
    User-Agent, refuse HEAD, or omit the length: so ask HEAD first, then for just the first byte of the file."""
    import re

    import requests  # lazy: tests inject `head` instead

    headers = {"User-Agent": USER_AGENT}
    try:
        response = requests.head(url, headers=headers, allow_redirects=True, timeout=15)
        if response.status_code < 400:
            length = response.headers.get("content-length")
            if length:
                return int(length)
        probe = requests.get(url, headers={**headers, "Range": "bytes=0-0"}, stream=True, allow_redirects=True, timeout=15)
        try:
            if probe.status_code == 206:
                match = re.search(r"/(\d+)\s*$", probe.headers.get("content-range", ""))
                return int(match.group(1)) if match else None
            if probe.status_code == 200:  # the server ignored the range: the header still carries the whole size
                length = probe.headers.get("content-length")
                return int(length) if length else None
            return None  # an error page's length says nothing about the real file
        finally:
            probe.close()
    except Exception:
        return None


def check_size(
    size_bytes: Optional[int],
    max_mb: float = DEFAULT_MAX_MB,
    allow_unknown_size: bool = False,
) -> None:
    if size_bytes is None:
        if allow_unknown_size:
            return
        raise SizeCapError("file size is unknown; pass --allow-unknown-size to download anyway")
    size_mb = size_bytes / (1024 * 1024)
    if size_mb > max_mb:
        raise SizeCapError(
            f"file is {size_mb:,.0f} MB, over the {max_mb:g} MB cap; pick a smaller clip or raise --max-mb"
        )


def _adapter_download(candidate: Any, dest: Path) -> Path:
    from tools.video.stock_sources import all_sources

    adapters = {s.name: s for s in all_sources()}
    return adapters[candidate.source].download(candidate, dest)


def fetch_clip(
    candidate: Any,
    dest: Path,
    *,
    max_mb: float = DEFAULT_MAX_MB,
    allow_unknown_size: bool = False,
    head: Callable[[str], Optional[int]] = _head_size,
    download: Optional[Callable[[Any, Path], Path]] = None,
) -> Path:
    check_size(head(candidate.download_url), max_mb, allow_unknown_size)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    return (download or _adapter_download)(candidate, dest)
