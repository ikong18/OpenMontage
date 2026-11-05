"""Facts about downloaded clips for the UI (length, contact-sheet step), cached by file and mtime."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable

from lib.shorts.sheets import probe_video

_CACHE: dict[tuple[str, float], dict[str, Any]] = {}


def sheet_step(duration: float) -> int:
    """Seconds between contact-sheet frames: 4 s, stretched so a sheet never needs more than 30 frames."""
    return 4 if duration <= 120 else math.ceil(duration / 30)


def source_info(project: Path, spec: dict[str, Any],
                probe: Callable[[Path], dict[str, Any]] = probe_video) -> dict[str, dict[str, Any] | None]:
    root = Path(project).resolve()
    out: dict[str, dict[str, Any] | None] = {}
    for key, src in (spec.get("sources") or {}).items():
        rel = src.get("path") if isinstance(src, dict) else None
        try:
            path = (root / str(rel)).resolve()
            if not rel or not path.is_relative_to(root) or not path.is_file():
                out[key] = None
                continue
            cache_key = (str(path), path.stat().st_mtime)
            if cache_key not in _CACHE:
                info = probe(path)
                _CACHE[cache_key] = {"duration": info["duration"], "sheet_step": sheet_step(info["duration"]),
                                     "width": info["width"], "height": info["height"]}
            out[key] = _CACHE[cache_key]
        except (OSError, ValueError, KeyError, TypeError):
            out[key] = None
    return out
