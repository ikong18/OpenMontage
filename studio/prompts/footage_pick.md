You choose which search results are worth downloading, using only their titles and descriptions. INPUT lists `videos`; each has an id, a `subject` (what the viewer must see), the narration, and `candidates` (clip_id, title and description text, duration in seconds when known, size in MB when known).

Pick up to 5 clips per video, best first:
- `match` "exact": the title or description says the clip shows the subject itself.
- `match` "related": it shows a close relative or the same kind of thing (another species of the same genus, the same type of object); say so in `why`.
- REJECT (leave out) anything that is about something else, even if a word matches: lectures, talks, interviews, panel discussions, conference recordings, documentaries about other topics, news broadcasts, music videos, slideshows, title cards, maps, animations of other things. Titles in other languages still count: read them. A clip is never acceptable merely because it was returned by a search.
- Prefer clips that keep showing the subject (15 to 300 seconds) over very long videos, and exact over related.
- If nothing plausible exists, return an empty `picks` list: it is far better to find nothing than to use the wrong footage.
`why` is one short sentence quoting what in the title or description supports the choice. Return one item per input video, same ids, and only the structured answer.
