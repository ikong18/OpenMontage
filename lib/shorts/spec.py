"""Load and validate projects/<id>/artifacts/batch_spec.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

HOOK_TYPES = ("question", "shocking_stat", "myth_bust", "comparison", "countdown")
SPEC_FILE = Path("artifacts") / "batch_spec.json"
MAX_TITLE = 100

_TOP_REQUIRED = ("project_id", "sources", "videos")
_VIDEO_REQUIRED = (
    "id", "topic", "hook_type", "hook_text", "narration",
    "claims", "cuts", "title", "description", "tags",
)


def load_spec(project_dir: Path) -> dict[str, Any]:
    path = Path(project_dir) / SPEC_FILE
    if not path.exists():
        raise FileNotFoundError(f"batch spec not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: invalid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return data


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_spec(spec: dict[str, Any], project_dir: Path) -> list[str]:
    violations: list[str] = []
    for field in _TOP_REQUIRED:
        if field not in spec:
            violations.append(f"spec: missing {field}")
    sources = spec.get("sources") if isinstance(spec.get("sources"), dict) else {}
    videos = spec.get("videos") if isinstance(spec.get("videos"), list) else []
    if not videos:
        violations.append("spec: no videos")

    checked_sources: set[str] = set()
    seen_ids: set[str] = set()
    ranges: dict[str, list[tuple[float, float, str]]] = {}

    for index, video in enumerate(videos):
        if not isinstance(video, dict):
            violations.append(f"video at index {index}: not an object")
            continue
        vid = str(video.get("id") or f"#{index}")
        for field in _VIDEO_REQUIRED:
            if field not in video or video[field] in (None, ""):
                if field == "tags" and video.get(field) == []:
                    continue
                violations.append(f"{vid}: missing {field}")
        if vid in seen_ids:
            violations.append(f"{vid}: duplicate video id")
        seen_ids.add(vid)
        if video.get("hook_type") and video["hook_type"] not in HOOK_TYPES:
            violations.append(f"{vid}: hook_type '{video['hook_type']}' not in {', '.join(HOOK_TYPES)}")
        if len(str(video.get("title") or "")) > MAX_TITLE:
            violations.append(f"{vid}: title longer than {MAX_TITLE} characters")

        for claim in video.get("claims") or []:
            srcs = claim.get("sources") if isinstance(claim, dict) else None
            if not any(str(s).startswith(("http://", "https://")) for s in srcs or []):
                violations.append(f"{vid}: claim has no http(s) source")

        for cut in video.get("cuts") or []:
            key = cut.get("source") if isinstance(cut, dict) else None
            if key not in sources:
                violations.append(f"{vid}: cut uses unknown source '{key}'")
                continue
            if key not in checked_sources:
                checked_sources.add(key)
                rel = sources[key].get("path", "")
                if not (Path(project_dir) / rel).is_file():
                    violations.append(f"source '{key}': file not found: {rel}")
            start, dur = cut.get("in"), cut.get("dur")
            if not _number(start) or not _number(dur) or dur <= 0:
                violations.append(f"{vid}: cut needs numeric 'in' and 'dur' > 0")
                continue
            ranges.setdefault(key, []).append((float(start), float(start) + float(dur), vid))

    for key, spans in ranges.items():
        spans.sort()
        for (a0, a1, av), (b0, b1, bv) in zip(spans, spans[1:]):
            if b0 < a1:
                violations.append(
                    f"source '{key}': cuts overlap ({av} {a0:g}-{a1:g} and {bv} {b0:g}-{b1:g})"
                )
    return violations
