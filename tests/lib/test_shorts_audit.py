"""Audit CLI for shorts-factory batches."""

import json

import pytest

from scripts import shorts_audit


def _video(i, hook_type, topic=None):
    return {
        "id": f"v{i}",
        "topic": topic or f"topic {i}",
        "hook_type": hook_type,
        "hook_text": f"hook {i}",
        "claims": [{"text": "x", "sources": ["https://example.com/a"]}],
        "clips": [
            {
                "clip_id": f"clip{i}",
                "source": "nasa",
                "license": "public-domain",
                "url": "https://example.com/c",
            }
        ],
        "render": f"renders/v{i}.mp4",
        "cost_entries": [{"tool": "piper_tts", "provider": "piper", "cost_usd": 0.0}],
    }


def _write_batch(project_dir, videos, render=True):
    artifacts = project_dir / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "shorts_batch.json").write_text(
        json.dumps({"videos": videos}, ensure_ascii=False), encoding="utf-8"
    )
    if render:
        (project_dir / "renders").mkdir()
        for video in videos:
            (project_dir / video["render"]).write_bytes(b"x")


def _good_probe(_path):
    return {"width": 1080, "height": 1920, "duration": 40.0}


def test_audit_passes_clean_batch(tmp_path):
    _write_batch(tmp_path, [_video(1, "question"), _video(2, "myth_bust")])
    result = shorts_audit.audit_project(tmp_path, probe=_good_probe)
    assert result == {"ok": True, "violations": []}


def test_audit_reports_missing_render(tmp_path):
    _write_batch(tmp_path, [_video(1, "question"), _video(2, "myth_bust")], render=False)
    result = shorts_audit.audit_project(tmp_path, probe=_good_probe)
    assert result["ok"] is False
    assert "missing render: v1" in result["violations"]


def test_audit_fails_when_batch_file_missing(tmp_path):
    with pytest.raises(FileNotFoundError) as exc:
        shorts_audit.audit_project(tmp_path, probe=_good_probe)
    assert "shorts_batch.json" in str(exc.value)


def test_batch_file_roundtrips_non_ascii_topics(tmp_path):
    _write_batch(tmp_path, [_video(1, "question", topic="Pokémon 日本")])
    batch = shorts_audit.load_batch(tmp_path)
    assert batch["videos"][0]["topic"] == "Pokémon 日本"


def test_main_exit_codes(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(shorts_audit, "probe_render", _good_probe)
    clean = tmp_path / "clean"
    _write_batch(clean, [_video(1, "question"), _video(2, "myth_bust")])
    assert shorts_audit.main([str(clean)]) == 0

    dirty = tmp_path / "dirty"
    _write_batch(dirty, [_video(1, "question"), _video(2, "myth_bust")], render=False)
    assert shorts_audit.main([str(dirty)]) == 1
    assert "missing render: v1" in capsys.readouterr().out
