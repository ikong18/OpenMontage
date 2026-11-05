"""Turn AI answers into spec changes. Each function returns a new spec and raises ValueError with
the validator's messages if the result would not validate; the input spec is never touched."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from lib.shorts.spec import validate_spec


def _http_only(sources: Any, where: str) -> None:
    for source in sources or []:
        if not str(source).lower().startswith(("http://", "https://")):
            raise ValueError(f"{where}: source '{str(source)[:60]}' is not an http(s) link")


def _check(spec: dict[str, Any], project_dir: Path, stage: str) -> dict[str, Any]:
    violations = validate_spec(spec, project_dir, stage)
    if violations:
        raise ValueError("; ".join(violations))
    return spec


def apply_topics(spec: dict[str, Any], items: list[dict[str, Any]], count: int, project_dir: Path) -> dict[str, Any]:
    if len(items) != count:
        raise ValueError(f"expected {count} topics but the AI returned {len(items)}")
    out = copy.deepcopy(spec)
    out["videos"] = []
    for index, item in enumerate(items, start=1):
        sources = [str(s) for s in item.get("sources") or []]
        _http_only(sources, f"topic {index}")
        out["videos"].append({
            "id": f"v{index}",
            "topic": item.get("topic"),
            "hook_type": item.get("hook_type"),
            "hook_text": item.get("hook_text"),
            "topic_sources": sources,
            # seed claim: the script step replaces it with the real claims
            "claims": [{"text": item.get("topic"), "sources": sources}],
        })
    return _check(out, project_dir, "idea")


def apply_scripts(spec: dict[str, Any], items: list[dict[str, Any]], project_dir: Path) -> dict[str, Any]:
    out = copy.deepcopy(spec)
    by_id = {v.get("id"): v for v in out.get("videos") or []}
    seen: set[str] = set()
    for item in items:
        vid = item.get("id")
        if vid not in by_id:
            raise ValueError(f"the AI returned a script for unknown video id '{vid}'")
        seen.add(vid)
        for claim in item.get("claims") or []:
            _http_only((claim or {}).get("sources"), f"{vid} claim")
        for field in ("narration", "claims", "title", "description", "tags"):
            if field in item:
                by_id[vid][field] = item[field]
    missing = [vid for vid in by_id if vid not in seen]
    if missing:
        raise ValueError(f"no script returned for {', '.join(missing)}")
    return _check(out, project_dir, "script")


def apply_cuts(spec: dict[str, Any], items: list[dict[str, Any]], project_dir: Path) -> dict[str, Any]:
    out = copy.deepcopy(spec)
    by_id = {v.get("id"): v for v in out.get("videos") or []}
    for item in items:
        vid = item.get("id")
        if vid not in by_id:
            raise ValueError(f"the AI returned cuts for unknown video id '{vid}'")
        by_id[vid]["cuts"] = [
            {"source": c.get("source"), "in": c.get("in"), "dur": c.get("dur")} for c in item.get("cuts") or []
        ]
    return _check(out, project_dir, "visuals")


def add_source(spec: dict[str, Any], key: str, entry: dict[str, Any], width: int = 0, height: int = 0) -> dict[str, Any]:
    out = copy.deepcopy(spec)
    sources = out.setdefault("sources", {})
    previous = sources.get(key) or {}
    if previous.get("mode"):
        mode = previous["mode"]
    elif width and height and width / height < 1.4:
        mode = "blurpad"
    else:
        mode = "fill"
    sources[key] = {**entry, "mode": mode}
    return out


def apply_research(spec: dict[str, Any], items: list[dict[str, Any]], ids: list[str], project_dir: Path) -> dict[str, Any]:
    """Fill the typed topics `ids` from the AI's research; only those videos have to be complete."""
    out = copy.deepcopy(spec)
    by_id = {v.get("id"): v for v in out.get("videos") or []}
    for vid in ids:
        if vid not in by_id:
            raise ValueError(f"unknown video id '{vid}'")
    found = {i.get("id"): i for i in items}
    missing = [vid for vid in ids if vid not in found]
    if missing:
        raise ValueError(f"no research returned for {', '.join(missing)}")
    for vid in ids:
        item = found[vid]
        sources = [str(s) for s in item.get("sources") or []]
        _http_only(sources, vid)
        by_id[vid].update({
            "topic": item.get("topic"), "hook_type": item.get("hook_type"), "hook_text": item.get("hook_text"),
            "topic_sources": sources, "claims": [{"text": item.get("topic"), "sources": sources}],
        })
    check = copy.deepcopy(out)
    check["videos"] = [v for v in check["videos"] if v["id"] in ids]
    _check(check, project_dir, "idea")
    return out
