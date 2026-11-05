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
