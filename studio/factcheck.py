"""A second AI checks the scripts: does each cited page really support each claim?

The record lives in projects/<id>/artifacts/fact_check.json and goes stale when a script is edited.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VERDICTS = ("supported", "partly", "unsupported", "unverifiable")
FILE = Path("artifacts") / "fact_check.json"


def video_hash(video: dict[str, Any]) -> str:
    payload = json.dumps({"narration": video.get("narration"), "claims": video.get("claims")}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def check_inputs(videos: list[dict[str, Any]]) -> dict[str, Any]:
    return {"videos": [{
        "id": v.get("id"), "narration": v.get("narration"),
        "claims": [{"index": i, "text": c.get("text"), "sources": c.get("sources") or []}
                   for i, c in enumerate(v.get("claims") or [])],
    } for v in videos]}


def claims_flagged(video_record: dict[str, Any]) -> int:
    return sum(1 for c in video_record.get("claims") or [] if c.get("verdict") != "supported")


def apply_check(spec: dict[str, Any], items: list[dict[str, Any]], writer: str, checker: str) -> dict[str, Any]:
    videos = {v.get("id"): v for v in spec.get("videos") or []}
    found = {i.get("id"): i for i in items}
    for vid in found:
        if vid not in videos:
            raise ValueError(f"the checker returned a result for unknown video id '{vid}'")
    missing = [vid for vid in videos if vid not in found]
    if missing:
        raise ValueError(f"the checker returned nothing for {', '.join(missing)}")
    record_videos: dict[str, Any] = {}
    for vid, video in videos.items():
        item = found[vid]
        claims = item.get("claims") or []
        wanted = list(range(len(video.get("claims") or [])))
        if sorted(c.get("index") for c in claims) != wanted:
            raise ValueError(f"{vid}: the checker must give a verdict for every claim (0 to {len(wanted) - 1})")
        for claim in claims:
            if claim.get("verdict") not in VERDICTS:
                raise ValueError(f"{vid}: verdict '{claim.get('verdict')}' is not one of {', '.join(VERDICTS)}")
        record_videos[vid] = {
            "hash": video_hash(video),
            "claims": sorted(({"index": c["index"], "verdict": c["verdict"], "note": str(c.get("note") or ""),
                               "quote": str(c.get("quote") or "")} for c in claims), key=lambda c: c["index"]),
            "issues": [str(s) for s in item.get("narration_issues") or []],
        }
    return {"checked_at": datetime.now(timezone.utc).isoformat(), "writer": writer, "checker": checker,
            "same_ai": writer == checker, "videos": record_videos}


def load_check(project_dir: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((Path(project_dir) / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("videos"), dict) else None


def save_check(project_dir: Path, record: dict[str, Any]) -> None:
    path = Path(project_dir) / FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")


def check_view(project_dir: Path, spec: dict[str, Any]) -> dict[str, Any]:
    record = load_check(project_dir)
    if record is None:
        return {"checked": False, "videos": {}}
    current = {v.get("id"): v for v in spec.get("videos") or []}
    videos = {}
    for vid, rec in record["videos"].items():
        if vid not in current:
            continue
        videos[vid] = {**rec, "stale": rec.get("hash") != video_hash(current[vid]), "flagged": claims_flagged(rec)}
    return {"checked": True, "checked_at": record.get("checked_at"), "writer": record.get("writer"),
            "checker": record.get("checker"), "same_ai": bool(record.get("same_ai")), "videos": videos}


def pick_checker(writer: str, ready: dict[str, bool]) -> tuple[str, bool]:
    """The checker is the other AI when it is available; (name, same_ai)."""
    other = "codex" if writer == "claude" else "claude"
    if ready.get(other):
        return other, False
    return writer, True
