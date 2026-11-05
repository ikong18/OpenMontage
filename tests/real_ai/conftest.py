"""Real-AI tests: they call the actual `claude` and `codex` CLIs and the real web.

They are skipped unless STUDIO_REAL_AI names the providers to try (comma separated, e.g. "claude,codex").
They use your subscriptions and take minutes, so the normal test run leaves them out:

    PowerShell:  $env:STUDIO_REAL_AI = "codex"; $env:OPENMONTAGE_ALLOW_NETWORK = "1"
                 python -m pytest tests/real_ai -s -p no:cacheprovider

`claude` refuses to start inside another Claude Code session, so run those from a normal terminal.
"""

import os
import re

import pytest

PROVIDERS = [p.strip() for p in os.environ.get("STUDIO_REAL_AI", "").split(",") if p.strip()]


def pytest_collection_modifyitems(config, items):
    if PROVIDERS:
        return
    skip = pytest.mark.skip(reason="set STUDIO_REAL_AI=claude,codex to run the real-AI tests")
    for item in items:
        if "real_ai" in str(item.fspath):
            item.add_marker(skip)


@pytest.fixture(params=PROVIDERS or ["claude"])
def provider(request):
    return request.param


@pytest.fixture
def ai(provider, tmp_path):
    from studio.ai import ClaudeCli, CodexCli

    work = tmp_path / "ai-work"
    return (ClaudeCli(work, timeout=900) if provider == "claude" else CodexCli(work, timeout=900))


def visible_text(html: str) -> str:
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text)


def page_status(url: str) -> tuple[int, str]:
    """(HTTP status, visible text). Status 0 = could not connect. Some sites answer scripts with 403/429 though the page exists."""
    import requests

    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (OpenMontage real-AI test)"}, timeout=45)
    except requests.RequestException:
        return 0, ""
    return resp.status_code, visible_text(resp.text)


def assert_sources_are_real(urls: list[str]) -> list[str]:
    """Every cited page must exist (200, or 403/429 = exists but blocks scripts); at least one must be readable here."""
    readable = []
    for url in urls:
        status, text = page_status(url)
        assert status == 200 or status in (401, 403, 429), f"{url} answered {status}: not a real page"
        if status == 200 and len(text) > 500:
            readable.append(url)
    assert readable, f"none of {urls} could be read to check it"
    return readable


def numbers_in(text: str) -> set[str]:
    """Figures with at least two digits: a lone 1 or 2 matches any page and proves nothing."""
    found = {m.replace(",", "") for m in re.findall(r"\d[\d,]*\.?\d*", text)}
    return {f for f in found if len(re.sub(r"\D", "", f)) >= 2}
