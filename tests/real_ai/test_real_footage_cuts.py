"""Real AI looks at a real contact sheet and picks cuts (this is the path where Codex gets an attached image)."""

from pathlib import Path

import pytest

from studio.ai import SCHEMAS, TASK_TOOLS

pytestmark = pytest.mark.live_api  # real, billable AI calls: needs OPENMONTAGE_ALLOW_NETWORK=1

SHEET = Path(__file__).resolve().parents[2] / "projects" / "shorts-space-batch-02" / "review" / "sheet_moon.png"
DURATION = 100.2


@pytest.mark.skipif(not SHEET.is_file(), reason="needs the batch-02 contact sheet (projects/ is not in git)")
def test_cuts_picked_from_a_real_contact_sheet_are_usable(ai, provider):
    needed = 12.0
    inputs = {
        "videos": [{"id": "v1", "narration": "The Moon swings from 260 degrees to minus 280 degrees Fahrenheit.", "needed_seconds": needed}],
        "sources": [{"key": "moon", "sheet": str(SHEET), "step": 4.0, "cols": 6, "duration": DURATION, "already_used": []}],
    }
    result = ai.run("footage_cuts", inputs, SCHEMAS["footage_cuts"], TASK_TOOLS["footage_cuts"], add_dirs=[SHEET.parent])
    items = {i["id"]: i for i in result["items"]}
    cuts = items["v1"]["cuts"]
    print(f"\n[{provider}] cuts: {cuts}")
    assert cuts and all(c["source"] == "moon" for c in cuts)
    assert sum(c["dur"] for c in cuts) >= needed
    assert all(2.0 <= c["dur"] <= 5.0 for c in cuts)
    assert all(0 <= c["in"] and c["in"] + c["dur"] <= DURATION + 0.05 for c in cuts)
    spans = sorted((c["in"], c["in"] + c["dur"]) for c in cuts)
    assert all(b[0] >= a[1] - 0.05 for a, b in zip(spans, spans[1:])), f"cuts overlap: {spans}"
    # the sheet's first frames are an intro title card and the last is an end card: cuts should avoid them
    assert all(c["in"] >= 4.0 and c["in"] + c["dur"] <= DURATION - 8 for c in cuts), spans
