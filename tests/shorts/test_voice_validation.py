"""Voice settings: pure checks that need no model files."""

from pathlib import Path

import pytest

from lib.shorts.batch import voice_fingerprint
from lib.shorts.spec import validate_spec
from lib.shorts.voices import ENGINES, PIPER_MODELS, validate_voice


def test_engines():
    assert ENGINES == ("piper", "kokoro") and PIPER_MODELS == ("en_US-lessac-medium",)


def test_validate_voice_accepts_the_real_choices():
    assert validate_voice({}) == [] and validate_voice(None) == []
    assert validate_voice({"model": "en_US-lessac-medium", "length_scale": 0.95}) == []  # specs made before engines existed
    assert validate_voice({"engine": "piper", "model": "en_US-lessac-medium"}) == []
    assert validate_voice({"engine": "kokoro", "voice": "af_heart", "speed": 1.05}) == []


@pytest.mark.parametrize("bad", [
    {"engine": "sapi"},
    {"engine": "kokoro", "voice": "nobody"},
    {"engine": "kokoro", "voice": "zf_xiaobei"},
    {"engine": "kokoro", "voice": "af_heart", "speed": 5},
    {"engine": "piper", "model": "C:/anywhere/x.onnx"},
    {"engine": "piper", "model": "--help"},
    {"engine": "piper", "model": {"a": 1}},
    {"engine": "piper", "length_scale": 9},
    "af_heart",
])
def test_validate_voice_rejects_bad_choices(bad):
    assert validate_voice(bad)


def test_the_spec_checks_the_voice():
    spec = {"project_id": "p", "voice": {"engine": "nope"}, "sources": {}, "videos": []}
    assert any("voice" in v for v in validate_spec(spec, Path("p"), "idea"))


def test_a_voice_change_means_new_audio_but_the_default_spelling_does_not():
    video = {"id": "v1", "narration": "Hello."}
    piper = voice_fingerprint({"voice": {"model": "en_US-lessac-medium", "length_scale": 0.95}}, video)
    kokoro = voice_fingerprint({"voice": {"engine": "kokoro", "voice": "af_heart"}}, video)
    assert piper != kokoro
