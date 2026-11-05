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


def test_idea_director_lists_the_fixed_hook_types():
    text = _read("idea-director")
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
    assert "idea" in text and "review_queue" in text
    assert "$0" in text


def test_idea_director_presents_both_runtimes_and_records_decision():
    text = _read("idea-director")
    for needle in ("render_runtime", "hyperframes", "render_runtime_selection", "remotion"):
        assert needle in text


def test_compose_director_routes_by_runtime():
    text = _read("compose-director")
    assert "render_runtime" in text and "HyperFrames" in text


def test_compose_director_sets_vertical_profile_and_caption_style():
    text = _read("compose-director")
    for needle in ("youtube_shorts", "captionStyle", "bottomOffset", "wordsPerPage"):
        assert needle in text


# --- batch runner: directors call scripts/shorts_batch.py instead of improvising ---

_COMMANDS = {
    "idea-director": "validate",
    "script-director": "validate",
    "visuals-director": "fetch",
    "voice-captions-director": "voice",
    "compose-director": "render",
    "review-queue-director": "checkpoints",
    "publish-director": "export",
}


def test_directors_name_their_batch_command():
    for skill, command in _COMMANDS.items():
        text = _read(skill)
        assert "scripts/shorts_batch.py" in text, skill
        assert f"shorts_batch.py {command}" in text, f"{skill} should show `{command}`"


def test_executive_producer_forbids_adhoc_scripts():
    text = _read("executive-producer")
    assert "ad-hoc" in text and "scripts/shorts_batch.py" in text


def test_script_director_names_batch_spec_fields():
    text = _read("script-director")
    for needle in ("batch_spec.json", "cuts", "narration"):
        assert needle in text


def test_approve_flag_is_tied_to_owner_approval():
    for skill in ("idea-director", "review-queue-director"):
        text = _read(skill)
        assert "--approve" in text and "owner" in text.lower()


def test_index_lists_the_batch_runner():
    assert "shorts_batch.py" in (SKILLS / "INDEX.md").read_text(encoding="utf-8")


def test_early_directors_validate_only_their_stage():
    """A full validate needs title/cuts/etc. that later stages fill in, so it would always fail early."""
    assert "validate projects/<id> --stage idea" in _read("idea-director")
    assert "validate projects/<id> --stage script" in _read("script-director")
    assert "validate projects/<id> --stage visuals" in _read("visuals-director")
