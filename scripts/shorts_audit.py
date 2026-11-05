"""Audit a shorts-factory batch before the review queue opens.

    python scripts/shorts_audit.py projects/<project-id>

Reads ``artifacts/shorts_batch.json``, probes each render with ffprobe and runs
every guardrail in ``lib.shorts_guardrails``. Exit code 0 = clean, 1 = violations.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.shorts_guardrails import run_all  # noqa: E402

BATCH_FILE = Path("artifacts") / "shorts_batch.json"


def load_batch(project_dir: Path) -> dict[str, Any]:
    path = Path(project_dir) / BATCH_FILE
    if not path.exists():
        raise FileNotFoundError(f"batch file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: invalid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object with a 'videos' list")
    return data


def probe_render(path: Path) -> dict[str, Any]:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height:format=duration",
            "-of", "json", str(path),
        ],
        capture_output=True, text=True, check=True, encoding="utf-8",
    ).stdout
    data = json.loads(out)
    stream = data["streams"][0]
    return {
        "width": int(stream["width"]),
        "height": int(stream["height"]),
        "duration": float(data["format"]["duration"]),
    }


def audit_project(
    project_dir: Path, probe: Callable[[Path], dict[str, Any]] | None = None
) -> dict[str, Any]:
    project_dir = Path(project_dir)
    probe = probe or probe_render
    batch = load_batch(project_dir)
    probes: dict[str, dict] = {}
    missing: list[str] = []
    cost_entries: list[dict] = []
    seen_ids: set[str] = set()
    for index, video in enumerate(batch.get("videos") or []):
        video_id = video.get("id")
        cost_entries.extend(video.get("cost_entries") or [])
        if not video_id:
            missing.append(f"video at index {index}: missing id")
            continue
        if video_id in seen_ids:
            missing.append(f"duplicate video id: {video_id}")
            continue
        seen_ids.add(video_id)
        render = project_dir / str(video.get("render", ""))
        if not render.is_file():
            missing.append(f"missing render: {video_id}")
            continue
        try:
            probes[video_id] = probe(render)
        except Exception:
            missing.append(f"unprobeable render: {video_id}")
    result = run_all(batch, probes, cost_entries)
    violations = missing + result["violations"]
    return {"ok": not violations, "violations": violations}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python scripts/shorts_audit.py <project_dir>")
        return 2
    try:
        result = audit_project(Path(args[0]), probe=probe_render)
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc))
        return 1
    for violation in result["violations"]:
        print(violation)
    if result["ok"]:
        print("shorts audit: OK")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
