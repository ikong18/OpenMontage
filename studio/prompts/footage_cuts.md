You choose cut points for each short video in INPUT from contact sheets you can open with the Read tool.

INPUT gives, per video: id, narration, `needed_seconds` (total length to cover), and `sources` (key, sheet image path, sheet `step` seconds, `cols`, `duration`, and any seconds already used by other videos). A sheet is a grid read left to right, top to bottom; frame number n (starting at 0) is at n * step seconds.

Rules:
- Open each sheet image and look at it. Pick moments that show the video's subject. Skip title cards, logos, presenters talking to camera, text-heavy overlays and black frames.
- Each cut has `source` (a key from INPUT), `in` (seconds, start a little after the frame you saw so the shot is stable) and `dur` (2.5 to 4 seconds). Stay inside the clip's duration.
- The cuts' durations must add up to at least `needed_seconds` and at most `needed_seconds` + 4.
- Cuts must not overlap each other, other videos' cuts, or the `already_used` ranges for the same source.
- Order the cuts so the strongest image comes first.
Return one item per input video, same ids, and only the structured answer.
