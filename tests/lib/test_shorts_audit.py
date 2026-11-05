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


# --- review fixes: the audit reports problems instead of crashing ---

def test_audit_reports_video_without_id(tmp_path):
    video = _video(1, "question")
    del video["id"]
    _write_batch(tmp_path, [video, _video(2, "myth_bust")], render=False)
    result = shorts_audit.audit_project(tmp_path, probe=_good_probe)
    assert result["ok"] is False
    assert any("missing id" in v for v in result["violations"])


def test_audit_reports_unprobeable_render(tmp_path):
    _write_batch(tmp_path, [_video(1, "question"), _video(2, "myth_bust")])

    def broken_probe(_path):
        raise RuntimeError("ffprobe exploded")

    result = shorts_audit.audit_project(tmp_path, probe=broken_probe)
    assert result["ok"] is False
    assert "unprobeable render: v1" in result["violations"]


def test_audit_reports_duplicate_video_ids(tmp_path):
    _write_batch(tmp_path, [_video(1, "question"), _video(1, "myth_bust")])
    result = shorts_audit.audit_project(tmp_path, probe=_good_probe)
    assert result["ok"] is False
    assert any("duplicate video id" in v for v in result["violations"])


def test_load_batch_accepts_utf8_bom(tmp_path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "shorts_batch.json").write_bytes(b"\xef\xbb\xbf" + json.dumps({"videos": []}).encode("utf-8"))
    assert shorts_audit.load_batch(tmp_path) == {"videos": []}


@pytest.mark.parametrize("content", ["{not json", "[]"])
def test_main_reports_malformed_batch_without_traceback(tmp_path, capsys, content):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "shorts_batch.json").write_text(content, encoding="utf-8")
    assert shorts_audit.main([str(tmp_path)]) == 1
    assert "shorts_batch.json" in capsys.readouterr().out
