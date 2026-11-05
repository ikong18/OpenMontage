# Voice and Captions Director — Shorts Factory Pipeline

## When to Use

Generate narration and word-level captions for every video, at zero cost.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/asset_manifest.schema.json` | Artifact validation |
| Prior artifacts | `script`, `scene_plan` | Narration text and beat timing |
| Playbook | `styles/shorts-captions.yaml` | Caption look and limits |

**Tools:** `piper_tts`, `subtitle_gen`, `audio_mixer`

## Process

### 1. Narrate with Piper

Use `piper_tts` for every video, with the same voice for the whole batch so the channel sounds consistent. Do not use any paid TTS tool. Announce the voice to the owner once.

### 2. Word-level captions

Use `subtitle_gen` to produce word-level timings. At most 4 words per caption line. The active word is highlighted per the playbook.

### 3. Loudness

Use `audio_mixer` to normalize narration loudness identically across the batch.

### 4. Record cost

For every tool call append to the video's `cost_entries` in `artifacts/shorts_batch.json`:

```json
{"tool": "piper_tts", "provider": "piper", "cost_usd": 0.0}
```

Every `cost_usd` must be `0.0`. A missing value fails the audit.

## Common Pitfalls

- Switching voices mid-batch.
- Captions that drift from the audio; check one video's timing by eye before doing the rest.

## Batch runner command

Run `python scripts/shorts_batch.py voice projects/<id>` (or `--video v1`). It narrates with `piper_tts`,
derives word timings from the audio's pauses, writes the caption files and records the zero-cost entries.
Timings are estimates, so check caption sync by eye in the finished video.
