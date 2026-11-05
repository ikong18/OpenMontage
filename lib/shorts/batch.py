"""The audited artifacts/shorts_batch.json, derived from the batch spec."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

BATCH_FILE = Path("artifacts") / "shorts_batch.json"


def _digest(payload: Any) -> str:
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def voice_fingerprint(spec: dict[str, Any], video: dict[str, Any]) -> str:
    """Changes when the narration or the voice settings change (so old audio is stale)."""
    return _digest({"narration": video.get("narration"), "voice": spec.get("voice") or {}})


def render_fingerprint(spec: dict[str, Any], video: dict[str, Any]) -> str:
    """Changes when anything that shapes the render changes (cuts, footage, narration, voice)."""
    sources = spec.get("sources") or {}
    used = {c.get("source"): sources.get(c.get("source")) for c in video.get("cuts") or []}
    return _digest({"cuts": video.get("cuts"), "sources": used, "voice": voice_fingerprint(spec, video)})


def derive_batch(spec: dict[str, Any]) -> dict[str, Any]:
    sources = spec.get("sources") or {}
    videos = []
    for video in spec.get("videos") or []:
        clips = []
        for cut in video.get("cuts") or []:
            src = sources[cut["source"]]
            start, end = float(cut["in"]), float(cut["in"]) + float(cut["dur"])
            clips.append({
                "clip_id": f"{src['clip_id']}@{start:.1f}-{end:.1f}",
                "source": src.get("provider", ""),
                "license": src.get("license", ""),
                "url": src.get("url", ""),
            })
        videos.append({
            "id": video["id"],
            "topic": video["topic"],
            "hook_type": video["hook_type"],
            "hook_text": video["hook_text"],
            "narration": video["narration"],
            "claims": video.get("claims") or [],
            "clips": clips,
            "render": f"renders/{video['id']}.mp4",
            "cost_entries": [],
        })
    return {"videos": videos}


def read_batch(project_dir: Path) -> dict[str, Any]:
    path = Path(project_dir) / BATCH_FILE
    if not path.exists():
        raise FileNotFoundError(f"batch file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_batch(project_dir: Path, batch: dict[str, Any]) -> None:
    path = Path(project_dir) / BATCH_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(batch, indent=2, ensure_ascii=False), encoding="utf-8")


def record_cost(batch: dict[str, Any], video_id: str, tool: str, provider: str, cost_usd: float) -> None:
    for video in batch["videos"]:
        if video["id"] == video_id:
            entries = [e for e in video.setdefault("cost_entries", []) if e.get("tool") != tool]
            entries.append({"tool": tool, "provider": provider, "cost_usd": cost_usd})
            video["cost_entries"] = entries
            return
    raise ValueError(f"unknown video id: {video_id}")
