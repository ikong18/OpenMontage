"""Explainer captions accept an optional captionStyle (used by shorts-factory).

Defaults must stay identical to the previous hard-coded values so existing
pipelines render exactly as before.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "remotion-composer" / "src"
EXPLAINER = (ROOT / "Explainer.tsx").read_text(encoding="utf-8")
OVERLAY = (ROOT / "components" / "CaptionOverlay.tsx").read_text(encoding="utf-8")


def test_props_declare_caption_style():
    assert "captionStyle?:" in EXPLAINER
    for field in ("fontSize", "wordsPerPage", "bottomOffset", "highlightColor"):
        assert field in EXPLAINER.split("captionStyle?:")[1][:400]


def test_explainer_falls_back_to_previous_defaults():
    assert "captionStyle?.fontSize ?? 42" in EXPLAINER
    assert "captionStyle?.wordsPerPage ?? 6" in EXPLAINER


def test_caption_overlay_bottom_offset_defaults_to_80():
    assert "bottomOffset = 80" in OVERLAY
    assert "paddingBottom: bottomOffset" in OVERLAY
