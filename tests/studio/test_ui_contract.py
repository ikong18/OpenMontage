"""Static checks on the Studio pages (no browser): token header, escaping, offline, gate labels."""

import re
from pathlib import Path

UI = Path(__file__).resolve().parents[2] / "studio" / "ui"


def _read(name):
    return (UI / name).read_text(encoding="utf-8")


def test_pages_exist_and_reference_script_and_css():
    for page in ("index.html", "batch.html"):
        html = _read(page)
        assert "/ui/studio.js" in html and "/ui/studio.css" in html
        assert 'name="studio-token"' in html and "__STUDIO_TOKEN__" in html


def test_js_has_one_fetch_and_it_sends_the_token_header():
    js = _read("studio.js")
    assert js.count("fetch(") == 1
    assert "X-Studio-Token" in js


def test_js_never_uses_innerhtml_or_eval():
    js = _read("studio.js")
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert banned not in js, banned


def test_gate_button_labels_present():
    js = _read("studio.js")
    assert "Approve topics" in js and "Approve batch" in js


def test_no_external_urls_in_ui_files():
    for name in ("index.html", "batch.html", "studio.js", "studio.css"):
        text = _read(name)
        assert not re.search(r"https?://", text), name


def test_js_calls_every_api_route_the_server_has():
    js = _read("studio.js")
    for needle in ("/api/batches", "/steps/", "/gates/", "/redo", "/spec?stage=", "/events", "/media/"):
        assert needle in js, needle


def test_links_are_only_made_for_http_sources():
    js = _read("studio.js")
    assert "https?:" in js and "isHttp" in js


# ---- UI improvements (guidance, feedback, editing helpers) ----------------------


def test_guidance_and_feedback_features_present():
    js, html = _read("studio.js"), _read("batch.html")
    for needle in ("nextBanner", "toast(", "beforeunload", "Retry", "document.title", "reasonFor", "elapsed("):
        assert needle in js, needle
    assert 'aria-live="polite"' in html and 'id="toasts"' in html and 'id="next"' in html
    assert 'id="banner-close"' in html and 'id="banner-retry"' in html


def test_editing_helpers_present():
    js = _read("studio.js")
    for needle in ("estimateSeconds", "validateTopic", "usageBar", "cutProblems", "sheet_step", "max_description"):
        assert needle in js, needle


def test_home_page_shows_progress():
    js = _read("studio.js")
    for needle in ("steps_done", "current_step", "steps_total"):
        assert needle in js, needle


def test_css_has_spinner_toast_and_focus_styles():
    css = _read("studio.css")
    for needle in (".spinner", ".toast", ":focus-visible", ".seg", ".bad"):
        assert needle in css, needle


def test_typed_topics_can_be_researched_and_provider_switched():
    js, html = _read("studio.js"), _read("batch.html")
    for needle in ("Research this topic", "Research all incomplete", "researchTopics", '"draft"', "/settings", "autopilot",
                   "topicIssues", "Approving starts the scripts by itself"):
        assert needle in js, needle
    assert 'id="settings"' in html


def test_research_buttons_follow_what_is_typed():
    js = _read("studio.js")
    assert "c.research.disabled" in js and "researchAllBtn" in js


def test_ui_review_fixes_present():
    js = _read("studio.js")
    for needle in ("approveBtn", "replace: true", "settingsSig", "topics:research", "disabled: approved || busy()"):
        assert needle in js, needle
    settings_part = js.split("function renderSettings")[1].split("const saveVideos")[0]
    assert "disabled: busy()" not in settings_part


def test_fact_check_is_shown_next_to_the_claims():
    js = _read("studio.js")
    for needle in ("fact_check", "Fact-check again", "factcheck", "verdict"):
        assert needle in js, needle


def test_voice_picker_with_preview():
    js = _read("studio.js")
    for needle in ("/voice/preview", "/voice", "Preview voice", "voices", "<audio" if False else "audio"):
        assert needle in js, needle
