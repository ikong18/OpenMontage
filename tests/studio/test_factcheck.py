import json

import pytest

from lib.shorts.spec import load_spec, save_spec
from studio.batches import create_batch
from studio.factcheck import (VERDICTS, apply_check, check_inputs, check_view, claims_flagged, load_check,
                              pick_checker, save_check, video_hash)

SRC = "https://science.nasa.gov/venus/venus-facts/"


def _video(**extra):
    base = {"id": "v1", "narration": "Venus is hot. It is about 467 C.",
            "claims": [{"text": "Venus is hot", "sources": [SRC]}, {"text": "About 467 C", "sources": [SRC]}]}
    base.update(extra)
    return base


def _result(vid="v1", verdicts=("supported", "supported"), issues=()):
    return {"id": vid, "claims": [{"index": i, "verdict": v, "note": "n", "quote": "q"} for i, v in enumerate(verdicts)],
            "narration_issues": list(issues)}


@pytest.fixture
def project(tmp_path):
    bid = create_batch(tmp_path, "FC", "youtube_shorts")
    spec = load_spec(tmp_path / bid)
    spec["videos"] = [_video()]
    save_spec(tmp_path / bid, spec)
    return tmp_path / bid


def test_check_inputs_number_the_claims():
    inputs = check_inputs([_video()])
    assert inputs["videos"][0]["claims"][1] == {"index": 1, "text": "About 467 C", "sources": [SRC]}
    assert inputs["videos"][0]["narration"].startswith("Venus is hot")


def test_apply_check_keeps_a_record_with_writer_checker_and_hash(project):
    spec = load_spec(project)
    record = apply_check(spec, [_result(verdicts=("supported", "unsupported"))], "claude", "codex")
    assert record["writer"] == "claude" and record["checker"] == "codex"
    assert record["videos"]["v1"]["hash"] == video_hash(spec["videos"][0])
    assert claims_flagged(record["videos"]["v1"]) == 1


@pytest.mark.parametrize("bad", [
    {"id": "v1", "claims": [{"index": 0, "verdict": "maybe", "note": "", "quote": ""}], "narration_issues": []},
    {"id": "v9", "claims": [], "narration_issues": []},
])
def test_apply_check_rejects_bad_answers(project, bad):
    with pytest.raises(ValueError):
        apply_check(load_spec(project), [bad], "claude", "codex")


def test_apply_check_requires_a_verdict_for_every_claim(project):
    with pytest.raises(ValueError, match="claim"):
        apply_check(load_spec(project), [_result(verdicts=("supported",))], "claude", "codex")


def test_view_marks_a_check_stale_when_the_script_changes(project):
    spec = load_spec(project)
    save_check(project, apply_check(spec, [_result()], "claude", "codex"))
    assert check_view(project, spec)["videos"]["v1"]["stale"] is False
    spec["videos"][0]["narration"] = "Venus is hot. It is about 470 C."
    assert check_view(project, spec)["videos"]["v1"]["stale"] is True


def test_view_without_a_check_is_empty_and_corrupt_files_are_ignored(project):
    assert check_view(project, load_spec(project)) == {"checked": False, "videos": {}}
    (project / "artifacts").mkdir(exist_ok=True)
    (project / "artifacts" / "fact_check.json").write_text("{nope", encoding="utf-8")
    assert load_check(project) is None


def test_pick_checker_prefers_the_other_ai_and_says_when_it_is_the_same():
    both = {"claude": True, "codex": True}
    assert pick_checker("claude", both) == ("codex", False)
    assert pick_checker("codex", both) == ("claude", False)
    assert pick_checker("claude", {"claude": True, "codex": False}) == ("claude", True)
    assert VERDICTS == ("supported", "partly", "unsupported", "unverifiable")
