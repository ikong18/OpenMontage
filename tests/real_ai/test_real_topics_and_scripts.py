"""Real AI, real web: does the provider find sources that load, write scripts that fit, and cite honestly?"""

import json
import time
from pathlib import Path

import pytest

from lib.shorts.spec import load_spec, save_spec, validate_spec
from studio.actions import plan_for
from studio.batches import create_batch
from studio.jobs import JobContext
from studio.apply import apply_research
from tests.real_ai.conftest import numbers_in, page_text

pytestmark = pytest.mark.live_api  # real, billable AI calls: needs OPENMONTAGE_ALLOW_NETWORK=1

TYPED_TOPIC = "Venus is hotter than Mercury even though Mercury is closer to the Sun"
HOOKS = {"question", "shocking_stat", "myth_bust", "comparison", "countdown"}


def _ctx(tmp_path):
    return JobContext(tmp_path / "job.log")


class _NoCli:
    def run(self, ctx, args, capture=False):  # the research and script steps never call the runner
        raise AssertionError(f"unexpected runner call: {args}")


def _typed_batch(tmp_path):
    bid = create_batch(tmp_path, "Real", "facebook_reels")
    spec = load_spec(tmp_path / bid)
    spec["videos"] = [{"id": "v1", "topic": TYPED_TOPIC}]
    save_spec(tmp_path / bid, spec)
    return tmp_path / bid


def _research(ai, project, tmp_path):
    started = time.time()
    plan_for("topics", project, ai, {"research": ["v1"], "theme": "space facts"}, _NoCli())(_ctx(tmp_path))
    return time.time() - started


def test_research_finds_sources_that_really_load(ai, provider, tmp_path):
    project = _typed_batch(tmp_path)
    seconds = _research(ai, project, tmp_path)
    video = load_spec(project)["videos"][0]
    print(f"\n[{provider}] research took {seconds:.0f}s -> {json.dumps(video, indent=1)}")
    assert video["hook_type"] in HOOKS and video["hook_text"].strip()
    assert 1 <= len(video["topic_sources"]) <= 3
    for url in video["topic_sources"]:
        assert len(page_text(url)) > 500, f"{url} did not load as a real page"
    assert not validate_spec(load_spec(project), project, "idea")


def test_scripts_fit_the_video_and_cite_pages_that_say_it(ai, provider, tmp_path):
    project = _typed_batch(tmp_path)
    _research(ai, project, tmp_path)
    started = time.time()
    plan_for("script", project, ai, {}, _NoCli())(_ctx(tmp_path))
    seconds = time.time() - started
    video = load_spec(project)["videos"][0]
    words = len(video["narration"].split())
    est = words / 2.9
    print(f"\n[{provider}] script took {seconds:.0f}s, {words} words (~{est:.0f}s)\n{video['narration']}\n"
          f"title={video['title']!r}\ndescription={video['description']!r}\ntags={video['tags']}")
    assert 18 <= est <= 55, f"narration is about {est:.0f} s"
    assert video["narration"].split(".")[0].strip().rstrip("?") in video["hook_text"] or video["hook_text"] in video["narration"], \
        "the first spoken sentence should be the hook"
    assert len(video["title"]) <= 100 and video["tags"] and "Source:" in video["description"]
    assert len(video["claims"]) >= 2
    assert not validate_spec(load_spec(project), project, "script")

    # every cited source must load; claims with figures must find one of those figures on a cited page
    verified, with_numbers, report = 0, 0, []
    for claim in video["claims"]:
        pages = {url: page_text(url) for url in claim["sources"]}
        figures = numbers_in(claim["text"])
        if figures:
            with_numbers += 1
            hit = any(figures & numbers_in(text) for text in pages.values())
            verified += 1 if hit else 0
            report.append((claim["text"], sorted(figures), hit))
    print(f"[{provider}] claims with figures verified on their cited pages: {verified}/{with_numbers}")
    for line in report:
        print("   ", line)
    assert with_numbers == 0 or verified / with_numbers >= 0.6, report
