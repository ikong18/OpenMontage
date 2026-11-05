"""Batch guardrails for the shorts-factory pipeline."""

from lib.shorts_guardrails import (
    check_fact_sources,
    check_footage_reuse,
    check_hook_variety,
    check_license_log,
    check_short_specs,
    check_zero_cost,
    run_all,
)


def _video(i, hook_type="question", hook_text=None, clip_id=None):
    return {
        "id": f"v{i}",
        "topic": f"topic {i}",
        "hook_type": hook_type,
        "hook_text": hook_text or f"hook {i}",
        "claims": [{"text": "x", "sources": ["https://example.com/a"]}],
        "clips": [
            {
                "clip_id": clip_id or f"clip{i}",
                "source": "nasa",
                "license": "public-domain",
                "url": "https://example.com/c",
            }
        ],
    }


def test_hook_variety_rejects_empty_batch():
    assert check_hook_variety([])["ok"] is False


def test_hook_variety_flags_dominant_type():
    videos = [
        _video(1, "question"),
        _video(2, "question"),
        _video(3, "question"),
        _video(4, "myth_bust"),
    ]
    result = check_hook_variety(videos)
    assert result["ok"] is False
    assert any("question" in v for v in result["violations"])


def test_hook_variety_flags_duplicate_hook_text():
    videos = [
        _video(1, "question", "Did you know X?"),
        _video(2, "myth_bust", "did you know x?"),
    ]
    assert check_hook_variety(videos)["ok"] is False


def test_hook_variety_flags_missing_hook_type():
    videos = [_video(1, "")]
    assert check_hook_variety(videos)["ok"] is False


def test_hook_variety_accepts_varied_batch():
    videos = [_video(1, "question"), _video(2, "myth_bust"), _video(3, "countdown")]
    assert check_hook_variety(videos)["ok"] is True


def test_footage_reuse_flags_shared_clip_id():
    videos = [_video(1, clip_id="same"), _video(2, clip_id="same")]
    result = check_footage_reuse(videos)
    assert result["ok"] is False
    assert any("same" in v for v in result["violations"])


def test_footage_reuse_rejects_empty_batch():
    assert check_footage_reuse([])["ok"] is False


def test_footage_reuse_accepts_distinct_clips():
    assert check_footage_reuse([_video(1), _video(2)])["ok"] is True


def test_specs_reject_exactly_60_seconds():
    bad = {"a": {"width": 1080, "height": 1920, "duration": 60.0}}
    good = {"a": {"width": 1080, "height": 1920, "duration": 59.9}}
    assert check_short_specs(bad)["ok"] is False
    assert check_short_specs(good)["ok"] is True


def test_specs_reject_landscape():
    probes = {"a": {"width": 1920, "height": 1080, "duration": 30.0}}
    assert check_short_specs(probes)["ok"] is False


def test_specs_reject_empty():
    assert check_short_specs({})["ok"] is False


def test_zero_cost_rejects_none_and_positive():
    none_cost = [{"tool": "piper_tts", "provider": "piper", "cost_usd": None}]
    paid = [{"tool": "x", "provider": "openai", "cost_usd": 0.01}]
    free = [{"tool": "piper_tts", "provider": "piper", "cost_usd": 0.0}]
    assert check_zero_cost(none_cost)["ok"] is False
    assert check_zero_cost(paid)["ok"] is False
    assert check_zero_cost(free)["ok"] is True


def test_zero_cost_empty_needs_opt_in():
    assert check_zero_cost([])["ok"] is False
    assert check_zero_cost([], allow_empty=True)["ok"] is True


def test_license_log_requires_all_three_fields():
    video = _video(1)
    assert check_license_log([video])["ok"] is True
    for field in ("source", "license", "url"):
        broken = _video(1)
        broken["clips"][0][field] = ""
        assert check_license_log([broken])["ok"] is False


def test_fact_sources_requires_http_source_per_claim():
    video = _video(1)
    assert check_fact_sources([video])["ok"] is True
    video["claims"].append({"text": "unsourced", "sources": []})
    assert check_fact_sources([video])["ok"] is False
    video["claims"][-1]["sources"] = ["not a url"]
    assert check_fact_sources([video])["ok"] is False


def test_fact_sources_rejects_video_with_no_claims():
    video = _video(1)
    video["claims"] = []
    assert check_fact_sources([video])["ok"] is False


def test_run_all_prefixes_violations():
    batch = {"videos": [_video(1, "question", "same"), _video(2, "question", "same")]}
    probes = {"v1": {"width": 1920, "height": 1080, "duration": 70.0}}
    result = run_all(batch, probes, [])
    assert result["ok"] is False
    prefixes = {v.split(":")[0] for v in result["violations"]}
    assert {"hook_variety", "short_specs", "zero_cost"} <= prefixes


def test_run_all_passes_clean_batch():
    batch = {"videos": [_video(1, "question"), _video(2, "myth_bust")]}
    probes = {
        "v1": {"width": 1080, "height": 1920, "duration": 40.0},
        "v2": {"width": 1080, "height": 1920, "duration": 35.0},
    }
    cost = [{"tool": "piper_tts", "provider": "piper", "cost_usd": 0.0}]
    assert run_all(batch, probes, cost) == {"ok": True, "violations": []}


# --- review fixes: provider allowlist, negative cost, malformed input never raises ---

def test_zero_cost_rejects_paid_provider_even_at_zero():
    paid = [{"tool": "elevenlabs_tts", "provider": "elevenlabs", "cost_usd": 0.0}]
    result = check_zero_cost(paid)
    assert result["ok"] is False
    assert any("elevenlabs" in v for v in result["violations"])


def test_zero_cost_rejects_negative_and_non_numeric_cost():
    assert check_zero_cost([{"tool": "t", "provider": "piper", "cost_usd": -1}])["ok"] is False
    assert check_zero_cost([{"tool": "t", "provider": "piper", "cost_usd": "0.5"}])["ok"] is False


def test_zero_cost_accepts_known_free_providers():
    for provider in ("piper", "openmontage", "remotion", "ffmpeg"):
        assert check_zero_cost([{"tool": "t", "provider": provider, "cost_usd": 0.0}])["ok"] is True


def test_short_specs_non_numeric_duration_is_a_violation():
    probes = {"a": {"width": 1080, "height": 1920, "duration": "N/A"}}
    assert check_short_specs(probes)["ok"] is False


def test_fact_sources_tolerates_malformed_claims():
    video = _video(1)
    video["claims"] = [{"text": None, "sources": []}, "just a string"]
    result = check_fact_sources([video])
    assert result["ok"] is False


def test_footage_reuse_reports_missing_clip_id():
    video = _video(1)
    del video["clips"][0]["clip_id"]
    result = check_footage_reuse([video, _video(2)])
    assert result["ok"] is False
    assert any("missing clip_id" in v for v in result["violations"])
