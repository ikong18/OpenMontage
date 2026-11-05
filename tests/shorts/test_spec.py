"""batch_spec.json loading and validation."""

import copy
import json

import pytest

from lib.shorts.spec import HOOK_TYPES, load_spec, validate_spec


def _spec(project_dir):
    src = project_dir / "assets" / "video" / "a.mp4"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"x")
    return {
        "project_id": "p1",
        "sources": {
            "a": {"path": "assets/video/a.mp4", "clip_id": "nasa_a", "provider": "nasa",
                  "mode": "fill", "license": "public domain", "url": "https://example.com/a"},
        },
        "videos": [
            _video("v1", "comparison", 56.0),
            _video("v2", "question", 60.0),
        ],
    }


def _video(vid, hook_type, cut_in, topic=None):
    return {
        "id": vid, "topic": topic or f"topic {vid}", "hook_type": hook_type, "hook_text": f"hook {vid}",
        "narration": "One. Two.",
        "claims": [{"text": "x", "sources": ["https://example.com/s"]}],
        "cuts": [{"source": "a", "in": cut_in, "dur": 4.0}],
        "title": "A title", "description": "d", "tags": ["t"],
    }


def _write(project_dir, content, raw=False):
    artifacts = project_dir / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    path = artifacts / "batch_spec.json"
    if raw:
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")


def test_load_spec_accepts_bom(tmp_path):
    _write(tmp_path, b"\xef\xbb\xbf" + json.dumps({"videos": []}).encode("utf-8"), raw=True)
    assert load_spec(tmp_path) == {"videos": []}


def test_load_spec_missing_file_names_path(tmp_path):
    with pytest.raises(FileNotFoundError) as exc:
        load_spec(tmp_path)
    assert "batch_spec.json" in str(exc.value)


def test_load_spec_malformed_json_names_the_file(tmp_path):
    _write(tmp_path, "{not json")
    with pytest.raises(ValueError) as exc:
        load_spec(tmp_path)
    assert "batch_spec.json" in str(exc.value)


def test_load_spec_rejects_non_object(tmp_path):
    _write(tmp_path, "[]")
    with pytest.raises(ValueError):
        load_spec(tmp_path)


def test_validate_clean_spec_has_no_violations(tmp_path):
    assert validate_spec(_spec(tmp_path), tmp_path) == []


def test_validate_flags_duplicate_ids(tmp_path):
    spec = _spec(tmp_path)
    spec["videos"][1]["id"] = "v1"
    assert any("duplicate" in v for v in validate_spec(spec, tmp_path))


def test_validate_flags_unknown_hook_type(tmp_path):
    spec = _spec(tmp_path)
    spec["videos"][0]["hook_type"] = "clickbait"
    assert any("hook_type" in v for v in validate_spec(spec, tmp_path))
    assert "question" in HOOK_TYPES


def test_validate_flags_missing_source_file(tmp_path):
    spec = _spec(tmp_path)
    (tmp_path / "assets" / "video" / "a.mp4").unlink()
    assert any("not found" in v for v in validate_spec(spec, tmp_path))


def test_validate_flags_unknown_source_key(tmp_path):
    spec = _spec(tmp_path)
    spec["videos"][0]["cuts"][0]["source"] = "nope"
    assert any("unknown source" in v for v in validate_spec(spec, tmp_path))


def test_validate_flags_overlapping_cuts_across_videos(tmp_path):
    spec = _spec(tmp_path)
    spec["videos"][1]["cuts"][0]["in"] = 58.0  # 56-60 overlaps 58-62
    assert any("overlap" in v for v in validate_spec(spec, tmp_path))


def test_validate_allows_adjacent_cuts(tmp_path):
    spec = _spec(tmp_path)  # 56-60 and 60-64 touch but do not overlap
    assert validate_spec(spec, tmp_path) == []


def test_validate_flags_claim_without_http_source(tmp_path):
    spec = _spec(tmp_path)
    spec["videos"][0]["claims"] = [{"text": "x", "sources": ["not a url"]}]
    assert any("claim" in v for v in validate_spec(spec, tmp_path))


def test_validate_flags_long_title_and_bad_duration(tmp_path):
    spec = _spec(tmp_path)
    spec["videos"][0]["title"] = "x" * 101
    spec["videos"][1]["cuts"][0]["dur"] = 0
    violations = validate_spec(spec, tmp_path)
    assert any("title" in v for v in violations)
    assert any("dur" in v for v in violations)


def test_validate_flags_missing_required_fields(tmp_path):
    spec = _spec(tmp_path)
    del spec["videos"][0]["narration"]
    assert any("narration" in v for v in validate_spec(spec, tmp_path))


def test_validate_accepts_non_ascii_topic(tmp_path):
    spec = _spec(tmp_path)
    spec["videos"][0]["topic"] = "Pokémon 日本"
    assert validate_spec(copy.deepcopy(spec), tmp_path) == []


def test_save_spec_roundtrips_non_ascii(tmp_path):
    from lib.shorts.spec import save_spec

    save_spec(tmp_path, {"videos": [], "note": "Pokémon 日本"})
    assert load_spec(tmp_path)["note"] == "Pokémon 日本"
