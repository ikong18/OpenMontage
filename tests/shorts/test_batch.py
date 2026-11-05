"""The audited shorts_batch.json derived from a batch spec."""

from lib.shorts.batch import derive_batch, read_batch, record_cost, write_batch


def _spec():
    return {
        "project_id": "p1",
        "sources": {"a": {"path": "assets/video/a.mp4", "clip_id": "nasa_X", "provider": "nasa",
                          "license": "public domain", "url": "https://example.com/a"}},
        "videos": [{
            "id": "v1", "topic": "Pokémon 日本", "hook_type": "comparison", "hook_text": "h",
            "narration": "One. Two.", "claims": [{"text": "c", "sources": ["https://e.com"]}],
            "cuts": [{"source": "a", "in": 56.0, "dur": 4.0}, {"source": "a", "in": 60.0, "dur": 3.5}],
            "title": "t", "description": "d", "tags": [],
        }],
    }


def test_derive_batch_builds_clip_ids_and_empty_costs():
    video = derive_batch(_spec())["videos"][0]
    assert [c["clip_id"] for c in video["clips"]] == ["nasa_X@56.0-60.0", "nasa_X@60.0-63.5"]
    assert video["clips"][0]["source"] == "nasa"
    assert video["clips"][0]["license"] == "public domain"
    assert video["cost_entries"] == []
    assert video["render"] == "renders/v1.mp4"
    assert video["narration"] == "One. Two."


def test_write_then_read_roundtrips_non_ascii(tmp_path):
    batch = derive_batch(_spec())
    write_batch(tmp_path, batch)
    assert read_batch(tmp_path)["videos"][0]["topic"] == "Pokémon 日本"


def test_record_cost_replaces_same_tool():
    batch = derive_batch(_spec())
    record_cost(batch, "v1", "piper_tts", "piper", 0.0)
    record_cost(batch, "v1", "piper_tts", "piper", 0.0)
    record_cost(batch, "v1", "subtitle_gen", "openmontage", 0.0)
    entries = batch["videos"][0]["cost_entries"]
    assert [e["tool"] for e in entries] == ["piper_tts", "subtitle_gen"]
