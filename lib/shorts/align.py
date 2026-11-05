"""Word timings for captions, without a speech-recognition model.

The narration text is known, so the aligner only needs to place each word in
time. It finds the speech spans between pauses (ffmpeg silencedetect), matches
them to sentences, and spreads each sentence's words across its span in
proportion to word length. This is an estimate, not true alignment; callers use
the WordAligner interface so a model-based aligner can replace it later.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

SENTENCE_GAP_WEIGHT = 4  # fallback: how much of the timeline a pause between sentences takes


@dataclass(frozen=True)
class WordTiming:
    word: str
    start_ms: int
    end_ms: int


class WordAligner(Protocol):
    def align(self, text: str, wav_path: Path) -> list[WordTiming]: ...


_ABBREVIATIONS = {"mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "inc", "ltd", "co", "no", "fig"}


def _ends_with_abbreviation(sentence: str) -> bool:
    words = sentence.split()
    if not words or not words[-1].endswith("."):
        return False
    token = words[-1].rstrip(".").lower()
    return token in _ABBREVIATIONS or bool(re.fullmatch(r"[a-z]", token)) or bool(re.fullmatch(r"(?:[a-z]\.)+[a-z]", token))


def split_sentences(text: str) -> list[str]:
    """Sentences by their final . ? ! - but 'Dr.', 'U.S.' and initials like 'J.' do not end one."""
    pieces = [s.strip() for s in re.split(r"(?<=[.?!])\s+", text.strip()) if s.strip()]
    out: list[str] = []
    for piece in pieces:
        if out and _ends_with_abbreviation(out[-1]):
            out[-1] = f"{out[-1]} {piece}"
        else:
            out.append(piece)
    return out


def _weight(word: str) -> int:
    base = max(len(re.sub(r"\W", "", word)), 1)
    return base + (2 if word[-1:] in (",", ";") else 0)


def _spread(words: list[str], start: float, end: float) -> list[WordTiming]:
    total = sum(_weight(w) for w in words)
    out: list[WordTiming] = []
    cursor = start
    for w in words:
        span = (end - start) * _weight(w) / total
        out.append(WordTiming(w, int(round(cursor * 1000)), int(round((cursor + span) * 1000))))
        cursor += span
    return out


def distribute_words(
    sentences: list[str], spans: list[tuple[float, float]], duration: float
) -> list[WordTiming]:
    if not sentences:
        return []
    if not spans:
        spans = [(0.0, duration)]
    if len(spans) == len(sentences):
        timings: list[WordTiming] = []
        for sentence, (a, b) in zip(sentences, spans):
            timings += _spread(sentence.split(), a, b)
        return timings
    a, b = spans[0][0], spans[-1][1]
    weights = [sum(_weight(w) for w in s.split()) for s in sentences]
    total = sum(weights) + SENTENCE_GAP_WEIGHT * (len(sentences) - 1)
    cursor = a
    timings = []
    for sentence, weight in zip(sentences, weights):
        span = (b - a) * weight / total
        timings += _spread(sentence.split(), cursor, cursor + span)
        cursor += span + (b - a) * SENTENCE_GAP_WEIGHT / total
    return timings


def detect_speech_spans(
    wav_path: Path, noise_db: float = -38, min_silence: float = 0.18
) -> tuple[list[tuple[float, float]], float]:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH; install it (for example: winget install Gyan.FFmpeg)")
    out = subprocess.run(
        ["ffmpeg", "-i", str(wav_path), "-af", f"silencedetect=noise={noise_db}dB:d={min_silence}",
         "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8",
    ).stderr
    match = re.search(r"Duration: (\d+):(\d+):([\d.]+)", out)
    if not match:
        raise RuntimeError(f"ffmpeg could not read audio: {wav_path}")
    duration = int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", out)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", out)]
    ends += [duration] * (len(starts) - len(ends))
    spans: list[tuple[float, float]] = []
    cursor = 0.0
    for s, e in zip(starts, ends):
        if s - cursor > 0.05:
            spans.append((cursor, s))
        cursor = e
    if duration - cursor > 0.05:
        spans.append((cursor, duration))
    return spans, duration


class SilenceAligner:
    def align(self, text: str, wav_path: Path) -> list[WordTiming]:
        spans, duration = detect_speech_spans(wav_path)
        return distribute_words(split_sentences(text), spans, duration)


def to_remotion_captions(timings: list[WordTiming]) -> list[dict]:
    return [{"word": t.word, "startMs": t.start_ms, "endMs": t.end_ms} for t in timings]
