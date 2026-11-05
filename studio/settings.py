"""Per-batch Studio settings (which AI to use, whether to keep going automatically)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PROVIDERS = ("claude", "codex")
DEFAULTS: dict[str, Any] = {"provider": "claude", "autopilot": False}


def _path(project_dir: Path) -> Path:
    return Path(project_dir) / "studio" / "settings.json"


def load_settings(project_dir: Path) -> dict[str, Any]:
    try:
        data = json.loads(_path(project_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(DEFAULTS)
    out = dict(DEFAULTS)
    if isinstance(data, dict):
        if data.get("provider") in PROVIDERS:
            out["provider"] = data["provider"]
        if isinstance(data.get("autopilot"), bool):
            out["autopilot"] = data["autopilot"]
    return out


def save_settings(project_dir: Path, new: dict[str, Any]) -> dict[str, Any]:
    merged = load_settings(project_dir)
    if "provider" in new:
        if new["provider"] not in PROVIDERS:
            raise ValueError(f"provider must be one of {', '.join(PROVIDERS)}")
        merged["provider"] = new["provider"]
    if "autopilot" in new:
        if not isinstance(new["autopilot"], bool):
            raise ValueError("autopilot must be true or false")
        merged["autopilot"] = new["autopilot"]
    path = _path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return merged


def _writer_path(project_dir: Path) -> Path:
    return Path(project_dir) / "studio" / "writer.json"


def save_writer(project_dir: Path, provider: str) -> None:
    """Remember which AI wrote the scripts, so the fact check stays 'the other one' even if the owner switches AI later."""
    path = _writer_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"writer": provider}), encoding="utf-8")


def load_writer(project_dir: Path) -> str | None:
    try:
        data = json.loads(_writer_path(project_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data.get("writer") if isinstance(data, dict) and data.get("writer") in PROVIDERS else None
