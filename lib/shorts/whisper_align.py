"""Word timings from a local speech-recognition model (faster-whisper), mapped onto the known narration.

The narration text is the truth: Whisper only says when each word was heard. Its words are matched to the narration
words (digits, hyphens and numbers spoken as words do not match one for one), and any narration word without a match is
spread between its matched neighbours. If too little matches, the estimate from pauses is used instead and `method`
says so, so a render never gets worse than before.
"""

from __future__ import annotations

import os
import re
from difflib import SequenceMatcher
from pathlib import Path

from lib.shorts.align import SilenceAligner, WordAligner, WordTiming, _spread, detect_speech_spans

MIN_MATCH = 0.5
MIN_WORD_MS = 30
_MODELS: dict[str, object] = {}


def _norm(word: str) -> str:
    return re.sub(r"[^a-z0-9]", "", word.lower())


def _model(size: str):
    if size not in _MODELS:
        from faster_whisper import WhisperModel

        try:  # the local copy first: no network call, so it also works offline
            _MODELS[size] = WhisperModel(size, device="cpu", compute_type="int8", local_files_only=True)
        except Exception:  # noqa: BLE001 - not downloaded yet: fetch it once
            _MODELS[size] = WhisperModel(size, device="cpu", compute_type="int8")
    return _MODELS[size]


class WhisperAligner:
    method = "estimate"

    def __init__(self, model_size: str = "base"):
        self.model_size = model_size
        self.error: str | None = None

    def _heard(self, text: str, wav_path: Path) -> list[tuple[str, float, float]]:
        segments, _info = _model(self.model_size).transcribe(
            # no initial_prompt: giving Whisper the whole transcript makes it skip speech it thinks it already heard
            str(wav_path), language="en", word_timestamps=True, beam_size=5, condition_on_previous_text=False,
        )
        return [(w.word, float(w.start), float(w.end)) for seg in segments for w in (seg.words or [])]

    def align(self, text: str, wav_path: Path) -> list[WordTiming]:
        words = text.split()
        self.error = None
        try:
            heard = self._heard(text, wav_path)
        except Exception as exc:  # noqa: BLE001 - no model, no download, unreadable audio: fall back to the estimate
            heard, self.error = [], f"{type(exc).__name__}: {exc}"
        starts: list[float | None] = [None] * len(words)
        ends: list[float | None] = [None] * len(words)
        if heard:
            narr = [_norm(w) for w in words]
            said = [_norm(h[0]) for h in heard]
            for block in SequenceMatcher(None, narr, said, autojunk=False).get_matching_blocks():
                for k in range(block.size):
                    i, j = block.a + k, block.b + k
                    if narr[i]:
                        starts[i], ends[i] = heard[j][1], heard[j][2]
        usable = sum(1 for w in words if _norm(w))
        matched = sum(1 for s in starts if s is not None)
        if not heard or usable == 0 or matched / usable < MIN_MATCH:
            self.method = "estimate"
            return SilenceAligner().align(text, wav_path)
        self.method = "whisper"
        _, duration = detect_speech_spans(wav_path)
        timings = self._fill(words, starts, ends, duration)
        return timings

    @staticmethod
    def _fill(words: list[str], starts: list, ends: list, duration: float) -> list[WordTiming]:
        n = len(words)
        i = 0
        while i < n:
            if starts[i] is not None:
                i += 1
                continue
            j = i
            while j < n and starts[j] is None:
                j += 1
            run = words[i:j]
            right = starts[j] if j < n else duration
            left = ends[i - 1] if i > 0 else max(0.0, right - 0.35 * len(run))
            if j >= n:
                right = min(duration, left + 0.35 * len(run)) if left + 0.35 * len(run) > left else duration
            if right <= left:
                right = left + 0.05 * len(run)
            for k, t in enumerate(_spread(run, left, right)):
                starts[i + k], ends[i + k] = t.start_ms / 1000, t.end_ms / 1000
            i = j
        out: list[WordTiming] = []
        prev_start = 0
        for word, s, e in zip(words, starts, ends):
            start_ms = max(int(round(s * 1000)), prev_start)
            end_ms = max(int(round(e * 1000)), start_ms + MIN_WORD_MS)
            out.append(WordTiming(word, start_ms, end_ms))
            prev_start = start_ms
        return out


def make_aligner() -> WordAligner:
    """Whisper when it is installed (set OPENMONTAGE_ALIGNER=estimate to force the pause-based estimate)."""
    if os.environ.get("OPENMONTAGE_ALIGNER", "").lower() == "estimate":
        return SilenceAligner()
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return SilenceAligner()
    return WhisperAligner()
