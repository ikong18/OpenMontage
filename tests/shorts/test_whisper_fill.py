"""WhisperAligner._fill without any model: how unmatched words are placed."""

from lib.shorts.whisper_align import WhisperAligner

fill = WhisperAligner._fill


def _run(words, starts, ends, duration):
    return fill(words, list(starts), list(ends), duration)


def test_matched_words_keep_their_times():
    out = _run(["a", "b"], [0.5, 1.0], [0.9, 1.4], 2.0)
    assert [(t.start_ms, t.end_ms) for t in out] == [(500, 900), (1000, 1400)]


def test_trailing_words_whisper_missed_get_a_readable_time_inside_the_audio():
    out = _run(["a", "b", "c", "d", "e"], [0.0, 0.5, None, None, None], [0.4, 2.0, None, None, None], 2.6)
    tail = out[2:]
    assert all(t.end_ms - t.start_ms >= 150 for t in tail), [(t.start_ms, t.end_ms) for t in tail]
    assert tail[-1].end_ms <= 2600 + 1
    assert all(b.start_ms >= a.start_ms for a, b in zip(out, out[1:]))


def test_trailing_words_at_the_very_end_of_the_audio_still_get_time():
    out = _run(["a", "b", "c", "d"], [0.0, None, None, None], [2.0, None, None, None], 2.0)
    assert out[-1].end_ms <= 2000 + 1
    assert all(t.end_ms - t.start_ms >= 100 for t in out[1:]), [(t.start_ms, t.end_ms) for t in out]
    assert all(b.start_ms >= a.start_ms for a, b in zip(out, out[1:]))


def test_leading_words_are_spread_before_the_first_match():
    out = _run(["x", "y", "a"], [None, None, 1.2], [None, None, 1.5], 2.0)
    assert out[0].start_ms >= 0 and out[1].end_ms <= 1200 + 1 and out[2].start_ms == 1200


def test_gap_between_matches_is_filled_in_order():
    out = _run(["a", "x", "y", "b"], [0.0, None, None, 2.0], [0.5, None, None, 2.4], 3.0)
    assert 500 <= out[1].start_ms < out[2].start_ms < 2000
    assert all(t.end_ms > t.start_ms for t in out)


def test_every_word_has_a_positive_length_even_when_squeezed():
    out = _run(["a", "b", "c"], [1.0, None, 1.0], [1.0, None, 1.0], 1.0)
    assert all(t.end_ms - t.start_ms >= 30 for t in out)
