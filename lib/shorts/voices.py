"""Narration voices: Piper (the original) and Kokoro (a newer local model, Apache-2.0 weights, runs on the CPU).

Both are free and offline. Kokoro's two model files live in models/kokoro (see models/kokoro/README below).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lib.shorts.align import split_sentences

REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_DIR = REPO_ROOT / "models" / "kokoro"
MODEL_FILE = MODEL_DIR / "kokoro-v1.0.int8.onnx"
VOICES_FILE = MODEL_DIR / "voices-v1.0.bin"

ENGINES = ("piper", "kokoro")
PIPER_MODELS = ("en_US-lessac-medium",)
ENGLISH_VOICES = (
    "af_alloy", "af_aoede", "af_bella", "af_heart", "af_jessica", "af_kore", "af_nicole", "af_nova", "af_river",
    "af_sarah", "af_sky", "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam", "am_michael", "am_onyx",
    "am_puck", "am_santa", "bf_alice", "bf_emma", "bf_isabella", "bf_lily", "bm_daniel", "bm_fable", "bm_george",
    "bm_lewis",
)
DEFAULT_KOKORO_VOICE = "af_heart"
PAUSE_SECONDS = 0.22  # between sentences: Kokoro is run one sentence at a time so a long script never hits its length limit

_KOKORO: dict[str, Any] = {}


def kokoro_available() -> bool:
    try:
        import kokoro_onnx  # noqa: F401
        import soundfile  # noqa: F401
    except ImportError:
        return False
    return MODEL_FILE.is_file() and VOICES_FILE.is_file()


def kokoro_voices() -> list[str]:
    return list(ENGLISH_VOICES)


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_voice(voice: Any) -> list[str]:
    if voice in (None, {}):
        return []
    if not isinstance(voice, dict):
        return ["voice must be an object"]
    engine = voice.get("engine", "piper")
    if engine not in ENGINES:
        return [f"voice engine '{engine}' is not one of {', '.join(ENGINES)}"]
    problems: list[str] = []
    if engine == "kokoro":
        name = voice.get("voice", DEFAULT_KOKORO_VOICE)
        if name not in ENGLISH_VOICES:
            problems.append(f"voice '{name}' is not an English Kokoro voice")
        if "speed" in voice and not (_number(voice["speed"]) and 0.5 <= voice["speed"] <= 1.5):
            problems.append("voice speed must be a number from 0.5 to 1.5")
    else:
        if "model" in voice and voice["model"] not in PIPER_MODELS:
            problems.append(f"Piper model must be one of {', '.join(PIPER_MODELS)}")
        if "length_scale" in voice and not (_number(voice["length_scale"]) and 0.5 <= voice["length_scale"] <= 2):
            problems.append("voice length_scale must be a number from 0.5 to 2")
    return problems


def _kokoro():
    if "model" not in _KOKORO:
        if not kokoro_available():
            raise RuntimeError(f"Kokoro is not set up: needs kokoro-onnx, soundfile and the files in {MODEL_DIR}")
        from kokoro_onnx import Kokoro

        _KOKORO["model"] = Kokoro(str(MODEL_FILE), str(VOICES_FILE))
    return _KOKORO["model"]


def synthesize_kokoro(voice: dict[str, Any], text: str, out: Path) -> float:
    """Write the narration to `out` (WAV) and return its length in seconds."""
    import numpy as np
    import soundfile

    model = _kokoro()
    name = voice.get("voice", DEFAULT_KOKORO_VOICE)
    speed = float(voice.get("speed", 1.0))
    parts, rate = [], 24000
    for sentence in split_sentences(text) or [text]:
        samples, rate = model.create(sentence, voice=name, speed=speed, lang="en-us")
        parts += [np.asarray(samples, dtype="float32"), np.zeros(int(PAUSE_SECONDS * rate), dtype="float32")]
    audio = np.concatenate(parts[:-1])
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    soundfile.write(str(out), audio, rate, subtype="PCM_16")
    return len(audio) / rate
