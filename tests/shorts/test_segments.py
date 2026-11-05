"""Segment cutting to 1080x1920."""

import shutil
import subprocess

import pytest

from lib.shorts.segments import cut_segment, segment_ffmpeg_args

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def test_fill_args_crop_to_vertical():
    args = segment_ffmpeg_args("fill")
    assert args[0] == "-vf"
    assert "crop=1080:1920" in args[1] and "fps=30" in args[1] and "yuv420p" in args[1]


def test_blurpad_args_use_filter_complex_and_blur():
    args = segment_ffmpeg_args("blurpad")
    assert args[0] == "-filter_complex"
    assert "boxblur" in args[1] and "overlay" in args[1] and "fps=30" in args[1]


def test_unknown_mode_raises():
    with pytest.raises(ValueError):
        segment_ffmpeg_args("stretch")


def _source(path):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=3",
         "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )


def _probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return out


@needs_ffmpeg
@pytest.mark.parametrize("mode", ["fill", "blurpad"])
def test_cut_segment_produces_1080x1920_30fps(tmp_path, mode):
    src = tmp_path / "src.mp4"
    _source(src)
    out = cut_segment(src, 0.5, 1.5, tmp_path / "seg.mp4", mode)
    assert out.is_file()
    assert _probe(out) == "1080,1920,30/1"


def test_cut_segment_missing_ffmpeg_message(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(RuntimeError) as exc:
        cut_segment(tmp_path / "a.mp4", 0, 1, tmp_path / "b.mp4", "fill")
    assert "ffmpeg" in str(exc.value)


@needs_ffmpeg
def test_cut_segment_failure_is_one_line_runtime_error(tmp_path):
    with pytest.raises(RuntimeError) as exc:
        cut_segment(tmp_path / "missing.mp4", 0, 1, tmp_path / "b.mp4", "fill")
    assert "\n" not in str(exc.value).strip()
