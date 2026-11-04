# Visuals Director — Shorts Factory Pipeline

## When to Use

Pick free stock footage for every beat of every script and record exactly where it came from.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/scene_plan.schema.json` | Artifact validation |
| Prior artifact | `script` and `artifacts/shorts_batch.json` | Beats and video ids |
| Playbook | `styles/shorts-captions.yaml` | 9:16 framing, hold times 1.5-4 s |

**Tools:** `direct_clip_search`, `pexels_video`, `pixabay_video`, `clip_search`

## Process

### 1. Search

Use `direct_clip_search` with queries per beat. Pass `sources` explicitly. Always include the keyless adapters `nasa` and `archive_org`; add `pexels` and `pixabay` when their keys are configured in `.env`. A missing key is never a reason to stop or to use a paid tool: search the keyless sources and tell the owner which sources were used.

### 2. Prefer vertical or crop-safe footage

Choose clips whose subject survives a center 9:16 crop. Hold each clip 1.5-4 seconds. Cut on the beat of the narration.

### 3. Never reuse a clip in a batch

Every `clip_id` is unique across the whole batch, not just within one video. Check `artifacts/shorts_batch.json` before choosing.

### 4. Log provenance

For each video add a `clips` list to its entry in `artifacts/shorts_batch.json`:

```json
{"clip_id": "nasa_12345", "source": "nasa", "license": "public domain / NASA media usage", "url": "https://..."}
```

`source`, `license` and `url` are all required for every clip. If a clip's licence is unclear, discard it.

## Common Pitfalls

- A favorite clip showing up in three videos.
- Footage with burned-in watermarks or text.
- Landscape clips with the subject at the edge.
