import json
import sys
import threading

import pytest

from studio.jobs import JobBusy, JobRunner

PY = sys.executable


def _wait(runner, batch_id):
    runner.wait(batch_id, timeout=30)


def test_job_runs_and_logs_output(tmp_path):
    runner = JobRunner(tmp_path)
    job = runner.start("b1", "render", [PY, "-c", "print('hello from job')"])
    _wait(runner, "b1")
    assert "hello from job" in runner.tail("b1", job)
    record = runner.recent("b1")[0]
    assert record["id"] == job and record["status"] == "ok" and record["step"] == "render"
    assert record["ended"] is not None


def test_failed_exit_code_marks_failed(tmp_path):
    runner = JobRunner(tmp_path)
    runner.start("b1", "render", [PY, "-c", "import sys; print('boom'); sys.exit(3)"])
    _wait(runner, "b1")
    assert runner.recent("b1")[0]["status"] == "failed"


def test_second_start_raises_job_busy(tmp_path):
    runner = JobRunner(tmp_path)
    runner.start("b1", "render", [PY, "-c", "import time; time.sleep(2)"])
    assert runner.running("b1") is not None
    with pytest.raises(JobBusy):
        runner.start("b1", "script", [PY, "-c", "print(1)"])
    _wait(runner, "b1")
    assert runner.running("b1") is None


def test_jobs_on_different_batches_run_together(tmp_path):
    runner = JobRunner(tmp_path)
    runner.start("a", "render", [PY, "-c", "import time; time.sleep(1)"])
    runner.start("b", "render", [PY, "-c", "import time; time.sleep(1)"])
    assert runner.running("a") and runner.running("b")
    _wait(runner, "a")
    _wait(runner, "b")


def test_concurrent_starts_only_one_wins(tmp_path):
    runner = JobRunner(tmp_path)
    results = []

    def go():
        try:
            runner.start("b1", "render", [PY, "-c", "import time; time.sleep(1)"])
            results.append("ok")
        except JobBusy:
            results.append("busy")

    threads = [threading.Thread(target=go) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("ok") == 1 and results.count("busy") == 5
    _wait(runner, "b1")


def test_recover_marks_orphaned_running_as_failed(tmp_path):
    folder = tmp_path / "b1" / "studio"
    folder.mkdir(parents=True)
    (folder / "jobs.jsonl").write_text(json.dumps({
        "id": "1-1", "step": "render", "argv": ["x"], "started": "t", "ended": None, "status": "running"}) + "\n",
        encoding="utf-8")
    runner = JobRunner(tmp_path)
    runner.recover("b1")
    record = runner.recent("b1")[0]
    assert record["status"] == "failed" and record["ended"] is not None
    assert runner.running("b1") is None


def test_run_steps_callable_error_marks_failed_and_logs_message(tmp_path):
    runner = JobRunner(tmp_path)

    def body(ctx):
        ctx.log("starting")
        raise ValueError("ai said no")

    job = runner.run_steps("b1", "script", body)
    _wait(runner, "b1")
    log = runner.tail("b1", job)
    assert "starting" in log and "ai said no" in log
    assert runner.recent("b1")[0]["status"] == "failed"


def test_run_steps_context_run_returns_exit_code_and_logs(tmp_path):
    runner = JobRunner(tmp_path)
    codes = []

    def body(ctx):
        codes.append(ctx.run([PY, "-c", "print('inner')"]))
        codes.append(ctx.run([PY, "-c", "import sys; sys.exit(2)"]))

    job = runner.run_steps("b1", "render", body)
    _wait(runner, "b1")
    assert codes == [0, 2] and "inner" in runner.tail("b1", job)
    assert runner.recent("b1")[0]["status"] == "ok"


def test_recent_newest_first_and_limit(tmp_path):
    runner = JobRunner(tmp_path)
    ids = []
    for _ in range(3):
        ids.append(runner.start("b1", "render", [PY, "-c", "print(1)"]))
        _wait(runner, "b1")
    assert [r["id"] for r in runner.recent("b1")] == ids[::-1]
    assert len(runner.recent("b1", limit=2)) == 2


def test_tail_rejects_unsafe_job_id(tmp_path):
    runner = JobRunner(tmp_path)
    with pytest.raises(FileNotFoundError):
        runner.tail("b1", "../../x")


def test_events_published_to_subscribers(tmp_path):
    import asyncio

    async def scenario():
        runner = JobRunner(tmp_path)
        q = runner.subscribe()
        runner.start("b1", "render", [PY, "-c", "print(1)"])
        seen = []
        while True:
            event = await asyncio.wait_for(q.get(), timeout=15)
            seen.append(event["status"])
            if event["status"] in ("ok", "failed"):
                break
        runner.unsubscribe(q)
        return seen

    seen = asyncio.run(scenario())
    assert seen[0] == "running" and seen[-1] == "ok"


# ---- chaining: a job can start the next one only when it succeeded ---------------------


def test_on_success_runs_after_ok_job_and_can_start_the_next(tmp_path):
    runner = JobRunner(tmp_path)
    order = []

    def second():
        order.append("second-start")
        runner.start("b1", "script", [PY, "-c", "print('two')"])

    runner.start("b1", "topics", [PY, "-c", "print('one')"], on_success=second)
    deadline = __import__("time").time() + 30
    while __import__("time").time() < deadline and len(runner.recent("b1")) < 2:
        __import__("time").sleep(0.1)
    runner.wait("b1")
    steps = [r["step"] for r in runner.recent("b1")]
    assert order == ["second-start"] and set(steps) == {"topics", "script"}


def test_on_success_not_called_when_job_fails(tmp_path):
    runner = JobRunner(tmp_path)
    called = []
    runner.start("b1", "topics", [PY, "-c", "import sys; sys.exit(1)"], on_success=lambda: called.append(1))
    _wait(runner, "b1")
    assert called == []


def test_on_success_error_is_written_to_the_finished_jobs_log(tmp_path):
    runner = JobRunner(tmp_path)

    def boom():
        raise RuntimeError("cannot continue")

    job = runner.start("b1", "topics", [PY, "-c", "print('ok')"], on_success=boom)
    _wait(runner, "b1")
    assert "cannot continue" in runner.tail("b1", job)
    assert runner.recent("b1")[0]["status"] == "ok"
