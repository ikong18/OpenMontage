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


def page_text(url: str) -> str:
    """The visible text of a page, for checking that a cited source really says what the claim says."""
    import requests

    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (OpenMontage real-AI test)"}, timeout=45)
    resp.raise_for_status()
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", resp.text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text)


def numbers_in(text: str) -> set[str]:
    return {m.replace(",", "") for m in re.findall(r"\d[\d,]*\.?\d*", text)}
