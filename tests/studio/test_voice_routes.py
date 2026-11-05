"""Voice picker routes with the real voices (Piper and Kokoro), no fakes."""

import shutil
import sys

import pytest
import soundfile
from starlette.testclient import TestClient

from lib.shorts.spec import load_spec
from lib.shorts.voices import kokoro_available
from studio.batches import create_batch
from studio.jobs import JobRunner
from studio.server import create_app

H = {"X-Studio-Token": "t"}


@pytest.fixture
def env(tmp_path):
    jobs = JobRunner(tmp_path)
    client = TestClient(create_app(tmp_path, jobs=jobs, token="t"), base_url="http://127.0.0.1:4760")
    return tmp_path, jobs, client, create_batch(tmp_path, "Voice", "facebook_reels")


def test_view_lists_engines_voices_and_the_current_choice(env):
    _, _, client, bid = env
    view = client.get(f"/api/batches/{bid}").json()
    assert view["voice"]["engine"] == "piper"
    assert "piper" in view["voices"] and "kokoro" in view["voices"]
    assert view["voices"]["kokoro"]["ready"] == kokoro_available()
    assert "af_heart" in [v["id"] for v in view["voices"]["kokoro"]["voices"]]
    assert all(v["label"] for v in view["voices"]["kokoro"]["voices"])


def test_choosing_a_voice_is_saved_in_the_spec_and_validated(env):
    tmp, _, client, bid = env
    ok = client.put(f"/api/batches/{bid}/voice", json={"engine": "kokoro", "voice": "am_michael", "speed": 1.05}, headers=H)
    assert ok.status_code == 200
    assert load_spec(tmp / bid)["voice"] == {"engine": "kokoro", "voice": "am_michael", "speed": 1.05}
    assert client.get(f"/api/batches/{bid}").json()["voice"]["voice"] == "am_michael"
    back = client.put(f"/api/batches/{bid}/voice", json={"engine": "piper"}, headers=H)
    assert back.status_code == 200 and load_spec(tmp / bid)["voice"]["engine"] == "piper"


@pytest.mark.parametrize("bad", [{"engine": "sapi"}, {"engine": "kokoro", "voice": "nobody"}, {"engine": "kokoro", "voice": "af_heart", "speed": 9}])
def test_a_bad_voice_is_refused_and_nothing_is_saved(env, bad):
    tmp, _, client, bid = env
    before = (tmp / bid / "artifacts" / "batch_spec.json").read_bytes()
    resp = client.put(f"/api/batches/{bid}/voice", json=bad, headers=H)
    assert resp.status_code == 422
    assert (tmp / bid / "artifacts" / "batch_spec.json").read_bytes() == before


def test_voice_routes_need_the_token_and_an_idle_batch(env):
    _, jobs, client, bid = env
    assert client.put(f"/api/batches/{bid}/voice", json={"engine": "piper"}).status_code == 403
    assert client.post(f"/api/batches/{bid}/voice/preview", json={"engine": "piper"}).status_code == 403
    jobs.start(bid, "render", [sys.executable, "-c", "import time; time.sleep(2)"])
    assert client.put(f"/api/batches/{bid}/voice", json={"engine": "piper"}, headers=H).status_code == 409
    jobs.wait(bid)


@pytest.mark.skipif(not kokoro_available(), reason="Kokoro model files not downloaded")
def test_kokoro_preview_is_a_real_playable_file(env):
    tmp, _, client, bid = env
    resp = client.post(f"/api/batches/{bid}/voice/preview", json={"engine": "kokoro", "voice": "af_heart", "speed": 1.0}, headers=H)
    assert resp.status_code == 200
    url = resp.json()["url"]
    audio = client.get(url)
    assert audio.status_code == 200 and len(audio.content) > 20_000
    wav = tmp / bid / "review" / url.rsplit("/", 1)[1]
    data, rate = soundfile.read(str(wav))
    assert 2 < len(data) / rate < 15


@pytest.mark.skipif(shutil.which("piper") is None, reason="needs the piper command on PATH")
def test_piper_preview_is_a_real_playable_file(env):
    tmp, _, client, bid = env
    resp = client.post(f"/api/batches/{bid}/voice/preview", json={"engine": "piper"}, headers=H)
    assert resp.status_code == 200
    assert len(client.get(resp.json()["url"]).content) > 20_000


def test_preview_of_a_bad_voice_is_refused(env):
    _, _, client, bid = env
    assert client.post(f"/api/batches/{bid}/voice/preview", json={"engine": "kokoro", "voice": "nobody"}, headers=H).status_code == 422
