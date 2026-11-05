"""Real AI looking at real pictures: is the footage about the topic?

The negative case is the actual failure that reached an owner's video: a Dutch lecture about mosquitoes was used for
"Wallace's giant bee". The positive case is a real Wikimedia bee clip.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from lib.shorts.sheets import make_contact_sheet, probe_video
from studio.ai import SCHEMAS, TASK_TOOLS

pytestmark = pytest.mark.live_api  # real, billable AI calls and downloads: needs OPENMONTAGE_ALLOW_NETWORK=1

REPO = Path(__file__).resolve().parents[2]
PROJECT = REPO / "projects" / "shorts-extinct-animals-but-returned-or-seen-aga"
LECTURE = PROJECT / "assets" / "video" / "wikimedia_78874851.webm"
SUBJECT = "Wallace's giant bee (Megachile pluto), the world's largest bee, or at least a large wild bee"
NARRATION = "Scientists thought this giant bee was gone for good, until it turned up again after thirty-eight years."
BEE_CLIP = "wikimedia_116331312"  # "Flying bee on flower in slow motion", 17 s


def _sheet_inputs(video: Path, out: Path, key: str, subject: str) -> dict:
    sheet = make_contact_sheet(video, out, step=4.0 if probe_video(video)["duration"] <= 120 else 28.0)
    info = probe_video(video)
    return {"subject": subject, "narration": NARRATION,
            "sources": [{"key": key, "sheet": sheet["path"], "step": sheet["step"], "cols": sheet["cols"], "duration": info["duration"]}]}


@pytest.mark.skipif(not LECTURE.is_file(), reason="needs the owner's batch with the lecture clip (projects/ is not in git)")
def test_the_mosquito_lecture_is_rejected_for_a_giant_bee_video(ai, provider, tmp_path):
    inputs = _sheet_inputs(LECTURE, tmp_path / "lecture.png", "lecture", SUBJECT)
    result = ai.run("footage_verify", inputs, SCHEMAS["footage_verify"], TASK_TOOLS["footage_verify"], add_dirs=[tmp_path])
    item = result["items"][0]
    print(f"\n[{provider}] lecture: {item['match']} frames={item['relevant_frames']} shows={item['shows']!r} note={item['note']!r}")
    assert item["match"] == "unrelated", item
    assert len(item["relevant_frames"]) <= 1, "a lecture must not offer usable frames"


def _bee_clip(tmp_path) -> Path:
    from tools.video.stock_sources import all_sources
    from tools.video.stock_sources.base import SearchFilters

    source = {s.name: s for s in all_sources()}["wikimedia"]
    candidate = next(c for c in source.search("Flying bee on flower in slow motion", SearchFilters(kind="video", per_page=12))
                     if c.clip_id == BEE_CLIP)
    return Path(source.download(candidate, tmp_path / f"{BEE_CLIP}.webm"))


def test_a_real_bee_clip_is_accepted_and_its_frames_are_listed(ai, provider, tmp_path):
    clip = _bee_clip(tmp_path)
    inputs = _sheet_inputs(clip, tmp_path / "bee.png", "bee", SUBJECT)
    result = ai.run("footage_verify", inputs, SCHEMAS["footage_verify"], TASK_TOOLS["footage_verify"], add_dirs=[tmp_path])
    item = result["items"][0]
    print(f"\n[{provider}] bee: {item['match']} frames={item['relevant_frames']} shows={item['shows']!r}")
    assert item["match"] in ("exact", "related"), item
    assert len(item["relevant_frames"]) >= 2
    assert "bee" in item["shows"].lower()


def _frame(video: Path, seconds: float, out: Path) -> Path:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{seconds}", "-i", str(video), "-frames:v", "1", "-vf", "scale=640:-1", str(out)], check=True)
    return out


@pytest.mark.skipif(not LECTURE.is_file() or shutil.which("ffmpeg") is None, reason="needs the lecture clip and ffmpeg")
def test_the_final_frame_review_separates_bee_frames_from_lecture_frames(ai, provider, tmp_path):
    clip = _bee_clip(tmp_path)
    frames = [_frame(clip, 9, tmp_path / "f0.jpg"), _frame(LECTURE, 197, tmp_path / "f1.jpg"),
              _frame(clip, 13, tmp_path / "f2.jpg"), _frame(LECTURE, 253, tmp_path / "f3.jpg")]
    inputs = {"videos": [{"id": "v1", "subject": SUBJECT, "narration": NARRATION,
                          "cuts": [{"index": i, "frame": str(f)} for i, f in enumerate(frames)]}],
              "images": [str(f) for f in frames]}
    result = ai.run("footage_review", inputs, SCHEMAS["footage_review"], TASK_TOOLS["footage_review"], add_dirs=[tmp_path])
    verdicts = {c["index"]: (c["verdict"], c["shows"]) for c in result["items"][0]["cuts"]}
    print(f"\n[{provider}] review: {verdicts}")
    assert verdicts[0][0] in ("yes", "partly") and verdicts[2][0] in ("yes", "partly")
    assert verdicts[1][0] == "no" and verdicts[3][0] == "no", verdicts
