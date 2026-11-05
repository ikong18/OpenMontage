import json
from pathlib import Path

import pytest

from lib.shorts.spec import load_spec
from studio.batches import batch_dir, create_batch, edit_videos, list_batches


def _plain_project(projects: Path, name: str, pipeline: str) -> None:
    folder = projects / name
    folder.mkdir(parents=True)
    (folder / "project.json").write_text(json.dumps({"pipeline_type": pipeline, "title": name}), encoding="utf-8")


def test_create_batch_writes_project_and_empty_spec(tmp_path):
    bid = create_batch(tmp_path, "Space Facts 03", "facebook_reels")
    project = json.loads((tmp_path / bid / "project.json").read_text(encoding="utf-8"))
    spec = load_spec(tmp_path / bid)
    assert project["pipeline_type"] == "shorts-factory"
    assert spec["project_id"] == bid
    assert spec["platform"] == "facebook_reels"
    assert spec["videos"] == []
    assert spec["voice"]["model"] == "en_US-lessac-medium"


def test_create_batch_collision_adds_suffix(tmp_path):
    first = create_batch(tmp_path, "Same Name", "youtube_shorts")
    second = create_batch(tmp_path, "Same Name", "youtube_shorts")
    assert first != second
    assert second.endswith("-2")


@pytest.mark.parametrize("name,platform", [("", "youtube_shorts"), ("   ", "youtube_shorts"), ("ok", "tiktok")])
def test_create_batch_rejects_empty_name_and_bad_platform(tmp_path, name, platform):
    with pytest.raises(ValueError):
        create_batch(tmp_path, name, platform)


@pytest.mark.parametrize("bad", ["../x", "a/b", "..%2fx", "/etc", "C:\\x", ""])
def test_batch_dir_rejects_traversal(tmp_path, bad):
    with pytest.raises(FileNotFoundError):
        batch_dir(tmp_path, bad)


def test_batch_dir_rejects_non_shorts_project(tmp_path):
    _plain_project(tmp_path, "other", "explainer")
    with pytest.raises(FileNotFoundError):
        batch_dir(tmp_path, "other")


def test_list_batches_only_shorts_factory(tmp_path):
    _plain_project(tmp_path, "other", "explainer")
    bid = create_batch(tmp_path, "Mine", "youtube_shorts")
    rows = list_batches(tmp_path)
    assert [r["id"] for r in rows] == [bid]
    assert rows[0]["platform"] == "youtube_shorts" and rows[0]["video_count"] == 0


def _video(**extra):
    base = {"id": "v1", "topic": "t", "hook_type": "question", "hook_text": "h?", "claims": [
        {"text": "c", "sources": ["https://example.com/a"]}]}
    base.update(extra)
    return base


def test_edit_videos_saves_valid_idea_stage(tmp_path):
    bid = create_batch(tmp_path, "Edit", "youtube_shorts")
    assert edit_videos(tmp_path / bid, [_video()], "idea") == []
    assert load_spec(tmp_path / bid)["videos"][0]["topic"] == "t"


def test_edit_videos_invalid_saves_nothing(tmp_path):
    bid = create_batch(tmp_path, "Edit", "youtube_shorts")
    path = tmp_path / bid / "artifacts" / "batch_spec.json"
    before = path.read_bytes()
    violations = edit_videos(tmp_path / bid, [_video(hook_type="bogus")], "idea")
    assert violations and path.read_bytes() == before


def test_edit_videos_drops_unknown_keys(tmp_path):
    bid = create_batch(tmp_path, "Edit", "youtube_shorts")
    edit_videos(tmp_path / bid, [_video(evil="x", path="../../x")], "idea")
    saved = load_spec(tmp_path / bid)["videos"][0]
    assert "evil" not in saved and "path" not in saved


def test_added_topic_without_claims_saves_with_seed_claim(tmp_path):
    bid = create_batch(tmp_path, "Add", "youtube_shorts")
    new = {"id": "v1", "topic": "Fresh", "hook_type": "question", "hook_text": "h?",
           "topic_sources": ["https://example.com/a"]}
    assert edit_videos(tmp_path / bid, [new], "idea") == []
    saved = load_spec(tmp_path / bid)["videos"][0]
    assert saved["claims"] == [{"text": "Fresh", "sources": ["https://example.com/a"]}]


def test_editing_topic_source_refreshes_seed_claim_but_not_real_claims(tmp_path):
    bid = create_batch(tmp_path, "Seed", "youtube_shorts")
    v = {"id": "v1", "topic": "T", "hook_type": "question", "hook_text": "h?", "topic_sources": ["https://a.example/1"]}
    edit_videos(tmp_path / bid, [v], "idea")
    v["topic_sources"] = ["https://b.example/2"]
    edit_videos(tmp_path / bid, [v], "idea")
    assert load_spec(tmp_path / bid)["videos"][0]["claims"][0]["sources"] == ["https://b.example/2"]
    scripted = {**v, "narration": "Words.", "claims": [{"text": "real", "sources": ["https://c.example/3"]}]}
    edit_videos(tmp_path / bid, [scripted], "idea")
    assert load_spec(tmp_path / bid)["videos"][0]["claims"][0]["text"] == "real"


# ---- list status and titles ------------------------------------------------------


def test_list_batches_reports_progress_and_nice_title(tmp_path):
    from studio.batches import list_batches

    bid = create_batch(tmp_path, "Space facts 04", "youtube_shorts")
    row = list_batches(tmp_path)[0]
    assert row["id"] == bid and row["title"] == "Space facts 04"
    assert row["current_step"] == "topics" and row["steps_done"] == 0 and row["steps_total"] == 6


def test_list_batches_prettifies_generated_titles(tmp_path):
    from studio.batches import list_batches

    folder = tmp_path / "shorts-space-batch-02"
    folder.mkdir()
    (folder / "project.json").write_text(json.dumps(
        {"pipeline_type": "shorts-factory", "title": "Shorts batch shorts-space-batch-02"}), encoding="utf-8")
    assert list_batches(tmp_path)[0]["title"] == "Space Batch 02"


def test_list_batches_newest_activity_first(tmp_path):
    import os
    from studio.batches import list_batches

    a = create_batch(tmp_path, "Older", "youtube_shorts")
    b = create_batch(tmp_path, "Newer", "youtube_shorts")
    os.utime(tmp_path / a / "project.json", (1000, 1000))
    os.utime(tmp_path / a / "artifacts" / "batch_spec.json", (1000, 1000))
    assert [r["id"] for r in list_batches(tmp_path)] == [b, a]


# ---- draft saves (typed topics that are not researched yet) -----------------------


def test_draft_stage_saves_incomplete_topics(tmp_path):
    bid = create_batch(tmp_path, "Draft", "youtube_shorts")
    assert edit_videos(tmp_path / bid, [{"id": "v1", "topic": "just typed"}], "draft") == []
    assert load_spec(tmp_path / bid)["videos"][0]["topic"] == "just typed"


def test_draft_stage_still_rejects_real_errors(tmp_path):
    bid = create_batch(tmp_path, "Draft2", "youtube_shorts")
    assert edit_videos(tmp_path / bid, [{"id": "../x", "topic": "t"}], "draft")
    assert edit_videos(tmp_path / bid, [{"id": "v1", "topic": "t", "hook_type": "bogus"}], "draft")
    assert edit_videos(tmp_path / bid, [{"id": "v1", "topic": "a"}, {"id": "v1", "topic": "b"}], "draft")


def test_mixed_draft_keeps_complete_topics_approvable(tmp_path):
    bid = create_batch(tmp_path, "Mixed", "youtube_shorts")
    complete = {"id": "v1", "topic": "New topic", "hook_type": "question", "hook_text": "h?", "topic_sources": ["https://new.example/1"]}
    typed = {"id": "v2", "topic": "only typed"}
    assert edit_videos(tmp_path / bid, [complete, typed], "draft") == []
    saved = load_spec(tmp_path / bid)["videos"]
    assert saved[0]["claims"] == [{"text": "New topic", "sources": ["https://new.example/1"]}]
    complete["topic"], complete["topic_sources"] = "Newer topic", ["https://newer.example/2"]
    edit_videos(tmp_path / bid, [complete, typed], "draft")
    assert load_spec(tmp_path / bid)["videos"][0]["claims"] == [{"text": "Newer topic", "sources": ["https://newer.example/2"]}]
