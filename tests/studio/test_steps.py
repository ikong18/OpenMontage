import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from lib.shorts.batch import render_fingerprint, write_batch
from lib.shorts.spec import load_spec, save_spec
from studio.batches import create_batch
from studio.steps import STEPS, gate_status, step_states


def _full_video(vid="v1", **extra):
    base = {
        "id": vid, "topic": "t", "hook_type": "question", "hook_text": "h?",
        "claims": [{"text": "c", "sources": ["https://example.com/a"]}],
        "narration": "Some narration.", "cuts": [{"source": "s", "in": 0, "dur": 3}],
        "title": "T", "description": "D", "tags": ["x"],
    }
    base.update(extra)
    return base


def _batch(tmp_path, with_source=True):
    bid = create_batch(tmp_path, "Steps", "youtube_shorts")
    project = tmp_path / bid
    spec = load_spec(project)
    spec["sources"] = {"s": {"path": "assets/video/s.mp4", "clip_id": "c1", "provider": "nasa",
                             "license": "x", "url": "https://example.com", "mode": "fill"}}
    spec["videos"] = [_full_video()]
    save_spec(project, spec)
    if with_source:
        (project / "assets" / "video").mkdir(parents=True)
        (project / "assets" / "video" / "s.mp4").write_bytes(b"x")
    return project


def _gate(project, stage, approved=True, stamp=None):
    (project / f"checkpoint_{stage}.json").write_text(json.dumps({
        "stage": stage, "status": "completed" if approved else "awaiting_human",
        "human_approved": approved,
        "timestamp": stamp or datetime.now(timezone.utc).isoformat(),
    }), encoding="utf-8")


def _render(project, spec, hash_ok=True):
    (project / "renders").mkdir(exist_ok=True)
    (project / "renders" / "v1.mp4").write_bytes(b"r")
    video = spec["videos"][0]
    write_batch(project, {"videos": [{"id": "v1", "render_hash": render_fingerprint(spec, video) if hash_ok else "old"}]})


def test_new_batch_only_topics_ready(tmp_path):
    bid = create_batch(tmp_path, "New", "youtube_shorts")
    states = step_states(tmp_path / bid)
    assert states["topics"] == "ready"
    assert all(states[s] == "locked" for s in STEPS[1:])


def test_topics_done_after_idea_gate_approved(tmp_path):
    project = _batch(tmp_path)
    _gate(project, "idea")
    states = step_states(project)
    assert states["topics"] == "done"
    assert states["script"] == "done" and states["footage"] == "done"
    assert states["render"] == "ready"


def test_script_ready_not_done_when_narration_missing(tmp_path):
    project = _batch(tmp_path)
    spec = load_spec(project)
    spec["videos"][0].pop("narration")
    save_spec(project, spec)
    _gate(project, "idea")
    states = step_states(project)
    assert states["script"] == "ready" and states["footage"] == "locked"


def test_gate_status_values(tmp_path):
    project = _batch(tmp_path)
    assert gate_status(project, "idea") == "none"
    _gate(project, "idea", approved=False)
    assert gate_status(project, "idea") == "awaiting"
    _gate(project, "idea", approved=True)
    assert gate_status(project, "idea") == "approved"


def test_render_done_then_stale_when_cut_changes(tmp_path):
    project = _batch(tmp_path)
    _gate(project, "idea")
    spec = load_spec(project)
    _render(project, spec)
    assert step_states(project)["render"] == "done"
    spec["videos"][0]["cuts"][0]["in"] = 5
    save_spec(project, spec)
    states = step_states(project)
    assert states["render"] == "stale" and states["review"] == "locked"


def test_review_not_done_if_render_newer_than_checkpoint(tmp_path):
    project = _batch(tmp_path)
    _gate(project, "idea")
    spec = load_spec(project)
    _render(project, spec)
    _gate(project, "review_queue", stamp="2000-01-01T00:00:00+00:00")
    assert step_states(project)["review"] == "ready"
    _gate(project, "review_queue", stamp=datetime.now(timezone.utc).isoformat())
    future = time.time() - 60
    os.utime(project / "renders" / "v1.mp4", (future, future))
    assert step_states(project)["review"] == "done"


def test_export_done_when_every_video_exported(tmp_path):
    project = _batch(tmp_path)
    _gate(project, "idea")
    spec = load_spec(project)
    _render(project, spec)
    old = time.time() - 600
    os.utime(project / "renders" / "v1.mp4", (old, old))
    _gate(project, "review_queue")
    assert step_states(project)["export"] == "ready"
    out = project / "export" / "01-v1" / "video"
    out.mkdir(parents=True)
    (out / "output.mp4").write_bytes(b"e")
    assert step_states(project)["export"] == "done"


def test_running_and_failed_flags_lock_later_steps(tmp_path):
    project = _batch(tmp_path)
    _gate(project, "idea")
    states = step_states(project, running="render")
    assert states["render"] == "running" and states["review"] == "locked"
    states = step_states(project, failed="render")
    assert states["render"] == "failed"


def test_unreadable_files_never_raise(tmp_path):
    bid = create_batch(tmp_path, "Broken", "youtube_shorts")
    project = tmp_path / bid
    (project / "artifacts" / "batch_spec.json").write_text("{not json", encoding="utf-8")
    (project / "checkpoint_idea.json").write_text("garbage", encoding="utf-8")
    states = step_states(project)
    assert states["topics"] == "ready"


def test_failed_flag_does_not_override_a_step_that_is_actually_done(tmp_path):
    project = _batch(tmp_path)
    _gate(project, "idea")
    states = step_states(project, failed="footage")
    assert states["footage"] == "done" and states["render"] == "ready"


# ---- next_action ---------------------------------------------------------------

from studio.steps import next_action  # noqa: E402


def _states(**over):
    base = {s: "locked" for s in STEPS}
    base.update(over)
    return base


GATES_NONE = {"idea": "none", "review_queue": "none"}


def test_next_action_new_batch_asks_for_topics():
    n = next_action(_states(topics="ready"), GATES_NONE, None, 0, "b")
    assert n["kind"] == "todo" and n["step"] == "topics" and "suggest topics" in n["text"].lower()


def test_next_action_topics_present_asks_to_approve():
    n = next_action(_states(topics="ready"), GATES_NONE, None, 3, "b")
    assert n["step"] == "topics" and "approve" in n["text"].lower()


def test_next_action_running_failed_stale_and_done():
    assert next_action(_states(topics="done", script="running"), GATES_NONE, {"step": "script"}, 3, "b")["kind"] == "running"
    f = next_action(_states(topics="done", script="failed"), GATES_NONE, None, 3, "b")
    assert f["kind"] == "failed" and f["step"] == "script"
    s = next_action(_states(topics="done", script="done", footage="done", render="stale"), GATES_NONE, None, 3, "b")
    assert s["step"] == "render" and "again" in s["text"].lower()
    done = {k: "done" for k in STEPS}
    d = next_action(done, {"idea": "approved", "review_queue": "approved"}, None, 3, "b")
    assert d["kind"] == "done" and "export" in d["text"]


def test_next_action_review_waits_for_prepare_then_approval():
    base = _states(topics="done", script="done", footage="done", render="done", review="ready")
    assert "prepare" in next_action(base, GATES_NONE, None, 3, "b")["text"].lower()
    n = next_action(base, {"idea": "approved", "review_queue": "awaiting"}, None, 3, "b")
    assert n["step"] == "review" and "approve the batch" in n["text"].lower()
