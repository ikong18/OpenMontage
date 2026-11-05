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


STEP_LABELS = {"topics": "Topics", "script": "Script", "footage": "Footage",
               "render": "Voice and render", "review": "Review", "export": "Export"}


def next_action(states: dict[str, str], gates: dict[str, str], running: dict | None, video_count: int,
                batch_id: str, flagged: int = 0) -> dict[str, str]:
    """One plain-language line telling the owner what to do next. kind: running | failed | todo | done."""
    if running:
        step = running.get("step") or "topics"
        return {"kind": "running", "step": step,
                "text": f"{STEP_LABELS.get(step, step)} is running - you can leave this page."}
    for step in STEPS:
        if states.get(step) == "failed":
            return {"kind": "failed", "step": step,
                    "text": f"{STEP_LABELS[step]} failed. Open it to read the log and retry."}
    step = next((s for s in STEPS if states.get(s) != "done"), None)
    if step is None:
        return {"kind": "done", "step": "export",
                "text": f"All done. Your files are in projects/{batch_id}/export/ - upload them by hand."}
    if step == "topics":
        text = ("Next: ask Claude to suggest topics." if video_count == 0
                else "Next: check the topics, then approve them.")
    elif step == "script":
        text = "Next: have Claude write the scripts, or write them yourself."
    elif step == "footage":
        text = "Next: find footage for each video."
    elif step == "render":
        text = ("Something changed after the last render. Make the videos again." if states.get("render") == "stale"
                else "Next: make the videos.")
    elif step == "review":
        review = gates.get("review_queue", "none")
        text = {"none": "Next: prepare the review.",
                "awaiting": "Next: watch the videos, then approve the batch."}.get(
                    review, "Next: prepare the review again, then approve the batch.")
    else:
        text = "Next: export the approved videos (private)."
    if flagged and step not in ("topics", "script"):
        text += f" The fact check flagged {flagged} claim{'s' if flagged != 1 else ''} - read {'them' if flagged != 1 else 'it'} in the Script step first."
    return {"kind": "todo", "step": step, "text": text}
