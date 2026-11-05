"""Create, find and edit shorts batches under projects/."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.shorts.platforms import PLATFORM_NAMES
from lib.shorts.spec import load_spec, save_spec, validate_spec

SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
PIPELINE = "shorts-factory"
EDITABLE_FIELDS = (
    "topic", "hook_type", "hook_text", "narration", "claims", "cuts",
    "title", "description", "tags", "redo_note", "redo_avoid", "topic_sources",
)


def _project_meta(folder: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((folder / "project.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("pipeline_type") == PIPELINE else None


def batch_dir(projects_dir: Path, batch_id: str) -> Path:
    """Return the batch folder, or raise FileNotFoundError for anything unsafe or not a shorts batch."""
    if not isinstance(batch_id, str) or not SAFE_ID.match(batch_id):
        raise FileNotFoundError(f"no such batch: {batch_id!r}")
    folder = Path(projects_dir) / batch_id
    if not folder.is_dir() or _project_meta(folder) is None:
        raise FileNotFoundError(f"no such batch: {batch_id!r}")
    return folder


def list_batches(projects_dir: Path) -> list[dict[str, Any]]:
    rows: list[tuple[float, dict[str, Any]]] = []
    root = Path(projects_dir)
    if not root.is_dir():
        return []
    for folder in root.iterdir():
        meta = _project_meta(folder) if folder.is_dir() and SAFE_ID.match(folder.name) else None
        if meta is None:
            continue
        try:
            spec = load_spec(folder)
        except (FileNotFoundError, ValueError):
            spec = {}
        rows.append((
            (folder / "project.json").stat().st_mtime,
            {
                "id": folder.name,
                "title": meta.get("title") or folder.name,
                "platform": spec.get("platform") or "youtube_shorts",
                "video_count": len(spec.get("videos") or []),
            },
        ))
    return [row for _, row in sorted(rows, key=lambda item: item[0], reverse=True)]


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40].strip("-")


def create_batch(projects_dir: Path, name: str, platform: str) -> str:
    if not isinstance(name, str) or not _slug(name):
        raise ValueError("give the batch a name (letters or digits)")
    if platform not in PLATFORM_NAMES:
        raise ValueError(f"platform must be one of {', '.join(PLATFORM_NAMES)}")
    root = Path(projects_dir)
    root.mkdir(parents=True, exist_ok=True)
    base = f"shorts-{_slug(name)}"
    batch_id, n = base, 1
    while (root / batch_id).exists():
        n += 1
        batch_id = f"{base}-{n}"
    folder = root / batch_id
    folder.mkdir(parents=True)
    (folder / "project.json").write_text(json.dumps({
        "version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project_id": batch_id,
        "title": name.strip(),
        "pipeline_type": PIPELINE,
        "style_playbook": "shorts-captions",
    }, indent=2), encoding="utf-8")
    save_spec(folder, {
        "project_id": batch_id,
        "platform": platform,
        "voice": {"model": "en_US-lessac-medium", "length_scale": 0.95},
        "sources": {},
        "videos": [],
    })
    return batch_id


def edit_videos(project_dir: Path, videos: list[dict[str, Any]], stage: str) -> list[str]:
    """Replace the spec's videos (editable fields only). Returns violations; saves nothing if any."""
    spec = load_spec(project_dir)
    cleaned = []
    for video in videos:
        if not isinstance(video, dict):
            return ["each video must be an object"]
        item: dict[str, Any] = {"id": video.get("id")}
        for field in EDITABLE_FIELDS:
            if field in video:
                item[field] = video[field]
        if stage == "idea" and not item.get("narration") and item.get("topic_sources"):
            # before the script exists the only claim is the topic itself, sourced from topic_sources
            item["claims"] = [{"text": item.get("topic"), "sources": [str(s) for s in item.get("topic_sources") or []]}]
        cleaned.append(item)
    spec["videos"] = cleaned
    violations = validate_spec(spec, project_dir, stage)
    if violations:
        return violations
    save_spec(project_dir, spec)
    return []
