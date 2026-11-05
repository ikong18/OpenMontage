"""Per-platform export profiles."""

import pytest

from lib.shorts.platforms import PLATFORM_NAMES, get_platform


def test_default_is_youtube_shorts_with_the_existing_values():
    platform = get_platform(None)
    assert platform.name == "youtube_shorts"
    assert platform.caption_bottom_offset == 380
    assert platform.hashtags == ("#Shorts",)


def test_facebook_reels_profile():
    platform = get_platform("facebook_reels")
    assert platform.label == "facebook_reels"
    assert platform.caption_bottom_offset == 420
    assert platform.hashtags == ("#Reels",)
    assert platform.max_description <= 2200  # the lowest limit reported by secondary sources
    assert platform.max_hashtags <= 30


def test_reels_keeps_captions_higher_than_shorts():
    assert get_platform("facebook_reels").caption_bottom_offset > get_platform("youtube_shorts").caption_bottom_offset


def test_unknown_platform_lists_the_valid_ones():
    with pytest.raises(ValueError) as exc:
        get_platform("tiktok")
    assert "facebook_reels" in str(exc.value) and "youtube_shorts" in str(exc.value)


def test_platform_names_cover_both():
    assert set(PLATFORM_NAMES) == {"youtube_shorts", "facebook_reels"}
