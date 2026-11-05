"""The footage step end to end with a real AI, real searches, real downloads and real contact sheets, on the topic that
once got a mosquito lecture: it must come back with footage that shows bees (verified and re-checked), never the lecture."""

import json
import shutil

import pytest

from lib.shorts.spec import load_spec, save_spec
from studio.actions import Cli, plan_for
from studio.batches import create_batch
from studio.footage import check_view, load_check, render_blockers
from studio.jobs import JobContext

pytestmark = pytest.mark.live_api  # real, billable AI calls and downloads: needs OPENMONTAGE_ALLOW_NETWORK=1

LECTURE_CLIPS = {"wikimedia_78874851"}  # the Dutch lecture series on mosquitoes that was used by mistake
NARRATION = ("Scientists thought this giant bee was gone for good, until it turned up again after thirty-eight years. "
             "Wallace's giant bee was rediscovered in Indonesia in twenty nineteen. It can grow up to four centimeters long, "
             "about four times the size of a honeybee. It nests inside termite mounds on trees.")


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("piper") is None, reason="needs ffmpeg and the piper command on PATH")
def test_the_giant_bee_video_gets_bee_footage_or_nothing(ai, provider, tmp_path):
    bid = create_batch(tmp_path, "Real Bee", "facebook_reels")
    project = tmp_path / bid
    spec = load_spec(project)
    spec["videos"] = [{"id": "v1", "topic": "Wallace's giant bee was rediscovered after 38 years", "hook_type": "question",
                       "hook_text": "Scientists thought this giant bee was gone for good.", "narration": NARRATION,
                       "claims": [{"text": "Wallace's giant bee was rediscovered in 2019", "sources": ["https://en.wikipedia.org/wiki/Wallace%27s_giant_bee"]}],
                       "title": "A bee lost for 38 years", "description": "Wallace's giant bee was rediscovered in 2019.", "tags": ["bee"]}]
    save_spec(project, spec)
    log = []

    class Ctx(JobContext):
        def log(self, line):
            log.append(line)
            super().log(line)

    plan_for("footage", project, ai, {}, Cli(), reviewer=(ai, provider, provider))(Ctx(tmp_path / "job.log"))

    spec = load_spec(project)
    video = spec["videos"][0]
    print(f"\n[{provider}] subject: {video['footage_subject']}")
    for key, source in spec["sources"].items():
        v = source["verified"]
        print(f"[{provider}] {key}: {v['match']} - {v['shows']} ({len(v['relevant_frames'])} frames) <- {v.get('title', '')[:70]}")
    for cut in video["cuts"]:
        print(f"[{provider}]   cut {cut['source']} {cut['in']}+{cut['dur']}: {cut['shows']}")
    check = check_view(project, spec)["videos"]["v1"]
    print(f"[{provider}] second look: {[(c['verdict'], c['shows'][:50]) for c in check['cuts']]}")

    assert video["cuts"] and all(c["shows"] for c in video["cuts"])
    assert not any(s["clip_id"] in LECTURE_CLIPS for s in spec["sources"].values()), "the mosquito lecture must never be used"
    assert all(s["verified"]["match"] in ("exact", "related") for s in spec["sources"].values())
    assert check["blocked"] == 0 and not check["stale"], check
    assert render_blockers(project, spec, ["v1"]) == []
    seen = " ".join([c["shows"] for c in video["cuts"]] + [c["shows"] for c in check["cuts"]]).lower()
    assert "bee" in seen, f"neither the cutting AI nor the second AI said it saw a bee: {seen}"
    assert not any(w in seen for w in ("lecture", "podium", "speaker")), seen
    if any(s["verified"]["match"] == "related" for s in spec["sources"].values()):
        assert "Footage is illustrative" in video["description"], "related footage must be disclosed"
    assert load_check(project)["videos"]["v1"]["reviewer"] == provider
    assert json.loads((project / "artifacts" / "footage_check.json").read_text(encoding="utf-8"))["videos"]["v1"]["cuts"]
