"""Approved-only export through export_bundle (fake registry, no files copied)."""

import pytest

from lib.checkpoint import read_checkpoint
from lib.shorts.batch import derive_batch, write_batch
from lib.shorts.checkpoints import write_idea_record, write_stage_records
from lib.shorts.export import export_videos

PID = "p1"


def _video(vid, hook_type, cut_in):
    return {
        "id": vid, "topic": f"topic {vid}", "hook_type": hook_type, "hook_text": f"hook {vid}",
        "narration": "One. Two.", "claims": [{"text": "c", "sources": ["https://e.com/s"]}],
        "cuts": [{"source": "a", "in": cut_in, "dur": 2.0}],
        "title": f"Title {vid}", "description": f"Desc {vid}", "tags": ["x", "y"],
    }


def _spec():
    return {
        "project_id": PID,
        "sources": {"a": {"path": "assets/video/a.mp4", "clip_id": "nasa_a", "provider": "nasa",
                          "license": "public domain", "url": "https://example.com/a"}},
        "videos": [_video("v1", "comparison", 1.0), _video("v2", "question", 5.0), _video("v3", "countdown", 9.0)],
    }


class _FakeExport:
    def __init__(self):
        self.calls = []

    def execute(self, inputs):
        self.calls.append(inputs)
        entry = {"platform": inputs["platform"], "status": "exported", "timestamp": "2026-11-05T00:00:00Z",
                 "visibility": inputs["visibility"], "export_path": inputs["export_dir"]}
        return type("R", (), {"success": True, "error": None,
                              "data": {"publish_log": {"entries": [entry]}, "export_path": inputs["export_dir"]}})()


class _FakeRegistry:
    def __init__(self):
        self.tool = _FakeExport()
        self._tools = {"export_bundle": self.tool}


def _frames(render, out_dir, count=4):
    return [out_dir / f"{render.stem}_{i}.jpg" for i in range(count)]


def _probe(_path):
    return {"width": 1080, "height": 1920, "duration": 4.5}


@pytest.fixture
def ready(tmp_path):
    projects = tmp_path / "projects"
    spec = _spec()
    write_idea_record(projects, spec, approve=True)
    batch = derive_batch(spec)
    for video in batch["videos"]:
        video.update({"audio": f"assets/audio/{video['id']}.wav",
                      "captions": f"assets/audio/{video['id']}.captions.json", "audio_duration": 4.0})
        video["cost_entries"] = [{"tool": "piper_tts", "provider": "piper", "cost_usd": 0.0}]
        (projects / PID / "renders").mkdir(parents=True, exist_ok=True)
        (projects / PID / "renders" / f"{video['id']}.mp4").write_bytes(b"x")
    write_batch(projects / PID, batch)
    write_stage_records(projects, spec, batch, approve=frozenset({"review_queue"}), probe=_probe, sampler=_frames)
    return projects, spec


def test_export_skips_unapproved_videos(ready):
    projects, spec = ready
    registry = _FakeRegistry()
    entries = export_videos(projects, spec, ["v1", "v3"], registry)
    assert len(entries) == 2
    assert [c["title"] for c in registry.tool.calls] == ["Title v1", "Title v3"]


def test_export_order_follows_approved_ids(ready):
    projects, spec = ready
    registry = _FakeRegistry()
    export_videos(projects, spec, ["v3", "v1"], registry)
    dirs = [c["export_dir"] for c in registry.tool.calls]
    assert dirs[0].endswith("01-v3") and dirs[1].endswith("02-v1")


def test_export_rejects_unknown_id(ready):
    projects, spec = ready
    with pytest.raises(ValueError) as exc:
        export_videos(projects, spec, ["v9"], _FakeRegistry())
    assert "v9" in str(exc.value)


def test_export_requires_completed_review_queue(tmp_path):
    projects = tmp_path / "projects"
    spec = _spec()
    write_idea_record(projects, spec, approve=True)
    with pytest.raises(ValueError) as exc:
        export_videos(projects, spec, ["v1"], _FakeRegistry())
    assert "review_queue" in str(exc.value)


def test_export_marks_private_shorts_and_writes_publish_stage(ready):
    projects, spec = ready
    registry = _FakeRegistry()
    export_videos(projects, spec, ["v1"], registry)
    call = registry.tool.calls[0]
    assert call["visibility"] == "private" and call["platform"] == "youtube_shorts"
    assert call["hashtags"] == ["#Shorts"] and call["tags"] == ["x", "y"]
    assert call["subtitles_path"].endswith("v1.srt")
    checkpoint = read_checkpoint(projects, PID, "publish")
    assert checkpoint["status"] == "completed"
    assert len(checkpoint["artifacts"]["publish_log"]["entries"]) == 1
