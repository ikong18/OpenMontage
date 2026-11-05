"""Studio CLI.

    python -m studio open [batch-id]   # start the server if needed, open the browser
    python -m studio serve [--port N]  # run the server in the foreground
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

from studio import DEFAULT_PORT

REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECTS_DIR = REPO_ROOT / "projects"


def _port() -> int:
    try:
        return int(os.environ.get("STUDIO_PORT", DEFAULT_PORT))
    except ValueError:
        return DEFAULT_PORT


def _server_alive(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1.5) as resp:
            return resp.status == 200
    except Exception:
        return False


def _spawn_server(port: int) -> None:
    cmd = [sys.executable, "-m", "studio", "serve", "--port", str(port)]
    kwargs: dict = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "stdin": subprocess.DEVNULL,
                    "cwd": str(REPO_ROOT)}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(cmd, **kwargs)


def default_work_dir() -> Path:
    """Where claude runs. Outside the repo tree: Claude Code also reads CLAUDE.md files in parent folders."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "OpenMontage" / "studio-work"


def build_app(projects_dir: Path = PROJECTS_DIR, work_dir: Path | None = None):
    from studio.ai import ClaudeCli
    from studio.server import create_app

    projects_dir = Path(projects_dir)
    return create_app(projects_dir, ai=ClaudeCli(Path(work_dir) if work_dir else default_work_dir()))


def cmd_open(batch_id: str | None) -> int:
    port = _port()
    if not _server_alive(port):
        _spawn_server(port)
        deadline = time.time() + 15
        while time.time() < deadline:
            if _server_alive(port):
                break
            time.sleep(0.4)
        else:
            print("studio: the server did not come up in time")
            return 1
    url = f"http://127.0.0.1:{port}/" + (f"batch/{batch_id}" if batch_id else "")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    print(f"studio: {url}")
    return 0


def cmd_serve(port: int) -> int:
    import uvicorn

    print(f"studio: http://127.0.0.1:{port}/  (Ctrl+C to stop)")
    uvicorn.run(build_app(), host="127.0.0.1", port=port, log_level="warning")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="studio", description=__doc__)
    sub = parser.add_subparsers(dest="command")
    p_open = sub.add_parser("open", help="open Studio in the browser (starts the server if needed)")
    p_open.add_argument("batch_id", nargs="?", default=None)
    p_serve = sub.add_parser("serve", help="run the Studio server in the foreground")
    p_serve.add_argument("--port", type=int, default=_port())
    args = parser.parse_args(argv)
    if args.command == "open":
        return cmd_open(args.batch_id)
    if args.command == "serve":
        return cmd_serve(args.port)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
