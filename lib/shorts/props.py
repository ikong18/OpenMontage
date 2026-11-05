"""Remotion props for a Short, and the guard that stops a black tail."""

from __future__ import annotations

from pathlib import Path
from typing import Any

# The shorts-captions playbook look. Without it Explainer falls back to 42 px,
# 6 words and 80 px from the bottom, which breaks the platform safe zone.
CAPTION_STYLE: dict[str, Any] = {
    "fontSize": 84,
    "wordsPerPage": 4,
    "bottomOffset": 380,
    "color": "#FFFFFF",
    "highlightColor": "#FACC15",
    "backgroundColor": "rgba(0,0,0,0.45)",
}


def check_cut_coverage(total_cut_seconds: float, audio_seconds: float, tolerance: float = 0.1) -> None:
    if total_cut_seconds < audio_seconds - tolerance:
        raise ValueError(
            f"cuts total {total_cut_seconds:g}s but narration is {audio_seconds:g}s; "
            "add footage so the video does not end early"
        )


def build_props(
    segments: list[Path],
    durations: list[float],
    captions: list[dict],
    narration_path: Path,
    caption_style: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cuts: list[dict[str, Any]] = []
    cursor = 0.0
    for index, (segment, duration) in enumerate(zip(segments, durations), start=1):
        cuts.append({
            "id": f"c{index}",
            "source": str(segment),
            "in_seconds": round(cursor, 3),
            "out_seconds": round(cursor + duration, 3),
            "source_in_seconds": 0,
            "layer": "primary",
        })
        cursor += duration
    return {
        "version": "1.0",
        "renderer_family": "explainer-data",
        "render_runtime": "remotion",
        "playbook": "shorts-captions",
        "cuts": cuts,
        "captions": captions,
        "captionStyle": {**CAPTION_STYLE, **(caption_style or {})},
        "audio": {"narration": {"src": str(narration_path), "volume": 1.0}},
    }


def render_video(registry: Any, props: dict[str, Any], output_path: Path, attempts: int = 2, retry_wait: float = 3.0) -> None:
    """Render one video. A render can fail for reasons that pass (a browser crash, a busy port), so try again once."""
    import time

    last = ""
    for attempt in range(1, attempts + 1):
        result = registry._tools["video_compose"].execute({
            "operation": "remotion_render",
            "edit_decisions": props,
            "output_path": str(output_path),
            "profile": "youtube_shorts",
        })
        if result.success:
            return
        lines = [ln.strip() for ln in (result.error or "render failed").strip().splitlines() if ln.strip()]
        # the first line only names the tool; the cause is at the end, so keep the last few lines (still on one line)
        last = lines[0] if len(lines) == 1 else f"{lines[0]} ... " + " | ".join(lines[-6:])
        if attempt < attempts:
            time.sleep(retry_wait)
    raise RuntimeError(f"render failed after {attempts} attempts: {last[:700]}")
