"""Batch runner for the shorts-factory pipeline.

    python scripts/shorts_batch.py validate    projects/<id>
    python scripts/shorts_batch.py fetch       projects/<id> --source nasa --query Q --clip-id ID --key KEY
    python scripts/shorts_batch.py voice       projects/<id> [--video v1]
    python scripts/shorts_batch.py render      projects/<id> [--video v1]
    python scripts/shorts_batch.py checkpoints projects/<id> [--idea-only] [--approve idea|review_queue]
    python scripts/shorts_batch.py export      projects/<id> --approved v1,v3

Expected problems print one line starting with "error:" and exit 1. Gated
stages are only completed when an explicit --approve is passed, which the agent
does only after the owner says yes in chat.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.shorts.align import detect_speech_spans, split_sentences, to_remotion_captions  # noqa: E402
from lib.shorts.whisper_align import make_aligner  # noqa: E402
from lib.shorts.voices import kokoro_available, synthesize_kokoro  # noqa: E402
from lib.shorts.batch import (  # noqa: E402
    derive_batch, read_batch, record_cost, render_fingerprint, voice_fingerprint, write_batch,
)
from lib.shorts.checkpoints import write_idea_record, write_stage_records  # noqa: E402
from lib.shorts.export import export_videos  # noqa: E402
from lib.shorts.fetch import DEFAULT_MAX_MB, fetch_clip  # noqa: E402
from lib.shorts.fetch import _head_size  # noqa: E402
from lib.shorts.sheets import make_contact_sheet, probe_video  # noqa: E402
from lib.shorts.platforms import get_platform  # noqa: E402
from lib.shorts.props import build_props, check_cut_coverage, render_video  # noqa: E402
from lib.shorts.segments import cut_segment  # noqa: E402
from lib.shorts.spec import STAGES, load_spec, save_spec, validate_spec  # noqa: E402
from scripts.shorts_audit import audit_project  # noqa: E402

_PRESERVED = ("audio", "captions", "audio_duration", "cost_entries", "voice_hash", "render_hash")


def _registry() -> Any:
    from tools.tool_registry import registry

    registry.discover()
    return registry


def _resolve_candidate(source: str, query: str, clip_id: str) -> Any:
    from tools.video.stock_sources import all_sources
    from tools.video.stock_sources.base import SearchFilters

    adapters = {s.name: s for s in all_sources()}
    if source not in adapters:
        raise ValueError(f"unknown source '{source}' (choose from {', '.join(sorted(adapters))})")
    candidates = adapters[source].search(query, SearchFilters(kind="video", per_page=12))
    matches = [c for c in candidates if c.clip_id == clip_id or c.clip_id.startswith(clip_id)]
    if not matches:
        raise ValueError(f"clip '{clip_id}' not found for query '{query}' on {source}")
    return matches[0]


def _first_line(text: str) -> str:
    lines = [ln for ln in str(text).strip().splitlines() if ln.strip()]
    return lines[0] if lines else "unknown error"


def _load(project_dir: str) -> tuple[Path, Path, dict[str, Any]]:
    project = Path(project_dir)
    return project, project.parent, load_spec(project)


def _load_batch(project: Path, spec: dict[str, Any]) -> dict[str, Any]:
    """Fresh batch from the spec (so clip ids follow the cuts).

    Per-video run results (audio, captions, costs, render hash) are kept only while the narration and voice
    settings still match the audio that was made; otherwise they are stale and dropped.
    """
    batch = derive_batch(spec)
    try:
        existing = {v["id"]: v for v in read_batch(project)["videos"]}
    except FileNotFoundError:
        existing = {}
    for video, source in zip(batch["videos"], spec["videos"]):
        old = existing.get(video["id"], {})
        if old.get("voice_hash") != voice_fingerprint(spec, source):
            continue
        for key in _PRESERVED:
            if key in old:
                video[key] = old[key]
    return batch


def _check_current(batch: dict[str, Any], spec: dict[str, Any], ids: list[str] | None = None) -> None:
    """Refuse to record or export videos whose voice or render no longer matches the spec."""
    by_id = {v["id"]: v for v in batch["videos"]}
    for video in spec["videos"]:
        if ids is not None and video["id"] not in ids:
            continue
        entry = by_id[video["id"]]
        if not entry.get("audio_duration"):
            raise ValueError(f"{video['id']}: no narration yet (or it changed); run `voice` first")
        if entry.get("render_hash") != render_fingerprint(spec, video):
            raise ValueError(f"{video['id']}: render is out of date; run `render` again")


def _selected(spec: dict[str, Any], only: str | None) -> list[dict[str, Any]]:
    """All videos, or the comma-separated ids given to --video (in spec order)."""
    if only is None:
        return list(spec["videos"])
    wanted = [w.strip() for w in only.split(",") if w.strip()]
    known = {v["id"] for v in spec["videos"]}
    for vid in wanted:
        if vid not in known:
            raise ValueError(f"no video with id '{vid}' in the spec")
    if not wanted:
        raise ValueError("no video ids given")
    return [v for v in spec["videos"] if v["id"] in wanted]


# ---- subcommands -------------------------------------------------------------


def cmd_validate(args: argparse.Namespace) -> int:
    project, _, spec = _load(args.project_dir)
    violations = validate_spec(spec, project, stage=args.stage)
    for violation in violations:
        print(violation)
    if not violations:
        print(f"spec OK ({args.stage}): {len(spec['videos'])} video(s)")
    return 1 if violations else 0


def cmd_fetch(args: argparse.Namespace) -> int:
    project, _, spec = _load(args.project_dir)
    candidate = _resolve_candidate(args.source, args.query, args.clip_id)
    suffix = Path(candidate.download_url.split("?")[0]).suffix or ".mp4"
    dest = project / "assets" / "video" / f"{candidate.clip_id}{suffix}"
    fetch_clip(candidate, dest, max_mb=args.max_mb, allow_unknown_size=args.allow_unknown_size)
    previous = spec.setdefault("sources", {}).get(args.key, {})
    spec["sources"][args.key] = {
        "path": dest.relative_to(project).as_posix(), "clip_id": candidate.clip_id,
        "provider": args.source, "mode": previous.get("mode", "fill"),
        "license": candidate.license, "url": candidate.source_url,
    }
    save_spec(project, spec)
    print(f"fetched {candidate.clip_id} -> {dest}")
    return 0


def _search_candidates(source: str, query: str) -> list[Any]:
    from tools.video.stock_sources import all_sources
    from tools.video.stock_sources.base import SearchFilters

    adapters = {s.name: s for s in all_sources()}
    if source not in adapters:
        raise ValueError(f"unknown source '{source}' (choose from {', '.join(sorted(adapters))})")
    return list(adapters[source].search(query, SearchFilters(kind="video", per_page=12)))


def cmd_search(args: argparse.Namespace) -> int:
    for cand in _search_candidates(args.source, args.query):
        size = _head_size(cand.download_url)
        size_text = f"{size / (1024 * 1024):.1f}" if size else "?"
        tags = " ".join(str(getattr(cand, "source_tags", "") or "").split())[:240]
        seconds = getattr(cand, "duration", 0) or 0
        print(f"{cand.clip_id}	{tags}	{size_text}	{round(seconds) if seconds else '?'}")
    return 0


def cmd_sheet(args: argparse.Namespace) -> int:
    project, _, spec = _load(args.project_dir)
    source = (spec.get("sources") or {}).get(args.key)
    if not isinstance(source, dict) or not source.get("path"):
        raise ValueError(f"no source named '{args.key}' in the spec")
    video = project / source["path"]
    info = probe_video(video)
    out = project / "review" / f"sheet_{args.key}.png"
    sheet = make_contact_sheet(video, out, step=args.step)
    print(json.dumps({"key": args.key, **sheet, **info}))
    return 0


def cmd_voice(args: argparse.Namespace) -> int:
    project, _, spec = _load(args.project_dir)
    violations = validate_spec(spec, project, stage="script")
    if violations:
        for violation in violations:
            print(violation)
        return 1
    registry = _registry()
    voice = spec.get("voice") or {}
    engine = voice.get("engine", "piper")
    tts, subs = registry._tools["piper_tts"], registry._tools["subtitle_gen"]
    if engine == "piper" and tts.get_status().value != "available":
        raise RuntimeError("piper_tts is unavailable; install piper-tts and a voice (pip install piper-tts)")
    if engine == "kokoro" and not kokoro_available():
        raise RuntimeError("Kokoro is not set up: pip install kokoro-onnx soundfile and download the two model files "
                           "into models/kokoro (see studio/README.md)")
    batch = _load_batch(project, spec)
    audio_dir = project / "assets" / "audio"
    for video in _selected(spec, args.video):
        wav = audio_dir / f"{video['id']}.wav"
        if engine == "kokoro":
            synthesize_kokoro(voice, video["narration"], wav)
        else:
            result = tts.execute({"text": video["narration"], "output_path": str(wav),
                                  "model": voice.get("model", "en_US-lessac-medium"),
                                  "length_scale": voice.get("length_scale", 0.95)})
            if not result.success:
                raise RuntimeError(f"{video['id']}: piper failed: {_first_line(result.error or '')}")
        aligner = make_aligner()
        timings = aligner.align(video["narration"], wav)
        timing_method = getattr(aligner, "method", "estimate")
        if getattr(aligner, "error", None):
            print(f"note: {video['id']}: the speech model was not used ({aligner.error}); captions use the pause-based estimate")
        _, duration = detect_speech_spans(wav)
        captions = audio_dir / f"{video['id']}.captions.json"
        captions.write_text(json.dumps(to_remotion_captions(timings)), encoding="utf-8")
        segments, cursor = [], 0
        for sentence in split_sentences(video["narration"]):
            chunk = timings[cursor:cursor + len(sentence.split())]
            cursor += len(chunk)
            if chunk:
                segments.append({"text": sentence, "start": chunk[0].start_ms / 1000, "end": chunk[-1].end_ms / 1000,
                                 "words": [{"word": t.word, "start": t.start_ms / 1000, "end": t.end_ms / 1000}
                                           for t in chunk]})
        for fmt, name in (("json", f"{video['id']}.caption.json"), ("srt", f"{video['id']}.srt")):
            out = subs.execute({"segments": segments, "format": fmt, "max_words_per_cue": 4,
                                "highlight_style": "word_by_word" if fmt == "json" else "none",
                                "output_path": str(audio_dir / name)})
            if not out.success:
                raise RuntimeError(f"{video['id']}: subtitle_gen failed: {_first_line(out.error or '')}")
        entry = next(v for v in batch["videos"] if v["id"] == video["id"])
        entry["audio"] = f"assets/audio/{video['id']}.wav"
        entry["captions"] = f"assets/audio/{video['id']}.captions.json"
        entry["audio_duration"] = round(duration, 2)
        entry["timing"] = timing_method
        entry["voice_hash"] = voice_fingerprint(spec, video)
        entry.pop("render_hash", None)  # the old render used the old audio
        entry["cost_entries"] = [e for e in entry.get("cost_entries", []) if e.get("tool") not in ("piper_tts", "kokoro_onnx")]
        if engine == "kokoro":
            record_cost(batch, video["id"], "kokoro_onnx", "kokoro", 0.0)
        else:
            record_cost(batch, video["id"], "piper_tts", "piper", 0.0)
        record_cost(batch, video["id"], "subtitle_gen", "openmontage", 0.0)
        write_batch(project, batch)
        print(f"voiced {video['id']}: {duration:.1f}s, {len(timings)} words (timing: {timing_method})")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    project, _, spec = _load(args.project_dir)
    violations = validate_spec(spec, project)
    if violations:
        for violation in violations:
            print(violation)
        return 1
    batch = _load_batch(project, spec)
    by_id = {v["id"]: v for v in batch["videos"]}
    videos = _selected(spec, args.video)
    for video in videos:  # check everything before cutting or rendering anything
        entry = by_id[video["id"]]
        if not entry.get("audio_duration"):
            raise RuntimeError(f"{video['id']}: no narration yet; run `voice` first")
        try:
            check_cut_coverage(sum(float(c["dur"]) for c in video["cuts"]), float(entry["audio_duration"]))
        except ValueError as exc:
            raise ValueError(f"{video['id']}: {exc}") from exc
    registry = None
    for video in videos:
        entry = by_id[video["id"]]
        segments, durations = [], []
        for index, cut in enumerate(video["cuts"], start=1):
            source = spec["sources"][cut["source"]]
            segment = project / "assets" / "video" / "segments" / f"{video['id']}_c{index}.mp4"
            cut_segment(project / source["path"], float(cut["in"]), float(cut["dur"]), segment,
                        source.get("mode", "fill"))
            segments.append(segment)
            durations.append(float(cut["dur"]))
        captions = json.loads((project / entry["captions"]).read_text(encoding="utf-8"))
        platform = get_platform(spec.get("platform"))
        style = {"bottomOffset": platform.caption_bottom_offset, **(spec.get("caption_style") or {})}
        props = build_props(segments, durations, captions, project / entry["audio"], caption_style=style)
        registry = registry or _registry()
        render_video(registry, props, project / entry["render"])
        record_cost(batch, video["id"], "video_compose", "remotion", 0.0)
        entry["render_hash"] = render_fingerprint(spec, video)
        write_batch(project, batch)
        print(f"rendered {video['id']} -> {project / entry['render']}")
    return 0


def cmd_checkpoints(args: argparse.Namespace) -> int:
    project, projects_dir, spec = _load(args.project_dir)
    approve = frozenset(args.approve or [])
    if args.idea_only:
        if "review_queue" in approve:
            raise ValueError("--approve review_queue belongs to the full checkpoints run, not --idea-only")
        violations = validate_spec(spec, project, stage="idea")
        if violations:
            for violation in violations:
                print(violation)
            return 1
        write_idea_record(projects_dir, spec, approve="idea" in approve)
        print("idea record written (" + ("approved" if "idea" in approve else "awaiting approval") + ")")
        return 0
    if "idea" in approve:
        raise ValueError("approve the idea gate with --idea-only --approve idea")
    batch = _load_batch(project, spec)
    _check_current(batch, spec)
    write_batch(project, batch)  # the audit reads this file, so it must match what is recorded
    audit = audit_project(project)
    if not audit["ok"]:
        for violation in audit["violations"]:
            print(violation)
        return 1
    written = write_stage_records(projects_dir, spec, batch, approve=approve)
    print("stage records written: " + ", ".join(written))
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    project, projects_dir, spec = _load(args.project_dir)
    audit = audit_project(project)
    if not audit["ok"]:
        for violation in audit["violations"]:
            print(violation)
        return 1
    ids = [i.strip() for i in args.approved.split(",") if i.strip()]
    _check_current(_load_batch(project, spec), spec, ids)
    entries = export_videos(projects_dir, spec, ids, _registry())
    print(f"exported {len(entries)} video(s) as private; nothing was uploaded")
    return 0


# ---- entry point -------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="shorts_batch", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, func: Any, help_text: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("project_dir")
        p.set_defaults(func=func)
        return p

    p = add("validate", cmd_validate, "check the batch spec")
    p.add_argument("--stage", choices=STAGES, default="full",
                   help="check only the fields needed up to this stage (default: everything)")
    p = add("fetch", cmd_fetch, "download one named clip (size-capped)")
    p.add_argument("--source", required=True)
    p.add_argument("--query", required=True)
    p.add_argument("--clip-id", required=True)
    p.add_argument("--key", required=True)
    p.add_argument("--max-mb", type=float, default=DEFAULT_MAX_MB)
    p.add_argument("--allow-unknown-size", action="store_true")
    p = add("search", cmd_search, "list clip candidates for a query (id, tags, size in MB)")
    p.add_argument("--source", required=True)
    p.add_argument("--query", required=True)
    p = add("sheet", cmd_sheet, "make a contact sheet for one source key")
    p.add_argument("--key", required=True)
    p.add_argument("--step", type=float, default=4.0)
    p = add("voice", cmd_voice, "narrate and time captions")
    p.add_argument("--video")
    p = add("render", cmd_render, "cut segments and render the Shorts")
    p.add_argument("--video")
    p = add("checkpoints", cmd_checkpoints, "write stage records")
    p.add_argument("--idea-only", action="store_true")
    p.add_argument("--approve", action="append", choices=["idea", "review_queue"])
    p = add("export", cmd_export, "export approved videos")
    p.add_argument("--approved", required=True, help="comma-separated ids in posting order")
    return parser


def _utf8_output() -> None:
    """Windows pipes stdout as cp1252; a non-ASCII topic must not turn a message into a crash."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    _utf8_output()
    args = _parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyError as exc:  # a spec field the validator does not cover yet
        print(f"error: spec is missing {exc}")
        return 1
    except (OSError, ValueError, RuntimeError) as exc:  # includes network errors and SizeCapError
        print(f"error: {_first_line(str(exc))}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
