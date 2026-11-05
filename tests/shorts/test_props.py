"""Remotion props and the cut-coverage guard."""

from pathlib import Path

import pytest

from lib.shorts.props import CAPTION_STYLE, build_props, check_cut_coverage, render_video


def test_cut_coverage_within_tolerance_passes():
    check_cut_coverage(24.15, 24.21)  # 0.06 s short is fine (tolerance 0.1)


def test_cut_coverage_rejects_short_cuts():
    with pytest.raises(ValueError) as exc:
        check_cut_coverage(23.0, 24.21)
    assert "23" in str(exc.value) and "24.21" in str(exc.value)


def test_build_props_cuts_are_consecutive():
    props = build_props([Path("a.mp4"), Path("b.mp4")], [3.0, 2.5], [], Path("n.wav"))
    cuts = props["cuts"]
    assert [(c["in_seconds"], c["out_seconds"]) for c in cuts] == [(0.0, 3.0), (3.0, 5.5)]
    assert all(c["source_in_seconds"] == 0 and c["layer"] == "primary" for c in cuts)
    assert [c["id"] for c in cuts] == ["c1", "c2"]


def test_build_props_carries_caption_style_and_narration():
    caps = [{"word": "Hi", "startMs": 0, "endMs": 300}]
    props = build_props([Path("a.mp4")], [3.0], caps, Path("n.wav"))
    assert props["captionStyle"] == CAPTION_STYLE
    assert CAPTION_STYLE["fontSize"] == 84 and CAPTION_STYLE["wordsPerPage"] == 4
    assert CAPTION_STYLE["bottomOffset"] == 380
    assert props["captions"] == caps
    assert props["audio"]["narration"]["src"].endswith("n.wav")
    assert props["render_runtime"] == "remotion"
    assert props["playbook"] == "shorts-captions"
    assert props["renderer_family"] == "explainer-data"


class _FakeTool:
    def __init__(self, ok=True):
        self.calls = []
        self.ok = ok

    def execute(self, inputs):
        self.calls.append(inputs)
        return type("R", (), {"success": self.ok, "error": None if self.ok else "boom\nsecond line"})()


class _FakeRegistry:
    def __init__(self, tool):
        self._tools = {"video_compose": tool}


def test_render_video_passes_profile_and_operation(tmp_path):
    tool = _FakeTool()
    render_video(_FakeRegistry(tool), {"cuts": []}, tmp_path / "v1.mp4")
    call = tool.calls[0]
    assert call["operation"] == "remotion_render"
    assert call["profile"] == "youtube_shorts"
    assert call["output_path"].endswith("v1.mp4")


def test_render_video_failure_is_a_one_line_error(tmp_path):
    with pytest.raises(RuntimeError) as exc:
        render_video(_FakeRegistry(_FakeTool(ok=False)), {"cuts": []}, tmp_path / "v1.mp4")
    assert "\n" not in str(exc.value).strip()


def test_build_props_applies_a_caption_style_override():
    props = build_props([Path("a.mp4")], [3.0], [], Path("n.wav"), caption_style={"bottomOffset": 420})
    assert props["captionStyle"]["bottomOffset"] == 420
    assert props["captionStyle"]["fontSize"] == CAPTION_STYLE["fontSize"]  # untouched fields keep the default


def test_build_props_override_does_not_mutate_the_default():
    build_props([Path("a.mp4")], [3.0], [], Path("n.wav"), caption_style={"bottomOffset": 999})
    assert CAPTION_STYLE["bottomOffset"] == 380
