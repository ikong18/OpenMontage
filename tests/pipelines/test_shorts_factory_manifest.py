"""Contracts for the shorts-factory manifest and its captions playbook."""

import pytest

from lib.checkpoint import _stage_requires_approval
from lib.pipeline_loader import load_pipeline
from styles.playbook_loader import list_playbooks, load_playbook

NAME = "shorts-factory"
STAGES = [
    "idea", "script", "visuals", "voice_captions",
    "compose", "review_queue", "publish",
]
PAID_TOOLS = {
    "elevenlabs_tts", "openai_tts", "google_tts", "azure_tts", "heygen_video",
    "kling_video", "wan_video", "image_selector", "video_selector", "tts_selector",
}


@pytest.fixture(scope="module")
def manifest():
    return load_pipeline(NAME)


def _stage(manifest, name):
    return next(s for s in manifest["stages"] if s["name"] == name)


def test_stage_order(manifest):
    assert [s["name"] for s in manifest["stages"]] == STAGES


def test_only_idea_and_review_queue_gate():
    gated = {n for n in STAGES if _stage_requires_approval(NAME, n)}
    assert gated == {"idea", "review_queue"}


def test_budget_is_zero(manifest):
    assert manifest["orchestration"]["budget_default_usd"] == 0.0


def test_no_paid_tools_listed(manifest):
    for stage in manifest["stages"]:
        listed = set(stage.get("tools_available", [])) | set(stage.get("required_tools", []))
        assert not (listed & PAID_TOOLS), f"{stage['name']} lists paid tools"


def test_visuals_works_without_api_keys(manifest):
    """direct_clip_search reaches keyless adapters (nasa, archive_org), so a
    missing Pexels/Pixabay key can never strand the stage."""
    stage = _stage(manifest, "visuals")
    assert "direct_clip_search" in stage["required_tools"]
    assert not {"pexels_video", "pixabay_video"} & set(stage["required_tools"])


def test_voice_stage_uses_piper(manifest):
    tools = set(_stage(manifest, "voice_captions")["tools_available"])
    assert {"piper_tts", "subtitle_gen"} <= tools


def test_playbook_listed_and_valid():
    assert "shorts-captions" in list_playbooks()
    assert load_playbook("shorts-captions")["identity"]["name"]


def test_every_stage_has_a_shorts_factory_skill(manifest):
    for stage in manifest["stages"]:
        assert stage["skill"].startswith("pipelines/shorts-factory/")
    assert "pipelines/shorts-factory/executive-producer" in manifest["required_skills"]
