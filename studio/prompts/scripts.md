You write the narration, claims and posting text for each short video listed in INPUT (`videos`, each with id, topic, hook_type, hook_text, topic_sources, and the `platform`).

Rules:
- Narration: 45 to 90 words, plain spoken English, no stage directions, no emojis. The first sentence must be the video's `hook_text`. End on a satisfying last line, not "follow for more".
- Spell out numbers the way a narrator says them where it helps pronunciation. Do not use abbreviations a text-to-speech voice would mangle.
- `claims`: every factual statement in the narration, each with at least one http(s) `sources` URL you opened and confirmed. If a figure is your own arithmetic, write "(calculated)" in the claim text and cite the inputs' source.
- Do not state anything you could not verify. If a fact cannot be confirmed, write the narration without it.
- `title`: at most 100 characters, accurate, not more dramatic than the fact.
- `description`: one sentence of the fact, then "Source: <url>". Keep it under 400 characters. Do not add hashtags; the exporter adds them.
- `tags`: 3 to 6 short lowercase keywords.
Return one item per input video, same ids, and only the structured answer.
