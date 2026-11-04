# Topics Director — Shorts Factory Pipeline

## When to Use

First stage. Turn the owner's request into an approved list of N topics, or propose N topics when the owner asks you to. This is the batch's first human gate.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/brief.schema.json` | Artifact validation |
| Playbook | `styles/shorts-captions.yaml` | Format and pacing expectations |

**Tools:** none. Use web search for fact discovery.

## Process

### 1. Collect or propose topics

If the owner gave a list, use it. Otherwise propose N topics for the channel's niche: surprising but verifiable facts. Prefer facts you can source from at least two reputable pages.

### 2. Give each topic an angle, a hook type and a fact

Every topic records:

- `topic` — plain-language subject
- `angle` — the single surprising point
- `hook_type` — exactly one of: `question`, `shocking_stat`, `myth_bust`, `comparison`, `countdown`
- `fact` — one sentence plus at least one http(s) source URL

### 3. Enforce variety

No `hook_type` should cover more than 40% of a batch of 3 or more. Reject topics that repeat an earlier batch (check `projects/*/artifacts/shorts_batch.json` for the same `topic`).

### 4. Present and get approval

Show a table of topics, angles, hook types and sources. Record the choice in `decision_log`. Wait for the owner's explicit approval before the `script` stage.

## Common Pitfalls

- Topics whose "fact" is a popular myth; verify before proposing.
- All topics in one hook type.
- Presenting topics without sources.
