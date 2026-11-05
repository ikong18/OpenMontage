"""Stage records written by the batch runner must satisfy the checkpoint contract."""

import pytest

from lib.checkpoint import read_checkpoint
from lib.shorts.batch import derive_batch, write_batch
from lib.shorts.checkpoints import write_idea_record, write_stage_records

PID = "p1"


def _spec():
    return {
        "project_id": PID,
        "sources": {"a": {"path": "assets/video/a.mp4", "clip_id": "nasa_a", "provider": "nasa",
                          "license": "public domain", "url": "https://example.com/a"}},
        "videos": [{
            "id": "v1", "topic": "Venus", "hook_type": "comparison", "hook_text": "h",
            "narration": "One. Two.", "claims": [{"text": "c", "sources": ["https://e.com/s"]}],
            "cuts": [{"source": "a", "in": 1.0, "dur": 2.0}, {"source": "a", "in": 5.0, "dur": 2.0}],
            "title": "t", "description": "d", "tags": ["x"],
        }],
    }


def _batch(project_dir):
    batch = derive_batch(_spec())
    video = batch["videos"][0]
    video.update({"audio": "assets/audio/v1.wav", "captions": "assets/audio/v1.captions.json", "audio_duration": 4.0})
    video["cost_entries"] = [{"tool": "piper_tts", "provider": "piper", "cost_usd": 0.0}]
    (project_dir / "renders").mkdir(parents=True, exist_ok=True)
    (project_dir / "renders" / "v1.mp4").write_bytes(b"x" * 10)
    write_batch(project_dir, batch)
    return batch


def _probe(_path):
    return {"width": 1080, "height": 1920, "duration": 4.5}


def _frames(render, out_dir, count=4):
    return [out_dir / f"{render.stem}_{i}.jpg" for i in range(count)]


@pytest.fixture
def projects(tmp_path):
    return tmp_path / "projects"


def test_idea_awaits_human_without_approval(projects):
    write_idea_record(projects, _spec(), approve=False)
    assert read_checkpoint(projects, PID, "idea")["status"] == "awaiting_human"


def test_idea_completes_with_approval(projects):
    write_idea_record(projects, _spec(), approve=True)
    checkpoint = read_checkpoint(projects, PID, "idea")
    assert checkpoint["status"] == "completed" and checkpoint["human_approved"] is True


def test_records_refuse_when_idea_not_approved(projects):
    write_idea_record(projects, _spec(), approve=False)
    batch = _batch(projects / PID)
    with pytest.raises(ValueError) as exc:
        write_stage_records(projects, _spec(), batch, probe=_probe, sampler=_frames)
    assert "idea" in str(exc.value) and "\n" not in str(exc.value).strip()


def test_stage_records_are_written_and_validate(projects):
    write_idea_record(projects, _spec(), approve=True)
    batch = _batch(projects / PID)
    written = write_stage_records(projects, _spec(), batch, probe=_probe, sampler=_frames)
    assert written == ["script", "visuals", "voice_captions", "compose", "review_queue"]
    for stage in written[:-1]:
        assert read_checkpoint(projects, PID, stage)["status"] == "completed"


def test_review_queue_awaits_human_by_default(projects):
    write_idea_record(projects, _spec(), approve=True)
    write_stage_records(projects, _spec(), _batch(projects / PID), probe=_probe, sampler=_frames)
    assert read_checkpoint(projects, PID, "review_queue")["status"] == "awaiting_human"


def test_review_queue_completes_only_with_approve(projects):
    write_idea_record(projects, _spec(), approve=True)
    write_stage_records(projects, _spec(), _batch(projects / PID),
                        approve=frozenset({"review_queue"}), probe=_probe, sampler=_frames)
    checkpoint = read_checkpoint(projects, PID, "review_queue")
    assert checkpoint["status"] == "completed" and checkpoint["human_approved"] is True


def test_scene_plan_has_one_scene_per_cut(projects):
    write_idea_record(projects, _spec(), approve=True)
    write_stage_records(projects, _spec(), _batch(projects / PID), probe=_probe, sampler=_frames)
    scenes = read_checkpoint(projects, PID, "visuals")["artifacts"]["scene_plan"]["scenes"]
    assert [(s["start_seconds"], s["end_seconds"]) for s in scenes] == [(0.0, 2.0), (2.0, 4.0)]
    assert all(a["source"] == "source" for s in scenes for a in s["required_assets"])


def test_render_report_uses_probe_and_file_size(projects):
    write_idea_record(projects, _spec(), approve=True)
    write_stage_records(projects, _spec(), _batch(projects / PID), probe=_probe, sampler=_frames)
    out = read_checkpoint(projects, PID, "compose")["artifacts"]["render_report"]["outputs"][0]
    assert out["resolution"] == "1080x1920" and out["duration_seconds"] == 4.5 and out["file_size_bytes"] == 10


def test_final_review_counts_sampled_frames(projects):
    write_idea_record(projects, _spec(), approve=True)
    write_stage_records(projects, _spec(), _batch(projects / PID), probe=_probe, sampler=_frames)
    checks = read_checkpoint(projects, PID, "compose")["artifacts"]["final_review"]["checks"]
    assert checks["visual_spotcheck"]["frames_sampled"] == 4
    assert len(checks["visual_spotcheck"]["frame_paths"]) == 4


import shutil  # noqa: E402
import subprocess  # noqa: E402

from lib.shorts.checkpoints import sample_frames  # noqa: E402


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_sample_frames_extracts_four_jpgs(tmp_path):
    video = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=4",
                    "-pix_fmt", "yuv420p", str(video)], check=True)
    frames = sample_frames(video, tmp_path / "frames")
    assert len(frames) == 4 and all(p.is_file() and p.stat().st_size > 0 for p in frames)


def test_sample_frames_missing_ffmpeg_message(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(RuntimeError) as exc:
        sample_frames(tmp_path / "v.mp4", tmp_path / "frames")
    assert "ffmpeg" in str(exc.value)
