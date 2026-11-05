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
    assert Path(argv[0]).stem.lower() == "claude" and "-p" in argv
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
    for task in ("topics", "scripts", "footage_queries", "footage_cuts", "topic_research"):
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


def test_strict_mcp_config_so_user_mcp_servers_are_not_offered(monkeypatch, tmp_path):
    seen = _fake_run(monkeypatch, stdout=_ok({"items": []}))
    ClaudeCli(tmp_path).run("topics", {}, SCHEMAS["topics"], [])
    assert "--strict-mcp-config" in seen["argv"]


# ---- Codex client: how the command is built (what the AI says is checked by tests/real_ai) -------

from studio.ai import CodexCli, strict_schema  # noqa: E402


def test_strict_schema_requires_every_property_and_forbids_extras():
    out = strict_schema(SCHEMAS["scripts"])
    item = out["properties"]["items"]["items"]
    assert out["additionalProperties"] is False and item["additionalProperties"] is False
    assert sorted(item["required"]) == sorted(item["properties"])
    claim = item["properties"]["claims"]["items"]
    assert claim["additionalProperties"] is False
    assert SCHEMAS["scripts"] is not out  # input is not mutated
    assert "additionalProperties" not in SCHEMAS["scripts"]["properties"]["items"]["items"]


def test_codex_command_line(monkeypatch, tmp_path):
    seen = {}

    def fake(argv, **kwargs):
        seen["argv"], seen["kwargs"] = argv, kwargs
        out = Path(argv[argv.index("-o") + 1])
        seen["schema"] = json.loads(Path(argv[argv.index("--output-schema") + 1]).read_text(encoding="utf-8"))
        out.write_text(json.dumps({"items": [{"a": 1}]}), encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(ai_module.subprocess, "run", fake)
    sheet = tmp_path / "s.png"
    sheet.write_bytes(b"x")
    work = tmp_path / "work"
    out = CodexCli(work).run("footage_cuts", {"sources": [{"key": "a", "sheet": str(sheet)}]}, SCHEMAS["footage_cuts"], ["Read"])
    argv = seen["argv"]
    assert out == {"items": [{"a": 1}]}
    assert Path(argv[0]).stem.lower() == "codex" and argv[1] == "exec"
    assert "--ephemeral" in argv and "--skip-git-repo-check" in argv
    assert argv[argv.index("-s") + 1] == "read-only"
    assert Path(argv[argv.index("-C") + 1]) == work
    assert argv[argv.index("-i") + 1] == str(sheet) and argv[-1] == "-"
    assert seen["schema"]["additionalProperties"] is False
    assert not list(work.glob("schema_*.json")) and not list(work.glob("answer_*.json"))  # temp files cleaned up
    assert "INPUT:" in seen["kwargs"]["input"] and Path(seen["kwargs"]["cwd"]) == work


def test_codex_errors_are_readable(monkeypatch, tmp_path):
    monkeypatch.setattr(ai_module.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("codex")))
    with pytest.raises(AiError, match="codex CLI not found"):
        CodexCli(tmp_path).run("topics", {}, SCHEMAS["topics"], [])
    monkeypatch.setattr(ai_module.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="auth failed"))
    with pytest.raises(AiError, match="auth failed"):
        CodexCli(tmp_path).run("topics", {}, SCHEMAS["topics"], [])
    monkeypatch.setattr(ai_module.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""))
    with pytest.raises(AiError, match="no answer"):
        CodexCli(tmp_path).run("topics", {}, SCHEMAS["topics"], [])


def test_cli_binaries_are_resolved_through_path_so_cmd_shims_work_on_windows(monkeypatch, tmp_path):
    """npm installs codex/claude as .cmd shims; a bare name cannot be launched without the full path."""
    monkeypatch.setattr(ai_module.shutil, "which", lambda name: rf"C:\npm\{name}.CMD")
    seen = []

    def fake(argv, **kwargs):
        seen.append(argv[0])
        out = [a for a in argv if str(a).endswith(".json") and "answer_" in str(a)]
        if out:
            Path(out[0]).write_text(json.dumps({"items": []}), encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout=_ok({"items": []}), stderr="")

    monkeypatch.setattr(ai_module.subprocess, "run", fake)
    ClaudeCli(tmp_path).run("topics", {}, SCHEMAS["topics"], [])
    CodexCli(tmp_path).run("topics", {}, SCHEMAS["topics"], [])
    assert seen == [r"C:\npm\claude.CMD", r"C:\npm\codex.CMD"]


def test_codex_runs_without_the_owners_mcp_servers(monkeypatch, tmp_path):
    seen = {}

    def fake(argv, **kwargs):
        seen["argv"] = argv
        Path(argv[argv.index("-o") + 1]).write_text(json.dumps({"items": []}), encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(ai_module.subprocess, "run", fake)
    CodexCli(tmp_path).run("topics", {}, SCHEMAS["topics"], [])
    argv = seen["argv"]
    assert argv[argv.index("-c") + 1] == "mcp_servers={}"


def test_fact_check_task_is_defined():
    assert "fact_check" in SCHEMAS and TASK_TOOLS["fact_check"] == ["WebFetch", "WebSearch"]
    assert (ai_module.PROMPTS / "fact_check.md").read_text(encoding="utf-8").strip()


@pytest.mark.parametrize("client_cls,name", [(ClaudeCli, "claude"), (CodexCli, "codex")])
def test_an_invalid_directory_error_explains_the_likely_cause(monkeypatch, tmp_path, client_cls, name):
    _fake_run(monkeypatch, returncode=1, stderr="The current directory is invalid.")
    with pytest.raises(AiError, match="work folder"):
        client_cls(tmp_path).run("topics", {}, SCHEMAS["topics"], [])


# ---- footage verification tasks ---------------------------------------------------------------------------------


def test_footage_tasks_are_defined_with_the_fields_the_checks_need():
    q = SCHEMAS["footage_queries"]["properties"]["items"]["items"]
    assert "subject" in q["required"]
    query = q["properties"]["queries"]["items"]
    assert query["properties"]["tier"]["enum"] == ["exact", "related"] and "tier" in query["required"]
    pick = SCHEMAS["footage_pick"]["properties"]["items"]["items"]
    assert pick["properties"]["picks"]["items"]["properties"]["match"]["enum"] == ["exact", "related"]
    verify = SCHEMAS["footage_verify"]["properties"]["items"]["items"]
    assert set(verify["required"]) == {"key", "match", "shows", "relevant_frames", "note"}
    assert verify["properties"]["match"]["enum"] == ["exact", "related", "unrelated"]
    assert "shows" in SCHEMAS["footage_cuts"]["properties"]["items"]["items"]["properties"]["cuts"]["items"]["required"]
    review = SCHEMAS["footage_review"]["properties"]["items"]["items"]["properties"]["cuts"]["items"]
    assert review["properties"]["verdict"]["enum"] == ["yes", "partly", "no"]
    for task, tools in (("footage_pick", []), ("footage_verify", ["Read"]), ("footage_review", ["Read"])):
        assert TASK_TOOLS[task] == tools
        assert (ai_module.PROMPTS / f"{task}.md").read_text(encoding="utf-8").strip()


def test_codex_attaches_every_image_a_task_lists(monkeypatch, tmp_path):
    seen = {}

    def fake(argv, **kwargs):
        seen["argv"] = argv
        Path(argv[argv.index("-o") + 1]).write_text(json.dumps({"items": []}), encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(ai_module.subprocess, "run", fake)
    a, b = tmp_path / "a.jpg", tmp_path / "b.jpg"
    CodexCli(tmp_path / "w").run("footage_review", {"images": [str(a), str(b)], "videos": []}, SCHEMAS["footage_review"], ["Read"])
    argv = seen["argv"]
    attached = [argv[i + 1] for i, x in enumerate(argv) if x == "-i"]
    assert attached == [str(a), str(b)]
