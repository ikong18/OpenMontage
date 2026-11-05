"""Studio CLI.

    python -m studio open [batch-id]   # start the server if needed, open the browser
    python -m studio serve [--port N]  # run the server in the foreground
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
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
    from studio.ai import ClaudeCli, CodexCli
    from studio.server import create_app

    projects_dir = Path(projects_dir)
    work = Path(work_dir) if work_dir else default_work_dir()
    return create_app(projects_dir, ai=ClaudeCli(work), providers={"codex": CodexCli(work)})


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


def _open_when_ready(port: int, timeout: float = 30.0) -> bool:
    """Open the browser once the server answers (never before), so the page is not 'refused to connect'."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _server_alive(port):
            try:
                webbrowser.open(f"http://127.0.0.1:{port}/")
            except Exception:
                pass
            return True
        time.sleep(0.4)
    return False


def cmd_serve(port: int, open_browser: bool = False) -> int:
    import uvicorn

    print(f"studio: http://127.0.0.1:{port}/  (Ctrl+C to stop)")
    if open_browser:
        threading.Thread(target=_open_when_ready, args=(port,), daemon=True).start()
    uvicorn.run(build_app(), host="127.0.0.1", port=port, log_level="warning")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="studio", description=__doc__)
    sub = parser.add_subparsers(dest="command")
    p_open = sub.add_parser("open", help="open Studio in the browser (starts the server if needed)")
    p_open.add_argument("batch_id", nargs="?", default=None)
    p_serve = sub.add_parser("serve", help="run the Studio server in the foreground")
    p_serve.add_argument("--port", type=int, default=_port())
    p_serve.add_argument("--open", action="store_true", help="open the browser once the server is ready")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "open":
        return cmd_open(args.batch_id)
    if args.command == "serve":
        return cmd_serve(args.port, args.open)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
