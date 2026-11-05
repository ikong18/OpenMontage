import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from studio import ai as ai_module
from studio.ai import SCHEMAS, TASK_TOOLS, AiError, ClaudeCli, FakeAi

REPO = Path(__file__).resolve().parents[2]


def _fake_run(monkeypatch, stdout=None, returncode=0, stderr="", raises=None):
    seen = {}

    def fake(argv, **kwargs):
        seen["argv"], seen["kwargs"] = argv, kwargs
        if raises:
            raise raises
        return SimpleNamespace(returncode=returncode, stdout=stdout if stdout is not None else "", stderr=stderr)

    monkeypatch.setattr(ai_module.subprocess, "run", fake)
    return seen


def _ok(payload):
    return json.dumps({"is_error": False, "structured_output": payload, "result": ""})


def test_command_line_has_required_flags(monkeypatch, tmp_path):
    seen = _fake_run(monkeypatch, stdout=_ok({"items": []}))
    work = tmp_path / "work"
    ClaudeCli(work).run("topics", {"theme": "space"}, SCHEMAS["topics"], ["WebFetch", "WebSearch"])
    argv = seen["argv"]
    assert argv[0] == "claude" and "-p" in argv
    assert argv[argv.index("--output-format") + 1] == "json"
    assert json.loads(argv[argv.index("--json-schema") + 1]) == SCHEMAS["topics"]
    assert "--no-session-persistence" in argv
    assert argv[argv.index("--tools") + 1] == "WebFetch,WebSearch"
    assert argv[argv.index("--allowedTools") + 1] == "WebFetch,WebSearch"
    assert Path(seen["kwargs"]["cwd"]) == work and work.is_dir()
    assert Path(seen["kwargs"]["cwd"]).resolve() != REPO
    assert '"theme": "space"' in seen["kwargs"]["input"] and "INPUT:" in seen["kwargs"]["input"]


def test_no_tools_passes_empty_tools_and_no_allowed(monkeypatch, tmp_path):
    seen = _fake_run(monkeypatch, stdout=_ok({"items": []}))
    ClaudeCli(tmp_path).run("footage_queries", {}, SCHEMAS["footage_queries"], [])
    argv = seen["argv"]
    assert argv[argv.index("--tools") + 1] == "" and "--allowedTools" not in argv


def test_add_dirs_are_passed(monkeypatch, tmp_path):
    seen = _fake_run(monkeypatch, stdout=_ok({"items": []}))
    review = tmp_path / "review"
    ClaudeCli(tmp_path / "w").run("footage_cuts", {}, SCHEMAS["footage_cuts"], ["Read"], add_dirs=[review])
    argv = seen["argv"]
    assert argv[argv.index("--add-dir") + 1] == str(review)


def test_parses_structured_output(monkeypatch, tmp_path):
    _fake_run(monkeypatch, stdout=_ok({"items": [{"a": 1}]}))
    assert ClaudeCli(tmp_path).run("topics", {}, SCHEMAS["topics"], []) == {"items": [{"a": 1}]}


def test_missing_binary_message(monkeypatch, tmp_path):
    _fake_run(monkeypatch, raises=FileNotFoundError("claude"))
    with pytest.raises(AiError, match="claude CLI not found"):
        ClaudeCli(tmp_path).run("topics", {}, SCHEMAS["topics"], [])


def test_nonzero_exit_raises_with_stderr_tail(monkeypatch, tmp_path):
    _fake_run(monkeypatch, returncode=1, stderr="line1\nNot logged in")
    with pytest.raises(AiError, match="Not logged in"):
        ClaudeCli(tmp_path).run("topics", {}, SCHEMAS["topics"], [])


def test_is_error_raises(monkeypatch, tmp_path):
    _fake_run(monkeypatch, stdout=json.dumps({"is_error": True, "result": "rate limited"}))
    with pytest.raises(AiError, match="rate limited"):
        ClaudeCli(tmp_path).run("topics", {}, SCHEMAS["topics"], [])


def test_timeout_raises(monkeypatch, tmp_path):
    _fake_run(monkeypatch, raises=subprocess.TimeoutExpired("claude", 5))
    with pytest.raises(AiError, match="timed out"):
        ClaudeCli(tmp_path, timeout=5).run("topics", {}, SCHEMAS["topics"], [])


@pytest.mark.parametrize("stdout", ["not json", json.dumps({"is_error": False}), json.dumps({"structured_output": "x"})])
def test_no_structured_output_raises(monkeypatch, tmp_path, stdout):
    _fake_run(monkeypatch, stdout=stdout)
    with pytest.raises(AiError):
        ClaudeCli(tmp_path).run("topics", {}, SCHEMAS["topics"], [])


def test_fake_ai_returns_and_raises_configured():
    fake = FakeAi({"topics": {"items": [1]}, "scripts": AiError("nope")})
    assert fake.run("topics", {"x": 1}, SCHEMAS["topics"], []) == {"items": [1]}
    with pytest.raises(AiError):
        fake.run("scripts", {}, SCHEMAS["scripts"], [])
    assert [c["task"] for c in fake.calls] == ["topics", "scripts"]


def test_every_task_has_prompt_schema_and_tools():
    for task in ("topics", "scripts", "footage_queries", "footage_cuts"):
        assert (REPO / "studio" / "prompts" / f"{task}.md").read_text(encoding="utf-8").strip()
        schema = SCHEMAS[task]
        assert schema["type"] == "object" and schema["required"] == ["items"]
        assert task in TASK_TOOLS
    assert "Bash" not in sum(TASK_TOOLS.values(), []) and "Write" not in sum(TASK_TOOLS.values(), [])
    assert TASK_TOOLS["topics"] == ["WebFetch", "WebSearch"] and TASK_TOOLS["footage_cuts"] == ["Read"]


def test_nested_session_error_gets_a_plain_hint(monkeypatch, tmp_path):
    _fake_run(monkeypatch, returncode=1,
              stderr="Error: Claude Code cannot be launched inside another Claude Code session.")
    with pytest.raises(AiError, match="normal terminal"):
        ClaudeCli(tmp_path).run("topics", {}, SCHEMAS["topics"], [])
