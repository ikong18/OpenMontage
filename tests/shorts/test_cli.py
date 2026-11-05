"""scripts/shorts_batch.py: exit codes, one-line errors, gate flags (no network, no rendering)."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from lib.shorts.batch import derive_batch, read_batch, render_fingerprint, voice_fingerprint, write_batch
from lib.shorts.fetch import SizeCapError
from lib.shorts.spec import load_spec, save_spec
from scripts import shorts_batch

PID = "p1"
REPO = Path(__file__).resolve().parents[2]


def _spec():
    return {
        "project_id": PID,
        "voice": {"model": "en_US-lessac-medium", "length_scale": 0.95},
        "sources": {"a": {"path": "assets/video/a.mp4", "clip_id": "nasa_a", "provider": "nasa",
                          "mode": "fill", "license": "public domain", "url": "https://example.com/a"}},
        "videos": [{
            "id": "v1", "topic": "t", "hook_type": "comparison", "hook_text": "h",
            "narration": "One. Two.", "claims": [{"text": "c", "sources": ["https://e.com/s"]}],
            "cuts": [{"source": "a", "in": 1.0, "dur": 2.0}, {"source": "a", "in": 5.0, "dur": 2.0}],
            "title": "t", "description": "d", "tags": ["x"],
        }],
    }


def _voiced_batch(spec, duration=10.0, rendered=True):
    batch = derive_batch(spec)
    for video, sv in zip(batch["videos"], spec["videos"]):
        video.update({"audio": f"assets/audio/{video['id']}.wav",
                      "captions": f"assets/audio/{video['id']}.captions.json",
                      "audio_duration": duration, "voice_hash": voice_fingerprint(spec, sv)})
        if rendered:
            video["render_hash"] = render_fingerprint(spec, sv)
    return batch


@pytest.fixture
def project(tmp_path):
    project_dir = tmp_path / "projects" / PID
    (project_dir / "assets" / "video").mkdir(parents=True)
    (project_dir / "assets" / "video" / "a.mp4").write_bytes(b"x")
    spec = _spec()
    save_spec(project_dir, spec)
    write_batch(project_dir, _voiced_batch(spec))
    return project_dir


def _run(capsys, *argv):
    code = shorts_batch.main(list(argv))
    return code, capsys.readouterr().out


def _edit_batch(project, **fields):
    batch = read_batch(project)
    batch["videos"][0].update(fields)
    write_batch(project, batch)


def _edit_spec(project, fn):
    spec = load_spec(project)
    fn(spec)
    save_spec(project, spec)


def _one_line(out):
    return len([ln for ln in out.splitlines() if ln.strip()]) == 1


def test_validate_exit_codes(project, capsys):
    code, out = _run(capsys, "validate", str(project))
    assert code == 0 and "OK" in out
    _edit_spec(project, lambda s: s["videos"][0].update(hook_type="clickbait"))
    code, out = _run(capsys, "validate", str(project))
    assert code == 1 and "hook_type" in out


def test_validate_stage_flag_checks_only_that_stage(project, capsys):
    def strip(spec):
        spec["sources"] = {}
        for field in ("narration", "cuts", "title", "description", "tags"):
            spec["videos"][0].pop(field)

    _edit_spec(project, strip)
    code, out = _run(capsys, "validate", str(project), "--stage", "idea")
    assert code == 0 and "OK" in out
    code, _ = _run(capsys, "validate", str(project))
    assert code == 1


def test_fetch_refuses_oversized_without_changing_spec(project, capsys, monkeypatch):
    cand = SimpleNamespace(download_url="https://example.com/big.mp4", source="nasa", clip_id="nasa_big",
                           license="pd", source_url="https://example.com/big", creator="x")
    monkeypatch.setattr(shorts_batch, "_resolve_candidate", lambda *a, **k: cand)

    def refuse(*_a, **_k):
        raise SizeCapError("file is 5,000 MB, over the 400 MB cap; pick a smaller clip or raise --max-mb")

    monkeypatch.setattr(shorts_batch, "fetch_clip", refuse)
    before = (project / "artifacts" / "batch_spec.json").read_text(encoding="utf-8")
    code, out = _run(capsys, "fetch", str(project), "--source", "nasa", "--query", "q",
                     "--clip-id", "nasa_big", "--key", "big")
    assert code == 1 and "cap" in out and "Traceback" not in out
    assert (project / "artifacts" / "batch_spec.json").read_text(encoding="utf-8") == before


def test_network_error_in_fetch_is_a_one_line_error(project, capsys, monkeypatch):
    import requests

    def offline(*_a, **_k):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(shorts_batch, "_resolve_candidate", offline)
    code, out = _run(capsys, "fetch", str(project), "--source", "nasa", "--query", "q",
                     "--clip-id", "x", "--key", "k")
    assert code == 1 and _one_line(out) and "Traceback" not in out


def test_render_refuses_when_spec_invalid(project, capsys, monkeypatch):
    _edit_spec(project, lambda s: s["videos"][0].update(hook_type="clickbait"))
    monkeypatch.setattr(shorts_batch, "cut_segment", lambda *a, **k: pytest.fail("must not cut"))
    code, out = _run(capsys, "render", str(project))
    assert code == 1 and "hook_type" in out


def test_render_refuses_short_cuts_before_cutting(project, capsys, monkeypatch):
    _edit_batch(project, audio_duration=30.0)  # cuts total 4.0
    monkeypatch.setattr(shorts_batch, "cut_segment", lambda *a, **k: pytest.fail("must not cut"))
    code, out = _run(capsys, "render", str(project))
    assert code == 1 and "cuts total" in out


def test_render_without_voice_says_so(project, capsys):
    batch = read_batch(project)
    for key in ("audio", "captions", "audio_duration", "voice_hash"):
        del batch["videos"][0][key]
    write_batch(project, batch)
    code, out = _run(capsys, "render", str(project))
    assert code == 1 and "voice" in out


def test_stale_voice_is_dropped_when_narration_changes(project, capsys, monkeypatch):
    _edit_spec(project, lambda s: s["videos"][0].update(narration="A completely different script now."))
    monkeypatch.setattr(shorts_batch, "cut_segment", lambda *a, **k: pytest.fail("must not cut"))
    code, out = _run(capsys, "render", str(project))
    assert code == 1 and "voice" in out and "Traceback" not in out


def test_missing_ffmpeg_prints_one_line_and_returns_1(project, capsys, monkeypatch):
    _edit_batch(project, audio_duration=4.0)
    (project / "assets" / "audio").mkdir(parents=True)
    (project / "assets" / "audio" / "v1.captions.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    code, out = _run(capsys, "render", str(project))
    assert code == 1 and _one_line(out) and "ffmpeg" in out and "Traceback" not in out


def test_voice_validates_the_spec_first(project, capsys, monkeypatch):
    _edit_spec(project, lambda s: s["sources"]["a"].pop("clip_id"))
    monkeypatch.setattr(shorts_batch, "_registry", lambda: pytest.fail("must not touch tools"))
    code, out = _run(capsys, "voice", str(project))
    assert code == 1 and "clip_id" in out and "Traceback" not in out


def test_checkpoints_idea_only_leaves_gate_open_without_approve(project, capsys, monkeypatch):
    calls = []
    monkeypatch.setattr(shorts_batch, "write_idea_record", lambda projects, spec, approve: calls.append(approve))
    code, _ = _run(capsys, "checkpoints", str(project), "--idea-only")
    assert code == 0 and calls == [False]
    code, _ = _run(capsys, "checkpoints", str(project), "--idea-only", "--approve", "idea")
    assert code == 0 and calls == [False, True]


def test_checkpoints_pass_review_approval_through(project, capsys, monkeypatch):
    seen = {}
    monkeypatch.setattr(shorts_batch, "audit_project", lambda *a, **k: {"ok": True, "violations": []})

    def fake_records(projects, spec, batch, approve=frozenset(), **_kw):
        seen["approve"] = approve
        return ["script"]

    monkeypatch.setattr(shorts_batch, "write_stage_records", fake_records)
    _run(capsys, "checkpoints", str(project))
    assert seen["approve"] == frozenset()
    _run(capsys, "checkpoints", str(project), "--approve", "review_queue")
    assert seen["approve"] == frozenset({"review_queue"})


def test_checkpoints_refuse_when_audit_fails(project, capsys, monkeypatch):
    monkeypatch.setattr(shorts_batch, "audit_project",
                        lambda *a, **k: {"ok": False, "violations": ["missing render: v1"]})
    monkeypatch.setattr(shorts_batch, "write_stage_records", lambda *a, **k: pytest.fail("must not write"))
    code, out = _run(capsys, "checkpoints", str(project))
    assert code == 1 and "missing render: v1" in out


def test_checkpoints_refuse_an_unvoiced_video(project, capsys, monkeypatch):
    def add_video(spec):
        second = json.loads(json.dumps(spec["videos"][0]))
        second.update(id="v2", cuts=[{"source": "a", "in": 20.0, "dur": 2.0}])
        spec["videos"].append(second)

    _edit_spec(project, add_video)
    monkeypatch.setattr(shorts_batch, "write_stage_records", lambda *a, **k: pytest.fail("must not write"))
    code, out = _run(capsys, "checkpoints", str(project))
    assert code == 1 and "v2" in out and "voice" in out and "Traceback" not in out


def test_checkpoints_refuse_a_stale_render(project, capsys, monkeypatch):
    _edit_spec(project, lambda s: s["videos"][0]["cuts"][0].update(dur=2.5))  # cuts changed after render
    monkeypatch.setattr(shorts_batch, "write_stage_records", lambda *a, **k: pytest.fail("must not write"))
    code, out = _run(capsys, "checkpoints", str(project))
    assert code == 1 and "out of date" in out


def test_export_refuses_a_stale_render(project, capsys, monkeypatch):
    _edit_spec(project, lambda s: s["videos"][0]["cuts"][0].update(dur=2.5))
    monkeypatch.setattr(shorts_batch, "audit_project", lambda *a, **k: {"ok": True, "violations": []})
    monkeypatch.setattr(shorts_batch, "export_videos", lambda *a, **k: pytest.fail("must not export"))
    code, out = _run(capsys, "export", str(project), "--approved", "v1")
    assert code == 1 and "out of date" in out


def test_export_refuses_when_audit_fails(project, capsys, monkeypatch):
    monkeypatch.setattr(shorts_batch, "audit_project",
                        lambda *a, **k: {"ok": False, "violations": ["zero_cost: x"]})
    monkeypatch.setattr(shorts_batch, "export_videos", lambda *a, **k: pytest.fail("must not export"))
    code, out = _run(capsys, "export", str(project), "--approved", "v1")
    assert code == 1 and "zero_cost" in out


def test_export_passes_approved_ids_in_order(project, capsys, monkeypatch):
    seen = {}

    def add_video(spec):
        for vid, start in (("v2", 20.0), ("v3", 30.0)):
            extra = json.loads(json.dumps(spec["videos"][0]))
            extra.update(id=vid, cuts=[{"source": "a", "in": start, "dur": 2.0}])
            spec["videos"].append(extra)

    _edit_spec(project, add_video)
    spec = load_spec(project)
    write_batch(project, _voiced_batch(spec))
    monkeypatch.setattr(shorts_batch, "audit_project", lambda *a, **k: {"ok": True, "violations": []})
    monkeypatch.setattr(shorts_batch, "_registry", lambda: object())
    monkeypatch.setattr(shorts_batch, "export_videos",
                        lambda projects, spec, ids, registry: seen.setdefault("ids", ids) or [])
    code, _ = _run(capsys, "export", str(project), "--approved", "v3,v1")
    assert code == 0 and seen["ids"] == ["v3", "v1"]


def test_unknown_project_is_a_one_line_error(tmp_path, capsys):
    code, out = _run(capsys, "validate", str(tmp_path / "nope"))
    assert code == 1 and _one_line(out)


def test_non_ascii_violation_survives_piped_stdout(project):
    """Windows pipes stdout as cp1252 unless the CLI sets UTF-8 itself."""
    _edit_spec(project, lambda s: s["videos"][0].update(hook_type="火星"))
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
    proc = subprocess.run([sys.executable, str(REPO / "scripts" / "shorts_batch.py"), "validate", str(project)],
                          capture_output=True, cwd=REPO, env=env)
    out = proc.stdout.decode("utf-8", errors="replace")
    assert proc.returncode == 1 and "hook_type" in out and "charmap" not in out


def test_render_uses_the_platform_caption_offset(project, capsys, monkeypatch):
    _edit_spec(project, lambda s: s.update(platform="facebook_reels"))
    _edit_batch(project, audio_duration=4.0)
    (project / "assets" / "audio").mkdir(parents=True, exist_ok=True)
    (project / "assets" / "audio" / "v1.captions.json").write_text("[]", encoding="utf-8")
    seen = {}
    monkeypatch.setattr(shorts_batch, "cut_segment", lambda src, in_s, dur, out, mode: out)
    monkeypatch.setattr(shorts_batch, "_registry", lambda: object())
    monkeypatch.setattr(shorts_batch, "render_video",
                        lambda registry, props, output: seen.setdefault("props", props))
    code, _ = _run(capsys, "render", str(project))
    assert code == 0 and seen["props"]["captionStyle"]["bottomOffset"] == 420


def test_render_spec_caption_style_overrides_the_platform(project, capsys, monkeypatch):
    _edit_spec(project, lambda s: s.update(platform="facebook_reels", caption_style={"bottomOffset": 560}))
    _edit_batch(project, audio_duration=4.0)
    (project / "assets" / "audio").mkdir(parents=True, exist_ok=True)
    (project / "assets" / "audio" / "v1.captions.json").write_text("[]", encoding="utf-8")
    seen = {}
    monkeypatch.setattr(shorts_batch, "cut_segment", lambda src, in_s, dur, out, mode: out)
    monkeypatch.setattr(shorts_batch, "_registry", lambda: object())
    monkeypatch.setattr(shorts_batch, "render_video",
                        lambda registry, props, output: seen.setdefault("props", props))
    code, _ = _run(capsys, "render", str(project))
    assert code == 0 and seen["props"]["captionStyle"]["bottomOffset"] == 560


# ---- search and sheet (used by Studio) ----------------------------------------


def test_search_prints_candidates_with_sizes(project, capsys, monkeypatch):
    cands = [
        SimpleNamespace(clip_id="nasa_a", source_tags="Sun flare", download_url="https://x/a.mp4", duration=42.4),
        SimpleNamespace(clip_id="nasa_b", source_tags="Sun big", download_url="https://x/b.mp4", duration=0),
    ]
    monkeypatch.setattr(shorts_batch, "_search_candidates", lambda source, query: cands)
    monkeypatch.setattr(shorts_batch, "_head_size", lambda url: 5 * 1024 * 1024 if url.endswith("a.mp4") else None)
    code, out = _run(capsys, "search", str(project), "--source", "nasa", "--query", "sun")
    assert code == 0
    lines = out.strip().splitlines()
    assert lines[0].split("\t") == ["nasa_a", "Sun flare", "5.0", "42"]
    assert lines[1].split("\t") == ["nasa_b", "Sun big", "?", "?"]


def test_search_unknown_source_is_one_line_error(project, capsys):
    code, out = _run(capsys, "search", str(project), "--source", "bogus", "--query", "sun")
    assert code == 1 and out.startswith("error:") and _one_line(out)


def test_sheet_command_writes_png_for_source_key(project, capsys, monkeypatch):
    seen = {}

    def fake_sheet(video, out, step=4.0, cols=6, rows=5, thumb_width=240):
        seen.update(video=video, out=out, step=step)
        return {"path": str(out), "step": step, "cols": cols, "frames": 3}

    monkeypatch.setattr(shorts_batch, "make_contact_sheet", fake_sheet)
    monkeypatch.setattr(shorts_batch, "probe_video", lambda p: {"width": 1280, "height": 720, "duration": 12.0})
    code, out = _run(capsys, "sheet", str(project), "--key", "a", "--step", "5")
    info = json.loads(out.strip().splitlines()[-1])
    assert code == 0 and seen["step"] == 5.0
    assert Path(seen["out"]) == project / "review" / "sheet_a.png"
    assert Path(seen["video"]) == project / "assets" / "video" / "a.mp4"
    assert info["key"] == "a" and info["width"] == 1280 and info["frames"] == 3


def test_sheet_unknown_key_errors_one_line(project, capsys):
    code, out = _run(capsys, "sheet", str(project), "--key", "nope")
    assert code == 1 and out.startswith("error:") and _one_line(out)


def test_video_option_accepts_several_ids_and_rejects_unknown_ones(project, capsys):
    spec = load_spec(project)
    assert [v["id"] for v in shorts_batch._selected(spec, "v1")] == ["v1"]
    assert [v["id"] for v in shorts_batch._selected(spec, "v1,v1")] == ["v1"]
    assert [v["id"] for v in shorts_batch._selected(spec, None)] == ["v1"]
    with pytest.raises(ValueError, match="v9"):
        shorts_batch._selected(spec, "v1,v9")


def test_fetch_picks_only_the_exact_clip_id_never_a_longer_one(project, monkeypatch):
    from types import SimpleNamespace as NS

    monkeypatch.setattr("scripts.shorts_batch.tools_stock_search", lambda *a, **k: None, raising=False)
    candidates = [NS(clip_id="wikimedia_1234", download_url="u", license="l", source_url="s"),
                  NS(clip_id="wikimedia_123", download_url="u", license="l", source_url="s")]

    class Adapter:
        name = "wikimedia"

        def search(self, query, filters):
            return candidates

    import tools.video.stock_sources as stock

    monkeypatch.setattr(stock, "all_sources", lambda: [Adapter()])
    assert shorts_batch._resolve_candidate("wikimedia", "q", "wikimedia_123").clip_id == "wikimedia_123"
    with pytest.raises(ValueError):
        shorts_batch._resolve_candidate("wikimedia", "q", "wikimedia_12")
