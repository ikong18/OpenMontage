"""Probe videos and make contact sheets (one frame every `step` seconds) so footage can be picked by eye."""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path
from typing import Any


def probe_video(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"video not found: {path}")
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height:format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise OSError(f"ffprobe failed for {path.name}: {(proc.stderr or '').strip()[:200]}")
    data = json.loads(proc.stdout or "{}")
    stream = (data.get("streams") or [{}])[0]
    return {
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
        "duration": float((data.get("format") or {}).get("duration") or 0.0),
    }


def make_contact_sheet(
    video: Path, out: Path, step: float = 4.0, cols: int = 6, rows: int = 5, thumb_width: int = 240
) -> dict[str, Any]:
    """Frame n (row-major) is at n * step seconds. No text overlay: drawtext crashes on some ffmpeg builds."""
    video, out = Path(video), Path(out)
    info = probe_video(video)
    out.parent.mkdir(parents=True, exist_ok=True)
    frames = min(cols * rows, max(1, math.ceil(info["duration"] / step)))
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(video), "-vf",
         f"fps=1/{step:g},scale={thumb_width}:-1,tile={cols}x{rows}", "-frames:v", "1", str(out)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0 or not out.is_file():
        raise OSError(f"ffmpeg could not make a contact sheet: {(proc.stderr or '').strip()[:200]}")
    return {"path": str(out), "step": step, "cols": cols, "frames": frames}


def extract_frame(video: Path, seconds: float, out: Path, width: int = 640) -> Path:
    """One real frame of `video` at `seconds`, as a JPEG, so it can be looked at."""
    video, out = Path(video), Path(out)
    if not video.is_file():
        raise FileNotFoundError(f"video not found: {video}")
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-ss", f"{max(seconds, 0):.2f}", "-i", str(video), "-frames:v", "1",
         "-vf", f"scale={width}:-1", str(out)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0 or not out.is_file():
        raise OSError(f"ffmpeg could not extract a frame at {seconds:g}s: {(proc.stderr or '').strip()[:200]}")
    return out
