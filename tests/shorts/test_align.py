"""Pause-based word aligner."""

import shutil
import subprocess

import pytest

from lib.shorts.align import (
    SilenceAligner,
    WordTiming,
    detect_speech_spans,
    distribute_words,
    split_sentences,
    to_remotion_captions,
)

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def test_split_sentences():
    assert split_sentences("Hello there. Is it? Yes! Done") == ["Hello there.", "Is it?", "Yes!", "Done"]


def test_distribute_words_covers_every_word_in_order():
    sentences = ["Hello there.", "Second sentence here."]
    timings = distribute_words(sentences, [(0.0, 1.0), (1.5, 3.0)], 3.2)
    assert [t.word for t in timings] == ["Hello", "there.", "Second", "sentence", "here."]
    starts = [t.start_ms for t in timings]
    assert starts == sorted(starts)
    assert all(t.end_ms >= t.start_ms for t in timings)
    assert timings[0].start_ms >= 0 and timings[-1].end_ms <= 3000


def test_matching_spans_keep_each_sentence_inside_its_span():
    timings = distribute_words(["One two.", "Three four."], [(0.0, 1.0), (2.0, 3.0)], 3.0)
    assert timings[1].end_ms <= 1000
    assert timings[2].start_ms >= 2000


def test_fallback_when_span_count_differs():
    timings = distribute_words(["One two.", "Three four.", "Five."], [(0.2, 2.8)], 3.0)
    assert len(timings) == 5
    assert timings[0].start_ms >= 200 and timings[-1].end_ms <= 2800
    assert [t.start_ms for t in timings] == sorted(t.start_ms for t in timings)


def test_single_word_sentence():
    timings = distribute_words(["Wow."], [(0.5, 1.0)], 1.5)
    assert timings == [WordTiming("Wow.", 500, 1000)]


def test_to_remotion_captions_keys():
    out = to_remotion_captions([WordTiming("Hi", 0, 400)])
    assert out == [{"word": "Hi", "startMs": 0, "endMs": 400}]


def _make_wav(path):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=0.6",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-filter_complex", "[0:a][1:a][2:a]concat=n=3:v=0:a=1[a]", "-map", "[a]", str(path)],
        check=True,
    )


@needs_ffmpeg
def test_detect_speech_spans_finds_two_bursts(tmp_path):
    wav = tmp_path / "t.wav"
    _make_wav(wav)
    spans, duration = detect_speech_spans(wav)
    assert len(spans) == 2
    assert 2.4 < duration < 2.8


@needs_ffmpeg
def test_silence_aligner_end_to_end(tmp_path):
    wav = tmp_path / "t.wav"
    _make_wav(wav)
    timings = SilenceAligner().align("Hello there. Second sentence here.", wav)
    assert [t.word for t in timings] == ["Hello", "there.", "Second", "sentence", "here."]
    assert [t.start_ms for t in timings] == sorted(t.start_ms for t in timings)


def test_missing_ffmpeg_gives_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(RuntimeError) as exc:
        detect_speech_spans(tmp_path / "x.wav")
    assert "ffmpeg" in str(exc.value)


def test_split_sentences_keeps_abbreviations_and_initials_inside_a_sentence():
    text = "The U.S. sent 99.8 percent of Dr. Smith's probes. Mr. Jones met J. R. Ewing at St. Mary's. It ended."
    assert split_sentences(text) == [
        "The U.S. sent 99.8 percent of Dr. Smith's probes.",
        "Mr. Jones met J. R. Ewing at St. Mary's.",
        "It ended.",
    ]
    assert " ".join(split_sentences(text)).split() == text.split()
