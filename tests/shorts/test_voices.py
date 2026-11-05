"""The Kokoro voice engine, with the real model: it must sound like the words it was given (checked by listening with Whisper)."""

import re
import shutil

import pytest

pytest.importorskip("kokoro_onnx")
soundfile = pytest.importorskip("soundfile")

from lib.shorts.batch import read_batch  # noqa: E402
from lib.shorts.spec import save_spec  # noqa: E402
from lib.shorts.voices import ENGINES, kokoro_available, kokoro_voices, synthesize_kokoro  # noqa: E402
from lib.shorts.whisper_align import _model  # noqa: E402
from scripts import shorts_batch  # noqa: E402

pytestmark = pytest.mark.skipif(not kokoro_available(), reason="Kokoro model files not downloaded (models/kokoro)")

TEXT = ("Venus is hotter than Mercury. Its thick air traps heat, so the surface stays near "
        "four hundred sixty seven degrees. That is hot enough to melt lead.")


ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def _spell(n: int) -> str:
    """Whisper writes numbers as digits; the narration spells them out, so compare like with like."""
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + (" " + ONES[n % 10] if n % 10 else "")
    return ONES[n // 100] + " hundred" + (" " + _spell(n % 100) if n % 100 else "")


def _tokens(text):
    text = re.sub(r"(\d{1,3})", lambda m: _spell(int(m.group(1))), text.lower().replace("'", ""))
    return [t for t in re.sub(r"[^a-z0-9 ]", " ", text).split() if t]


def word_error_rate(reference: str, heard: str) -> float:
    ref, hyp = _tokens(reference), _tokens(heard)
    d = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, d[0] = d[0], i
        for j, h in enumerate(hyp, 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (r != h))
            prev, d[j] = d[j], cur
    return d[len(hyp)] / max(len(ref), 1)


def _heard(wav) -> str:
    segments, _ = _model("base").transcribe(str(wav), language="en", beam_size=5, condition_on_previous_text=False)
    return " ".join(s.text for s in segments)


def test_engines_and_voices_are_listed():
    assert ENGINES == ("piper", "kokoro")
    voices = kokoro_voices()
    assert "af_heart" in voices and "am_michael" in voices and not any(v.startswith(("zf_", "jf_")) for v in voices)


def test_kokoro_speaks_the_words_it_was_given(tmp_path):
    out = tmp_path / "k.wav"
    seconds = synthesize_kokoro({"engine": "kokoro", "voice": "af_heart", "speed": 1.0}, TEXT, out)
    data, rate = soundfile.read(str(out))
    assert abs(len(data) / rate - seconds) < 0.05 and 6 < seconds < 25
    assert abs(data).max() > 0.05 and abs(data).max() <= 1.0
    wer = word_error_rate(TEXT, _heard(out))
    print(f"\nkokoro af_heart WER {wer:.3f}")
    assert wer <= 0.15


def test_speed_changes_the_length(tmp_path):
    slow = synthesize_kokoro({"voice": "af_heart", "speed": 0.9}, TEXT, tmp_path / "s.wav")
    fast = synthesize_kokoro({"voice": "af_heart", "speed": 1.2}, TEXT, tmp_path / "f.wav")
    assert fast < slow * 0.9


def test_a_long_narration_is_not_cut_off(tmp_path):
    long_text = " ".join([TEXT] * 4)
    out = tmp_path / "long.wav"
    synthesize_kokoro({"voice": "am_michael"}, long_text, out)
    assert word_error_rate(long_text, _heard(out)) <= 0.2


def test_voice_command_with_kokoro_records_free_tool_and_timing(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("needs ffmpeg on PATH")
    folder = tmp_path / "projects" / "kvoice"
    folder.mkdir(parents=True)
    save_spec(folder, {
        "project_id": "kvoice", "voice": {"engine": "kokoro", "voice": "af_heart", "speed": 1.0}, "sources": {},
        "videos": [{"id": "v1", "topic": "t", "hook_type": "comparison", "hook_text": "h", "narration": TEXT,
                    "claims": [{"text": "c", "sources": ["https://example.com/a"]}]}],
    })
    assert shorts_batch.main(["voice", str(folder)]) == 0
    entry = read_batch(folder)["videos"][0]
    assert entry["timing"] == "whisper" and entry["audio_duration"] > 5
    assert {c["tool"] for c in entry["cost_entries"]} >= {"kokoro_onnx"}
    assert all(c["cost_usd"] == 0 for c in entry["cost_entries"])
