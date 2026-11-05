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
from studio.jobs import JobContext
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


def _script(project: Path, ai: AiClient, params: dict[str, Any], cli: Any) -> Callable[[JobContext], None]:
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


# ---- footage -----------------------------------------------------------------


def _candidates(ctx: JobContext, cli: Any, project: Path, queries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Search every query; return unique candidates in query order: {clip_id, source, size_mb | None}."""
    found: list[dict[str, Any]] = []
    for q in queries:
        code, out = cli.run(ctx, ["search", str(project), "--source", q["source"], "--query", q["query"]], capture=True)
        if code != 0:
            continue
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) < 3 or not parts[0].strip():
                continue
            clip_id = parts[0].strip()
            if any(c["clip_id"] == clip_id for c in found):
                continue
            try:
                size = float(parts[-1])
            except ValueError:
                size = None
            found.append({"clip_id": clip_id, "source": q["source"], "query": q["query"], "size_mb": size})
    return found


def _fetch_for(ctx, cli, project, cands, refused, avoid=frozenset()) -> str | None:
    """Return the key of the first usable clip among cands (already fetched, or fetched now)."""
    tries = 0
    for cand in cands:
        key = _safe_key(cand["clip_id"])
        if key in avoid:
            continue
        if key in (load_spec(project).get("sources") or {}):
            return key
        if cand["clip_id"] in refused:
            continue
        if cand["size_mb"] is not None and cand["size_mb"] > MAX_MB:
            refused[cand["clip_id"]] = f"{cand['size_mb']:.0f} MB, over the size cap"
            continue
        if tries >= MAX_TRIES_PER_VIDEO:
            break
        tries += 1
        code, out = cli.run(ctx, ["fetch", str(project), "--source", cand["source"], "--query", cand["query"],
                                  "--clip-id", cand["clip_id"], "--key", key, "--max-mb", str(MAX_MB)], capture=True)
        if code != 0:
            refused[cand["clip_id"]] = (out.strip().splitlines() or ["download refused"])[-1][:120]
            continue
        return key
    return None


def _sheet(ctx, cli, project, key, step=None) -> dict[str, Any]:
    args = ["sheet", str(project), "--key", key] + (["--step", str(step)] if step else [])
    code, out = cli.run(ctx, args, capture=True)
    _check(code, f"contact sheet for {key}")
    return json.loads(out.strip().splitlines()[-1])


def _footage(project: Path, ai: AiClient, params: dict[str, Any], cli: Any) -> Callable[[JobContext], None]:
    def body(ctx: JobContext) -> None:
        spec = load_spec(project)
        need = [v for v in spec.get("videos") or [] if not v.get("cuts")]
        if not need:
            ctx.log("every video already has cuts")
            return
        # narration length decides how much footage is needed, so voice the narration first
        for video in need:
            _check(cli.run(ctx, ["voice", str(project), "--video", video["id"]])[0], f"voice for {video['id']}")
        audio = {v["id"]: float(v.get("audio_duration") or 0) for v in read_batch(project).get("videos", [])}
        if any(audio.get(v["id"], 0) <= 0 for v in need):
            raise ValueError("narration length unknown after the voice step")
        needed = {v["id"]: round(audio[v["id"]] + 0.5, 1) for v in need}

        ctx.log("asking claude for search queries ...")
        queries = ai.run("footage_queries", {"videos": [
            {"id": v["id"], "topic": v.get("topic"), "narration": v.get("narration"),
             "redo_note": v.get("redo_note")} for v in need]},
            SCHEMAS["footage_queries"], TASK_TOOLS["footage_queries"])
        q_by_id = {i["id"]: i.get("queries") or [] for i in queries.get("items") or []}

        refused: dict[str, str] = {}
        keys: list[str] = []
        for video in need:
            key = _fetch_for(ctx, cli, project, _candidates(ctx, cli, project, q_by_id.get(video["id"], [])), refused,
                             avoid=set(video.get("redo_avoid") or []))
            if key is None:
                reasons = "; ".join(f"{c}: {r}" for c, r in list(refused.items())[:4]) or "no candidates found"
                raise RuntimeError(f"no clip within the {MAX_MB} MB size cap for {video['id']} ({reasons})")
            if key not in keys:
                keys.append(key)

        sheets = []
        spec = load_spec(project)
        for key in keys:
            info = _sheet(ctx, cli, project, key)
            if info["duration"] > info["step"] * SHEET_FRAMES:
                info = _sheet(ctx, cli, project, key, step=math.ceil(info["duration"] / SHEET_FRAMES))
            spec["sources"][key].pop("mode", None)  # re-choose from the real picture shape
            spec = add_source(spec, key, spec["sources"][key], info["width"], info["height"])
            used = [[c["in"], c["in"] + c["dur"]] for v in spec["videos"] for c in v.get("cuts") or []
                    if c.get("source") == key]
            sheets.append({"key": key, "sheet": info["path"], "step": info["step"], "cols": info["cols"],
                           "duration": info["duration"], "already_used": used})
        save_spec(project, spec)

        ctx.log("asking claude to pick cuts from the contact sheets ...")
        result = ai.run("footage_cuts", {
            "videos": [{"id": v["id"], "narration": v.get("narration"), "needed_seconds": needed[v["id"]]} for v in need],
            "sources": sheets,
        }, SCHEMAS["footage_cuts"], TASK_TOOLS["footage_cuts"], add_dirs=[project / "review"])
        items = [i for i in result.get("items") or [] if i.get("id") in needed]
        missing = [vid for vid in needed if vid not in {i.get("id") for i in items}]
        if missing:
            raise ValueError(f"the AI returned no cuts for {', '.join(missing)}")
        lengths = {s["key"]: s["duration"] for s in sheets}
        for item in items:
            for cut in item.get("cuts") or []:
                length = lengths.get(cut.get("source"))
                if length is not None and float(cut.get("in", 0)) + float(cut.get("dur", 0)) > length + 0.05:
                    raise ValueError(f"{item['id']}: a cut runs past the end of the clip "
                                     f"('{cut.get('source')}' is {length:.1f} s long)")
        merged = apply_cuts(spec, items, project)
        for video in merged["videos"]:
            if video["id"] in needed:
                total = sum(float(c["dur"]) for c in video.get("cuts") or [])
                if total < needed[video["id"]]:
                    raise ValueError(f"cuts for {video['id']} total {total:.1f} s but the narration needs "
                                     f"{needed[video['id']]:.1f} s")
        for video in merged["videos"]:
            if video["id"] in needed:
                video.pop("redo_note", None)
                video.pop("redo_avoid", None)
        save_spec(project, merged)
        ctx.log("saved cuts")
    return body


# ---- render, review, export --------------------------------------------------


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
        for vid in stale:
            _check(cli.run(ctx, ["voice", str(project), "--video", vid])[0], f"voice for {vid}")
            _check(cli.run(ctx, ["render", str(project), "--video", vid])[0], f"render for {vid}")
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


_PLANS = {"topics": _topics, "script": _script, "footage": _footage, "render": _render,
          "review": _review, "export": _export}


def plan_for(step: str, project_dir: Path, ai: AiClient | None, params: dict[str, Any],
             cli: Any = None) -> Callable[[JobContext], None]:
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
    video["redo_note"] = note
    save_spec(project, spec)
    try:
        (project / "renders" / f"{video_id}.mp4").unlink()
    except FileNotFoundError:
        pass
