import json

import pytest

from lib.shorts.spec import load_spec, save_spec
from studio.batches import create_batch
from studio.footage import (VERDICTS, apply_review, check_view, cuts_hash, cut_frames, cut_is_verified,
                            disclosure_for, ensure_disclosure, frame_of, load_check, relevant_seconds,
                            render_blockers, review_inputs, save_check, source_problem, unverified_cuts)

VERIFIED = {"match": "related", "shows": "a leafcutter bee on a flower", "step": 4, "relevant_frames": [2, 3, 4, 7]}


def _cut(start, dur=3.4, source="s"):
    return {"source": source, "in": start, "dur": dur}


def test_frame_math():
    assert frame_of(0, 4) == 0 and frame_of(3.99, 4) == 0 and frame_of(4, 4) == 1 and frame_of(27.9, 28) == 0
    assert cut_frames(_cut(10.0, 3.4), 4) == {2, 3}         # the frames at the start, middle and end of the shot
    assert cut_frames(_cut(8.0, 3.0), 4) == {2}


def test_a_cut_is_verified_only_when_every_sampled_frame_shows_the_subject():
    assert cut_is_verified(_cut(8.0, 3.0), VERIFIED)            # frame 2 is relevant
    assert cut_is_verified(_cut(14.0, 3.0), VERIFIED)           # frames 3 and 4 are both relevant
    assert not cut_is_verified(_cut(0.0, 3.0), VERIFIED)        # frame 0 is not relevant
    assert not cut_is_verified(_cut(15.0, 6.0), VERIFIED)       # reaches frame 5 (20.99), which is not relevant
    assert not cut_is_verified(_cut(20, 3), VERIFIED)
    assert not cut_is_verified(_cut(8, 3), {"match": "related"})  # no verification recorded at all


def test_relevant_seconds_is_what_the_verified_frames_can_supply():
    assert relevant_seconds(VERIFIED) == 16
    assert relevant_seconds({"step": 28, "relevant_frames": [1]}) == 28
    assert relevant_seconds({}) == 0


def test_unverified_cuts_lists_the_shots_the_ai_may_not_use():
    spec = {"sources": {"s": {"verified": VERIFIED}, "t": {}},
            "videos": [{"id": "v1", "cuts": [_cut(8, 3), _cut(0, 3), _cut(8, 3, "t"), _cut(8, 3, "missing")]}]}
    assert [(p["video"], p["index"]) for p in unverified_cuts(spec)] == [("v1", 1), ("v1", 2), ("v1", 3)]


def test_source_problem_rejects_unrelated_or_too_thin_clips():
    assert source_problem({"match": "unrelated", "relevant_frames": [1, 2]}, needed_seconds=5) == "the clip does not show the subject"
    assert "too little" in source_problem({"match": "related", "step": 4, "relevant_frames": [1]}, needed_seconds=12)
    assert source_problem({"match": "exact", "step": 4, "relevant_frames": [1, 2, 3, 4]}, needed_seconds=12) is None
    assert source_problem({"match": "maybe", "step": 4, "relevant_frames": [1]}, needed_seconds=1)


def test_disclosure_is_added_once_and_only_for_related_footage():
    spec = {"sources": {"s": {"verified": VERIFIED}, "e": {"verified": {"match": "exact", "shows": "the bee"}}},
            "videos": [{"id": "v1", "description": "A fact.", "cuts": [_cut(8, 3)]},
                       {"id": "v2", "description": "Other.", "cuts": [_cut(8, 3, "e")]}]}
    assert disclosure_for(spec, spec["videos"][0]) == "Footage is illustrative (it shows a related subject, not the exact one): a leafcutter bee on a flower."
    assert disclosure_for(spec, spec["videos"][1]) is None
    ensure_disclosure(spec)
    ensure_disclosure(spec)
    assert spec["videos"][0]["description"] == "A fact. Footage is illustrative (it shows a related subject, not the exact one): a leafcutter bee on a flower."
    assert spec["videos"][1]["description"] == "Other."


# ---- the second AI's review of the final frames ----------------------------------------------------------------


@pytest.fixture
def project(tmp_path):
    bid = create_batch(tmp_path, "FT", "youtube_shorts")
    spec = load_spec(tmp_path / bid)
    spec["sources"] = {"s": {"path": "a.mp4", "clip_id": "c", "verified": VERIFIED}}
    spec["videos"] = [{"id": "v1", "topic": "bees", "cuts": [_cut(8, 3), _cut(12, 3)]}]
    save_spec(tmp_path / bid, spec)
    return tmp_path / bid


def _review(vid="v1", verdicts=("yes", "yes")):
    return {"id": vid, "cuts": [{"index": i, "verdict": v, "shows": f"shot {i}"} for i, v in enumerate(verdicts)]}


def test_review_inputs_list_the_frame_files_in_order(project):
    spec = load_spec(project)
    frames = {("v1", 0): project / "review" / "footage" / "v1_0.jpg", ("v1", 1): project / "review" / "footage" / "v1_1.jpg"}
    inputs = review_inputs(spec, {"v1": "wild bees on flowers"}, frames)
    assert inputs["images"] == [str(frames[("v1", 0)]), str(frames[("v1", 1)])]
    assert inputs["videos"][0]["subject"] == "wild bees on flowers"
    assert [c["index"] for c in inputs["videos"][0]["cuts"]] == [0, 1]


def test_apply_review_keeps_a_record_with_who_looked_and_which_frames(project):
    spec = load_spec(project)
    record = apply_review(spec, [_review(verdicts=("yes", "no"))], "claude", "codex", {"v1": ["review/footage/v1_0.jpg", "review/footage/v1_1.jpg"]})
    v1 = record["videos"]["v1"]
    assert v1["reviewer"] == "codex" and v1["writer"] == "claude" and v1["hash"] == cuts_hash(spec["videos"][0], spec)
    assert [c["verdict"] for c in v1["cuts"]] == ["yes", "no"] and v1["cuts"][1]["frame"].endswith("v1_1.jpg")


@pytest.mark.parametrize("bad", [
    {"id": "v1", "cuts": [{"index": 0, "verdict": "maybe", "shows": "x"}, {"index": 1, "verdict": "yes", "shows": "x"}]},
    {"id": "v1", "cuts": [{"index": 0, "verdict": "yes", "shows": "x"}]},
    {"id": "v9", "cuts": []},
])
def test_apply_review_rejects_incomplete_or_invalid_answers(project, bad):
    with pytest.raises(ValueError):
        apply_review(load_spec(project), [bad], "claude", "codex", {})


def test_the_view_marks_blocked_and_stale(project):
    spec = load_spec(project)
    save_check(project, apply_review(spec, [_review(verdicts=("yes", "no"))], "a", "b", {}))
    view = check_view(project, spec)["videos"]["v1"]
    assert view["blocked"] == 1 and view["stale"] is False and VERDICTS == ("yes", "partly", "no")
    spec["videos"][0]["cuts"][0]["in"] = 9
    assert check_view(project, spec)["videos"]["v1"]["stale"] is True


def test_render_gate_blocks_unchecked_stale_and_rejected_footage_but_allows_an_owner_override(project):
    spec = load_spec(project)
    assert "not been checked" in " ".join(render_blockers(project, spec, ["v1"]))
    save_check(project, apply_review(spec, [_review(verdicts=("yes", "no"))], "a", "b", {}))
    assert "does not show" in " ".join(render_blockers(project, spec, ["v1"]))
    spec["videos"][0]["footage_override"] = True
    assert render_blockers(project, spec, ["v1"]) == []
    spec["videos"][0]["footage_override"] = False
    save_check(project, apply_review(spec, [_review(verdicts=("yes", "partly"))], "a", "b", {}))
    assert render_blockers(project, spec, ["v1"]) == []                      # "partly" is shown to the owner, not blocked
    spec["videos"][0]["cuts"][0]["in"] = 9
    assert "changed since" in " ".join(render_blockers(project, spec, ["v1"]))


def test_a_damaged_record_is_ignored(project):
    (project / "artifacts").mkdir(exist_ok=True)
    (project / "artifacts" / "footage_check.json").write_text("{nope", encoding="utf-8")
    assert load_check(project) is None
    (project / "artifacts" / "footage_check.json").write_text(json.dumps({"videos": {"v1": "garbage"}}), encoding="utf-8")
    assert check_view(project, load_spec(project))["videos"] == {}
