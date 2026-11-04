"""shorts-factory skills must exist and point at real tools."""

import re
from pathlib import Path

from lib.pipeline_loader import load_pipeline
from tools.tool_registry import registry

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS = REPO_ROOT / "skills"
SF_DIR = SKILLS / "pipelines" / "shorts-factory"

registry.discover()
KNOWN_TOOLS = set(registry._tools)

MANIFEST = load_pipeline("shorts-factory")


def _read(name: str) -> str:
    return (SF_DIR / f"{name}.md").read_text(encoding="utf-8")


def test_every_manifest_skill_exists_and_is_nonempty():
    paths = set(MANIFEST["required_skills"]) | {s["skill"] for s in MANIFEST["stages"]}
    for skill in paths:
        text = (SKILLS / f"{skill}.md").read_text(encoding="utf-8")
        assert len(text.strip()) > 300, f"{skill} is a stub"


def test_manifest_tools_exist_in_registry():
    for stage in MANIFEST["stages"]:
        listed = set(stage.get("tools_available", [])) | set(stage.get("required_tools", []))
        missing = listed - KNOWN_TOOLS
        assert not missing, f"{stage['name']}: unknown tools {sorted(missing)}"


def test_skills_reference_only_real_tools():
    pattern = re.compile(r"^\*\*Tools:\*\*\s*(.+)$", re.MULTILINE)
    for path in SF_DIR.glob("*.md"):
        for line in pattern.findall(path.read_text(encoding="utf-8")):
            for name in re.findall(r"`([a-z0-9_]+)`", line):
                assert name in KNOWN_TOOLS, f"{path.name}: unknown tool {name}"


def test_script_director_mentions_hook_types_and_sources():
    text = _read("script-director")
    for needle in ("hook_type", "hook_text", "claims", "sources", "shorts_batch.json"):
        assert needle in text


def test_topics_director_lists_the_fixed_hook_types():
    text = _read("topics-director")
    for hook in ("question", "shocking_stat", "myth_bust", "comparison", "countdown"):
        assert hook in text


def test_visuals_director_requires_license_fields_and_keyless_fallback():
    text = _read("visuals-director")
    for needle in ("clip_id", "license", "direct_clip_search", "nasa", "archive_org"):
        assert needle in text


def test_review_queue_runs_audit():
    assert "scripts/shorts_audit.py" in _read("review-queue-director")


def test_executive_producer_states_the_two_gates_and_zero_cost():
    text = _read("executive-producer")
    assert "topics" in text and "review_queue" in text
    assert "$0" in text
