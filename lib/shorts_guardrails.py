"""Machine-checkable batch rules for the shorts-factory pipeline.

Every check is pure (no I/O) and returns ``{"ok": bool, "violations": [str]}``.
An empty batch never passes: a vacuous pass would let a failed run look clean.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

SHORT_WIDTH = 1080
SHORT_HEIGHT = 1920
MAX_DURATION_S = 60.0  # strictly under


def _result(violations: list[str]) -> dict[str, Any]:
    return {"ok": not violations, "violations": violations}


def check_hook_variety(videos: list[dict], max_share: float = 0.4) -> dict[str, Any]:
    if not videos:
        return _result(["empty batch"])
    violations: list[str] = []
    types: list[str] = []
    for video in videos:
        hook_type = str(video.get("hook_type") or "").strip()
        if not hook_type:
            violations.append(f"{video.get('id', '?')}: missing hook_type")
        else:
            types.append(hook_type)
    if len(videos) >= 3:
        for hook_type, count in Counter(types).items():
            if count / len(videos) > max_share:
                violations.append(
                    f"hook_type '{hook_type}' used by {count}/{len(videos)} videos "
                    f"(max share {max_share:.0%})"
                )
    seen: dict[str, str] = {}
    for video in videos:
        text = str(video.get("hook_text") or "").strip().lower()
        if not text:
            continue
        if text in seen:
            violations.append(
                f"duplicate hook_text between {seen[text]} and {video.get('id', '?')}"
            )
        else:
            seen[text] = video.get("id", "?")
    return _result(violations)


def check_footage_reuse(videos: list[dict]) -> dict[str, Any]:
    if not videos:
        return _result(["empty batch"])
    users: dict[str, list[str]] = {}
    for video in videos:
        for clip in video.get("clips") or []:
            users.setdefault(str(clip.get("clip_id")), []).append(video.get("id", "?"))
    violations = [
        f"clip '{clip_id}' reused by {', '.join(ids)}"
        for clip_id, ids in users.items()
        if len(ids) > 1
    ]
    return _result(violations)


def check_short_specs(probes: dict[str, dict]) -> dict[str, Any]:
    if not probes:
        return _result(["no renders probed"])
    violations: list[str] = []
    for video_id, probe in probes.items():
        width, height = probe.get("width"), probe.get("height")
        duration = probe.get("duration")
        if (width, height) != (SHORT_WIDTH, SHORT_HEIGHT):
            violations.append(
                f"{video_id}: size {width}x{height}, expected {SHORT_WIDTH}x{SHORT_HEIGHT}"
            )
        if duration is None or duration >= MAX_DURATION_S:
            violations.append(f"{video_id}: duration {duration}s must be under {MAX_DURATION_S:g}s")
    return _result(violations)


def check_zero_cost(entries: list[dict], allow_empty: bool = False) -> dict[str, Any]:
    if not entries:
        return _result([] if allow_empty else ["no cost entries recorded"])
    violations: list[str] = []
    for entry in entries:
        cost = entry.get("cost_usd")
        label = f"{entry.get('tool', '?')} ({entry.get('provider', '?')})"
        if cost is None:
            violations.append(f"{label}: cost_usd missing")
        elif cost > 0:
            violations.append(f"{label}: cost_usd {cost} is not free")
    return _result(violations)


def check_license_log(videos: list[dict]) -> dict[str, Any]:
    if not videos:
        return _result(["empty batch"])
    violations: list[str] = []
    for video in videos:
        clips = video.get("clips") or []
        if not clips:
            violations.append(f"{video.get('id', '?')}: no clips logged")
        for clip in clips:
            for field in ("source", "license", "url"):
                if not str(clip.get(field) or "").strip():
                    violations.append(
                        f"{video.get('id', '?')}: clip '{clip.get('clip_id', '?')}' missing {field}"
                    )
    return _result(violations)


def check_fact_sources(videos: list[dict]) -> dict[str, Any]:
    if not videos:
        return _result(["empty batch"])
    violations: list[str] = []
    for video in videos:
        claims = video.get("claims") or []
        if not claims:
            violations.append(f"{video.get('id', '?')}: no claims recorded")
        for claim in claims:
            sources = claim.get("sources") or []
            if not any(str(s).startswith(("http://", "https://")) for s in sources):
                violations.append(
                    f"{video.get('id', '?')}: claim has no http(s) source: {claim.get('text', '')[:60]!r}"
                )
    return _result(violations)


def run_all(batch: dict, probes: dict[str, dict], cost_entries: list[dict]) -> dict[str, Any]:
    videos = batch.get("videos") or []
    checks = {
        "hook_variety": check_hook_variety(videos),
        "footage_reuse": check_footage_reuse(videos),
        "short_specs": check_short_specs(probes),
        "zero_cost": check_zero_cost(cost_entries),
        "license_log": check_license_log(videos),
        "fact_sources": check_fact_sources(videos),
    }
    violations = [f"{name}: {v}" for name, res in checks.items() for v in res["violations"]]
    return _result(violations)
