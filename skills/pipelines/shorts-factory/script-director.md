# Script Director — Shorts Factory Pipeline

## When to Use

Write one script per approved topic. Each is a 25-45 second narration for a vertical Short.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/script.schema.json` | Artifact validation |
| Prior artifact | `brief` | Approved topics, angles, hook types |

**Tools:** none.

## Script Structure

1. **Hook (0-2 s):** one line in the topic's `hook_type`. The viewer must know the payoff is coming.
2. **Fact (2-30 s):** one fact, explained in short spoken sentences. No filler openers such as "In today's video".
3. **Payoff or loop (last 3-5 s):** a closing line that lands the surprise or leads back into the hook so the Short loops.

Target 70-110 spoken words total. Write for the ear, not the page.

## Required Output

For every video append to `artifacts/shorts_batch.json` (create the file with `{"videos": []}` if missing, UTF-8):

```json
{
  "id": "v1",
  "topic": "...",
  "hook_type": "myth_bust",
  "hook_text": "The exact first spoken line",
  "claims": [{"text": "A factual statement in the script", "sources": ["https://..."]}]
}
```

Every factual statement in the script is a `claims` entry with at least one http(s) URL in `sources`. A claim you cannot source is removed from the script, not left in. Also save the narration into the canonical `script` artifact.

## Batch Rules

- `hook_text` is unique in the batch (case-insensitive).
- No `hook_type` covers more than 40% of the batch when there are 3 or more videos.

## Common Pitfalls

- Burying the hook after a greeting.
- Two facts in one Short.
- Claims sourced only to an AI summary or a content farm.

## Batch runner command

Put each script into its video in `batch_spec.json`: `narration` (the full spoken text, hook first),
`hook_text`, and `claims` (each with http(s) `sources`). `cuts`, `title`, `description` and `tags` come in the
next stages. Run `python scripts/shorts_batch.py validate projects/<id> --stage script` and fix every violation it reports before moving on (it checks only the fields up to this stage; a plain `validate` runs at the end).
