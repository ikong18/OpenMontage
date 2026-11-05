"""The thinking steps: run the owner's local `claude` CLI headlessly and return validated JSON.

The AI never writes project files and never approves anything; Studio validates and saves what it returns.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Protocol

PROMPTS = Path(__file__).resolve().parent / "prompts"
HOOKS = ["question", "shocking_stat", "myth_bust", "comparison", "countdown"]

_STR = {"type": "string"}
_STRS = {"type": "array", "items": _STR}
_CLAIM = {
    "type": "object",
    "properties": {"text": _STR, "sources": _STRS},
    "required": ["text", "sources"],
}


def _wrap(item: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": {"items": {"type": "array", "items": item}}, "required": ["items"]}


SCHEMAS: dict[str, dict[str, Any]] = {
    "topics": _wrap({
        "type": "object",
        "properties": {"topic": _STR, "hook_type": {"type": "string", "enum": HOOKS}, "hook_text": _STR,
                       "sources": _STRS},
        "required": ["topic", "hook_type", "hook_text", "sources"],
    }),
    "scripts": _wrap({
        "type": "object",
        "properties": {"id": _STR, "narration": _STR, "claims": {"type": "array", "items": _CLAIM},
                       "title": _STR, "description": _STR, "tags": _STRS},
        "required": ["id", "narration", "claims", "title", "description", "tags"],
    }),
    "footage_queries": _wrap({
        "type": "object",
        "properties": {"id": _STR, "queries": {"type": "array", "items": {
            "type": "object",
            "properties": {"source": {"type": "string", "enum": ["nasa", "wikimedia"]}, "query": _STR},
            "required": ["source", "query"]}}},
        "required": ["id", "queries"],
    }),
    "footage_cuts": _wrap({
        "type": "object",
        "properties": {"id": _STR, "cuts": {"type": "array", "items": {
            "type": "object",
            "properties": {"source": _STR, "in": {"type": "number"}, "dur": {"type": "number"}},
            "required": ["source", "in", "dur"]}}},
        "required": ["id", "cuts"],
    }),
}

TASK_TOOLS: dict[str, list[str]] = {
    "topics": ["WebFetch", "WebSearch"],
    "scripts": ["WebFetch"],
    "footage_queries": [],
    "footage_cuts": ["Read"],
}


class AiError(Exception):
    """A failure the owner can read: the message says what went wrong."""


class AiClient(Protocol):
    def run(self, task: str, inputs: dict[str, Any], schema: dict[str, Any], tools: list[str],
            add_dirs: list[Path] | None = None) -> dict[str, Any]: ...


class ClaudeCli:
    def __init__(self, work_dir: Path, binary: str = "claude", timeout: int = 600):
        self.work_dir = Path(work_dir)
        self.binary = binary
        self.timeout = timeout

    def run(self, task: str, inputs: dict[str, Any], schema: dict[str, Any], tools: list[str],
            add_dirs: list[Path] | None = None) -> dict[str, Any]:
        prompt = (PROMPTS / f"{task}.md").read_text(encoding="utf-8")
        text = f"{prompt}\n\nINPUT:\n{json.dumps(inputs, indent=2, ensure_ascii=False)}\n"
        # cwd must not be the repo root: its CLAUDE.md would redirect the run to the production guide.
        self.work_dir.mkdir(parents=True, exist_ok=True)
        argv = [self.binary, "-p", "--output-format", "json", "--json-schema", json.dumps(schema),
                "--no-session-persistence", "--tools", ",".join(tools)]
        if tools:
            argv += ["--allowedTools", ",".join(tools)]
        for directory in add_dirs or []:
            argv += ["--add-dir", str(directory)]
        try:
            proc = subprocess.run(
                argv, input=text, cwd=str(self.work_dir), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=self.timeout,
            )
        except FileNotFoundError as exc:
            raise AiError("claude CLI not found - install it and sign in") from exc
        except subprocess.TimeoutExpired as exc:
            raise AiError(f"claude timed out after {self.timeout} s") from exc
        if proc.returncode != 0:
            raw = proc.stderr or proc.stdout or ""
            tail = " | ".join(raw.strip().splitlines()[-3:])
            hint = ""
            if "another Claude Code session" in raw:
                hint = " - start Studio from a normal terminal, not from inside a Claude Code session"
            raise AiError(f"claude failed (exit {proc.returncode}): {tail[:300]}{hint}")
        try:
            data = json.loads(proc.stdout)
        except ValueError as exc:
            raise AiError("claude returned something that is not JSON") from exc
        if data.get("is_error"):
            raise AiError(f"claude reported an error: {str(data.get('result'))[:300]}")
        out = data.get("structured_output")
        if not isinstance(out, dict):
            raise AiError("claude returned no structured answer")
        return out


class FakeAi:
    """Test double: responses maps a task name to a dict to return or an exception to raise."""

    def __init__(self, responses: dict[str, Any]):
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def run(self, task: str, inputs: dict[str, Any], schema: dict[str, Any], tools: list[str],
            add_dirs: list[Path] | None = None) -> dict[str, Any]:
        self.calls.append({"task": task, "inputs": inputs, "tools": tools, "add_dirs": add_dirs})
        result = self.responses[task]
        if isinstance(result, Exception):
            raise result
        return result
