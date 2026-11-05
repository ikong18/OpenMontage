import shutil
import subprocess

import pytest

from lib.shorts.sheets import make_contact_sheet, probe_video

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.fixture
def clip(tmp_path):
    path = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=10:duration=12",
         "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def test_probe_video_reports_size_and_duration(clip):
    info = probe_video(clip)
    assert (info["width"], info["height"]) == (320, 180)
    assert 11.5 < info["duration"] < 12.5


def test_make_contact_sheet_frame_count_and_step(clip, tmp_path):
    out = tmp_path / "review" / "sheet.png"
    info = make_contact_sheet(clip, out, step=4.0, cols=6, rows=5)
    assert out.is_file() and out.stat().st_size > 0
    assert info["frames"] == 3 and info["step"] == 4.0 and info["cols"] == 6
    assert info["path"] == str(out)


def test_contact_sheet_frames_capped_by_grid(clip, tmp_path):
    info = make_contact_sheet(clip, tmp_path / "s.png", step=1.0, cols=2, rows=2)
    assert info["frames"] == 4


def test_probe_missing_file_raises(tmp_path):
    with pytest.raises(OSError):
        probe_video(tmp_path / "nope.mp4")


def test_extract_frame_writes_a_picture_from_the_given_second(clip, tmp_path):
    from lib.shorts.sheets import extract_frame

    out = extract_frame(clip, 5.0, tmp_path / "frames" / "f.jpg", width=320)
    assert out.is_file() and out.stat().st_size > 1000


def test_extract_frame_fails_clearly_for_a_missing_video(tmp_path):
    from lib.shorts.sheets import extract_frame

    with pytest.raises(OSError):
        extract_frame(tmp_path / "nope.mp4", 1.0, tmp_path / "f.jpg")
