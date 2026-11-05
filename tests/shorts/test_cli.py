"""scripts/shorts_batch.py: exit codes, one-line errors, gate flags (no network, no rendering)."""

import json
import shutil
from types import SimpleNamespace

import pytest

from lib.shorts.batch import derive_batch, write_batch
from lib.shorts.fetch import SizeCapError
from lib.shorts.spec import save_spec
from scripts import shorts_batch

PID = "p1"


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


@pytest.fixture
def project(tmp_path):
    project_dir = tmp_path / "projects" / PID
    (project_dir / "assets" / "video").mkdir(parents=True)
    (project_dir / "assets" / "video" / "a.mp4").write_bytes(b"x")
    spec = _spec()
    save_spec(project_dir, spec)
    batch = derive_batch(spec)
    batch["videos"][0].update({"audio": "assets/audio/v1.wav", "captions": "assets/audio/v1.captions.json",
                               "audio_duration": 10.0})
    write_batch(project_dir, batch)
    return project_dir


def _run(capsys, *argv):
    code = shorts_batch.main(list(argv))
    return code, capsys.readouterr().out


def test_validate_exit_codes(project, capsys):
    code, out = _run(capsys, "validate", str(project))
    assert code == 0 and "OK" in out
    spec = _spec()
    spec["videos"][0]["hook_type"] = "clickbait"
    save_spec(project, spec)
    code, out = _run(capsys, "validate", str(project))
    assert code == 1 and "hook_type" in out


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


def test_render_refuses_when_spec_invalid(project, capsys, monkeypatch):
    spec = _spec()
    spec["videos"][0]["hook_type"] = "clickbait"
    save_spec(project, spec)
    monkeypatch.setattr(shorts_batch, "cut_segment", lambda *a, **k: pytest.fail("must not cut"))
    code, out = _run(capsys, "render", str(project))
    assert code == 1 and "hook_type" in out


def test_render_refuses_short_cuts_before_cutting(project, capsys, monkeypatch):
    batch = json.loads((project / "artifacts" / "shorts_batch.json").read_text(encoding="utf-8"))
    batch["videos"][0]["audio_duration"] = 30.0  # cuts total 4.0
    write_batch(project, batch)
    monkeypatch.setattr(shorts_batch, "cut_segment", lambda *a, **k: pytest.fail("must not cut"))
    code, out = _run(capsys, "render", str(project))
    assert code == 1 and "cuts total" in out


def test_render_without_voice_says_so(project, capsys):
    batch = json.loads((project / "artifacts" / "shorts_batch.json").read_text(encoding="utf-8"))
    del batch["videos"][0]["audio_duration"]
    write_batch(project, batch)
    code, out = _run(capsys, "render", str(project))
    assert code == 1 and "voice" in out


def test_missing_ffmpeg_prints_one_line_and_returns_1(project, capsys, monkeypatch):
    batch = json.loads((project / "artifacts" / "shorts_batch.json").read_text(encoding="utf-8"))
    batch["videos"][0]["audio_duration"] = 4.0
    write_batch(project, batch)
    (project / "assets" / "audio").mkdir(parents=True)
    (project / "assets" / "audio" / "v1.captions.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    code, out = _run(capsys, "render", str(project))
    lines = [line for line in out.splitlines() if line.strip()]
    assert code == 1 and len(lines) == 1 and "ffmpeg" in lines[0] and "Traceback" not in out


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


def test_export_refuses_when_audit_fails(project, capsys, monkeypatch):
    monkeypatch.setattr(shorts_batch, "audit_project",
                        lambda *a, **k: {"ok": False, "violations": ["zero_cost: x"]})
    monkeypatch.setattr(shorts_batch, "export_videos", lambda *a, **k: pytest.fail("must not export"))
    code, out = _run(capsys, "export", str(project), "--approved", "v1")
    assert code == 1 and "zero_cost" in out


def test_export_passes_approved_ids_in_order(project, capsys, monkeypatch):
    seen = {}
    monkeypatch.setattr(shorts_batch, "audit_project", lambda *a, **k: {"ok": True, "violations": []})
    monkeypatch.setattr(shorts_batch, "_registry", lambda: object())
    monkeypatch.setattr(shorts_batch, "export_videos",
                        lambda projects, spec, ids, registry: seen.setdefault("ids", ids) or [])
    code, _ = _run(capsys, "export", str(project), "--approved", "v3,v1")
    assert code == 0 and seen["ids"] == ["v3", "v1"]


def test_unknown_project_is_a_one_line_error(tmp_path, capsys):
    code, out = _run(capsys, "validate", str(tmp_path / "nope"))
    assert code == 1 and len([ln for ln in out.splitlines() if ln.strip()]) == 1
