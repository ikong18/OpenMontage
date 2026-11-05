"""Load and validate projects/<id>/artifacts/batch_spec.json."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from lib.shorts.platforms import PLATFORM_NAMES, get_platform
from lib.shorts.props import CAPTION_STYLE

HOOK_TYPES = ("question", "shocking_stat", "myth_bust", "comparison", "countdown")
SPEC_FILE = Path("artifacts") / "batch_spec.json"
MAX_TITLE = 100
STAGES = ("idea", "script", "visuals", "full")

# Ids become file names (assets/audio/<id>.wav, renders/<id>.mp4) and folder names,
# so they must not be able to escape the project directory.
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

_VIDEO_FIELDS = {
    "idea": ("id", "topic", "hook_type", "hook_text", "claims"),
    "script": ("id", "topic", "hook_type", "hook_text", "claims", "narration"),
    "visuals": ("id", "topic", "hook_type", "hook_text", "claims", "narration", "cuts"),
    "full": ("id", "topic", "hook_type", "hook_text", "claims", "narration", "cuts",
             "title", "description", "tags"),
}


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


def save_spec(project_dir: Path, spec: dict[str, Any]) -> None:
    path = Path(project_dir) / SPEC_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _inside(project: Path, relative: str) -> bool:
    try:
        return (project / relative).resolve().is_relative_to(project.resolve())
    except (OSError, ValueError):
        return False


def validate_spec(spec: dict[str, Any], project_dir: Path, stage: str = "full") -> list[str]:
    if stage not in STAGES:
        raise ValueError(f"unknown stage '{stage}' (use {', '.join(STAGES)})")
    project = Path(project_dir)
    violations: list[str] = []

    project_id = spec.get("project_id")
    if not isinstance(project_id, str) or not _SAFE_ID.match(project_id):
        violations.append("spec: project_id missing or not a safe name (letters, digits, - and _)")
    elif project_id != project.resolve().name:
        violations.append(
            f"spec: project_id '{project_id}' does not match the folder name '{project.resolve().name}'"
        )

    platform_name = spec.get("platform")
    platform = None
    if platform_name is not None and platform_name not in PLATFORM_NAMES:
        violations.append(f"spec: platform '{platform_name}' is not one of {', '.join(PLATFORM_NAMES)}")
    else:
        platform = get_platform(platform_name)

    style = spec.get("caption_style")
    if style is not None:
        if not isinstance(style, dict):
            violations.append("spec: caption_style must be an object")
        else:
            for key, value in style.items():
                if key not in CAPTION_STYLE:
                    violations.append(f"spec: caption_style has unknown key '{key}'")
                elif isinstance(CAPTION_STYLE[key], (int, float)) and not _number(value):
                    violations.append(f"spec: caption_style {key} must be a number")
                elif isinstance(CAPTION_STYLE[key], str) and not isinstance(value, str):
                    violations.append(f"spec: caption_style {key} must be text")

    needs_sources = stage in ("visuals", "full")
    raw_sources = spec.get("sources")
    if raw_sources is None:
        sources: dict[str, Any] = {}
        if needs_sources:
            violations.append("spec: missing sources")
    elif isinstance(raw_sources, dict):
        sources = raw_sources
    else:
        sources = {}
        violations.append("spec: sources must be an object")

    for key, src in sources.items():
        if not isinstance(src, dict):
            violations.append(f"source '{key}': must be an object with path and clip_id")
            continue
        for field in ("path", "clip_id"):
            if not isinstance(src.get(field), str) or not src[field].strip():
                violations.append(f"source '{key}': missing {field}")
        path = src.get("path")
        if isinstance(path, str) and path and not _inside(project, path):
            violations.append(f"source '{key}': path is outside the project folder: {path}")

    videos = spec.get("videos")
    if not isinstance(videos, list) or not videos:
        violations.append("spec: no videos")
        videos = []

    checked_files: set[str] = set()
    seen_ids: set[str] = set()
    ranges: dict[str, list[tuple[float, float, str]]] = {}

    for index, video in enumerate(videos):
        if not isinstance(video, dict):
            violations.append(f"video at index {index}: not an object")
            continue
        vid = video.get("id")
        label = vid if isinstance(vid, str) and vid else f"#{index}"
        if not isinstance(vid, str) or not _SAFE_ID.match(vid):
            violations.append(f"{label}: id must be letters, digits, - and _ (no paths or spaces)")
        elif vid in seen_ids:
            violations.append(f"{label}: duplicate video id")
        else:
            seen_ids.add(vid)

        for field in _VIDEO_FIELDS[stage]:
            value = video.get(field)
            if field == "tags":
                if not isinstance(value, list):
                    violations.append(f"{label}: missing {field}")
            elif value in (None, "", []):
                violations.append(f"{label}: missing {field}")

        hook = video.get("hook_type")
        if hook and hook not in HOOK_TYPES:
            violations.append(f"{label}: hook_type '{hook}' not in {', '.join(HOOK_TYPES)}")
        if len(str(video.get("title") or "")) > MAX_TITLE:
            violations.append(f"{label}: title longer than {MAX_TITLE} characters")
        description = video.get("description")
        if stage == "full" and platform is not None and isinstance(description, str):
            if len(description) > platform.max_description:
                violations.append(
                    f"{label}: description is {len(description)} characters; {platform.name} allows {platform.max_description}"
                )
            tags = len(re.findall(r"#\w+", description)) + len(platform.hashtags)
            if tags > platform.max_hashtags:
                violations.append(f"{label}: {tags} hashtags; {platform.name} allows {platform.max_hashtags}")

        for claim in video.get("claims") or []:
            srcs = claim.get("sources") if isinstance(claim, dict) else None
            if not isinstance(srcs, list) or not any(
                str(s).startswith(("http://", "https://")) for s in srcs
            ):
                violations.append(f"{label}: claim has no http(s) source")

        for cut in video.get("cuts") or []:
            key = cut.get("source") if isinstance(cut, dict) else None
            if not isinstance(key, str) or key not in sources:
                violations.append(f"{label}: cut uses unknown source '{key}'")
                continue
            src = sources[key]
            if not isinstance(src, dict):
                continue
            rel = src.get("path")
            if isinstance(rel, str) and rel and rel not in checked_files:
                checked_files.add(rel)
                if _inside(project, rel) and not (project / rel).is_file():
                    violations.append(f"source '{key}': file not found: {rel}")
            start, dur = cut.get("in"), cut.get("dur")
            if not _number(start) or not _number(dur) or dur <= 0 or start < 0:
                violations.append(f"{label}: cut needs numeric 'in' >= 0 and 'dur' > 0")
                continue
            # Same footage can sit under two keys; identity is the clip, not the key.
            identity = str(src.get("clip_id") or src.get("path"))
            ranges.setdefault(identity, []).append((float(start), float(start) + float(dur), label))

    for identity, spans in ranges.items():
        spans.sort()
        for (a0, a1, av), (b0, b1, bv) in zip(spans, spans[1:]):
            if b0 < a1:
                violations.append(
                    f"clip '{identity}': cuts overlap ({av} {a0:g}-{a1:g} and {bv} {b0:g}-{b1:g})"
                )
    return violations
