You choose cut points for each short video in INPUT from contact sheets you can open with the Read tool.

INPUT gives, per video: id, narration, `subject` (what the viewer must see), `needed_seconds` (total length to cover), and `sources` (key, sheet image path, sheet `step` seconds, `cols`, `duration`, `relevant_frames` = the frame numbers already verified as showing the subject, `shows` = what they show, and any seconds `already_used` by other videos). A sheet is a grid read left to right, top to bottom; frame number n (starting at 0) is at n * step seconds.

Rules:
- Open each sheet image and look at it. Use ONLY moments inside the `relevant_frames`: a cut that starts, runs through its middle and ends inside a relevant frame's time range (n * step to (n + 1) * step). Never cut to a frame that is not listed, even if it looks interesting.
- Each cut has `source` (a key from INPUT), `in` (seconds, a little after the start of a relevant frame so the shot is stable), `dur` (2.5 to 4 seconds), and `shows`: one plain sentence describing what that shot shows, from what you see (not from the title).
- The cuts' durations must add up to at least `needed_seconds` and at most `needed_seconds` + 4. If the verified frames cannot supply that, return as many valid cuts as you can; do not pad with unverified footage.
- Cuts must not overlap each other, other videos' cuts, or the `already_used` ranges for the same source.
- Order the cuts so the strongest image comes first.
Return one item per input video, same ids, and only the structured answer.
