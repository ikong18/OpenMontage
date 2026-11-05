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


def _footage_ai(cuts_per_video=3.4, n=3):
    def cuts(i, vid):
        return {"id": vid, "cuts": [{"source": "KEY", "in": i * 40 + k * 4, "dur": cuts_per_video} for k in range(n)]}
    return cuts


def _run_footage(batch, candidates, refuse_ids=(), cuts_dur=3.4, per_video=4):
    ai_state = {}

    class Ai(FakeAi):
        def run(self, task, inputs, schema, tools, add_dirs=None):
            if task == "footage_cuts":
                key = inputs["sources"][0]["key"]
                items = [{"id": v["id"], "cuts": [{"source": key, "in": i * 40 + k * 4, "dur": cuts_dur}
                                                  for k in range(per_video)]}
                         for i, v in enumerate(inputs["videos"])]
                self.responses["footage_cuts"] = {"items": items}
            return super().run(task, inputs, schema, tools, add_dirs)

    ai = Ai({"footage_queries": {"items": [{"id": "v1", "queries": [{"source": "nasa", "query": "sun"}]},
                                           {"id": "v2", "queries": [{"source": "nasa", "query": "sun"}]}]},
             "footage_cuts": {"items": []}})
    cli = FakeCli(batch, _footage_handler(candidates, refuse_ids))
    plan_for("footage", batch, ai, {}, cli)(_ctx(batch))
    return ai, cli


def test_footage_skips_oversize_candidate_and_uses_next(batch):
    _ready_for_footage(batch)
    ai, cli = _run_footage(batch, [("nasa_big", "900.0"), ("nasa_unknown", "?"), ("nasa_ok", "50.0")],
                           refuse_ids=("nasa_unknown",))
    fetched = [a[a.index("--clip-id") + 1] for a in cli.calls if a[0] == "fetch"]
    assert "nasa_big" not in fetched and fetched[:2] == ["nasa_unknown", "nasa_ok"]
    spec = load_spec(batch)
    assert all(v["cuts"] for v in spec["videos"])
    assert spec["sources"][next(iter(spec["sources"]))]["mode"] == "blurpad"  # 720x720 probe


def test_footage_all_oversize_fails_with_reason(batch):
    _ready_for_footage(batch)
    with pytest.raises(RuntimeError, match="size cap"):
        _run_footage(batch, [("nasa_big", "900.0"), ("nasa_huge", "5000.0")])


def test_footage_runs_voice_first_and_asks_cuts_for_audio_length(batch):
    _ready_for_footage(batch)
    ai, cli = _run_footage(batch, [("nasa_ok", "50.0")])
    assert cli.calls[0][0] == "voice"
    cut_call = next(c for c in ai.calls if c["task"] == "footage_cuts")
    assert all(v["needed_seconds"] >= 10.0 for v in cut_call["inputs"]["videos"])
    assert cut_call["add_dirs"] == [batch / "review"]
    assert cut_call["tools"] == ["Read"]


def test_footage_rejects_cuts_shorter_than_narration(batch):
    _ready_for_footage(batch)
    before = (batch / "artifacts" / "batch_spec.json").read_bytes()
    with pytest.raises(ValueError, match="need"):
        _run_footage(batch, [("nasa_ok", "50.0")], cuts_dur=2.5, per_video=2)
    spec = load_spec(batch)
    assert not any(v.get("cuts") for v in spec["videos"])
    assert before  # spec still loads and has no cuts applied


# ---- render, review, export -------------------------------------------------


def test_render_runs_voice_then_render(batch):
    cli = FakeCli(batch)
    plan_for("render", batch, FakeAi({}), {}, cli)(_ctx(batch))
    assert [c[0] for c in cli.calls] == ["voice", "render"]


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
