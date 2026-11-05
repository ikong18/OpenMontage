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
        "project_id": project_dir.name,
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


# --- review fixes ---


def test_validate_flags_project_id_folder_mismatch(tmp_path):
    spec = _spec(tmp_path)
    spec["project_id"] = "other"
    assert any("project_id" in v for v in validate_spec(spec, tmp_path))


@pytest.mark.parametrize("bad_id", ["../../evil", "a/b", "a b", "", "火星"])
def test_validate_rejects_unsafe_video_ids(tmp_path, bad_id):
    spec = _spec(tmp_path)
    spec["videos"][0]["id"] = bad_id
    assert any("id" in v for v in validate_spec(spec, tmp_path))


def test_validate_rejects_source_path_outside_project(tmp_path):
    spec = _spec(tmp_path)
    outside = tmp_path.parent / "outside.mp4"
    outside.write_bytes(b"x")
    spec["sources"]["a"]["path"] = "../outside.mp4"
    assert any("outside" in v for v in validate_spec(spec, tmp_path))


def test_validate_flags_same_clip_reused_under_two_source_keys(tmp_path):
    spec = _spec(tmp_path)
    spec["sources"]["b"] = dict(spec["sources"]["a"])  # same file and clip_id under a new key
    spec["videos"][0]["cuts"][0] = {"source": "a", "in": 1.0, "dur": 5.0}
    spec["videos"][1]["cuts"][0] = {"source": "b", "in": 2.0, "dur": 5.0}
    assert any("overlap" in v for v in validate_spec(spec, tmp_path))


def test_validate_flags_source_without_clip_id(tmp_path):
    spec = _spec(tmp_path)
    del spec["sources"]["a"]["clip_id"]
    assert any("clip_id" in v for v in validate_spec(spec, tmp_path))


def test_validate_malformed_shapes_report_instead_of_raising(tmp_path):
    spec = _spec(tmp_path)
    spec["videos"][0]["cuts"][0]["source"] = ["a"]
    spec["sources"]["c"] = "path.mp4"
    violations = validate_spec(spec, tmp_path)
    assert violations and all(isinstance(v, str) for v in violations)


def test_validate_stage_idea_needs_only_idea_fields(tmp_path):
    spec = {"project_id": tmp_path.name, "videos": [{
        "id": "v1", "topic": "t", "hook_type": "question", "hook_text": "h",
        "claims": [{"text": "c", "sources": ["https://e.com"]}]}]}
    assert validate_spec(spec, tmp_path, stage="idea") == []
    assert validate_spec(spec, tmp_path, stage="full") != []


def test_validate_stage_script_requires_narration_and_visuals_requires_cuts(tmp_path):
    spec = _spec(tmp_path)
    for video in spec["videos"]:
        for field in ("narration", "cuts", "title", "description", "tags"):
            video.pop(field)
    assert any("narration" in v for v in validate_spec(spec, tmp_path, stage="script"))
    for video in spec["videos"]:
        video["narration"] = "One."
    assert validate_spec(spec, tmp_path, stage="script") == []
    assert any("cuts" in v for v in validate_spec(spec, tmp_path, stage="visuals"))


def test_validate_unknown_stage_raises_value_error(tmp_path):
    with pytest.raises(ValueError):
        validate_spec(_spec(tmp_path), tmp_path, stage="nope")


# --- platform profiles ---


def test_validate_flags_unknown_platform(tmp_path):
    spec = _spec(tmp_path)
    spec["platform"] = "tiktok"
    assert any("platform" in v for v in validate_spec(spec, tmp_path))


def test_validate_accepts_facebook_reels(tmp_path):
    spec = _spec(tmp_path)
    spec["platform"] = "facebook_reels"
    assert validate_spec(spec, tmp_path) == []


def test_validate_flags_description_over_the_platform_limit(tmp_path):
    spec = _spec(tmp_path)
    spec["platform"] = "facebook_reels"
    spec["videos"][0]["description"] = "x" * 2001
    assert any("description" in v for v in validate_spec(spec, tmp_path))
    spec["platform"] = "youtube_shorts"  # the same text is fine on YouTube (5000)
    assert validate_spec(spec, tmp_path) == []


def test_validate_flags_too_many_hashtags(tmp_path):
    spec = _spec(tmp_path)
    spec["platform"] = "facebook_reels"
    spec["videos"][0]["description"] = " ".join(f"#tag{i}" for i in range(30))  # + the profile's #Reels = 31
    assert any("hashtag" in v for v in validate_spec(spec, tmp_path))


def test_validate_description_is_not_checked_before_the_final_stage(tmp_path):
    spec = _spec(tmp_path)
    spec["platform"] = "facebook_reels"
    spec["videos"][0]["description"] = "x" * 5000
    assert validate_spec(spec, tmp_path, stage="visuals") == []


def test_validate_flags_bad_caption_style(tmp_path):
    spec = _spec(tmp_path)
    spec["caption_style"] = {"bottomOffset": "high", "sparkle": 1}
    violations = validate_spec(spec, tmp_path)
    assert any("bottomOffset" in v for v in violations)
    assert any("sparkle" in v for v in violations)


def test_validate_accepts_a_caption_style_override(tmp_path):
    spec = _spec(tmp_path)
    spec["caption_style"] = {"bottomOffset": 500, "highlightColor": "#00FFAA"}
    assert validate_spec(spec, tmp_path) == []
