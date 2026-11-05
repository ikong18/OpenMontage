import copy

import pytest

from lib.shorts.spec import load_spec, save_spec
from studio.apply import add_source, apply_cuts, apply_scripts, apply_topics
from studio.batches import create_batch

SRC = "https://science.nasa.gov/sun/facts/"


def _topics(n=2):
    hooks = ["question", "shocking_stat", "comparison"]
    return [{"topic": f"Topic {i}", "hook_type": hooks[i % 3], "hook_text": f"Hook {i}?", "sources": [SRC]}
            for i in range(n)]


@pytest.fixture
def batch(tmp_path):
    bid = create_batch(tmp_path, "Apply", "youtube_shorts")
    return tmp_path / bid


def test_apply_topics_assigns_ids_and_checks_count(batch):
    spec = load_spec(batch)
    out = apply_topics(spec, _topics(2), 2, batch)
    assert [v["id"] for v in out["videos"]] == ["v1", "v2"]
    assert out["videos"][0]["topic_sources"] == [SRC]
    assert out["videos"][0]["claims"][0]["sources"] == [SRC]
    with pytest.raises(ValueError, match="expected 3"):
        apply_topics(spec, _topics(2), 3, batch)


def test_apply_topics_rejects_bad_hook_type_without_changing_input(batch):
    spec = load_spec(batch)
    before = copy.deepcopy(spec)
    bad = _topics(2)
    bad[1]["hook_type"] = "clickbait"
    with pytest.raises(ValueError, match="hook_type"):
        apply_topics(spec, bad, 2, batch)
    assert spec == before


def test_apply_topics_rejects_topic_without_http_source(batch):
    bad = _topics(1)
    bad[0]["sources"] = ["not a url"]
    with pytest.raises(ValueError, match="source"):
        apply_topics(load_spec(batch), bad, 1, batch)


def _script(vid="v1", **extra):
    base = {"id": vid, "narration": "Narration here.", "claims": [{"text": "c", "sources": [SRC]}],
            "title": "Title", "description": f"Desc. Source: {SRC}", "tags": ["a", "b"]}
    base.update(extra)
    return base


def test_apply_scripts_merges_by_id_and_rejects_unknown_id(batch):
    spec = apply_topics(load_spec(batch), _topics(2), 2, batch)
    out = apply_scripts(spec, [_script("v1"), _script("v2")], batch)
    assert out["videos"][1]["narration"] == "Narration here." and out["videos"][1]["topic"] == "Topic 1"
    with pytest.raises(ValueError, match="v9"):
        apply_scripts(spec, [_script("v9")], batch)


def test_apply_scripts_requires_http_source_on_each_claim(batch):
    spec = apply_topics(load_spec(batch), _topics(1), 1, batch)
    with pytest.raises(ValueError, match="source"):
        apply_scripts(spec, [_script("v1", claims=[{"text": "c", "sources": []}])], batch)


def test_apply_scripts_requires_every_video_present(batch):
    spec = apply_topics(load_spec(batch), _topics(2), 2, batch)
    with pytest.raises(ValueError, match="v2"):
        apply_scripts(spec, [_script("v1")], batch)


def _with_sources(batch, spec):
    (batch / "assets" / "video").mkdir(parents=True, exist_ok=True)
    (batch / "assets" / "video" / "s.mp4").write_bytes(b"x")
    return add_source(spec, "s", {"path": "assets/video/s.mp4", "clip_id": "c1", "provider": "nasa",
                                  "license": "pd", "url": "https://x"}, width=1280, height=720)


def test_apply_cuts_merges_and_validates(batch):
    spec = apply_scripts(apply_topics(load_spec(batch), _topics(2), 2, batch),
                         [_script("v1"), _script("v2")], batch)
    spec = _with_sources(batch, spec)
    out = apply_cuts(spec, [{"id": "v1", "cuts": [{"source": "s", "in": 0, "dur": 3}]},
                            {"id": "v2", "cuts": [{"source": "s", "in": 10, "dur": 3}]}], batch)
    assert out["videos"][0]["cuts"][0]["dur"] == 3


def test_apply_cuts_rejects_overlap_on_same_source(batch):
    spec = apply_scripts(apply_topics(load_spec(batch), _topics(2), 2, batch),
                         [_script("v1"), _script("v2")], batch)
    spec = _with_sources(batch, spec)
    with pytest.raises(ValueError, match="overlap"):
        apply_cuts(spec, [{"id": "v1", "cuts": [{"source": "s", "in": 0, "dur": 3}]},
                          {"id": "v2", "cuts": [{"source": "s", "in": 2, "dur": 3}]}], batch)


def test_apply_cuts_rejects_unknown_source_key(batch):
    spec = apply_scripts(apply_topics(load_spec(batch), _topics(1), 1, batch), [_script("v1")], batch)
    spec = _with_sources(batch, spec)
    with pytest.raises(ValueError, match="unknown source"):
        apply_cuts(spec, [{"id": "v1", "cuts": [{"source": "zzz", "in": 0, "dur": 3}]}], batch)


def test_add_source_mode_blurpad_for_square_fill_for_wide():
    entry = {"path": "p", "clip_id": "c", "provider": "wikimedia", "license": "x", "url": "u"}
    assert add_source({"sources": {}}, "a", entry, width=720, height=720)["sources"]["a"]["mode"] == "blurpad"
    assert add_source({"sources": {}}, "a", entry, width=1920, height=1080)["sources"]["a"]["mode"] == "fill"
    kept = add_source({"sources": {"a": {"mode": "blurpad"}}}, "a", entry, width=1920, height=1080)
    assert kept["sources"]["a"]["mode"] == "blurpad"


def test_input_spec_never_mutated(batch):
    spec = load_spec(batch)
    before = copy.deepcopy(spec)
    apply_topics(spec, _topics(2), 2, batch)
    add_source(spec, "x", {"path": "p", "clip_id": "c", "provider": "n", "license": "l", "url": "u"}, 1, 1)
    assert spec == before
    save_spec(batch, spec)
