"""Background jobs for Studio: one at a time per batch, with logs and a durable record."""

from __future__ import annotations

import asyncio
import itertools
import json
import os
import re
import subprocess
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

_JOB_ID = re.compile(r"^\d+-\d+$")


class JobBusy(Exception):
    """A job is already running for this batch."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobContext:
    def __init__(self, log_path: Path):
        self._log_path = log_path

    def log(self, line: str) -> None:
        with self._log_path.open("a", encoding="utf-8") as handle:
            handle.write(line.rstrip("\n") + "\n")

    def run(self, argv: list[str], cwd: Path | None = None) -> int:
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        with self._log_path.open("a", encoding="utf-8") as handle:
            handle.write("$ " + " ".join(argv) + "\n")
            handle.flush()
            proc = subprocess.run(
                argv, cwd=str(cwd) if cwd else None, env=env, stdout=handle, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
            )
        return proc.returncode


class JobRunner:
    def __init__(self, projects_dir: Path):
        self.projects_dir = Path(projects_dir)
        self._lock = threading.Lock()
        self._running: dict[str, dict[str, Any]] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._counter = itertools.count(1)
        self._subscribers: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []

    # ---- paths -----------------------------------------------------------
    def _studio_dir(self, batch_id: str) -> Path:
        return self.projects_dir / batch_id / "studio"

    def _records_path(self, batch_id: str) -> Path:
        return self._studio_dir(batch_id) / "jobs.jsonl"

    def _log_path(self, batch_id: str, job_id: str) -> Path:
        return self._studio_dir(batch_id) / "jobs" / f"{job_id}.log"

    # ---- records ---------------------------------------------------------
    def _append(self, batch_id: str, record: dict[str, Any]) -> None:
        path = self._records_path(batch_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")

    def _read(self, batch_id: str) -> dict[str, dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        try:
            text = self._records_path(batch_id).read_text(encoding="utf-8")
        except OSError:
            return merged
        for line in text.splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict) and "id" in record:
                merged[record["id"]] = record
        return merged

    def recent(self, batch_id: str, limit: int = 10) -> list[dict[str, Any]]:
        rows = sorted(self._read(batch_id).values(), key=lambda r: r.get("started") or "", reverse=True)
        return rows[:limit]

    def running(self, batch_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._running.get(batch_id)

    def tail(self, batch_id: str, job_id: str, lines: int = 40) -> str:
        if not _JOB_ID.match(job_id or ""):
            raise FileNotFoundError(job_id)
        path = self._log_path(batch_id, job_id)
        text = path.read_text(encoding="utf-8", errors="replace")
        return "\n".join(text.splitlines()[-lines:])

    def recover(self, batch_id: str) -> None:
        with self._lock:
            live = batch_id in self._running
        if live:
            return
        for record in self._read(batch_id).values():
            if record.get("status") == "running":
                self._append(batch_id, {**record, "status": "failed", "ended": _now(),
                                        "note": "server restarted while this job was running"})

    def recover_all(self) -> None:
        if self.projects_dir.is_dir():
            for folder in self.projects_dir.iterdir():
                if (folder / "studio" / "jobs.jsonl").is_file():
                    self.recover(folder.name)

    # ---- events ----------------------------------------------------------
    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.append((asyncio.get_running_loop(), queue))
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers = [(loop, q) for loop, q in self._subscribers if q is not queue]

    def _publish(self, event: dict[str, Any]) -> None:
        for loop, queue in list(self._subscribers):
            try:
                loop.call_soon_threadsafe(queue.put_nowait, event)
            except RuntimeError:
                pass

    # ---- launching -------------------------------------------------------
    def _launch(self, batch_id: str, step: str, argv: list[str], body: Callable[[JobContext], int | None],
                on_success: Callable[[], None] | None = None) -> str:
        with self._lock:
            if batch_id in self._running:
                raise JobBusy(f"a job is already running for {batch_id}")
            job_id = f"{int(time.time() * 1000)}-{next(self._counter)}"
            record = {"id": job_id, "step": step, "argv": argv, "started": _now(), "ended": None, "status": "running"}
            self._running[batch_id] = record
        log_path = self._log_path(batch_id, job_id)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("", encoding="utf-8")
        self._append(batch_id, record)
        self._publish({"batch_id": batch_id, "job_id": job_id, "status": "running"})

        def work() -> None:
            ctx = JobContext(log_path)
            status = "ok"
            try:
                code = body(ctx)
                if code not in (None, 0):
                    status = "failed"
            except Exception as exc:  # noqa: BLE001 - every failure must end up in the job log
                status = "failed"
                ctx.log(traceback.format_exc().rstrip())
                ctx.log(f"error: {exc}")
            finished = {**record, "status": status, "ended": _now()}
            self._append(batch_id, finished)
            with self._lock:
                self._running.pop(batch_id, None)
            self._publish({"batch_id": batch_id, "job_id": job_id, "status": status})
            if status == "ok" and on_success is not None:
                try:
                    on_success()
                except Exception as exc:  # noqa: BLE001 - a stopped chain is reported in the finished job's log
                    ctx.log(f"next step did not start: {exc}")

        thread = threading.Thread(target=work, name=f"studio-job-{job_id}", daemon=True)
        self._threads[batch_id] = thread
        thread.start()
        return job_id

    def start(self, batch_id: str, step: str, argv: list[str], cwd: Path | None = None,
              on_success: Callable[[], None] | None = None) -> str:
        return self._launch(batch_id, step, argv, lambda ctx: ctx.run(argv, cwd=cwd), on_success)

    def run_steps(self, batch_id: str, step: str, plan: Callable[[JobContext], None],
                  on_success: Callable[[], None] | None = None) -> str:
        return self._launch(batch_id, step, [f"<{step}>"], plan, on_success)

    def wait(self, batch_id: str, timeout: float = 30) -> None:
        thread = self._threads.get(batch_id)
        if thread is not None:
            thread.join(timeout)
