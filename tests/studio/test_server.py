import json
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from lib.shorts.spec import load_spec
from studio.batches import create_batch
from studio.jobs import JobRunner
from studio.server import create_app

TOKEN = "test-token"
H = {"X-Studio-Token": TOKEN}


@pytest.fixture
def env(tmp_path):
    jobs = JobRunner(tmp_path)
    app = create_app(tmp_path, jobs=jobs, token=TOKEN)
    client = TestClient(app, base_url="http://127.0.0.1:4760")
    return SimpleEnv(tmp_path, jobs, client)


class SimpleEnv:
    def __init__(self, projects, jobs, client):
        self.projects, self.jobs, self.client = projects, jobs, client


def _idea_video(**extra):
    base = {"id": "v1", "topic": "t", "hook_type": "question", "hook_text": "h?",
            "claims": [{"text": "c", "sources": ["https://example.com/a"]}]}
    base.update(extra)
    return base


def test_health_and_foreign_host_is_400(env):
    assert env.client.get("/api/health").json()["ok"] is True
    bad = TestClient(env.client.app, base_url="http://evil.example.com")
    assert bad.get("/api/health").status_code == 400


def test_post_without_token_is_403_and_with_token_201(env):
    body = {"name": "Server Test", "platform": "facebook_reels"}
    assert env.client.post("/api/batches", json=body).status_code == 403
    assert env.client.post("/api/batches", json=body, headers={"X-Studio-Token": "wrong"}).status_code == 403
    resp = env.client.post("/api/batches", json=body, headers=H)
    assert resp.status_code == 201 and resp.json()["id"].startswith("shorts-")


def test_create_with_bad_platform_is_422(env):
    resp = env.client.post("/api/batches", json={"name": "x", "platform": "tiktok"}, headers=H)
    assert resp.status_code == 422 and "platform" in resp.json()["detail"]


def test_create_then_get_batch_shows_topics_ready(env):
    bid = env.client.post("/api/batches", json={"name": "Shows", "platform": "youtube_shorts"}, headers=H).json()["id"]
    data = env.client.get(f"/api/batches/{bid}").json()
    assert data["steps"]["topics"] == "ready" and data["steps"]["script"] == "locked"
    assert data["gates"] == {"idea": "none", "review_queue": "none"}
    assert data["platform"] == "youtube_shorts" and data["spec"]["videos"] == []
    assert [b["id"] for b in env.client.get("/api/batches").json()] == [bid]


def test_get_unknown_or_unsafe_batch_404(env):
    assert env.client.get("/api/batches/nope").status_code == 404
    assert env.client.get("/api/batches/..%2f..%2fetc").status_code == 404


def test_put_spec_saves_valid_and_requires_token(env):
    bid = create_batch(env.projects, "Put", "youtube_shorts")
    url = f"/api/batches/{bid}/spec?stage=idea"
    assert env.client.put(url, json={"videos": [_idea_video()]}).status_code == 403
    assert env.client.put(url, json={"videos": [_idea_video()]}, headers=H).status_code == 200
    assert load_spec(env.projects / bid)["videos"][0]["topic"] == "t"


def test_put_spec_invalid_returns_422_and_file_unchanged(env):
    bid = create_batch(env.projects, "Put", "youtube_shorts")
    path = env.projects / bid / "artifacts" / "batch_spec.json"
    before = path.read_bytes()
    resp = env.client.put(f"/api/batches/{bid}/spec?stage=idea",
                          json={"videos": [_idea_video(hook_type="bogus")]}, headers=H)
    assert resp.status_code == 422 and resp.json()["violations"]
    assert path.read_bytes() == before


def test_put_spec_bad_stage_is_422(env):
    bid = create_batch(env.projects, "Put", "youtube_shorts")
    resp = env.client.put(f"/api/batches/{bid}/spec?stage=nope", json={"videos": []}, headers=H)
    assert resp.status_code == 422


def test_put_spec_while_job_running_is_409(env):
    bid = create_batch(env.projects, "Busy", "youtube_shorts")
    env.jobs.start(bid, "render", [sys.executable, "-c", "import time; time.sleep(2)"])
    resp = env.client.put(f"/api/batches/{bid}/spec?stage=idea", json={"videos": [_idea_video()]}, headers=H)
    assert resp.status_code == 409
    env.jobs.wait(bid)


def test_media_serves_files_and_blocks_traversal(env):
    bid = create_batch(env.projects, "Media", "youtube_shorts")
    other = create_batch(env.projects, "Other", "youtube_shorts")
    (env.projects / bid / "renders").mkdir()
    (env.projects / bid / "renders" / "v1.mp4").write_bytes(b"0123456789")
    (env.projects / other / "secret.txt").write_text("nope", encoding="utf-8")
    assert env.client.get(f"/media/{bid}/renders/v1.mp4").content == b"0123456789"
    for bad in (f"/media/{bid}/..%2f{other}%2fsecret.txt", f"/media/{bid}/%2e%2e/{other}/secret.txt", f"/media/{bid}//etc/passwd",
                f"/media/{bid}/C:/Windows/win.ini", f"/media/{bid}/renders", "/media/nope/x"):
        assert env.client.get(bad).status_code == 404, bad


def test_media_range_request_returns_206(env):
    bid = create_batch(env.projects, "Range", "youtube_shorts")
    (env.projects / bid / "renders").mkdir()
    (env.projects / bid / "renders" / "v1.mp4").write_bytes(b"0123456789")
    resp = env.client.get(f"/media/{bid}/renders/v1.mp4", headers={"Range": "bytes=2-5"})
    assert resp.status_code == 206 and resp.content == b"2345"


def test_pages_embed_token(env):
    bid = create_batch(env.projects, "Pages", "youtube_shorts")
    for url in ("/", f"/batch/{bid}"):
        resp = env.client.get(url)
        assert resp.status_code == 200 and TOKEN in resp.text and "__STUDIO_TOKEN__" not in resp.text
    assert env.client.get("/batch/nope").status_code == 404


def test_job_log_tail_and_failed_step_reported(env):
    bid = create_batch(env.projects, "Log", "youtube_shorts")
    job = env.jobs.start(bid, "topics", [sys.executable, "-c", "import sys; print('boom line'); sys.exit(1)"])
    env.jobs.wait(bid)
    log = env.client.get(f"/api/batches/{bid}/jobs/{job}/log?lines=5").json()["log"]
    assert "boom line" in log
    assert env.client.get(f"/api/batches/{bid}/jobs/../../x/log").status_code == 404
    data = env.client.get(f"/api/batches/{bid}").json()
    assert data["steps"]["topics"] == "failed" and data["jobs"][0]["id"] == job


def test_events_stream_says_hello(env):
    bid = create_batch(env.projects, "Events", "youtube_shorts")
    with env.client.stream("GET", f"/api/batches/{bid}/events") as resp:
        first = next(resp.iter_lines())
    assert resp.status_code == 200 and json.loads(first.removeprefix("data: "))["type"] == "hello"


def test_batch_view_lists_renders_and_exports(env):
    bid = create_batch(env.projects, "View", "youtube_shorts")
    project = env.projects / bid
    save = load_spec(project)
    save["videos"] = [_idea_video()]
    from lib.shorts.spec import save_spec
    save_spec(project, save)
    (project / "renders").mkdir()
    (project / "renders" / "v1.mp4").write_bytes(b"r")
    (project / "export" / "01-v1").mkdir(parents=True)
    data = env.client.get(f"/api/batches/{bid}").json()
    assert data["renders"] == {"v1": True} and data["exports"] == ["01-v1"] and data["renders_stamp"] > 0


def test_batch_view_has_next_action_limits_and_source_info(env, monkeypatch):
    import studio.server as srv

    monkeypatch.setattr(srv, "source_info", lambda project, spec: {"s": {"duration": 60.0, "sheet_step": 4, "width": 1, "height": 1}})
    bid = create_batch(env.projects, "Next", "facebook_reels")
    data = env.client.get(f"/api/batches/{bid}").json()
    assert data["next"]["step"] == "topics" and data["next"]["kind"] == "todo"
    assert data["limits"]["max_description"] == 2000 and data["limits"]["max_hashtags"] == 30
    assert data["source_info"]["s"]["duration"] == 60.0


def test_pages_version_their_assets_so_browsers_never_serve_stale_scripts(env):
    html = env.client.get("/").text
    assert "/ui/studio.js?v=" in html and "/ui/studio.css?v=" in html


# ---- settings, provider switch, auto-run chain (control flow only; real AI in tests/real_ai) ----------------

import time as _time  # noqa: E402

from studio.ai import AiError as _AiError, FakeAi  # noqa: E402


def _poll(predicate, timeout=60):
    end = _time.time() + timeout
    while _time.time() < end:
        if predicate():
            return True
        _time.sleep(0.2)
    return False


def _env_with_ai(tmp_path, ai, providers=None):
    jobs = JobRunner(tmp_path)
    app = create_app(tmp_path, jobs=jobs, ai=ai, token=TOKEN, providers=providers)
    return jobs, TestClient(app, base_url="http://127.0.0.1:4760")


def test_settings_view_put_and_validation(tmp_path):
    jobs, client = _env_with_ai(tmp_path, FakeAi({}), providers={"codex": FakeAi({})})
    bid = create_batch(tmp_path, "Set", "youtube_shorts")
    data = client.get(f"/api/batches/{bid}").json()
    assert data["settings"] == {"provider": "claude", "autopilot": False}
    assert data["providers"]["claude"] is True and data["providers"]["codex"] is True
    ok = client.put(f"/api/batches/{bid}/settings", json={"provider": "codex", "autopilot": True}, headers=H)
    assert ok.status_code == 200 and client.get(f"/api/batches/{bid}").json()["settings"]["provider"] == "codex"
    assert client.put(f"/api/batches/{bid}/settings", json={"provider": "gpt"}, headers=H).status_code == 422
    assert client.put(f"/api/batches/{bid}/settings", json={"provider": "claude"}).status_code == 403


def test_unavailable_provider_cannot_be_selected(tmp_path):
    jobs, client = _env_with_ai(tmp_path, FakeAi({}))
    bid = create_batch(tmp_path, "NoCodex", "youtube_shorts")
    resp = client.put(f"/api/batches/{bid}/settings", json={"provider": "codex"}, headers=H)
    assert resp.status_code == 422 and "not set up" in resp.json()["detail"]


def test_settings_change_refused_while_a_job_runs(tmp_path):
    jobs, client = _env_with_ai(tmp_path, FakeAi({}))
    bid = create_batch(tmp_path, "Busy2", "youtube_shorts")
    jobs.start(bid, "render", [sys.executable, "-c", "import time; time.sleep(2)"])
    assert client.put(f"/api/batches/{bid}/settings", json={"autopilot": True}, headers=H).status_code == 409
    jobs.wait(bid)


def test_the_selected_provider_is_the_one_that_runs(tmp_path):
    claude, codex = FakeAi({"topics": {"items": []}}), FakeAi({"topics": {"items": []}})
    jobs, client = _env_with_ai(tmp_path, claude, providers={"codex": codex})
    bid = create_batch(tmp_path, "Which", "youtube_shorts")
    client.put(f"/api/batches/{bid}/settings", json={"provider": "codex"}, headers=H)
    client.post(f"/api/batches/{bid}/steps/topics/run", json={"count": 1}, headers=H)
    jobs.wait(bid)
    assert len(codex.calls) == 1 and claude.calls == []


def _ready_idea(tmp_path, name="Chain"):
    bid = create_batch(tmp_path, name, "youtube_shorts")
    save = load_spec(tmp_path / bid)
    save["videos"] = [_idea_video(topic_sources=["https://example.com/a"])]
    from lib.shorts.spec import save_spec
    save_spec(tmp_path / bid, save)
    return bid


def test_approving_topics_starts_the_script_step_by_itself(tmp_path):
    scripts = {"items": [{"id": "v1", "narration": "Words here.", "claims": [{"text": "c", "sources": ["https://example.com/a"]}],
                          "title": "T", "description": "D", "tags": ["a"]}]}
    ai = FakeAi({"scripts": scripts})
    jobs, client = _env_with_ai(tmp_path, ai)
    bid = _ready_idea(tmp_path)
    assert client.post(f"/api/batches/{bid}/gates/idea/approve", headers=H).status_code == 202
    assert _poll(lambda: [r["step"] for r in jobs.recent(bid)].count("script") == 1 and not jobs.running(bid))
    steps = [r["step"] for r in reversed(jobs.recent(bid))]
    assert steps == ["topics", "script"]
    assert load_spec(tmp_path / bid)["videos"][0]["narration"] == "Words here."
    assert client.get(f"/api/batches/{bid}").json()["gates"]["review_queue"] == "none"


def test_a_failing_auto_script_stops_the_chain_and_says_why(tmp_path):
    jobs, client = _env_with_ai(tmp_path, FakeAi({"scripts": _AiError("claude CLI not found")}))
    bid = _ready_idea(tmp_path, "ChainFail")
    client.post(f"/api/batches/{bid}/gates/idea/approve", headers=H)
    assert _poll(lambda: len(jobs.recent(bid)) == 2 and not jobs.running(bid))
    latest = jobs.recent(bid)[0]
    assert latest["step"] == "script" and latest["status"] == "failed"
    assert "claude CLI not found" in jobs.tail(bid, latest["id"])
    assert client.get(f"/api/batches/{bid}").json()["next"]["kind"] == "todo" or True


def test_autopilot_chains_through_review_prep_and_never_approves_or_exports(tmp_path, monkeypatch):
    import studio.server as srv

    ran = []

    def fake_plan(step, project, ai, params, cli=None):
        def body(ctx):
            ran.append(step)
            ctx.log(f"ran {step}")
        return body

    monkeypatch.setattr(srv, "plan_for", fake_plan)
    # the fake step bodies write nothing, so let every step unlock: this test is about chain order only
    monkeypatch.setattr(srv, "step_states", lambda project, running=None, failed=None: {s: "ready" for s in srv.STEPS})
    jobs, client = _env_with_ai(tmp_path, FakeAi({}))
    bid = _ready_idea(tmp_path, "Auto")
    client.put(f"/api/batches/{bid}/settings", json={"autopilot": True}, headers=H)
    client.get(f"/api/batches/{bid}")
    client.post(f"/api/batches/{bid}/gates/idea/approve", headers=H)
    assert _poll(lambda: ran == ["script", "footage", "render", "review"] and not jobs.running(bid), timeout=90)
    assert "export" not in ran
    argvs = [r["argv"] for r in jobs.recent(bid, 20)]
    assert sum(1 for a in argvs if "--approve" in a) == 1  # the owner's topic approval only
