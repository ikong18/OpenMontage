"""Footage must show what the video is about. This module holds the checks that do not depend on trusting an AI's word:

* a clip's contact sheet is looked at once; the frames that show the subject are recorded with the clip (`verified`);
* every cut must lie on those verified frames (checked in code);
* a second AI later looks at one real frame from every cut of the finished video, and its verdicts are kept in
  artifacts/footage_check.json, shown to the owner, and used to block the render when a shot does not show the subject.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VERDICTS = ("yes", "partly", "no")
MATCHES = ("exact", "related", "unrelated")
FILE = Path("artifacts") / "footage_check.json"
DISCLOSURE_MARK = "Footage is illustrative"


# ---- frames and cuts ---------------------------------------------------------------------------------------------


def frame_of(seconds: float, step: float) -> int:
    return int(float(seconds) // float(step))


def cut_frames(cut: dict[str, Any], step: float) -> set[int]:
    start, dur = float(cut["in"]), float(cut["dur"])
    return {frame_of(t, step) for t in (start, start + dur / 2, start + dur - 0.01)}


def cut_is_verified(cut: dict[str, Any], verified: dict[str, Any] | None) -> bool:
    if not verified or not verified.get("relevant_frames") or not verified.get("step"):
        return False
    return cut_frames(cut, verified["step"]) <= set(verified["relevant_frames"])


def relevant_seconds(verified: dict[str, Any]) -> float:
    return len(verified.get("relevant_frames") or []) * float(verified.get("step") or 0)


def unverified_cuts(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Cuts that are not on frames an AI verified as showing the subject."""
    problems = []
    sources = spec.get("sources") or {}
    for video in spec.get("videos") or []:
        for index, cut in enumerate(video.get("cuts") or []):
            verified = (sources.get(cut.get("source")) or {}).get("verified")
            if not cut_is_verified(cut, verified):
                problems.append({"video": video.get("id"), "index": index, "source": cut.get("source")})
    return problems


def source_problem(verified: dict[str, Any], needed_seconds: float) -> str | None:
    """Why a clip cannot be used for a video, or None."""
    if verified.get("match") not in ("exact", "related"):
        return "the clip does not show the subject"
    if relevant_seconds(verified) < needed_seconds:
        return f"too little of the clip shows the subject ({relevant_seconds(verified):.0f} s of the {needed_seconds:.0f} s needed)"
    return None


def disclosure_for(spec: dict[str, Any], video: dict[str, Any]) -> str | None:
    """Text to add to the description when the footage only illustrates the subject (a relative, not the thing itself)."""
    sources = spec.get("sources") or {}
    related = []
    for cut in video.get("cuts") or []:
        verified = (sources.get(cut.get("source")) or {}).get("verified") or {}
        if verified.get("match") == "related" and verified.get("shows") and verified["shows"] not in related:
            related.append(verified["shows"].rstrip("."))
    return f"{DISCLOSURE_MARK} (it shows a related subject, not the exact one): {'; '.join(related)}." if related else None


def ensure_disclosure(spec: dict[str, Any]) -> None:
    for video in spec.get("videos") or []:
        text = disclosure_for(spec, video)
        description = str(video.get("description") or "")
        if text and DISCLOSURE_MARK not in description:
            video["description"] = f"{description} {text}".strip()


# ---- the second AI's look at the finished cuts --------------------------------------------------------------------


def cuts_hash(video: dict[str, Any], spec: dict[str, Any]) -> str:
    sources = spec.get("sources") or {}
    used = {c.get("source"): (sources.get(c.get("source")) or {}).get("clip_id") for c in video.get("cuts") or []}
    payload = json.dumps({"cuts": video.get("cuts"), "clips": used}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def review_inputs(spec: dict[str, Any], subjects: dict[str, str], frames: dict[tuple[str, int], Path]) -> dict[str, Any]:
    videos, images = [], []
    for video in spec.get("videos") or []:
        cuts = []
        for index, _cut in enumerate(video.get("cuts") or []):
            path = frames.get((video["id"], index))
            if path is None:
                continue
            cuts.append({"index": index, "frame": str(path)})
            images.append(str(path))
        if cuts:
            videos.append({"id": video["id"], "subject": subjects.get(video["id"]) or video.get("topic"),
                           "narration": video.get("narration"), "cuts": cuts})
    return {"videos": videos, "images": images}


def apply_review(spec: dict[str, Any], items: list[dict[str, Any]], writer: str, reviewer: str,
                 frame_paths: dict[str, list[str]]) -> dict[str, Any]:
    by_id = {v.get("id"): v for v in spec.get("videos") or []}
    found = {i.get("id"): i for i in items}
    for vid in found:
        if vid not in by_id:
            raise ValueError(f"the reviewer returned a result for unknown video id '{vid}'")
    videos: dict[str, Any] = {}
    for vid, video in by_id.items():
        if not video.get("cuts"):
            continue
        item = found.get(vid)
        if item is None:
            raise ValueError(f"the reviewer returned nothing for {vid}")
        cuts = item.get("cuts") or []
        if sorted(c.get("index") for c in cuts) != list(range(len(video["cuts"]))):
            raise ValueError(f"{vid}: the reviewer must give a verdict for every shot (0 to {len(video['cuts']) - 1})")
        for cut in cuts:
            if cut.get("verdict") not in VERDICTS:
                raise ValueError(f"{vid}: verdict '{cut.get('verdict')}' is not one of {', '.join(VERDICTS)}")
        frames = frame_paths.get(vid) or []
        videos[vid] = {
            "hash": cuts_hash(video, spec), "reviewer": reviewer, "writer": writer, "same_ai": writer == reviewer,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "cuts": [{"index": c["index"], "verdict": c["verdict"], "shows": str(c.get("shows") or ""),
                      "frame": frames[c["index"]] if c["index"] < len(frames) else ""}
                     for c in sorted(cuts, key=lambda c: c["index"])],
        }
    return {"checked_at": datetime.now(timezone.utc).isoformat(), "writer": writer, "reviewer": reviewer,
            "same_ai": writer == reviewer, "videos": videos}


def load_check(project_dir: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((Path(project_dir) / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("videos"), dict) else None


def save_check(project_dir: Path, record: dict[str, Any], merge: bool = False) -> None:
    path = Path(project_dir) / FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    if merge:
        old = load_check(project_dir)
        if old:
            record = {**record, "videos": {**old["videos"], **record["videos"]}}
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")


def check_view(project_dir: Path, spec: dict[str, Any]) -> dict[str, Any]:
    record = load_check(project_dir)
    if record is None:
        return {"checked": False, "videos": {}}
    current = {v.get("id"): v for v in spec.get("videos") or []}
    videos = {}
    for vid, rec in record["videos"].items():
        if vid not in current or not isinstance(rec, dict):
            continue
        cuts = [c for c in rec.get("cuts") or [] if isinstance(c, dict)]
        videos[vid] = {
            **rec, "cuts": cuts, "stale": rec.get("hash") != cuts_hash(current[vid], spec),
            "blocked": sum(1 for c in cuts if c.get("verdict") == "no"),
            "partly": sum(1 for c in cuts if c.get("verdict") == "partly"),
            "override": bool(current[vid].get("footage_override")),
        }
    return {"checked": True, "checked_at": record.get("checked_at"), "reviewer": record.get("reviewer"),
            "writer": record.get("writer"), "same_ai": bool(record.get("same_ai")), "videos": videos}


def render_blockers(project_dir: Path, spec: dict[str, Any], ids: list[str]) -> list[str]:
    """Reasons a video must not be rendered yet. The owner can override one video explicitly (footage_override)."""
    view = check_view(project_dir, spec)["videos"]
    messages = []
    for video in spec.get("videos") or []:
        vid = video.get("id")
        if vid not in ids or not video.get("cuts") or video.get("footage_override"):
            continue
        rec = view.get(vid)
        if rec is None:
            messages.append(f"{vid}: the footage has not been checked yet - run the footage check")
        elif rec["stale"]:
            messages.append(f"{vid}: the footage changed since it was checked - run the footage check again")
        elif rec["blocked"]:
            shots = ", ".join(str(c["index"] + 1) for c in rec["cuts"] if c.get("verdict") == "no")
            messages.append(f"{vid}: the footage check says shot {shots} does not show the subject - "
                            "replace the footage (Redo this video) or choose Use anyway")
    return messages
