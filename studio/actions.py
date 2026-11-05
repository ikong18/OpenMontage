"""What each Studio button does: job bodies for the six steps, owner-only gate commands, per-video redo.

Everything that changes a batch goes through lib/shorts or scripts/shorts_batch.py. The AI only returns
JSON; nothing in this module lets it approve a gate.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from lib.shorts.batch import read_batch, render_fingerprint
from lib.shorts.spec import load_spec, save_spec, validate_spec
from studio.ai import SCHEMAS, TASK_TOOLS, AiClient
from studio.apply import add_source, apply_cuts, apply_research, apply_scripts, apply_topics
from studio.factcheck import apply_check, check_inputs, save_check
from studio.footage import render_blockers
from studio.footage_step import footage_plan
from studio.jobs import JobContext
from studio.sources import source_info
from studio.steps import gate_status, step_states

REPO = Path(__file__).resolve().parents[1]
CLI_SCRIPT = REPO / "scripts" / "shorts_batch.py"
MAX_MB = 250
MAX_TRIES_PER_VIDEO = 8
SHEET_FRAMES = 30
GATES = ("idea", "review_queue")
AI_STEPS = ("topics", "script", "footage")


class Cli:
    """Runs scripts/shorts_batch.py. `run(ctx, args, capture)` returns (exit code, captured output)."""

    def __init__(self, repo: Path = REPO):
        self.repo = repo

    def run(self, ctx: JobContext, args: list[str], capture: bool = False) -> tuple[int, str]:
        argv = [sys.executable, str(CLI_SCRIPT), *args]
        if not capture:
            return ctx.run(argv, cwd=self.repo), ""
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        proc = subprocess.run(argv, cwd=str(self.repo), env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
        ctx.log("$ " + " ".join(argv))
        ctx.log((proc.stdout or "") + (proc.stderr or ""))
        return proc.returncode, proc.stdout or ""


def _check(code: int, what: str) -> None:
    if code != 0:
        raise RuntimeError(f"{what} failed - see the log above")


def _safe_key(clip_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", clip_id)[:48].strip("_") or "clip"


# ---- topics and script -------------------------------------------------------


def _research(project: Path, ai: AiClient, params: dict[str, Any]) -> Callable[[JobContext], None]:
    def body(ctx: JobContext) -> None:
        spec = load_spec(project)
        ids = [str(i) for i in params.get("research") or []]
        videos = [v for v in spec.get("videos") or [] if v.get("id") in ids]
        unknown = [i for i in ids if i not in {v.get("id") for v in videos}]
        if unknown:
            raise ValueError(f"unknown video id '{unknown[0]}'")
        ctx.log(f"asking the AI to research {len(videos)} topic(s) and find sources ...")
        result = ai.run("topic_research", {
            "theme": str(params.get("theme") or "space facts"),
            "videos": [{k: v.get(k) for k in ("id", "topic", "hook_type", "hook_text", "topic_sources")} for v in videos],
        }, SCHEMAS["topic_research"], TASK_TOOLS["topic_research"])
        save_spec(project, apply_research(spec, result.get("items") or [], ids, project))
        ctx.log("saved sources, hook type and first sentence - check them, then approve")
    return body


def _topics(project: Path, ai: AiClient, params: dict[str, Any], cli: Any) -> Callable[[JobContext], None]:
    if params.get("research"):
        return _research(project, ai, params)

    def body(ctx: JobContext) -> None:
        spec = load_spec(project)
        if spec.get("videos") and not params.get("replace"):
            raise ValueError(f"there are already {len(spec['videos'])} topics; suggesting again would replace them "
                             "- confirm the replacement in the page, or use Research on the topics you typed")
        count = max(1, min(int(params.get("count") or 5), 12))
        ctx.log(f"asking claude for {count} topics ...")
        result = ai.run("topics", {
            "theme": str(params.get("theme") or "space facts"), "count": count,
            "existing_topics": [str(t) for t in params.get("avoid") or []],
            "platform": spec.get("platform") or "youtube_shorts",
        }, SCHEMAS["topics"], TASK_TOOLS["topics"])
        save_spec(project, apply_topics(spec, result.get("items") or [], count, project))
        ctx.log(f"saved {count} topics - review them, then approve")
    return body


def _factcheck(project: Path, ai: AiClient, params: dict[str, Any]) -> Callable[[JobContext], None]:
    def body(ctx: JobContext) -> None:
        spec = load_spec(project)
        who = f"{params.get('checker') or 'the AI'}" + (" (the same AI that wrote them)" if params.get("writer") == params.get("checker") else "")
        ctx.log(f"asking {who} to open every cited page and check every claim ...")
        result = ai.run("fact_check", check_inputs(spec.get("videos") or []), SCHEMAS["fact_check"], TASK_TOOLS["fact_check"])
        record = apply_check(spec, result.get("items") or [], str(params.get("writer") or ""), str(params.get("checker") or ""))
        save_check(project, record)
        flagged = sum(1 for v in record["videos"].values() for c in v["claims"] if c["verdict"] != "supported")
        ctx.log(f"fact check done: {flagged} claim(s) need a look")
    return body


def _script(project: Path, ai: AiClient, params: dict[str, Any], cli: Any) -> Callable[[JobContext], None]:
    if params.get("factcheck"):
        return _factcheck(project, ai, params)

    def body(ctx: JobContext) -> None:
        spec = load_spec(project)
        ctx.log("asking claude for scripts ...")
        result = ai.run("scripts", {
            "platform": spec.get("platform") or "youtube_shorts",
            "videos": [{k: v.get(k) for k in ("id", "topic", "hook_type", "hook_text", "topic_sources")}
                       for v in spec.get("videos") or []],
        }, SCHEMAS["scripts"], TASK_TOOLS["scripts"])
        save_spec(project, apply_scripts(spec, result.get("items") or [], project))
        ctx.log("saved scripts")
    return body


# ---- render, review, export --------------------------------------------------


def _cover_audio(ctx: JobContext, project: Path, ids: list[str]) -> None:
    """A different voice can run longer than the footage. Lengthen the last shot if the clip has room, else say so."""
    spec = load_spec(project)
    try:
        recorded = {v.get("id"): v for v in read_batch(project).get("videos", [])}
    except (OSError, ValueError):
        return  # nothing voiced yet: the render command will say so
    info = None
    changed = False
    for video in spec.get("videos") or []:
        if video["id"] not in ids:
            continue
        audio = float((recorded.get(video["id"]) or {}).get("audio_duration") or 0)
        cuts = video.get("cuts") or []
        total = sum(float(c["dur"]) for c in cuts)
        if not audio or not cuts or total >= audio - 0.1:
            continue
        delta = audio + 0.15 - total
        last = cuts[-1]
        info = info if info is not None else source_info(project, spec)
        meta = info.get(last["source"])
        end = float(last["in"]) + float(last["dur"]) + delta
        room = meta is not None and end <= meta["duration"] + 0.01
        if room:
            identity = (spec.get("sources") or {}).get(last["source"], {}).get("clip_id")
            for other in spec["videos"]:
                for cut in other.get("cuts") or []:
                    same_clip = (spec["sources"].get(cut["source"]) or {}).get("clip_id") == identity
                    if cut is not last and same_clip and not (cut["in"] + cut["dur"] <= last["in"] or cut["in"] >= end):
                        room = False
        if not room:
            raise ValueError(f"{video['id']}: the voice is {audio:.1f} s but the footage is only {total:.1f} s and the last "
                             "shot cannot be made longer (the clip ends or the next seconds are used); redo this video's footage")
        last["dur"] = round(float(last["dur"]) + delta, 2)
        changed = True
        ctx.log(f"{video['id']}: the voice is {audio:.1f} s, so the last shot was lengthened by {delta:.1f} s")
    if changed:
        save_spec(project, spec)


def _render(project: Path, ai: AiClient, params: dict[str, Any], cli: Any) -> Callable[[JobContext], None]:
    def body(ctx: JobContext) -> None:
        spec = load_spec(project)
        try:
            recorded = {v.get("id"): v for v in read_batch(project).get("videos", [])}
        except (OSError, ValueError):
            recorded = {}
        stale = []
        for video in spec.get("videos") or []:
            try:
                current = (project / "renders" / f"{video['id']}.mp4").is_file() and                     recorded.get(video["id"], {}).get("render_hash") == render_fingerprint(spec, video)
            except (KeyError, TypeError, ValueError):
                current = False
            if not current:
                stale.append(video["id"])
        if not stale:
            ctx.log("every render is already current - nothing to do")
            return
        blockers = render_blockers(project, spec, stale)  # footage that was not verified, or was judged not to show the subject
        if blockers:
            raise ValueError("; ".join(blockers))
        ids = ",".join(stale)
        _check(cli.run(ctx, ["voice", str(project), "--video", ids])[0], "voice")
        _cover_audio(ctx, project, stale)
        _check(cli.run(ctx, ["render", str(project), "--video", ids])[0], "render")
    return body


def _review(project: Path, ai: AiClient, params: dict[str, Any], cli: Any) -> Callable[[JobContext], None]:
    def body(ctx: JobContext) -> None:
        _check(cli.run(ctx, ["checkpoints", str(project)])[0], "review preparation")
    return body


def _export(project: Path, ai: AiClient, params: dict[str, Any], cli: Any) -> Callable[[JobContext], None]:
    def body(ctx: JobContext) -> None:
        ids = [v["id"] for v in load_spec(project).get("videos") or [] if not v.get("redo_note")]
        if not ids:
            raise ValueError("no videos to export")
        _check(cli.run(ctx, ["export", str(project), "--approved", ",".join(ids)])[0], "export")
    return body


_PLANS = {"topics": _topics, "script": _script, "render": _render, "review": _review, "export": _export}


def plan_for(step: str, project_dir: Path, ai: AiClient | None, params: dict[str, Any],
             cli: Any = None, reviewer: tuple[AiClient, str, str] | None = None) -> Callable[[JobContext], None]:
    """reviewer = (the other AI, its name, the writer's name): it looks at the footage frames after the cuts are chosen."""
    if step == "footage":
        return footage_plan(Path(project_dir), ai, params or {}, cli or Cli(), reviewer)
    if step not in _PLANS:
        raise ValueError(f"unknown step '{step}'")
    return _PLANS[step](Path(project_dir), ai, params or {}, cli or Cli())


# ---- gates and redo ----------------------------------------------------------


def approval_argv(project_dir: Path, gate: str) -> list[str]:
    """The one command that approves a gate. Only the owner's button click may run it."""
    project = Path(project_dir)
    if gate not in GATES:
        raise ValueError(f"unknown gate '{gate}'")
    base = [sys.executable, str(CLI_SCRIPT), "checkpoints", str(project)]
    if gate == "idea":
        violations = validate_spec(load_spec(project), project, "idea")
        if violations:
            raise ValueError("; ".join(violations))
        return base + ["--idea-only", "--approve", "idea"]
    states = step_states(project)
    if states["render"] != "done" or gate_status(project, "review_queue") == "none":
        raise ValueError("prepare the review first: every video must be rendered and the review step run")
    return base + ["--approve", "review_queue"]


def redo_video(project_dir: Path, video_id: str, note: str) -> None:
    project = Path(project_dir)
    spec = load_spec(project)
    video = next((v for v in spec.get("videos") or [] if v.get("id") == video_id), None)
    if video is None:
        raise ValueError(f"no such video '{video_id}'")
    video["redo_avoid"] = sorted({c.get("source") for c in video.get("cuts") or [] if c.get("source")})
    video["cuts"] = []
    video.pop("footage_override", None)
    video["redo_note"] = note
    save_spec(project, spec)
    try:
        (project / "renders" / f"{video_id}.mp4").unlink()
    except FileNotFoundError:
        pass
