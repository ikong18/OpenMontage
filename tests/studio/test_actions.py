import json
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from lib.shorts.batch import read_batch, write_batch
from lib.shorts.spec import load_spec, save_spec
from studio.actions import approval_argv, plan_for, redo_video
from studio.ai import AiError, FakeAi
from studio.batches import create_batch
from studio.jobs import JobContext, JobRunner
from studio.server import create_app

SRC = "https://science.nasa.gov/sun/facts/"
TOKEN = "t"
H = {"X-Studio-Token": TOKEN}


class FakeCli:
    """Records every command; `handler(args, project)` may return (code, output) or None for (0, "")."""

    def __init__(self, project: Path, handler=None):
        self.project, self.handler, self.calls = project, handler, []

    def run(self, ctx, args, capture=False):
        self.calls.append(list(args))
        result = self.handler(args, self.project) if self.handler else None
        return result if result is not None else (0, "")


def _ctx(tmp_path):
    return JobContext(tmp_path / "job.log")


def _topics(n=2):
    return {"items": [{"topic": f"Topic {i}", "hook_type": ["question", "shocking_stat"][i % 2],
                       "hook_text": f"Hook {i}?", "sources": [SRC]} for i in range(n)]}


def _scripts(ids):
    return {"items": [{"id": i, "narration": "Narration. " * 5, "claims": [{"text": "c", "sources": [SRC]}],
                       "title": "T", "description": f"D. Source: {SRC}", "tags": ["a"]} for i in ids]}


@pytest.fixture
def batch(tmp_path):
    bid = create_batch(tmp_path, "Act", "facebook_reels")
    return tmp_path / bid


def _with_topics(batch, n=2):
    FakeAiRun = FakeAi({"topics": _topics(n)})
    plan_for("topics", batch, FakeAiRun, {"count": n, "theme": "space"}, FakeCli(batch))(_ctx(batch))


# ---- topics and script ------------------------------------------------------


def test_topics_suggest_applies_items_and_passes_theme(batch):
    ai = FakeAi({"topics": _topics(3)})
    plan_for("topics", batch, ai, {"count": 3, "theme": "space"}, FakeCli(batch))(_ctx(batch))
    spec = load_spec(batch)
    assert [v["id"] for v in spec["videos"]] == ["v1", "v2", "v3"]
    assert ai.calls[0]["inputs"]["theme"] == "space" and ai.calls[0]["inputs"]["count"] == 3
    assert ai.calls[0]["tools"] == ["WebFetch", "WebSearch"]


def test_ai_returns_wrong_count_fails_with_message_and_spec_unchanged(batch):
    path = batch / "artifacts" / "batch_spec.json"
    before = path.read_bytes()
    with pytest.raises(ValueError, match="expected 5"):
        plan_for("topics", batch, FakeAi({"topics": _topics(2)}), {"count": 5}, FakeCli(batch))(_ctx(batch))
    assert path.read_bytes() == before


def test_ai_error_propagates_and_spec_unchanged(batch):
    before = (batch / "artifacts" / "batch_spec.json").read_bytes()
    with pytest.raises(AiError, match="not found"):
        plan_for("topics", batch, FakeAi({"topics": AiError("claude CLI not found")}), {"count": 2},
                 FakeCli(batch))(_ctx(batch))
    assert (batch / "artifacts" / "batch_spec.json").read_bytes() == before


def test_script_applies_and_leaves_spec_unchanged_on_ai_failure(batch):
    _with_topics(batch)
    plan_for("script", batch, FakeAi({"scripts": _scripts(["v1", "v2"])}), {}, FakeCli(batch))(_ctx(batch))
    assert load_spec(batch)["videos"][1]["narration"].startswith("Narration.")
    before = (batch / "artifacts" / "batch_spec.json").read_bytes()
    with pytest.raises(AiError):
        plan_for("script", batch, FakeAi({"scripts": AiError("boom")}), {}, FakeCli(batch))(_ctx(batch))
    assert (batch / "artifacts" / "batch_spec.json").read_bytes() == before


# ---- footage ----------------------------------------------------------------


def _footage_handler(candidates, refuse_ids=()):
    def handler(args, project):
        cmd = args[0]
        if cmd == "voice":
            write_batch(project, {"videos": [{"id": v["id"], "audio_duration": 10.0}
                                             for v in load_spec(project)["videos"]]})
        elif cmd == "search":
            return 0, "\n".join(f"{cid}\ttags\t{size}" for cid, size in candidates)
        elif cmd == "fetch":
            clip_id = args[args.index("--clip-id") + 1]
            key = args[args.index("--key") + 1]
            if clip_id in refuse_ids:
                return 1, "error: file is 900 MB, over the 250 MB cap; pick a smaller clip"
            spec = load_spec(project)
            (project / "assets" / "video").mkdir(parents=True, exist_ok=True)
            (project / "assets" / "video" / f"{key}.mp4").write_bytes(b"x")
            spec["sources"][key] = {"path": f"assets/video/{key}.mp4", "clip_id": clip_id, "provider": "nasa",
                                    "mode": "fill", "license": "pd", "url": "https://x"}
            save_spec(project, spec)
            return 0, f"fetched {clip_id}"
        elif cmd == "sheet":
            key = args[args.index("--key") + 1]
            (project / "review").mkdir(exist_ok=True)
            (project / "review" / f"sheet_{key}.png").write_bytes(b"png")
            return 0, json.dumps({"key": key, "path": str(project / "review" / f"sheet_{key}.png"), "step": 4.0,
                                  "cols": 6, "frames": 20, "width": 720, "height": 720, "duration": 80.0})
        return None
    return handler


def _ready_for_footage(batch):
    _with_topics(batch)
    plan_for("script", batch, FakeAi({"scripts": _scripts(["v1", "v2"])}), {}, FakeCli(batch))(_ctx(batch))


# ---- render, review, export -------------------------------------------------


def test_render_runs_voice_then_render_for_each_stale_video(batch):
    _ready_for_footage(batch)
    cli = FakeCli(batch)
    plan_for("render", batch, FakeAi({}), {}, cli)(_ctx(batch))
    assert cli.calls == [["voice", str(batch), "--video", "v1,v2"], ["render", str(batch), "--video", "v1,v2"]]


def test_review_prepare_runs_checkpoints_without_approve(batch):
    cli = FakeCli(batch)
    plan_for("review", batch, FakeAi({}), {}, cli)(_ctx(batch))
    assert cli.calls == [["checkpoints", str(batch)]]


def test_export_uses_ids_in_order_and_skips_redo_flagged(batch):
    _ready_for_footage(batch)
    spec = load_spec(batch)
    spec["videos"][0]["redo_note"] = "weak footage"
    save_spec(batch, spec)
    cli = FakeCli(batch)
    plan_for("export", batch, FakeAi({}), {}, cli)(_ctx(batch))
    assert cli.calls == [["export", str(batch), "--approved", "v2"]]


def test_no_ai_plan_ever_passes_approve_flag(batch):
    _ready_for_footage(batch)
    seen = []
    for step in ("topics", "script", "footage", "render", "review", "export"):
        cli = FakeCli(batch, _footage_handler([("nasa_ok", "50.0")]))
        ai = FakeAi({"topics": _topics(2), "scripts": _scripts(["v1", "v2"]),
                     "footage_queries": {"items": [{"id": "v1", "queries": [{"source": "nasa", "query": "q"}]},
                                                   {"id": "v2", "queries": [{"source": "nasa", "query": "q"}]}]},
                     "footage_cuts": {"items": []}})
        try:
            plan_for(step, batch, ai, {"count": 2}, cli)(_ctx(batch))
        except Exception:  # noqa: BLE001 - only the commands issued matter here
            pass
        seen += cli.calls
    assert seen and not any("--approve" in call for call in seen)


# ---- gates and redo ---------------------------------------------------------


def test_approve_idea_refused_when_spec_invalid(batch):
    with pytest.raises(ValueError, match="no videos"):
        approval_argv(batch, "idea")


def test_approve_idea_argv_is_exactly_the_approve_command(batch):
    _with_topics(batch)
    argv = approval_argv(batch, "idea")
    assert argv[-5:] == ["checkpoints", str(batch), "--idea-only", "--approve", "idea"]
    assert argv[0] == sys.executable


def test_approve_review_refused_until_renders_current_and_prepared(batch):
    _ready_for_footage(batch)
    with pytest.raises(ValueError, match="review"):
        approval_argv(batch, "review_queue")


def test_unknown_gate_rejected(batch):
    with pytest.raises(ValueError, match="gate"):
        approval_argv(batch, "publish")


def test_redo_clears_cuts_and_render_and_keeps_other_videos(batch):
    _ready_for_footage(batch)
    spec = load_spec(batch)
    spec["sources"]["s"] = {"path": "a.mp4", "clip_id": "c", "provider": "n", "license": "l", "url": "u", "mode": "fill"}
    for v in spec["videos"]:
        v["cuts"] = [{"source": "s", "in": 0 if v["id"] == "v1" else 10, "dur": 3}]
    save_spec(batch, spec)
    (batch / "renders").mkdir()
    (batch / "renders" / "v1.mp4").write_bytes(b"r")
    (batch / "renders" / "v2.mp4").write_bytes(b"r")
    redo_video(batch, "v1", "footage is boring")
    spec = load_spec(batch)
    assert spec["videos"][0]["cuts"] == [] and spec["videos"][0]["redo_note"] == "footage is boring"
    assert spec["videos"][1]["cuts"] and not (batch / "renders" / "v1.mp4").exists()
    assert (batch / "renders" / "v2.mp4").exists()
    with pytest.raises(ValueError, match="v9"):
        redo_video(batch, "v9", "x")


# ---- routes -----------------------------------------------------------------


@pytest.fixture
def api(tmp_path):
    jobs = JobRunner(tmp_path)
    ai = FakeAi({"topics": _topics(2)})
    app = create_app(tmp_path, jobs=jobs, ai=ai, token=TOKEN)
    return type("Api", (), {"projects": tmp_path, "jobs": jobs, "ai": ai,
                            "client": TestClient(app, base_url="http://127.0.0.1:4760")})


def test_run_locked_step_422(api):
    bid = create_batch(api.projects, "Locked", "youtube_shorts")
    resp = api.client.post(f"/api/batches/{bid}/steps/render/run", json={}, headers=H)
    assert resp.status_code == 422 and "locked" in resp.json()["detail"]


def test_run_step_requires_token_and_known_step(api):
    bid = create_batch(api.projects, "Tok", "youtube_shorts")
    assert api.client.post(f"/api/batches/{bid}/steps/topics/run", json={}).status_code == 403
    assert api.client.post(f"/api/batches/{bid}/steps/bogus/run", json={}, headers=H).status_code == 404


def test_topics_run_through_route_creates_videos(api):
    bid = create_batch(api.projects, "Route", "youtube_shorts")
    resp = api.client.post(f"/api/batches/{bid}/steps/topics/run", json={"count": 2, "theme": "space"}, headers=H)
    assert resp.status_code == 202
    api.jobs.wait(bid)
    data = api.client.get(f"/api/batches/{bid}").json()
    assert len(data["spec"]["videos"]) == 2 and data["jobs"][0]["status"] == "ok"


def test_concurrent_run_second_409(api):
    bid = create_batch(api.projects, "Conc", "youtube_shorts")
    api.jobs.start(bid, "render", [sys.executable, "-c", "import time; time.sleep(2)"])
    resp = api.client.post(f"/api/batches/{bid}/steps/topics/run", json={"count": 2}, headers=H)
    assert resp.status_code == 409
    api.jobs.wait(bid)


def test_approve_route_invalid_spec_422_and_never_runs(api):
    bid = create_batch(api.projects, "Gate", "youtube_shorts")
    resp = api.client.post(f"/api/batches/{bid}/gates/idea/approve", headers=H)
    assert resp.status_code == 422
    assert api.jobs.recent(bid) == []
    assert api.client.post(f"/api/batches/{bid}/gates/idea/approve").status_code == 403


def test_redo_route_unknown_video_404_and_busy_409(api):
    bid = create_batch(api.projects, "Redo", "youtube_shorts")
    assert api.client.post(f"/api/batches/{bid}/videos/v9/redo", json={"note": "x"}, headers=H).status_code == 404
    _with_topics(api.projects / bid)
    api.jobs.start(bid, "render", [sys.executable, "-c", "import time; time.sleep(2)"])
    assert api.client.post(f"/api/batches/{bid}/videos/v1/redo", json={"note": "x"}, headers=H).status_code == 409
    api.jobs.wait(bid)
    assert api.client.post(f"/api/batches/{bid}/videos/v1/redo", json={"note": "x"}, headers=H).status_code == 200


# ---- review fixes -------------------------------------------------------------


# ---- research through the topics step (control flow only; real AI is checked in tests/real_ai) --------


def test_topics_research_fills_typed_topics_and_leaves_the_rest(batch):
    spec = load_spec(batch)
    spec["videos"] = [{"id": "v1", "topic": "Venus is hotter than Mercury"}, {"id": "v2", "topic": "keep me"}]
    save_spec(batch, spec)
    ai = FakeAi({"topic_research": {"items": [{"id": "v1", "topic": "Venus is hotter than Mercury",
                                               "hook_type": "comparison", "hook_text": "Venus beats Mercury.",
                                               "sources": [SRC]}]}})
    plan_for("topics", batch, ai, {"research": ["v1"], "theme": "space"}, FakeCli(batch))(_ctx(batch))
    saved = load_spec(batch)["videos"]
    assert saved[0]["topic_sources"] == [SRC] and saved[1] == {"id": "v2", "topic": "keep me"}
    call = ai.calls[0]
    assert call["task"] == "topic_research" and call["tools"] == ["WebFetch", "WebSearch"]
    assert [v["id"] for v in call["inputs"]["videos"]] == ["v1"]


def test_topics_research_ai_failure_leaves_spec_unchanged(batch):
    spec = load_spec(batch)
    spec["videos"] = [{"id": "v1", "topic": "typed"}]
    save_spec(batch, spec)
    before = (batch / "artifacts" / "batch_spec.json").read_bytes()
    with pytest.raises(AiError):
        plan_for("topics", batch, FakeAi({"topic_research": AiError("offline")}), {"research": ["v1"]},
                 FakeCli(batch))(_ctx(batch))
    assert (batch / "artifacts" / "batch_spec.json").read_bytes() == before


def test_suggest_refuses_to_replace_existing_topics_unless_told(batch):
    spec = load_spec(batch)
    spec["videos"] = [{"id": "v1", "topic": "typed by the owner"}]
    save_spec(batch, spec)
    ai = FakeAi({"topics": _topics(2)})
    with pytest.raises(ValueError, match="already"):
        plan_for("topics", batch, ai, {"count": 2}, FakeCli(batch))(_ctx(batch))
    assert ai.calls == [] and load_spec(batch)["videos"][0]["topic"] == "typed by the owner"
    plan_for("topics", batch, ai, {"count": 2, "replace": True}, FakeCli(batch))(_ctx(batch))
    assert len(load_spec(batch)["videos"]) == 2


def test_research_sends_the_owners_source_links_to_the_ai(batch):
    spec = load_spec(batch)
    spec["videos"] = [{"id": "v1", "topic": "typed", "topic_sources": ["https://owner.example/page"]}]
    save_spec(batch, spec)
    ai = FakeAi({"topic_research": {"items": [{"id": "v1", "topic": "typed", "hook_type": "question",
                                               "hook_text": "Is it?", "sources": [SRC]}]}})
    plan_for("topics", batch, ai, {"research": ["v1"]}, FakeCli(batch))(_ctx(batch))
    assert ai.calls[0]["inputs"]["videos"][0]["topic_sources"] == ["https://owner.example/page"]
    assert load_spec(batch)["videos"][0]["topic_sources"][0] == "https://owner.example/page"


# ---- fact check step (control flow only; whether the AI judges correctly is tested in tests/real_ai) -------------


def test_factcheck_saves_the_record_and_asks_for_web_tools(batch):
    _with_topics(batch)
    plan_for("script", batch, FakeAi({"scripts": _scripts(["v1", "v2"])}), {}, FakeCli(batch))(_ctx(batch))
    checks = {"items": [{"id": i, "claims": [{"index": 0, "verdict": "supported", "note": "ok", "quote": "q"}],
                         "narration_issues": []} for i in ("v1", "v2")]}
    ai = FakeAi({"fact_check": checks})
    plan_for("script", batch, ai, {"factcheck": True, "writer": "claude", "checker": "codex"}, FakeCli(batch))(_ctx(batch))
    from studio.factcheck import load_check
    record = load_check(batch)
    assert record["writer"] == "claude" and record["checker"] == "codex" and set(record["videos"]) == {"v1", "v2"}
    assert ai.calls[0]["task"] == "fact_check" and ai.calls[0]["tools"] == ["WebFetch", "WebSearch"]


def test_factcheck_failure_leaves_the_scripts_untouched(batch):
    _with_topics(batch)
    plan_for("script", batch, FakeAi({"scripts": _scripts(["v1", "v2"])}), {}, FakeCli(batch))(_ctx(batch))
    before = (batch / "artifacts" / "batch_spec.json").read_bytes()
    with pytest.raises(AiError):
        plan_for("script", batch, FakeAi({"fact_check": AiError("offline")}), {"factcheck": True}, FakeCli(batch))(_ctx(batch))
    assert (batch / "artifacts" / "batch_spec.json").read_bytes() == before


# ---- a longer voice must not leave a video longer than its footage (real ffmpeg clip) --------------------------

import shutil as _shutil  # noqa: E402
import subprocess as _subprocess  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(_shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def _clip_project(batch, cuts, audio_seconds):
    video_dir = batch / "assets" / "video"
    video_dir.mkdir(parents=True, exist_ok=True)
    _subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=5:duration=30",
                     "-pix_fmt", "yuv420p", str(video_dir / "s.mp4")], check=True)
    spec = load_spec(batch)
    spec["sources"] = {"s": {"path": "assets/video/s.mp4", "clip_id": "clip1", "provider": "nasa", "license": "pd",
                             "url": "https://x", "mode": "fill"}}
    spec["videos"] = [{"id": "v1", "topic": "t", "hook_type": "question", "hook_text": "h?", "narration": "Words. More words.",
                       "claims": [{"text": "c", "sources": [SRC]}], "title": "T", "description": "D", "tags": ["a"],
                       "cuts": [{"source": "s", "in": a, "dur": b} for a, b in cuts], "footage_override": True}]
    save_spec(batch, spec)

    def handler(args, project):
        if args[0] == "voice":
            write_batch(project, {"videos": [{"id": "v1", "audio_duration": audio_seconds}]})
    return handler


@needs_ffmpeg
def test_render_extends_the_last_shot_when_the_new_voice_is_longer(batch):
    cli = FakeCli(batch, _clip_project(batch, [(0, 5), (10, 5)], 11.0))
    plan_for("render", batch, FakeAi({}), {}, cli)(_ctx(batch))
    cuts = load_spec(batch)["videos"][0]["cuts"]
    assert sum(c["dur"] for c in cuts) >= 11.0 and cuts[0] == {"source": "s", "in": 0, "dur": 5}
    assert cuts[1]["in"] == 10 and [c[0] for c in cli.calls] == ["voice", "render"]


@needs_ffmpeg
def test_render_stops_with_a_clear_message_when_the_footage_cannot_cover_the_voice(batch):
    cli = FakeCli(batch, _clip_project(batch, [(0, 5), (28, 2)], 12.0))   # the last shot already ends at the end of the clip
    with pytest.raises(ValueError, match="footage"):
        plan_for("render", batch, FakeAi({}), {}, cli)(_ctx(batch))
    assert [c[0] for c in cli.calls] == ["voice"], "must not render a video that ends early"


@needs_ffmpeg
def test_extension_never_overlaps_another_cut_of_the_same_clip(batch):
    cli = FakeCli(batch, _clip_project(batch, [(10, 5), (5, 5)], 11.0))   # extending 5..10 would run into 10..15
    with pytest.raises(ValueError, match="footage"):
        plan_for("render", batch, FakeAi({}), {}, cli)(_ctx(batch))


@needs_ffmpeg
def test_nothing_changes_when_the_footage_already_covers_the_voice(batch):
    cli = FakeCli(batch, _clip_project(batch, [(0, 5), (10, 5)], 9.9))
    before = (batch / "artifacts" / "batch_spec.json").read_bytes()
    plan_for("render", batch, FakeAi({}), {}, cli)(_ctx(batch))
    assert (batch / "artifacts" / "batch_spec.json").read_bytes() == before


def test_render_only_touches_stale_videos_and_nothing_when_all_current(batch):
    from lib.shorts.batch import render_fingerprint

    spec = load_spec(batch)
    spec["sources"] = {"s": {"path": "assets/video/s.webm", "clip_id": "clip", "provider": "wikimedia", "license": "x",
                             "url": "https://x", "mode": "fill"}}
    spec["videos"] = [{"id": vid, "topic": "t", "hook_type": "question", "hook_text": "h?", "narration": "Words here.",
                       "claims": [{"text": "c", "sources": [SRC]}], "title": "T", "description": "D", "tags": ["a"],
                       "cuts": [{"source": "s", "in": 10 * i, "dur": 4}], "footage_override": True}
                      for i, vid in enumerate(("v1", "v2"))]
    save_spec(batch, spec)
    (batch / "renders").mkdir(exist_ok=True)
    for vid in ("v1", "v2"):
        (batch / "renders" / f"{vid}.mp4").write_bytes(b"r")
    write_batch(batch, {"videos": [{"id": "v1", "render_hash": render_fingerprint(spec, spec["videos"][0])},
                                   {"id": "v2", "render_hash": "old"}]})
    cli = FakeCli(batch)
    plan_for("render", batch, FakeAi({}), {}, cli)(_ctx(batch))
    assert cli.calls == [["voice", str(batch), "--video", "v2"], ["render", str(batch), "--video", "v2"]]
    write_batch(batch, {"videos": [{"id": v["id"], "render_hash": render_fingerprint(spec, v)} for v in spec["videos"]]})
    cli = FakeCli(batch)
    plan_for("render", batch, FakeAi({}), {}, cli)(_ctx(batch))
    assert cli.calls == []
