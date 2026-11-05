"""Real AI through the real server: type a topic, research it, approve, and the scripts appear on their own."""

import time

import pytest

from starlette.testclient import TestClient

from lib.shorts.spec import load_spec
from studio.jobs import JobRunner
from studio.server import create_app
from tests.real_ai.conftest import assert_sources_are_real

pytestmark = pytest.mark.live_api  # real, billable AI calls: needs OPENMONTAGE_ALLOW_NETWORK=1

H = {"X-Studio-Token": "t"}


def _wait_idle(jobs, batch_id, timeout=1500):
    end = time.time() + timeout
    while time.time() < end:
        if not jobs.running(batch_id):
            return True
        time.sleep(1)
    return False


def test_approving_a_typed_topic_writes_the_scripts_by_itself(ai, provider, tmp_path):
    jobs = JobRunner(tmp_path)
    app = create_app(tmp_path, jobs=jobs, token="t", providers={provider: ai}, ai=ai if provider == "claude" else None)
    client = TestClient(app, base_url="http://127.0.0.1:4760")
    bid = client.post("/api/batches", json={"name": "Real chain", "platform": "facebook_reels"}, headers=H).json()["id"]
    if provider != "claude":
        assert client.put(f"/api/batches/{bid}/settings", json={"provider": provider}, headers=H).status_code == 200

    # the owner types a topic and nothing else; it is saved as a draft
    typed = [{"id": "v1", "topic": "Lightning strikes Earth about 100 times every second"}]
    assert client.put(f"/api/batches/{bid}/spec?stage=draft", json={"videos": typed}, headers=H).status_code == 200
    # approving an unresearched topic must be refused
    assert client.post(f"/api/batches/{bid}/gates/idea/approve", headers=H).status_code == 422

    assert client.post(f"/api/batches/{bid}/steps/topics/run", json={"research": ["v1"]}, headers=H).status_code == 202
    assert _wait_idle(jobs, bid), "research did not finish"
    researched = load_spec(tmp_path / bid)["videos"][0]
    print(f"\n[{provider}] researched: {researched['hook_type']!r} {researched['hook_text']!r} {researched['topic_sources']}")
    assert researched["topic_sources"]
    assert_sources_are_real(researched["topic_sources"])

    # approve: the script step must start by itself, and nothing else may be approved
    assert client.post(f"/api/batches/{bid}/gates/idea/approve", headers=H).status_code == 202
    end = time.time() + 1800
    while time.time() < end:
        steps = [r["step"] for r in jobs.recent(bid)]
        if "script" in steps and not jobs.running(bid):
            break
        time.sleep(2)
    view = client.get(f"/api/batches/{bid}").json()
    log = {r["step"]: jobs.tail(bid, r["id"], 15) for r in jobs.recent(bid)}
    print(f"[{provider}] jobs: {[(r['step'], r['status']) for r in reversed(jobs.recent(bid))]}")
    assert view["gates"]["idea"] == "approved" and view["gates"]["review_queue"] == "none"
    assert [r["status"] for r in jobs.recent(bid) if r["step"] == "script"] == ["ok"], log.get("script")
    assert view["steps"]["script"] == "done"
    assert len(load_spec(tmp_path / bid)["videos"][0]["narration"].split()) >= 40
    assert "footage" not in [r["step"] for r in jobs.recent(bid)], "autopilot is off: the chain must stop after the scripts"
