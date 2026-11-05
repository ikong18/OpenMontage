import json

import pytest

from studio.batches import create_batch
from studio.settings import DEFAULTS, PROVIDERS, load_settings, save_settings


def test_defaults_when_no_file(tmp_path):
    bid = create_batch(tmp_path, "S", "youtube_shorts")
    assert load_settings(tmp_path / bid) == DEFAULTS == {"provider": "claude", "autopilot": False}
    assert PROVIDERS == ("claude", "codex")


def test_save_and_load_roundtrip(tmp_path):
    bid = create_batch(tmp_path, "S", "youtube_shorts")
    save_settings(tmp_path / bid, {"provider": "codex", "autopilot": True})
    assert load_settings(tmp_path / bid) == {"provider": "codex", "autopilot": True}


@pytest.mark.parametrize("bad", [{"provider": "gpt"}, {"autopilot": "yes"}, {"provider": 3}])
def test_invalid_settings_rejected_and_nothing_saved(tmp_path, bad):
    bid = create_batch(tmp_path, "S", "youtube_shorts")
    with pytest.raises(ValueError):
        save_settings(tmp_path / bid, bad)
    assert not (tmp_path / bid / "studio" / "settings.json").exists()


def test_unknown_keys_ignored_and_corrupt_file_falls_back(tmp_path):
    bid = create_batch(tmp_path, "S", "youtube_shorts")
    save_settings(tmp_path / bid, {"provider": "codex", "evil": 1})
    assert "evil" not in json.loads((tmp_path / bid / "studio" / "settings.json").read_text(encoding="utf-8"))
    (tmp_path / bid / "studio" / "settings.json").write_text("{nope", encoding="utf-8")
    assert load_settings(tmp_path / bid) == DEFAULTS
