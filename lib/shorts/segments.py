"""Cut a source clip into a 1080x1920, 30 fps vertical segment.

Remotion cannot reliably seek into large originals ("no frame found at
position"), and it copies each source into its public dir on every render, so
every cut is pre-rendered to its own small file first.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

_FILL = "scale=-2:1920,crop=1080:1920,fps=30,format=yuv420p"
_BLURPAD = (
    "[0:v]split[a][b];"
    "[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=40:6[bg];"
    "[b]scale=1080:-2[fg];"
    "[bg][fg]overlay=(W-w)/2:(H-h)/2,fps=30,format=yuv420p"
)


def segment_ffmpeg_args(mode: str) -> list[str]:
    if mode == "fill":
        return ["-vf", _FILL]
    if mode == "blurpad":
        return ["-filter_complex", _BLURPAD]
    raise ValueError(f"unknown segment mode '{mode}' (use 'fill' or 'blurpad')")


def cut_segment(src: Path, in_s: float, dur: float, out: Path, mode: str) -> Path:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH; install it (for example: winget install Gyan.FFmpeg)")
    args = segment_ffmpeg_args(mode)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-ss", str(in_s), "-i", str(src), "-t", str(dur), "-an",
         *args, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-g", "30", str(out)],
        capture_output=True, text=True, encoding="utf-8",
    )
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        raise RuntimeError(f"ffmpeg failed cutting {Path(src).name}: {detail[-1] if detail else 'unknown error'}")
    return out
