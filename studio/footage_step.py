"""The footage step: find clips, LOOK at them, cut only from frames that show the subject, then have a second AI look at
one real frame of every shot. Nothing is used because a search returned it or because it fits the download limit.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Callable

from lib.shorts.batch import read_batch
from lib.shorts.sheets import extract_frame
from lib.shorts.spec import load_spec, save_spec
from studio.ai import SCHEMAS, TASK_TOOLS, AiClient, AiError
from studio.apply import apply_cuts
from studio.footage import (apply_review, cut_is_verified, ensure_disclosure, relevant_seconds, review_inputs,
                            save_check)
from studio.jobs import JobContext

MAX_MB = 250
MAX_PICKS = 5
SHEET_FRAMES = 30
MAX_STEP = 12  # seconds between sheet frames; a coarser sheet cannot prove a 3 s shot
MAX_DURATION = SHEET_FRAMES * MAX_STEP
MIN_SHOT, MAX_SHOT = 1.0, 6.0


def _safe_key(video_id: str, clip_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", f"{video_id}_{clip_id}")[:64].strip("_")


def _check(code: int, what: str) -> None:
    if code != 0:
        raise RuntimeError(f"{what} failed - see the log above")


# ---- searching -----------------------------------------------------------------------------------------------------------


def _candidates(ctx: JobContext, cli: Any, project: Path, queries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Search every query; unique results in order, with title text, size and length. Over-size files are dropped."""
    found: list[dict[str, Any]] = []
    for q in queries:
        code, out = cli.run(ctx, ["search", str(project), "--source", q["source"], "--query", q["query"]], capture=True)
        if code != 0:
            continue
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) < 3 or not parts[0].strip() or any(c["clip_id"] == parts[0].strip() for c in found):
                continue

            def number(text: str) -> float | None:
                try:
                    return float(text)
                except ValueError:
                    return None

            found.append({"clip_id": parts[0].strip(), "source": q["source"], "query": q["query"], "title": parts[1].strip(),
                          "size_mb": number(parts[2]), "duration": number(parts[3]) if len(parts) > 3 else None})
    return [c for c in found if (c["size_mb"] is None or c["size_mb"] <= MAX_MB)
            and (c["duration"] is None or c["duration"] <= MAX_DURATION)]


# ---- a downloaded clip --------------------------------------------------------------------------------------------------


def _sheet(ctx: JobContext, cli: Any, project: Path, key: str) -> dict[str, Any]:
    import json

    def run(step: int | None) -> dict[str, Any]:
        args = ["sheet", str(project), "--key", key] + (["--step", str(step)] if step else [])
        code, out = cli.run(ctx, args, capture=True)
        _check(code, f"contact sheet for {key}")
        return json.loads(out.strip().splitlines()[-1])

    info = run(None)
    if info["duration"] > info["step"] * SHEET_FRAMES:
        info = run(math.ceil(info["duration"] / SHEET_FRAMES))
    if info["step"] > MAX_STEP or info["duration"] > MAX_DURATION:
        raise RuntimeError(f"the clip is {info['duration']:.0f} s long - too long to verify frame by frame")
    return info


def _discard(project: Path, key: str) -> None:
    """Remove a rejected clip: its source entry, its file (unless another source uses it) and its contact sheet."""
    spec = load_spec(project)
    source = (spec.get("sources") or {}).pop(key, None)
    if source:
        path = source.get("path")
        if path and not any(s.get("path") == path for s in spec["sources"].values()):
            try:
                (project / path).unlink()
            except OSError:
                pass
        save_spec(project, spec)
    try:
        (project / "review" / f"sheet_{key}.png").unlink()
    except OSError:
        pass


def _verify(ctx: JobContext, ai: AiClient, project: Path, key: str, info: dict[str, Any], subject: str,
            narration: str, stand_ins: str = "") -> dict[str, Any]:
    result = ai.run("footage_verify", {
        "subject": subject, "stand_ins": stand_ins, "narration": narration,
        "sources": [{"key": key, "sheet": info["path"], "step": info["step"], "cols": info["cols"], "duration": info["duration"]}],
    }, SCHEMAS["footage_verify"], TASK_TOOLS["footage_verify"], add_dirs=[project / "review"])
    item = next((i for i in result.get("items") or [] if i.get("key") == key), None)
    if item is None:
        raise ValueError(f"the AI returned no verdict for the clip {key}")
    total_frames = max(1, math.ceil(info["duration"] / info["step"]))
    frames = sorted({int(f) for f in item.get("relevant_frames") or [] if isinstance(f, int) and not isinstance(f, bool) and 0 <= f < total_frames})
    match = item.get("match")
    if match not in ("exact", "related", "unrelated"):
        raise ValueError(f"the AI gave an unknown verdict '{match}' for {key}")
    return {"match": match, "shows": str(item.get("shows") or "").strip(), "relevant_frames": [] if match == "unrelated" else frames,
            "step": info["step"], "cols": info["cols"], "duration": info["duration"], "note": str(item.get("note") or ""),
            "subject": subject}


# ---- the plan --------------------------------------------------------------------------------------------------------------


def footage_plan(project: Path, ai: AiClient, params: dict[str, Any], cli: Any,
                 reviewer: tuple[AiClient, str, str] | None) -> Callable[[JobContext], None]:
    project = Path(project).resolve()  # the AI runs in another folder: every path it is given must be absolute
    reviewers = reviewer if isinstance(reviewer, list) else [reviewer or (ai, "ai", "ai")]

    def body(ctx: JobContext) -> None:
        if params.get("verify_only"):
            _reverify(ctx, ai, cli, project)
            _review(ctx, project, reviewers, None)
            return
        spec = load_spec(project)
        need = [v for v in spec.get("videos") or [] if not v.get("cuts")]
        if not need:
            ctx.log("every video already has cuts")
            return
        ids = ",".join(v["id"] for v in need)
        _check(cli.run(ctx, ["voice", str(project), "--video", ids])[0], "voice")
        audio = {v["id"]: float(v.get("audio_duration") or 0) for v in read_batch(project).get("videos", [])}
        if any(audio.get(v["id"], 0) <= 0 for v in need):
            raise ValueError("narration length unknown after the voice step")
        needed = {v["id"]: round(audio[v["id"]] + 0.5, 1) for v in need}

        ctx.log("asking the AI what each video must show, and for search terms ...")
        planned = ai.run("footage_queries", {"videos": [
            {"id": v["id"], "topic": v.get("topic"), "narration": v.get("narration"), "redo_note": v.get("redo_note")} for v in need]},
            SCHEMAS["footage_queries"], TASK_TOOLS["footage_queries"])
        plans = {i["id"]: i for i in planned.get("items") or []}
        for video in need:
            if video["id"] not in plans or not str(plans[video["id"]].get("subject") or "").strip():
                raise ValueError(f"the AI did not say what {video['id']} must show")
        subjects = {vid: str(p["subject"]).strip() for vid, p in plans.items()}
        stand_ins = {vid: str(p.get("stand_ins") or "").strip() for vid, p in plans.items()}

        accepted: dict[str, list[str]] = {}
        created: list[str] = []
        try:
            for video in need:
                accepted[video["id"]] = _gather(ctx, ai, cli, project, video, subjects[video["id"]], plans[video["id"]],
                                                needed[video["id"]], created)
            _cut_and_save(ctx, ai, project, need, subjects, accepted, needed, stand_ins)
        except BaseException:
            for key in created:  # nothing from a failed run stays behind
                _discard(project, key)
            raise
        _review(ctx, project, reviewers, [v["id"] for v in need])
    return body


def _gather(ctx, ai, cli, project, video, subject, plan, needed, created) -> list[str]:
    vid = video["id"]
    avoid = set(video.get("redo_avoid") or [])
    candidates = [c for c in _candidates(ctx, cli, project, plan.get("queries") or [])
                  if _safe_key(vid, c["clip_id"]) not in avoid and c["clip_id"] not in avoid]
    trace: list[str] = []
    picks: list[dict[str, Any]] = []
    if candidates:
        ctx.log(f"{vid}: {len(candidates)} search results; asking the AI which ones could show the subject ...")
        chosen = ai.run("footage_pick", {"videos": [{
            "id": vid, "subject": subject, "narration": video.get("narration"),
            "candidates": [{k: c[k] for k in ("clip_id", "title", "duration", "size_mb")} for c in candidates]}]},
            SCHEMAS["footage_pick"], TASK_TOOLS["footage_pick"])
        known = {c["clip_id"] for c in candidates}
        for item in chosen.get("items") or []:
            if item.get("id") == vid:
                picks = [p for p in item.get("picks") or [] if p.get("clip_id") in known][:MAX_PICKS]
    else:
        trace.append("the searches returned no usable results")
    if not picks and candidates:
        trace.append("no search result looked like it shows the subject")

    keys: list[str] = []
    total = 0.0
    for pick in picks:
        key = _safe_key(vid, pick["clip_id"])
        code, out = cli.run(ctx, ["fetch", str(project), "--source", next(c["source"] for c in candidates if c["clip_id"] == pick["clip_id"]),
                                  "--query", next(c["query"] for c in candidates if c["clip_id"] == pick["clip_id"]),
                                  "--clip-id", pick["clip_id"], "--key", key, "--max-mb", str(MAX_MB)], capture=True)
        if code != 0:
            trace.append(f"{pick['clip_id']}: download refused ({(out.strip().splitlines() or ['no reason'])[-1][:100]})")
            continue
        created.append(key)
        try:
            info = _sheet(ctx, cli, project, key)
        except RuntimeError as exc:
            trace.append(f"{pick['clip_id']}: {exc}")
            _discard(project, key)
            created.remove(key)
            continue
        verified = _verify(ctx, ai, project, key, info, subject, str(video.get("narration") or ""), str(plan.get("stand_ins") or ""))
        verified["title"] = next(c["title"] for c in candidates if c["clip_id"] == pick["clip_id"])[:160]
        if verified["match"] == "unrelated" or not verified["relevant_frames"]:
            trace.append(f"{pick['clip_id']}: not about the subject (it shows {verified['shows'] or 'something else'})")
            _discard(project, key)
            created.remove(key)
            continue
        spec = load_spec(project)
        spec["sources"][key]["verified"] = verified
        save_spec(project, spec)
        keys.append(key)
        total += relevant_seconds(verified)
        ctx.log(f"{vid}: {pick['clip_id']} is {verified['match']}: {verified['shows']} ({relevant_seconds(verified):.0f} s verified)")
        if total >= needed + 1.0:
            break
        trace.append(f"{pick['clip_id']}: too little of the clip shows the subject ({relevant_seconds(verified):.0f} s)")
    if total < needed + 1.0:
        raise RuntimeError(
            f"No footage that shows {subject!r} was found for {vid}: " + ("; ".join(trace) or "nothing usable")
            + ". Studio will not use footage it could not verify. Add your own clip, change the topic, or try again.")
    return keys


def _cut_and_save(ctx, ai, project, need, subjects, accepted, needed, stand_ins=None) -> None:
    spec = load_spec(project)
    sources_in = {}
    for vid, keys in accepted.items():
        for key in keys:
            v = spec["sources"][key]["verified"]
            used = [[c["in"], c["in"] + c["dur"]] for other in spec["videos"] for c in other.get("cuts") or [] if c.get("source") == key]
            sheet = project / "review" / f"sheet_{key}.png"
            sources_in[key] = {"key": key, "sheet": str(sheet), "step": v["step"], "cols": v["cols"], "duration": v["duration"],
                               "relevant_frames": v["relevant_frames"], "shows": v["shows"], "already_used": used}
    videos_in = [{"id": v["id"], "narration": v.get("narration"), "subject": subjects[v["id"]], "needed_seconds": needed[v["id"]],
                  "allowed_sources": accepted[v["id"]]} for v in need]
    inputs: dict[str, Any] = {"videos": videos_in, "sources": list(sources_in.values())}
    problems: list[str] = []
    items: list[dict[str, Any]] = []
    for attempt in range(2):
        if problems:
            inputs = {**inputs, "problems": problems}
        ctx.log("asking the AI to cut only from the verified frames ..." if attempt == 0 else "sending the problems back for one more try ...")
        result = ai.run("footage_cuts", inputs, SCHEMAS["footage_cuts"], TASK_TOOLS["footage_cuts"], add_dirs=[project / "review"])
        items = [i for i in result.get("items") or [] if i.get("id") in needed]
        problems = _cut_problems(items, need, accepted, spec, needed)
        if not problems:
            break
    if problems:
        raise ValueError("the cuts are not on verified footage: " + "; ".join(problems[:6]))

    merged = apply_cuts(spec, [{"id": i["id"], "cuts": [{"source": c["source"], "in": c["in"], "dur": c["dur"], "shows": str(c["shows"]).strip()} for c in i["cuts"]]}
                                for i in items], project)
    by_id = {v["id"]: v for v in merged["videos"]}
    for item in items:
        video = by_id[item["id"]]
        video["footage_subject"] = subjects[item["id"]]
        video["footage_stand_ins"] = (stand_ins or {}).get(item["id"], "")
        for field in ("redo_note", "redo_avoid", "footage_override"):
            video.pop(field, None)
    ensure_disclosure(merged)
    save_spec(project, merged)
    ctx.log("saved cuts")


def _cut_problems(items, need, accepted, spec, needed) -> list[str]:
    problems: list[str] = []
    got = {i["id"]: i for i in items}
    for video in need:
        vid = video["id"]
        item = got.get(vid)
        if item is None or not item.get("cuts"):
            problems.append(f"{vid}: no cuts were returned")
            continue
        total = 0.0
        for index, cut in enumerate(item["cuts"]):
            label = f"{vid} shot {index + 1}"
            source = spec["sources"].get(cut.get("source"))
            if cut.get("source") not in accepted[vid] or source is None:
                problems.append(f"{label} uses '{cut.get('source')}', which is not one of this video's verified clips")
                continue
            if not str(cut.get("shows") or "").strip():
                problems.append(f"{label}: describe what the shot shows")
            dur = float(cut.get("dur") or 0)
            start = float(cut.get("in") or 0)
            if not (MIN_SHOT <= dur <= MAX_SHOT):
                problems.append(f"{label}: a shot must last {MIN_SHOT:g} to {MAX_SHOT:g} s")
            verified = source["verified"]
            if start + dur > verified["duration"] + 0.05:
                problems.append(f"{label} runs past the end of the clip ({verified['duration']:.1f} s long)")
            elif not cut_is_verified({"in": start, "dur": dur}, verified):
                problems.append(f"{label} at {start:g}-{start + dur:g} s is not on a verified frame (verified frames: {verified['relevant_frames']})")
            total += dur
        if total < needed[vid] - 0.1:
            problems.append(f"{vid}: the cuts total {total:.1f} s but the narration needs {needed[vid]:.1f} s")
    return problems


_UNREADABLE = re.compile(
    r"^\s*(the\s+)?(frame|image|file|picture)s?\b.{0,30}\b(is missing|are missing|not found|could not be (opened|read|loaded)|cannot be (opened|read|loaded)|unreadable)"
    r"|^\s*(i\s+)?(cannot|could not|can't|couldn't|unable to)\s+(open|read|load)\b", re.I)


def _reject_unreadable(items: list[dict[str, Any]], reviewer_name: str) -> None:
    """If the reviewer could not open a picture, its verdict is meaningless: that is an error, never a 'no' or a 'yes'."""
    for item in items:
        for cut in item.get("cuts") or []:
            if _UNREADABLE.search(str(cut.get("shows") or "")):
                raise AiError(f"{reviewer_name} could not open the frame for {item.get('id')} shot {int(cut.get('index', 0)) + 1}: "
                              f"{str(cut.get('shows'))[:120]}")


# ---- the second AI looks at the real frames ---------------------------------------------------------------------------------


def _review(ctx: JobContext, project: Path, reviewers: list[tuple[AiClient, str, str]], ids: list[str] | None) -> None:
    spec = load_spec(project)
    videos = [v for v in spec.get("videos") or [] if v.get("cuts") and (ids is None or v["id"] in ids)]
    if not videos:
        ctx.log("no shots to check")
        return
    frames: dict[tuple[str, int], list[Path]] = {}
    frame_paths: dict[str, list[str]] = {}
    for video in videos:
        rels = []
        for index, cut in enumerate(video["cuts"]):
            source = spec["sources"][cut["source"]]
            start, dur = float(cut["in"]), float(cut["dur"])
            edge = min(0.3, dur / 4)
            paths = []
            for k, seconds in enumerate((start + edge, start + dur / 2, start + dur - edge)):
                out = project / "review" / "footage" / f"{video['id']}_{index}_{k}.jpg"
                extract_frame(project / source["path"], seconds, out)
                if not out.is_file() or out.stat().st_size == 0:
                    raise RuntimeError(f"could not take a frame at {seconds:.1f} s of {source['path']} for {video['id']} shot {index + 1}")
                paths.append(out)
            frames[(video["id"], index)] = paths
            rels.append(paths[1].relative_to(project).as_posix())
        frame_paths[video["id"]] = rels
    subjects = {v["id"]: v.get("footage_subject") or f"what this narration is about: {v.get('narration')}" for v in videos}
    stand_ins = {v["id"]: v.get("footage_stand_ins") or "" for v in videos}
    subset = {**spec, "videos": videos}
    record = None
    for position, (client, reviewer_name, writer_name) in enumerate(reviewers):
        ctx.log(f"asking {reviewer_name} to look at {sum(len(p) for p in frames.values())} real frames ...")
        try:
            result = client.run("footage_review", review_inputs(subset, subjects, frames, stand_ins), SCHEMAS["footage_review"],
                                TASK_TOOLS["footage_review"], add_dirs=[project / "review"])
            _reject_unreadable(result.get("items") or [], reviewer_name)
            record = apply_review(subset, result.get("items") or [], writer_name, reviewer_name, frame_paths)
            break
        except AiError as exc:
            if position == len(reviewers) - 1:
                raise
            ctx.log(f"{reviewer_name} could not check ({exc}); asking {reviewers[position + 1][1]} instead")
    save_check(project, record, merge=True)
    bad = sum(1 for v in record["videos"].values() for c in v["cuts"] if c["verdict"] == "no")
    partly = sum(1 for v in record["videos"].values() for c in v["cuts"] if c["verdict"] == "partly")
    ctx.log(f"footage check done: {bad} shot(s) do not show the subject, {partly} only partly")


def _reverify(ctx: JobContext, ai: AiClient, cli: Any, project: Path) -> None:
    """Look at the clips behind the current shots again, against each video's subject. Footage that was cut by hand or
    before verification existed gets a verdict here; a clip that does not show the subject blocks the render."""
    spec = load_spec(project)
    wanted: dict[str, dict[str, Any]] = {}
    for video in spec.get("videos") or []:
        if not video.get("cuts"):
            continue
        if not video.get("footage_subject"):
            video["footage_subject"] = f"what this narration is about: {video.get('narration') or video.get('topic')}"
        for cut in video["cuts"]:
            wanted.setdefault(cut["source"], video)  # a clip shared by several videos is judged against the first one's subject
    save_spec(project, spec)
    for key, video in wanted.items():
        ctx.log(f"looking at {key} again ...")
        try:
            info = _sheet(ctx, cli, project, key)
        except RuntimeError as exc:
            ctx.log(f"{key}: {exc}")
            continue
        verified = _verify(ctx, ai, project, key, info, video["footage_subject"], str(video.get("narration") or ""),
                           str(video.get("footage_stand_ins") or ""))
        spec = load_spec(project)
        verified["title"] = (spec["sources"][key].get("verified") or {}).get("title", "")
        spec["sources"][key]["verified"] = verified
        save_spec(project, spec)
    spec = load_spec(project)
    ensure_disclosure(spec)
    save_spec(project, spec)
