"""The footage step must verify before it uses. Control flow with scripted AI answers; what the real AIs say about real
pictures is tested in tests/real_ai/test_real_footage_relevance.py (including the mosquito-lecture case)."""

import json
from pathlib import Path

import pytest

import studio.footage_step as step_module
from lib.shorts.batch import write_batch
from lib.shorts.spec import load_spec, save_spec
from studio.actions import plan_for
from studio.batches import create_batch
from studio.footage import load_check, render_blockers
from studio.jobs import JobContext

SUBJECT = "Wallace's giant bee (Megachile pluto), the world's largest bee"
GOOD_FRAMES = [2, 3, 4, 5, 6, 7]


class ScriptedAi:
    def __init__(self, handlers):
        self.handlers, self.calls = handlers, []

    def run(self, task, inputs, schema, tools, add_dirs=None):
        self.calls.append({"task": task, "inputs": inputs, "tools": tools, "add_dirs": add_dirs})
        handler = self.handlers[task]
        result = handler(inputs) if callable(handler) else handler
        if isinstance(result, Exception):
            raise result
        return result


class Cli:
    """Plays scripts/shorts_batch.py: search results, downloads (as files) and contact sheets, from tables."""

    def __init__(self, project, results, durations=None, refuse=(), shared=False):
        self.project, self.results, self.durations, self.refuse, self.calls = project, results, durations or {}, set(refuse), []
        self.shared = shared

    def run(self, ctx, args, capture=False):
        self.calls.append(list(args))
        cmd, project = args[0], self.project
        if cmd == "voice":
            write_batch(project, {"videos": [{"id": v["id"], "audio_duration": 10.0} for v in load_spec(project)["videos"]]})
            return 0, ""
        if cmd == "search":
            return 0, "\n".join(f"{cid}\t{title}\t{size}\t{dur}" for cid, title, size, dur in self.results)
        if cmd == "fetch":
            clip_id = args[args.index("--clip-id") + 1]
            key = args[args.index("--key") + 1]
            if clip_id in self.refuse:
                return 1, "error: file size is unknown; pass --allow-unknown-size to download anyway"
            (project / "assets" / "video").mkdir(parents=True, exist_ok=True)
            name = clip_id if self.shared else key
            (project / "assets" / "video" / f"{name}.webm").write_bytes(b"clip")
            spec = load_spec(project)
            spec["sources"][key] = {"path": f"assets/video/{name}.webm", "clip_id": clip_id, "provider": "wikimedia",
                                    "mode": "fill", "license": "CC BY 3.0", "url": f"https://commons.example/{clip_id}"}
            save_spec(project, spec)
            return 0, f"fetched {clip_id}"
        if cmd == "sheet":
            key = args[args.index("--key") + 1]
            (project / "review").mkdir(exist_ok=True)
            (project / "review" / f"sheet_{key}.png").write_bytes(b"png")
            duration = self.durations.get(key, 60.0)
            return 0, json.dumps({"key": key, "path": str(project / "review" / f"sheet_{key}.png"), "step": 4.0, "cols": 6,
                                  "frames": 15, "width": 1280, "height": 720, "duration": duration})
        return 0, ""


def _ctx(tmp_path):
    return JobContext(tmp_path / "job.log")


@pytest.fixture(autouse=True)
def _fake_frames(monkeypatch):
    def fake_extract(video, seconds, out, width=640):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"jpg")
        return out

    monkeypatch.setattr(step_module, "extract_frame", fake_extract)


@pytest.fixture
def batch(tmp_path):
    bid = create_batch(tmp_path, "FF", "youtube_shorts")
    project = tmp_path / bid
    spec = load_spec(project)
    spec["videos"] = [{"id": "v1", "topic": "Wallace's giant bee", "hook_type": "question", "hook_text": "h?",
                       "narration": "Scientists thought this giant bee was gone for good.", "claims": [{"text": "c", "sources": ["https://e.com/a"]}],
                       "title": "T", "description": "A fact.", "tags": ["a"]}]
    save_spec(project, spec)
    return project


def queries(inputs):
    return {"items": [{"id": "v1", "subject": SUBJECT, "stand_ins": "another large wild bee, such as a leafcutter bee", "queries": [
        {"source": "wikimedia", "query": "Megachile pluto", "tier": "exact"},
        {"source": "wikimedia", "query": "giant bee", "tier": "related"}]}]}


def pick(*clip_ids, match="related"):
    def handler(inputs):
        return {"items": [{"id": "v1", "picks": [{"clip_id": c, "match": match, "why": "title says bee"} for c in clip_ids]}]}
    return handler


def verify(table):
    def handler(inputs):
        key = inputs["sources"][0]["key"]
        return {"items": [{"key": key, **table[next(name for name in table if key.endswith(name))]}]}
    return handler


def good(match="related", shows="a leafcutter bee on a flower", frames=None):
    return {"match": match, "shows": shows, "relevant_frames": frames if frames is not None else GOOD_FRAMES, "note": ""}


BAD = {"match": "unrelated", "shows": "a man giving a lecture on a stage", "relevant_frames": [], "note": "a talk"}


def cuts_ok(inputs):
    return {"items": [{"id": "v1", "cuts": [{"source": inputs["sources"][0]["key"], "in": 8.0 + 4 * i, "dur": 3.0, "shows": f"bee {i}"} for i in range(4)]}]}


def review(*verdicts):
    return lambda inputs: {"items": [{"id": "v1", "cuts": [{"index": i, "verdict": v, "shows": f"frame {i}"} for i, v in enumerate(verdicts)]}]}


def run(batch, tmp_path, ai, cli, reviewer, params=None):
    plan_for("footage", batch, ai, params or {}, cli, reviewer=reviewer)(_ctx(tmp_path))


def handlers(**over):
    base = {"footage_queries": queries, "footage_pick": pick("c_bee"), "footage_verify": verify({"c_bee": good()}),
            "footage_cuts": cuts_ok, "footage_review": review("yes", "yes", "partly", "yes")}
    base.update(over)
    return base


RESULTS = [("c_lecture", "Hoe hebben muggen het verloop van de wereldgeschiedenis bepaald (2-5)", "180.0", "300"),
           ("c_bee", "Flying bee on flower in slow motion", "6.0", "17"),
           ("c_huge", "Giant bee documentary", "900.0", "300")]


# ---- the happy path and what it records ----------------------------------------------------------------------------


def test_verified_footage_is_used_recorded_disclosed_and_reviewed_by_the_other_ai(batch, tmp_path):
    ai, other = ScriptedAi(handlers()), ScriptedAi(handlers())
    run(batch, tmp_path, ai, Cli(batch, RESULTS), reviewer=(other, "codex", "claude"))
    spec = load_spec(batch)
    video = spec["videos"][0]
    verified = spec["sources"]["v1_c_bee"]["verified"]
    assert verified["match"] == "related" and verified["relevant_frames"] == GOOD_FRAMES and verified["step"] == 4
    assert all(c["shows"] for c in video["cuts"]) and len(video["cuts"]) == 4
    assert video["footage_subject"] == SUBJECT
    assert video["description"] == "A fact. Footage is illustrative (it shows a related subject, not the exact one): a leafcutter bee on a flower."
    record = load_check(batch)["videos"]["v1"]
    assert record["reviewer"] == "codex" and [c["verdict"] for c in record["cuts"]] == ["yes", "yes", "partly", "yes"]
    assert [c["task"] for c in other.calls] == ["footage_review"]
    assert "footage_review" not in [c["task"] for c in ai.calls]
    review_call = other.calls[0]
    assert review_call["tools"] == ["Read"] and review_call["add_dirs"] == [batch / "review"]
    assert len(review_call["inputs"]["images"]) == 12, "three real frames (start, middle, end) for each of the four shots"
    assert review_call["inputs"]["videos"][0]["stand_ins"].startswith("another large wild bee")
    assert load_spec(batch)["videos"][0]["footage_stand_ins"].startswith("another large wild bee")


def test_oversize_results_never_reach_the_ai_and_unknown_ids_are_ignored(batch, tmp_path):
    ai = ScriptedAi(handlers(footage_pick=lambda inputs: {"items": [{"id": "v1", "picks": [
        {"clip_id": "invented", "match": "exact", "why": "x"}, {"clip_id": "c_bee", "match": "related", "why": "y"}]}]}))
    run(batch, tmp_path, ai, Cli(batch, RESULTS), reviewer=(ai, "claude", "claude"))
    shown = [c["clip_id"] for c in next(c for c in ai.calls if c["task"] == "footage_pick")["inputs"]["videos"][0]["candidates"]]
    assert "c_huge" not in shown and {"c_lecture", "c_bee"} <= set(shown)
    assert load_spec(batch)["videos"][0]["cuts"]


# ---- the failure that reached the owner: an unrelated clip ---------------------------------------------------------------


def test_a_clip_that_does_not_show_the_subject_is_deleted_and_the_next_one_is_tried(batch, tmp_path):
    ai = ScriptedAi(handlers(footage_pick=pick("c_lecture", "c_bee"), footage_verify=verify({"c_lecture": BAD, "c_bee": good()})))
    cli = Cli(batch, RESULTS)
    run(batch, tmp_path, ai, cli, reviewer=(ai, "claude", "claude"))
    spec = load_spec(batch)
    assert not [k for k in spec["sources"] if k.endswith("c_lecture")]
    assert not list((batch / "assets" / "video").glob("*c_lecture*"))
    assert {c["source"] for c in spec["videos"][0]["cuts"]} == {"v1_c_bee"}


def test_when_every_candidate_is_unrelated_the_step_fails_and_leaves_nothing_behind(batch, tmp_path):
    ai = ScriptedAi(handlers(footage_pick=pick("c_lecture"), footage_verify=verify({"c_lecture": BAD})))
    before = (batch / "artifacts" / "batch_spec.json").read_bytes()
    with pytest.raises(RuntimeError) as exc:
        run(batch, tmp_path, ai, Cli(batch, RESULTS), reviewer=(ai, "claude", "claude"))
    message = str(exc.value)
    assert "No footage that shows" in message and "lecture" in message
    assert load_spec(batch)["sources"] == {} and not list((batch / "assets" / "video").glob("*.webm"))
    assert not load_spec(batch)["videos"][0].get("cuts")
    assert before


def test_when_nothing_looks_relevant_the_ai_picks_nothing_and_the_step_says_so(batch, tmp_path):
    ai = ScriptedAi(handlers(footage_pick=lambda inputs: {"items": [{"id": "v1", "picks": []}]}))
    cli = Cli(batch, RESULTS)
    with pytest.raises(RuntimeError, match="No footage that shows"):
        run(batch, tmp_path, ai, cli, reviewer=(ai, "claude", "claude"))
    assert not [c for c in cli.calls if c[0] == "fetch"], "nothing may be downloaded when nothing was picked"


def test_a_clip_with_too_few_verified_frames_is_not_enough_alone(batch, tmp_path):
    ai = ScriptedAi(handlers(footage_pick=pick("c_bee"), footage_verify=verify({"c_bee": good(frames=[2])})))
    with pytest.raises(RuntimeError, match="too little"):
        run(batch, tmp_path, ai, Cli(batch, RESULTS), reviewer=(ai, "claude", "claude"))


def test_two_partly_useful_clips_can_add_up(batch, tmp_path):
    results = RESULTS + [("c_bee2", "Bee on apple", "5.0", "116")]
    ai = ScriptedAi(handlers(footage_pick=pick("c_bee", "c_bee2"),
                             footage_verify=verify({"c_bee2": good(frames=[2, 3, 4, 5]), "c_bee": good(frames=[2, 3])}),
                             footage_cuts=lambda inputs: {"items": [{"id": "v1", "cuts": [
                                 {"source": "v1_c_bee", "in": 8.0, "dur": 3.0, "shows": "a"}, {"source": "v1_c_bee", "in": 12.0, "dur": 3.0, "shows": "b"},
                                 {"source": "v1_c_bee2", "in": 8.0, "dur": 3.0, "shows": "c"}, {"source": "v1_c_bee2", "in": 12.0, "dur": 3.0, "shows": "d"}]}]}))
    run(batch, tmp_path, ai, Cli(batch, results), reviewer=(ai, "claude", "claude"))
    assert {c["source"] for c in load_spec(batch)["videos"][0]["cuts"]} == {"v1_c_bee", "v1_c_bee2"}


# ---- cuts must lie on verified frames ------------------------------------------------------------------------------------


def test_cuts_outside_the_verified_frames_are_sent_back_once_with_the_problem(batch, tmp_path):
    attempts = []

    def cuts(inputs):
        attempts.append(inputs.get("problems"))
        if len(attempts) == 1:
            return {"items": [{"id": "v1", "cuts": [{"source": inputs["sources"][0]["key"], "in": 0.0, "dur": 3.0, "shows": "x"}] * 4}]}   # frame 0 is not verified
        return cuts_ok(inputs)

    ai = ScriptedAi(handlers(footage_cuts=cuts))
    run(batch, tmp_path, ai, Cli(batch, RESULTS), reviewer=(ai, "claude", "claude"))
    assert attempts[0] is None and "not on a verified frame" in " ".join(attempts[1])


def test_cuts_that_stay_outside_the_verified_frames_fail_the_step_and_are_not_saved(batch, tmp_path):
    bad = lambda inputs: {"items": [{"id": "v1", "cuts": [{"source": inputs["sources"][0]["key"], "in": 0.0, "dur": 3.0, "shows": "x"}] * 4}]}  # noqa: E731
    ai = ScriptedAi(handlers(footage_cuts=bad))
    with pytest.raises(ValueError, match="verified"):
        run(batch, tmp_path, ai, Cli(batch, RESULTS), reviewer=(ai, "claude", "claude"))
    assert not load_spec(batch)["videos"][0].get("cuts")


def test_a_cut_without_a_description_is_refused(batch, tmp_path):
    nodesc = lambda inputs: {"items": [{"id": "v1", "cuts": [{"source": inputs["sources"][0]["key"], "in": 8.0 + 4 * i, "dur": 3.0, "shows": " "} for i in range(4)]}]}  # noqa: E731
    with pytest.raises(ValueError, match="describe"):
        run(batch, tmp_path, ScriptedAi(handlers(footage_cuts=nodesc)), Cli(batch, RESULTS), reviewer=(ScriptedAi(handlers()), "codex", "claude"))


# ---- the second AI's frame review and the render gate ----------------------------------------------------------------------


def test_a_rejected_shot_is_recorded_and_blocks_the_render_until_the_owner_overrides(batch, tmp_path):
    other = ScriptedAi(handlers(footage_review=review("yes", "no", "yes", "yes")))
    run(batch, tmp_path, ScriptedAi(handlers()), Cli(batch, RESULTS), reviewer=(other, "codex", "claude"))
    spec = load_spec(batch)
    assert "does not show" in " ".join(render_blockers(batch, spec, ["v1"]))
    plan = plan_for("render", batch, ScriptedAi({}), {}, Cli(batch, RESULTS))
    with pytest.raises(ValueError, match="does not show"):
        plan(_ctx(tmp_path))
    from studio.footage import cuts_hash

    spec["videos"][0]["footage_override"] = cuts_hash(spec["videos"][0], spec)
    save_spec(batch, spec)
    assert render_blockers(batch, load_spec(batch), ["v1"]) == []


def test_if_the_reviewer_fails_the_cuts_are_kept_but_the_render_stays_blocked(batch, tmp_path):
    from studio.ai import AiError

    other = ScriptedAi(handlers(footage_review=AiError("not logged in")))
    with pytest.raises(AiError):
        run(batch, tmp_path, ScriptedAi(handlers()), Cli(batch, RESULTS), reviewer=(other, "codex", "claude"))
    assert load_spec(batch)["videos"][0]["cuts"]
    assert "not been checked" in " ".join(render_blockers(batch, load_spec(batch), ["v1"]))


def test_the_review_must_cover_every_shot(batch, tmp_path):
    other = ScriptedAi(handlers(footage_review=review("yes", "yes")))
    with pytest.raises(ValueError, match="every shot"):
        run(batch, tmp_path, ScriptedAi(handlers()), Cli(batch, RESULTS), reviewer=(other, "codex", "claude"))


def test_checking_existing_footage_does_not_search_or_change_the_cuts(batch, tmp_path):
    run(batch, tmp_path, ScriptedAi(handlers()), Cli(batch, RESULTS), reviewer=(ScriptedAi(handlers()), "codex", "claude"))
    before = load_spec(batch)["videos"][0]["cuts"]
    other = ScriptedAi(handlers(footage_review=review("yes", "no", "yes", "yes")))
    cli = Cli(batch, RESULTS)
    ai = ScriptedAi({"footage_verify": verify({"c_bee": good()})})
    run(batch, tmp_path, ai, cli, reviewer=(other, "codex", "claude"), params={"verify_only": True})
    assert load_spec(batch)["videos"][0]["cuts"] == before
    assert not [c for c in cli.calls if c[0] in ("search", "fetch", "voice")], "checking must not search or download"
    assert [c["task"] for c in ai.calls] == ["footage_verify"], "the clip behind the shots is looked at again"
    assert [c["verdict"] for c in load_check(batch)["videos"]["v1"]["cuts"]] == ["yes", "no", "yes", "yes"]


def test_new_footage_clears_an_old_override(batch, tmp_path):
    spec = load_spec(batch)
    spec["videos"][0]["footage_override"] = "stale-hash"
    save_spec(batch, spec)
    run(batch, tmp_path, ScriptedAi(handlers()), Cli(batch, RESULTS), reviewer=(ScriptedAi(handlers()), "codex", "claude"))
    assert not load_spec(batch)["videos"][0].get("footage_override")


# ---- behaviours kept from the first version of the step ---------------------------------------------------------------------


def _two_videos(batch, v2_has_cuts):
    spec = load_spec(batch)
    spec["sources"] = {"old": {"path": "assets/video/old.webm", "clip_id": "oldclip", "provider": "nasa", "license": "x",
                               "url": "https://x", "mode": "fill"}}
    video = dict(spec["videos"][0])
    video2 = {**video, "id": "v2", "topic": "second", "narration": "Second narration here."}
    if v2_has_cuts:
        video2["cuts"] = [{"source": "old", "in": 0, "dur": 4}, {"source": "old", "in": 5, "dur": 4}, {"source": "old", "in": 10, "dur": 4}]
    spec["videos"] = [video, video2]
    (batch / "assets" / "video").mkdir(parents=True, exist_ok=True)
    (batch / "assets" / "video" / "old.webm").write_bytes(b"old")
    save_spec(batch, spec)
    return spec


def test_voice_is_made_first_and_only_for_the_videos_that_need_footage(batch, tmp_path):
    _two_videos(batch, v2_has_cuts=True)
    before = load_spec(batch)["videos"][1]["cuts"]
    ai = ScriptedAi(handlers())
    cli = Cli(batch, RESULTS)
    run(batch, tmp_path, ai, cli, reviewer=(ScriptedAi(handlers()), "codex", "claude"))
    assert cli.calls[0] == ["voice", str(batch), "--video", "v1"]
    asked = next(c for c in ai.calls if c["task"] == "footage_cuts")["inputs"]["videos"]
    assert [v["id"] for v in asked] == ["v1"] and asked[0]["needed_seconds"] == 10.5   # 10.0 s of audio + 0.5 s margin
    assert load_spec(batch)["videos"][1]["cuts"] == before, "a video that already had cuts must be left alone"


def test_cuts_shorter_than_the_narration_are_refused(batch, tmp_path):
    short = lambda inputs: {"items": [{"id": "v1", "cuts": [  # noqa: E731
        {"source": inputs["sources"][0]["key"], "in": 8.0, "dur": 3.0, "shows": "a"}, {"source": inputs["sources"][0]["key"], "in": 12.0, "dur": 3.0, "shows": "b"}]}]}
    with pytest.raises(ValueError, match="needs 10.5"):
        run(batch, tmp_path, ScriptedAi(handlers(footage_cuts=short)), Cli(batch, RESULTS), reviewer=(ScriptedAi(handlers()), "codex", "claude"))
    assert not load_spec(batch)["videos"][0].get("cuts")


def test_a_video_the_ai_returned_no_cuts_for_fails(batch, tmp_path):
    nothing = lambda inputs: {"items": []}  # noqa: E731
    with pytest.raises(ValueError, match="no cuts were returned"):
        run(batch, tmp_path, ScriptedAi(handlers(footage_cuts=nothing)), Cli(batch, RESULTS), reviewer=(ScriptedAi(handlers()), "codex", "claude"))


def test_a_cut_past_the_end_of_the_clip_is_refused(batch, tmp_path):
    def past(inputs):
        key = inputs["sources"][0]["key"]
        return {"items": [{"id": "v1", "cuts": [{"source": key, "in": 18.0 + 0.0, "dur": 3.0, "shows": "x"}] * 4}]}

    ai = ScriptedAi(handlers(footage_cuts=past, footage_verify=verify({"c_bee": good(frames=[2, 3, 4, 5, 6])})))
    with pytest.raises(ValueError, match="past the end"):
        run(batch, tmp_path, ai, Cli(batch, RESULTS, durations={"v1_c_bee": 20.0}), reviewer=(ScriptedAi(handlers()), "codex", "claude"))


def test_a_redo_avoids_the_old_clip_passes_the_note_and_clears_it_afterwards(batch, tmp_path):
    spec = load_spec(batch)
    spec["videos"][0]["redo_note"] = "the shots are dull"
    spec["videos"][0]["redo_avoid"] = ["v1_c_bee"]
    spec["videos"][0]["cuts"] = []
    save_spec(batch, spec)
    results = RESULTS + [("c_bee_two", "Bee on apple blossom", "5.0", "116")]
    ai = ScriptedAi(handlers(footage_pick=pick("c_bee", "c_bee_two"), footage_verify=verify({"c_bee_two": good()})))
    cli = Cli(batch, results)
    run(batch, tmp_path, ai, cli, reviewer=(ScriptedAi(handlers()), "codex", "claude"))
    fetched = [a[a.index("--clip-id") + 1] for a in cli.calls if a[0] == "fetch"]
    assert fetched == ["c_bee_two"]
    assert next(c for c in ai.calls if c["task"] == "footage_queries")["inputs"]["videos"][0]["redo_note"] == "the shots are dull"
    video = load_spec(batch)["videos"][0]
    assert "redo_note" not in video and "redo_avoid" not in video
    export = FakeCliRecorder()
    plan_for("export", batch, ScriptedAi({}), {}, export)(_ctx(tmp_path))
    assert export.calls == [["export", str(batch), "--approved", "v1"]]


class FakeCliRecorder:
    def __init__(self):
        self.calls = []

    def run(self, ctx, args, capture=False):
        self.calls.append(list(args))
        return 0, ""


def test_a_clip_file_shared_by_two_videos_survives_one_video_rejecting_it(batch, tmp_path):
    _two_videos(batch, v2_has_cuts=False)
    spec = load_spec(batch)
    spec["sources"] = {}
    save_spec(batch, spec)

    def queries2(inputs):
        return {"items": [{"id": v["id"], "subject": f"subject of {v['id']}", "queries": [{"source": "wikimedia", "query": "bee", "tier": "exact"}]}
                          for v in inputs["videos"]]}

    def pick2(inputs):
        vid = inputs["videos"][0]["id"]
        return {"items": [{"id": vid, "picks": [{"clip_id": "c_bee", "match": "related", "why": "t"}] if vid == "v1" else
                          [{"clip_id": "c_bee", "match": "related", "why": "t"}, {"clip_id": "c_other", "match": "related", "why": "t"}]}]}

    def verify2(inputs):
        key = inputs["sources"][0]["key"]
        table = {"v1_c_bee": good(), "v2_c_bee": BAD, "v2_c_other": good()}
        return {"items": [{"key": key, **table[key]}]}

    def cuts2(inputs):
        out = []
        for video in inputs["videos"]:
            key = video["allowed_sources"][0]
            out.append({"id": video["id"], "cuts": [{"source": key, "in": 8.0 + 4 * i, "dur": 3.0, "shows": f"s{i}"} for i in range(4)]})
        return {"items": out}

    def review2(inputs):
        return {"items": [{"id": v["id"], "cuts": [{"index": c["index"], "verdict": "yes", "shows": "x"} for c in v["cuts"]]} for v in inputs["videos"]]}

    results = RESULTS + [("c_other", "Another bee", "5.0", "60")]
    ai = ScriptedAi({"footage_queries": queries2, "footage_pick": pick2, "footage_verify": verify2, "footage_cuts": cuts2,
                     "footage_review": review2})
    cli = Cli(batch, results, shared=True)
    run(batch, tmp_path, ai, cli, reviewer=(ai, "claude", "claude"))
    spec = load_spec(batch)
    assert (batch / "assets" / "video" / "c_bee.webm").is_file(), "v1 still needs the file v2 rejected"
    assert {c["source"] for c in spec["videos"][0]["cuts"]} == {"v1_c_bee"} and {c["source"] for c in spec["videos"][1]["cuts"]} == {"v2_c_other"}
    assert "v2_c_bee" not in spec["sources"]


def test_image_paths_given_to_the_ai_are_absolute_even_for_a_relative_project_path(batch, tmp_path, monkeypatch):
    """The AI runs in another folder: a relative path would point at nothing and it would 'see' no picture."""
    import os

    monkeypatch.chdir(batch.parent.parent if batch.parent.parent.exists() else batch.parent)
    relative = Path(os.path.relpath(batch, Path.cwd()))
    ai, other = ScriptedAi(handlers()), ScriptedAi(handlers())
    plan_for("footage", relative, ai, {}, Cli(batch, RESULTS), reviewer=(other, "codex", "claude"))(_ctx(tmp_path))
    checked = 0
    for call in ai.calls + other.calls:
        inputs = call["inputs"]
        paths = [s["sheet"] for s in inputs.get("sources") or [] if isinstance(s, dict) and s.get("sheet")] + list(inputs.get("images") or [])
        assert all(Path(p).is_absolute() for p in paths), (call["task"], paths)
        assert all(Path(d).is_absolute() for d in call["add_dirs"] or [])
        checked += bool(paths)
    assert checked >= 3, "the verify, cuts and review calls all carry images"


def test_a_frame_the_ai_could_not_open_is_an_error_not_a_verdict(batch, tmp_path):
    claims_missing = lambda inputs: {"items": [{"id": "v1", "cuts": [  # noqa: E731
        {"index": i, "verdict": "no", "shows": "The frame file is missing, so its visual content cannot be assessed."} for i in range(4)]}]}
    from studio.ai import AiError

    with pytest.raises(AiError, match="could not open"):
        run(batch, tmp_path, ScriptedAi(handlers()), Cli(batch, RESULTS), reviewer=(ScriptedAi(handlers(footage_review=claims_missing)), "codex", "claude"))


# ---- an already-rendered video with bad footage must not be approved or exported either ---------------------------------


def _rendered_with_bad_footage(batch, tmp_path, monkeypatch, verdicts=("yes", "no", "yes", "yes")):
    import studio.actions as actions
    from studio.footage import apply_review, save_check

    run(batch, tmp_path, ScriptedAi(handlers()), Cli(batch, RESULTS), reviewer=(ScriptedAi(handlers()), "codex", "claude"))
    spec = load_spec(batch)
    save_check(batch, apply_review(spec, [{"id": "v1", "cuts": [{"index": i, "verdict": v, "shows": "x"} for i, v in enumerate(verdicts)]}],
                                   "claude", "codex", {}))
    (batch / "checkpoint_review_queue.json").write_text(json.dumps(
        {"stage": "review_queue", "status": "awaiting_human", "human_approved": False, "timestamp": "2026-01-01T00:00:00+00:00"}), encoding="utf-8")
    monkeypatch.setattr(actions, "step_states", lambda project, running=None, failed=None: {"render": "done"})
    return spec


def test_the_review_cannot_be_approved_while_a_shot_does_not_show_the_subject(batch, tmp_path, monkeypatch):
    from studio.actions import approval_argv

    spec = _rendered_with_bad_footage(batch, tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="does not show"):
        approval_argv(batch, "review_queue")
    from studio.footage import cuts_hash

    spec["videos"][0]["footage_override"] = cuts_hash(spec["videos"][0], spec)
    save_spec(batch, spec)
    assert approval_argv(batch, "review_queue")[-2:] == ["--approve", "review_queue"]


def test_the_review_can_be_approved_when_every_shot_passes(batch, tmp_path, monkeypatch):
    from studio.actions import approval_argv

    _rendered_with_bad_footage(batch, tmp_path, monkeypatch, verdicts=("yes", "partly", "yes", "yes"))
    assert approval_argv(batch, "review_queue")[-1] == "review_queue"


def test_export_refuses_footage_that_was_not_verified(batch, tmp_path, monkeypatch):
    _rendered_with_bad_footage(batch, tmp_path, monkeypatch)
    cli = FakeCliRecorder()
    with pytest.raises(ValueError, match="does not show"):
        plan_for("export", batch, ScriptedAi({}), {}, cli)(_ctx(tmp_path))
    assert cli.calls == []


def test_footage_with_no_check_at_all_cannot_be_exported_either(batch, tmp_path):
    spec = load_spec(batch)
    spec["sources"] = {"s": {"path": "a.webm", "clip_id": "c", "provider": "wikimedia", "license": "x", "url": "https://x", "mode": "fill"}}
    spec["videos"][0]["cuts"] = [{"source": "s", "in": 0, "dur": 3}]
    save_spec(batch, spec)
    cli = FakeCliRecorder()
    with pytest.raises(ValueError, match="not been checked"):
        plan_for("export", batch, ScriptedAi({}), {}, cli)(_ctx(tmp_path))



# ---- the review findings -------------------------------------------------------------------------------------------------------


def test_a_shot_moved_by_hand_is_not_verified_until_the_clip_is_looked_at_again(batch, tmp_path):
    run(batch, tmp_path, ScriptedAi(handlers()), Cli(batch, RESULTS), reviewer=(ScriptedAi(handlers()), "codex", "claude"))
    spec = load_spec(batch)
    spec["videos"][0]["cuts"][0]["in"] = 0.0                       # frame 0 was never verified
    save_spec(batch, spec)
    ai, other = ScriptedAi({"footage_verify": verify({"c_bee": good()})}), ScriptedAi(handlers())
    run(batch, tmp_path, ai, Cli(batch, RESULTS), reviewer=(other, "codex", "claude"), params={"verify_only": True})
    message = " ".join(render_blockers(batch, load_spec(batch), ["v1"]))
    assert "shot 1" in message and "not on footage that was verified" in message


def test_checking_legacy_footage_verifies_its_clips_against_the_videos_subject(batch, tmp_path):
    """Footage chosen before verification existed has no `verified` record: the check creates one, or blocks."""
    spec = load_spec(batch)
    (batch / "assets" / "video").mkdir(parents=True, exist_ok=True)
    (batch / "assets" / "video" / "old.webm").write_bytes(b"old")
    spec["sources"] = {"old": {"path": "assets/video/old.webm", "clip_id": "oldclip", "provider": "wikimedia", "license": "x",
                               "url": "https://x", "mode": "fill"}}
    spec["videos"][0]["cuts"] = [{"source": "old", "in": 8 + 4 * i, "dur": 3} for i in range(4)]
    save_spec(batch, spec)
    ai = ScriptedAi({"footage_verify": verify({"old": BAD})})
    run(batch, tmp_path, ai, Cli(batch, RESULTS, durations={"old": 60.0}), reviewer=(ScriptedAi(handlers(footage_review=review("no", "no", "no", "no"))), "codex", "claude"),
        params={"verify_only": True})
    spec = load_spec(batch)
    assert spec["sources"]["old"]["verified"]["match"] == "unrelated"
    assert spec["videos"][0]["footage_subject"]
    message = " ".join(render_blockers(batch, spec, ["v1"]))
    assert "not on footage that was verified" in message or "does not show" in message


def test_a_long_clip_is_not_verified_from_coarse_frames(batch, tmp_path):
    long_results = [("c_long", "A very long documentary about bees", "100.0", "3000"), ("c_bee", "Bee on flower", "6.0", "40")]
    ai = ScriptedAi(handlers(footage_pick=pick("c_long", "c_bee")))
    cli = Cli(batch, long_results)
    run(batch, tmp_path, ai, cli, reviewer=(ScriptedAi(handlers()), "codex", "claude"))
    shown = [c["clip_id"] for c in next(c for c in ai.calls if c["task"] == "footage_pick")["inputs"]["videos"][0]["candidates"]]
    assert "c_long" not in shown, "a 50 minute video cannot be checked frame by frame"


def test_a_clip_that_needs_a_coarse_sheet_is_rejected(batch, tmp_path):
    ai = ScriptedAi(handlers(footage_pick=pick("c_bee")))
    with pytest.raises(RuntimeError, match="too long to verify"):
        run(batch, tmp_path, ai, Cli(batch, RESULTS, durations={"v1_c_bee": 800.0}), reviewer=(ScriptedAi(handlers()), "codex", "claude"))


def test_a_stand_in_is_never_called_exact_unless_the_ai_says_so_and_the_disclosure_follows(batch, tmp_path):
    ai = ScriptedAi(handlers(footage_verify=verify({"c_bee": good(match="related", shows="an ordinary honey bee on a flower")})))
    run(batch, tmp_path, ai, Cli(batch, RESULTS), reviewer=(ScriptedAi(handlers()), "codex", "claude"))
    verify_call = next(c for c in ai.calls if c["task"] == "footage_verify")["inputs"]
    assert verify_call["subject"] == SUBJECT and verify_call["stand_ins"].startswith("another large wild bee")
    assert "honey bee" in load_spec(batch)["videos"][0]["description"]


def test_a_genuine_no_that_mentions_not_seeing_something_is_kept_and_blocks(batch, tmp_path):
    honest_no = lambda inputs: {"items": [{"id": "v1", "cuts": [  # noqa: E731
        {"index": i, "verdict": "no", "shows": "A man lecturing at a podium; I cannot see any bee."} for i in range(4)]}]}
    other = ScriptedAi(handlers(footage_review=honest_no))
    run(batch, tmp_path, ScriptedAi(handlers()), Cli(batch, RESULTS), reviewer=(other, "codex", "claude"))
    assert load_check(batch)["videos"]["v1"]["reviewer"] == "codex"
    assert "does not show" in " ".join(render_blockers(batch, load_spec(batch), ["v1"]))


def test_the_second_ai_gets_the_start_middle_and_end_of_every_shot(batch, tmp_path, monkeypatch):
    times = []

    def spy(video, seconds, out, width=640):
        times.append(round(seconds, 2))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"jpg")
        return out

    monkeypatch.setattr(step_module, "extract_frame", spy)
    run(batch, tmp_path, ScriptedAi(handlers()), Cli(batch, RESULTS), reviewer=(ScriptedAi(handlers()), "codex", "claude"))
    first = load_spec(batch)["videos"][0]["cuts"][0]
    assert times[:3] == [round(first["in"] + 0.3, 2), round(first["in"] + first["dur"] / 2, 2), round(first["in"] + first["dur"] - 0.3, 2)]
