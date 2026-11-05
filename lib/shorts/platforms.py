"""Per-platform profiles for rendering and exporting Shorts / Reels.

The numbers for facebook_reels come from conflicting secondary sources (Meta's
own safe-zone page was not found), so they are deliberately conservative and
can be overridden per batch with `caption_style` in batch_spec.json.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Platform:
    name: str
    label: str  # the platform label written to the publish_log
    caption_bottom_offset: int  # px from the bottom of the 1920 px frame to the caption box
    hashtags: tuple[str, ...]
    max_hashtags: int
    max_description: int


PLATFORMS: dict[str, Platform] = {
    "youtube_shorts": Platform(
        name="youtube_shorts", label="youtube_shorts", caption_bottom_offset=380,
        hashtags=("#Shorts",), max_hashtags=15, max_description=5000,
    ),
    "facebook_reels": Platform(
        name="facebook_reels", label="facebook_reels", caption_bottom_offset=420,
        hashtags=("#Reels",), max_hashtags=30, max_description=2000,
    ),
}
PLATFORM_NAMES = tuple(PLATFORMS)
DEFAULT_PLATFORM = "youtube_shorts"


def get_platform(name: str | None) -> Platform:
    key = name or DEFAULT_PLATFORM
    if key not in PLATFORMS:
        raise ValueError(f"unknown platform '{key}' (use {', '.join(PLATFORM_NAMES)})")
    return PLATFORMS[key]
