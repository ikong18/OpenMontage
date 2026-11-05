from lib.shorts.spec import load_spec, save_spec
from studio.batches import create_batch
from studio.sources import sheet_step, source_info


def test_sheet_step_rule():
    assert sheet_step(60) == 4 and sheet_step(120) == 4 and sheet_step(140.4) == 5 and sheet_step(300) == 10


def test_source_info_reports_duration_and_step_and_caches(tmp_path):
    bid = create_batch(tmp_path, "Src", "youtube_shorts")
    project = tmp_path / bid
    (project / "assets" / "video").mkdir(parents=True)
    (project / "assets" / "video" / "a.mp4").write_bytes(b"x")
    spec = load_spec(project)
    spec["sources"] = {"a": {"path": "assets/video/a.mp4", "clip_id": "c"}, "gone": {"path": "assets/video/zz.mp4", "clip_id": "d"}}
    save_spec(project, spec)
    calls = []

    def fake_probe(path):
        calls.append(path)
        return {"width": 1280, "height": 720, "duration": 140.4}

    info = source_info(project, spec, probe=fake_probe)
    assert info["a"] == {"duration": 140.4, "sheet_step": 5, "width": 1280, "height": 720}
    assert info["gone"] is None
    source_info(project, spec, probe=fake_probe)
    assert len(calls) == 1  # cached by path and mtime


def test_source_info_survives_probe_errors(tmp_path):
    bid = create_batch(tmp_path, "Src2", "youtube_shorts")
    project = tmp_path / bid
    (project / "a.mp4").write_bytes(b"x")
    spec = {"sources": {"a": {"path": "a.mp4", "clip_id": "c"}}}

    def boom(path):
        raise OSError("no ffprobe")

    assert source_info(project, spec, probe=boom) == {"a": None}
