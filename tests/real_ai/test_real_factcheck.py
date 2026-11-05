"""Real AI, real web: does the fact checker catch a planted false claim, a miscited claim and an uncovered falsehood?

A checker that waves everything through is worse than none, so this test plants three traps in an otherwise true script.
"""

import pytest

from studio.ai import SCHEMAS, TASK_TOOLS
from studio.factcheck import apply_check, check_inputs

pytestmark = pytest.mark.live_api  # real, billable AI calls: needs OPENMONTAGE_ALLOW_NETWORK=1

VENUS = "https://science.nasa.gov/venus/venus-facts/"

SPEC = {"videos": [{
    "id": "v1",
    "narration": ("Venus is the hottest planet. Its surface is about four hundred sixty seven degrees Celsius. "
                  "Mercury is hotter than Venus on average. Saturn could float in a bathtub. Venus also has ninety moons."),
    "claims": [
        {"text": "Venus's surface temperature is about 467 degrees Celsius.", "sources": [VENUS]},          # true
        {"text": "On average Mercury's surface is hotter than Venus's surface.", "sources": [VENUS]},        # false
        {"text": "Saturn could float in a bathtub.", "sources": [VENUS]},                                    # true elsewhere, miscited here
    ],
}]}


def test_the_checker_catches_planted_errors(ai, provider):
    result = ai.run("fact_check", check_inputs(SPEC["videos"]), SCHEMAS["fact_check"], TASK_TOOLS["fact_check"])
    record = apply_check(SPEC, result["items"], "writer", provider)
    video = record["videos"]["v1"]
    verdicts = {c["index"]: c["verdict"] for c in video["claims"]}
    print(f"\n[{provider}] verdicts: {verdicts}")
    for c in video["claims"]:
        print(f"   {c['index']}: {c['verdict']} - {c['note']} | {c['quote']!r}")
    print(f"[{provider}] narration issues: {video['issues']}")
    assert verdicts[0] == "supported", "the true, correctly cited claim should be supported"
    assert verdicts[1] in ("unsupported", "partly"), "the false claim must not be supported"
    assert verdicts[2] != "supported", "a claim cited to a page that does not say it must not be supported"
    issues = " ".join(video["issues"]).lower()
    assert video["issues"] and ("moon" in issues or "ninety" in issues or "90" in issues), "the invented moons should be flagged"
