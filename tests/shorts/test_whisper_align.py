"""WhisperAligner against real audio with known sentence boundaries (real Piper voice, real Whisper model)."""

import shutil
from pathlib import Path

import numpy as np
import pytest

soundfile = pytest.importorskip("soundfile")
pytest.importorskip("faster_whisper")

from lib.shorts.align import SilenceAligner, WordTiming  # noqa: E402
from lib.shorts.whisper_align import WhisperAligner, make_aligner  # noqa: E402
from tools.tool_registry import registry  # noqa: E402

SENTENCES = [
    "The Sun holds ninety nine point eight percent of everything in our solar system.",
    "All eight planets, every moon, every asteroid and comet, together make up the leftover two tenths of a percent.",
    "Our whole planet is a rounding error next to it.",
]
TOLERANCE_MS = 300


def _need_tools():
    if shutil.which("ffmpeg") is None or shutil.which("piper") is None:
        pytest.skip("needs ffmpeg and the piper command on PATH")
    registry.discover()
    tts = registry._tools["piper_tts"]
    if tts.get_status().value != "available":
        pytest.skip("piper_tts unavailable")
    return tts


@pytest.fixture(scope="module")
def narration(tmp_path_factory):
    tts = _need_tools()
    folder = tmp_path_factory.mktemp("align")
    chunks, truth, cursor = [], [], 0.0
    rate = None
    for i, sentence in enumerate(SENTENCES):
        wav = folder / f"s{i}.wav"
        result = tts.execute({"text": sentence, "output_path": str(wav), "model": "en_US-lessac-medium", "length_scale": 0.95})
        assert result.success, result.error
        data, rate = soundfile.read(str(wav))
        data = data if data.ndim == 1 else data[:, 0]
        loud = np.flatnonzero(np.abs(data) > 0.02)
        lead, tail = loud[0] / rate, loud[-1] / rate
        truth.append((cursor + lead, cursor + tail))
        cursor += len(data) / rate
        chunks.append(data)
    out = folder / "all.wav"
    soundfile.write(str(out), np.concatenate(chunks), rate)
    return " ".join(SENTENCES), out, truth


def _sentence_edges(timings: list[WordTiming]):
    edges, cursor = [], 0
    for sentence in SENTENCES:
        n = len(sentence.split())
        chunk = timings[cursor:cursor + n]
        cursor += n
        edges.append((chunk[0].start_ms / 1000, chunk[-1].end_ms / 1000))
    return edges


def test_one_timing_per_narration_word_in_order(narration):
    text, wav, _ = narration
    aligner = WhisperAligner()
    timings = aligner.align(text, wav)
    assert [t.word for t in timings] == text.split()
    assert all(t.end_ms > t.start_ms for t in timings)
    assert all(b.start_ms >= a.start_ms for a, b in zip(timings, timings[1:]))
    assert aligner.method == "whisper"


def test_sentence_edges_match_the_known_boundaries(narration):
    text, wav, truth = narration
    timings = WhisperAligner().align(text, wav)
    for (start, end), (true_start, true_end) in zip(_sentence_edges(timings), truth):
        assert abs(start - true_start) * 1000 <= TOLERANCE_MS, (start, true_start)
        assert abs(end - true_end) * 1000 <= TOLERANCE_MS, (end, true_end)


def test_whisper_is_at_least_as_accurate_as_the_estimate(narration):
    text, wav, truth = narration

    def error(timings):
        return sum(abs(a - b) + abs(c - d) for (a, c), (b, d) in zip(_sentence_edges(timings), truth))

    assert error(WhisperAligner().align(text, wav)) < error(SilenceAligner().align(text, wav))


def test_digits_and_odd_tokens_still_get_one_timing_each(narration):
    _, wav, _ = narration
    odd = "The Sun holds 99.8 percent of everything - about 330,000 Earths - in our solar system."
    aligner = WhisperAligner()
    timings = aligner.align(odd, wav)
    assert len(timings) == len(odd.split()) and all(t.end_ms > t.start_ms for t in timings)
    assert aligner.method == "whisper", "the words around the digits should still match the audio"


def test_unrelated_audio_falls_back_to_the_estimate(tmp_path):
    rate = 16000
    noise = (np.random.default_rng(1).standard_normal(rate * 4) * 0.01).astype("float32")
    wav = tmp_path / "noise.wav"
    soundfile.write(str(wav), noise, rate)
    aligner = WhisperAligner()
    timings = aligner.align("Venus is the hottest planet in the solar system.", wav)
    assert aligner.method == "estimate" and len(timings) == 9


def test_make_aligner_returns_a_whisper_aligner_when_available(monkeypatch):
    monkeypatch.delenv("OPENMONTAGE_ALIGNER", raising=False)
    assert isinstance(make_aligner(), WhisperAligner)
