from tests.real_ai.conftest import numbers_in, visible_text


def test_visible_text_drops_script_and_style_bodies():
    html = "<p>Venus 467 C</p><script type='x'>var a = 999;</script><style>.a{width:123px}</style><b>done</b>"
    text = visible_text(html)
    assert "467" in text and "done" in text and "999" not in text and "123" not in text


def test_numbers_in_ignores_single_digit_noise():
    assert numbers_in("about 4 or 44 flashes, 1,200 times, 0.72 AU") == {"44", "1200", "0.72"}
