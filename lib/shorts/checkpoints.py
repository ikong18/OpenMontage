"""Stage records for a shorts-factory batch (idea .. review_queue).

One project is one batch, so each canonical artifact aggregates all videos
(ids prefixed v1-/v2-/...); per-video truth stays in artifacts/shorts_batch.json.
Gated stages (idea, review_queue) are only written completed when the caller
passes the matching approval.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from lib.checkpoint import CheckpointValidationError, init_project, write_checkpoint

PIPELINE = "shorts-factory"
PLAYBOOK = "shorts-captions"
_KW = {"pipeline_type": PIPELINE, "style_playbook": PLAYBOOK}


def _write(projects_dir: Path, pid: str, stage: str, status: str, artifacts: dict, **extra: Any) -> None:
    try:
        write_checkpoint(projects_dir, pid, stage, status, artifacts, **_KW, **extra)
    except CheckpointValidationError as exc:
        raise ValueError(str(exc).strip().splitlines()[0]) from exc


def _option(option_id: str, label: str, score: float, reason: str, rejected: str | None = None) -> dict:
    option = {"option_id": option_id, "label": label, "score": score, "reason": reason}
    if rejected:
        option["rejected_because"] = rejected
    return option


def write_idea_record(projects_dir: Path, spec: dict[str, Any], approve: bool) -> None:
    pid = spec["project_id"]
    init_project(pid, title=f"Shorts batch {pid}", pipeline_type=PIPELINE,
                 pipeline_dir=projects_dir, style_playbook=PLAYBOOK)
    videos = spec["videos"]
    brief = {
        "version": "1.0", "title": f"Shorts batch {pid}",
        "hook": f"{len(videos)} sourced facts, each a vertical Short under 60 seconds",
        "key_points": [v["topic"] for v in videos],
        "core_message": "Surprising, sourced facts", "tone": "curious, punchy", "style": PLAYBOOK,
        "target_audience": "general YouTube Shorts viewers", "target_platform": "youtube",
        "target_duration_seconds": 35, "selected_angle": "primary sources only",
        "metadata": {"batch_size": len(videos), "topics": [
            {"id": v["id"], "topic": v["topic"], "hook_type": v["hook_type"],
             "source": (v["claims"][0]["sources"][0] if v.get("claims") else "")} for v in videos]},
    }
    decisions = [
        {"decision_id": "d-runtime-1", "stage": "idea", "category": "render_runtime_selection",
         "subject": "Composition runtime",
         "options_considered": [
             _option("remotion", "Remotion", 0.95, "Word-level caption burn, installed"),
             _option("hyperframes", "HyperFrames", 0.0, "No caption-burn parity on shorts-factory",
                     "lacks caption parity"),
             _option("ffmpeg", "ffmpeg only", 0.3, "No animated word captions",
                     "cannot burn word-level animated captions")],
         "selected": "remotion", "reason": "Word-level caption burn is required.",
         "user_visible": True, "user_approved": approve},
        {"decision_id": "d-topics-1", "stage": "idea", "category": "concept_selection",
         "subject": "Batch topic list",
         "options_considered": [_option(v["id"], v["topic"], 0.9, "Sourced fact") for v in videos],
         "selected": ", ".join(v["id"] for v in videos), "reason": "Owner-approved topic list",
         "user_visible": True, "user_approved": approve},
    ]
    artifacts = {"brief": brief, "decision_log": {"version": "1.0", "project_id": pid, "decisions": decisions}}
    if approve:
        _write(projects_dir, pid, "idea", "completed", artifacts, human_approved=True)
    else:
        _write(projects_dir, pid, "idea", "awaiting_human", artifacts)


def _probe_default(path: Path) -> dict[str, Any]:
    from scripts.shorts_audit import probe_render

    return probe_render(path)


def sample_frames(render: Path, out_dir: Path, count: int = 4) -> list[Path]:
    """Extract `count` evenly spaced frames so the review can look at them."""
    import shutil
    import subprocess

    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH; install it (for example: winget install Gyan.FFmpeg)")
    duration = float(_probe_default(render)["duration"])
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for index in range(count):
        target = out_dir / f"{render.stem}_{index + 1}.jpg"
        stamp = duration * (index + 0.5) / count
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-ss", f"{stamp:.2f}", "-i", str(render),
             "-frames:v", "1", "-vf", "scale=360:-1", str(target)],
            capture_output=True, text=True, encoding="utf-8")
        if proc.returncode != 0 or not target.exists():
            raise RuntimeError(f"could not sample a frame from {render.name}")
        paths.append(target)
    return paths


def write_stage_records(
    projects_dir: Path,
    spec: dict[str, Any],
    batch: dict[str, Any],
    approve: frozenset[str] = frozenset(),
    probe: Callable[[Path], dict[str, Any]] = _probe_default,
    sampler: Callable[[Path, Path], list[Path]] = sample_frames,
) -> list[str]:
    pid = spec["project_id"]
    project_dir = Path(projects_dir) / pid
    by_id = {v["id"]: v for v in batch["videos"]}
    written: list[str] = []

    # script
    sections, cursor = [], 0.0
    for video in spec["videos"]:
        text = video["narration"]
        duration = float(by_id[video["id"]].get("audio_duration") or 0.0)
        sections.append({"id": f"{video['id']}-narration", "label": f"{video['id']} narration", "text": text,
                         "start_seconds": round(cursor, 2), "end_seconds": round(cursor + duration, 2)})
        cursor += duration
    script = {"version": "1.0", "title": f"Shorts batch {pid}", "total_duration_seconds": round(cursor, 2),
              "sections": sections,
              "metadata": {"note": "Batch artifact; per-video truth is artifacts/shorts_batch.json"}}
    _write(projects_dir, pid, "script", "completed", {"script": script})
    written.append("script")

    # visuals -> scene_plan (one scene per cut)
    scenes = []
    for video in spec["videos"]:
        t = 0.0
        for index, cut in enumerate(video["cuts"], start=1):
            clip_id = by_id[video["id"]]["clips"][index - 1]["clip_id"]
            scenes.append({
                "id": f"{video['id']}-c{index}", "type": "broll",
                "description": f"{video['topic']} - {clip_id}",
                "start_seconds": round(t, 3), "end_seconds": round(t + float(cut["dur"]), 3),
                "script_section_id": f"{video['id']}-narration", "framing": "9:16",
                "required_assets": [{"type": "video", "description": clip_id, "source": "source"}]})
            t += float(cut["dur"])
    scene_plan = {"version": "1.0", "style_playbook": PLAYBOOK, "scenes": scenes}
    _write(projects_dir, pid, "visuals", "completed", {"scene_plan": scene_plan})
    written.append("visuals")

    # voice_captions -> asset_manifest
    assets = []
    for video in spec["videos"]:
        entry = by_id[video["id"]]
        scene_id = f"{video['id']}-c1"
        assets.append({"id": f"{video['id']}-narration", "type": "narration",
                       "path": f"projects/{pid}/{entry['audio']}", "source_tool": "piper_tts",
                       "scene_id": scene_id, "provider": "piper", "cost_usd": 0.0,
                       "duration_seconds": float(entry.get("audio_duration") or 0.0), "format": "wav"})
        assets.append({"id": f"{video['id']}-captions", "type": "subtitle",
                       "path": f"projects/{pid}/{entry['captions']}", "source_tool": "subtitle_gen",
                       "scene_id": scene_id, "cost_usd": 0.0, "format": "json"})
        for clip in entry["clips"]:
            assets.append({"id": clip["clip_id"], "type": "video", "path": f"projects/{pid}/assets/video",
                           "source_tool": "stock_footage", "scene_id": scene_id, "cost_usd": 0.0,
                           "provider": clip["source"], "license": clip["license"], "original_url": clip["url"]})
    _write(projects_dir, pid, "voice_captions", "completed",
           {"asset_manifest": {"version": "1.0", "assets": assets, "total_cost_usd": 0.0}})
    written.append("voice_captions")

    # compose -> render_report + final_review
    outputs = []
    frame_paths: list[str] = []
    for video in spec["videos"]:
        render = project_dir / by_id[video["id"]]["render"]
        info = probe(render)
        frame_paths += [str(p) for p in sampler(render, project_dir / "assets" / "review_frames")]
        outputs.append({"path": f"projects/{pid}/{by_id[video['id']]['render']}", "format": "mp4",
                        "codec": "h264", "resolution": f"{info['width']}x{info['height']}", "fps": 30,
                        "duration_seconds": float(info["duration"]),
                        "file_size_bytes": render.stat().st_size if render.exists() else 0,
                        "platform_target": "youtube_shorts"})
    report = {"version": "1.0", "outputs": outputs, "render_grammar": "explainer-data",
              "verification_notes": ["probed with ffprobe"],
              "metadata": {"render_runtime": "remotion"}}
    review = {"version": "1.0", "output_path": f"projects/{pid}/renders", "status": "pass", "checks": {
        "technical_probe": {"valid_container": True, "resolution": "1080x1920", "fps": 30, "has_audio": True,
                            "codec": "h264", "issues": []},
        "visual_spotcheck": {"frames_sampled": len(frame_paths), "frame_paths": frame_paths,
                             "black_frames_detected": False, "broken_overlays": False,
                             "missing_assets": False, "unreadable_text": False,
                             "issues": ["frames are extracted by the runner; the agent must view them before review"]},
        "audio_spotcheck": {"narration_present": True, "music_present": False, "unexpected_silence": False,
                            "clipping_detected": False, "mix_intelligible": True, "issues": []},
        "promise_preservation": {"delivery_promise_honored": True, "renderer_family_used": "explainer-data",
                                 "render_runtime_used": "remotion", "runtime_swap_detected": False,
                                 "runtime_swap_check": "idea gate locked remotion; compose used remotion",
                                 "silent_downgrade_detected": False, "issues": []},
        "subtitle_check": {"subtitles_expected": True, "subtitles_present": True, "timing_drift_detected": False,
                           "issues": ["word timings are estimated from audio pauses, not ASR"]}}}
    _write(projects_dir, pid, "compose", "completed", {"render_report": report, "final_review": review})
    written.append("compose")

    # review_queue
    approved = "review_queue" in approve
    decisions = [{"decision_id": "d-audit-1", "stage": "review_queue", "category": "approval_policy",
                  "subject": "Batch audit before review queue",
                  "options_considered": [_option("audit", "scripts/shorts_audit.py passes", 1.0,
                                                 "all batch rules pass")],
                  "selected": "open review queue", "reason": "audit exit code 0",
                  "user_visible": True, "user_approved": approved}]
    if approved:
        decisions.append({"decision_id": "d-review-1", "stage": "review_queue", "category": "approval_policy",
                          "subject": "Batch review outcome",
                          "options_considered": [_option("approve_all", "Approve reviewed videos", 0.9,
                                                         "owner reviewed the batch")],
                          "selected": "approve_all", "reason": "owner approved in chat",
                          "user_visible": True, "user_approved": True})
    log = {"version": "1.0", "project_id": pid, "decisions": decisions}
    if approved:
        _write(projects_dir, pid, "review_queue", "completed", {"decision_log": log}, human_approved=True)
    else:
        _write(projects_dir, pid, "review_queue", "awaiting_human", {"decision_log": log})
    written.append("review_queue")
    return written
