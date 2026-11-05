"""`shorts_batch.py voice` end to end with the real Piper voice and the real Whisper model."""

import json
import shutil

import pytest

pytest.importorskip("faster_whisper")

from lib.shorts.batch import read_batch  # noqa: E402
from lib.shorts.spec import save_spec  # noqa: E402
from scripts import shorts_batch  # noqa: E402

NARRATION = "Venus is hotter than Mercury. Its thick air traps heat, so the surface stays near four hundred sixty seven degrees."


@pytest.fixture
def project(tmp_path):
    if shutil.which("ffmpeg") is None or shutil.which("piper") is None:
        pytest.skip("needs ffmpeg and the piper command on PATH")
    folder = tmp_path / "projects" / "voicetest"
    folder.mkdir(parents=True)
    save_spec(folder, {
        "project_id": "voicetest", "voice": {"model": "en_US-lessac-medium", "length_scale": 0.95},
        "sources": {},
        "videos": [{"id": "v1", "topic": "t", "hook_type": "comparison", "hook_text": "h", "narration": NARRATION,
                    "claims": [{"text": "c", "sources": ["https://example.com/a"]}]}],
    })
    return folder


def test_voice_records_how_the_captions_were_timed(project, capsys):
    assert shorts_batch.main(["voice", str(project)]) == 0
    out = capsys.readouterr().out
    entry = read_batch(project)["videos"][0]
    captions = json.loads((project / entry["captions"]).read_text(encoding="utf-8"))
    assert len(captions) == len(NARRATION.split())
    assert all(b["startMs"] >= a["startMs"] for a, b in zip(captions, captions[1:]))
    assert entry["timing"] == "whisper" and "timing: whisper" in out
    assert captions[-1]["endMs"] / 1000 <= entry["audio_duration"] + 0.1


def test_the_estimate_can_be_forced(project, capsys, monkeypatch):
    monkeypatch.setenv("OPENMONTAGE_ALIGNER", "estimate")
    assert shorts_batch.main(["voice", str(project)]) == 0
    assert read_batch(project)["videos"][0]["timing"] == "estimate"
