"""Export approved Shorts through export_bundle and write the publish stage.

Only the ids the owner approved are exported, in the order given. Nothing is
uploaded; every entry is marked private.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lib.checkpoint import CheckpointValidationError, read_checkpoint, write_checkpoint

PIPELINE = "shorts-factory"
PLAYBOOK = "shorts-captions"


def export_videos(
    projects_dir: Path, spec: dict[str, Any], approved_ids: list[str], registry: Any
) -> list[dict[str, Any]]:
    pid = spec["project_id"]
    review = read_checkpoint(projects_dir, pid, "review_queue")
    if not review or review.get("status") != "completed":
        raise ValueError("review_queue is not completed; approve the batch before exporting")
    by_id = {v["id"]: v for v in spec["videos"]}
    unknown = [i for i in approved_ids if i not in by_id]
    if unknown:
        raise ValueError(f"unknown video id(s): {', '.join(unknown)}")

    project_dir = Path(projects_dir) / pid
    entries: list[dict[str, Any]] = []
    for position, vid in enumerate(approved_ids, start=1):
        video = by_id[vid]
        result = registry._tools["export_bundle"].execute({
            "video_path": str(project_dir / "renders" / f"{vid}.mp4"),
            "title": video["title"],
            "description": video["description"],
            "tags": video["tags"],
            "hashtags": ["#Shorts"],
            "project_name": f"{pid}-{position:02d}-{vid}",
            "export_dir": str(project_dir / "export" / f"{position:02d}-{vid}"),
            "platform": "youtube_shorts",
            "visibility": "private",
            "subtitles_path": str(project_dir / "assets" / "audio" / f"{vid}.srt"),
        })
        if not result.success:
            first = (result.error or "export failed").strip().splitlines()[0]
            raise RuntimeError(f"export failed for {vid}: {first}")
        entries.extend(result.data["publish_log"]["entries"])

    log = {"version": "1.0", "entries": entries,
           "metadata": {"posting_order": approved_ids,
                        "note": "Exported locally; nothing uploaded. Intended visibility: private first."}}
    try:
        write_checkpoint(projects_dir, pid, "publish", "completed", {"publish_log": log},
                         pipeline_type=PIPELINE, style_playbook=PLAYBOOK)
    except CheckpointValidationError as exc:
        raise ValueError(str(exc).strip().splitlines()[0]) from exc
    return entries
