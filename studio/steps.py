"""Derive each step's state from the files in a batch folder. Never raises on bad files."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from lib.shorts.batch import read_batch, render_fingerprint
from lib.shorts.spec import load_spec, validate_spec

STEPS = ("topics", "script", "footage", "render", "review", "export")


def _read_checkpoint(project: Path, gate: str) -> dict[str, Any] | None:
    try:
        data = json.loads((project / f"checkpoint_{gate}.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def gate_status(project_dir: Path, gate: str) -> str:
    data = _read_checkpoint(Path(project_dir), gate)
    if data is None:
        return "none"
    if data.get("status") == "completed" and data.get("human_approved") is True:
        return "approved"
    return "awaiting"


def _spec(project: Path) -> dict[str, Any] | None:
    try:
        return load_spec(project)
    except (OSError, ValueError):
        return None


def _validates(spec: dict[str, Any] | None, project: Path, stage: str) -> bool:
    if spec is None:
        return False
    try:
        return not validate_spec(spec, project, stage)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return False


def _render_state(project: Path, spec: dict[str, Any]) -> str:
    """'done', 'stale' (renders exist but no longer match the spec) or 'none'."""
    videos = spec.get("videos") or []
    if not videos:
        return "none"
    try:
        batch = read_batch(project)
    except (OSError, ValueError):
        batch = {"videos": []}
    recorded = {v.get("id"): v for v in batch.get("videos") or [] if isinstance(v, dict)}
    present = current = 0
    for video in videos:
        if not (project / "renders" / f"{video.get('id')}.mp4").is_file():
            continue
        present += 1
        try:
            if recorded.get(video.get("id"), {}).get("render_hash") == render_fingerprint(spec, video):
                current += 1
        except (KeyError, TypeError, ValueError):
            pass
    if present == len(videos) and current == len(videos):
        return "done"
    return "stale" if present else "none"


def _review_done(project: Path, spec: dict[str, Any]) -> bool:
    if gate_status(project, "review_queue") != "approved":
        return False
    data = _read_checkpoint(project, "review_queue") or {}
    try:
        stamp = datetime.fromisoformat(str(data.get("timestamp")).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return False
    for video in spec.get("videos") or []:
        render = project / "renders" / f"{video.get('id')}.mp4"
        if render.is_file() and render.stat().st_mtime > stamp:
            return False
    return True


def _export_done(project: Path, spec: dict[str, Any]) -> bool:
    videos = spec.get("videos") or []
    if not videos:
        return False
    return all(
        any(project.glob(f"export/*-{video.get('id')}/video/output.mp4")) for video in videos
    )


def step_states(project_dir: Path, running: str | None = None, failed: str | None = None) -> dict[str, str]:
    project = Path(project_dir)
    spec = _spec(project)
    states: dict[str, str] = {}
    previous_done = True
    for step in STEPS:
        done = False
        stale = False
        if step == "topics":
            done = gate_status(project, "idea") == "approved"
        elif not previous_done or spec is None:
            done = False
        elif step == "script":
            done = _validates(spec, project, "script")
        elif step == "footage":
            done = _validates(spec, project, "visuals")
        elif step == "render":
            kind = _render_state(project, spec)
            done, stale = kind == "done", kind == "stale"
        elif step == "review":
            done = _review_done(project, spec)
        elif step == "export":
            done = _export_done(project, spec)

        if step == running:
            states[step] = "running"
        elif step == failed and not done:
            states[step] = "failed"
        elif not previous_done:
            states[step] = "locked"
        elif done:
            states[step] = "done"
        elif stale:
            states[step] = "stale"
        else:
            states[step] = "ready"
        previous_done = states[step] == "done"
    return states
