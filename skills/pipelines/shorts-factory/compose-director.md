# Compose Director — Shorts Factory Pipeline

## When to Use

Render every video to a finished 1080x1920 MP4.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/render_report.schema.json` | Artifact validation |
| Prior artifacts | `asset_manifest`, `scene_plan` | Narration, captions, footage |
| Playbook | `styles/shorts-captions.yaml` | Caption position and styling |

**Tools:** `video_compose`, `audio_mixer`

## Process

### 1. Render

Use `video_compose` at 1080x1920 with the `shorts-captions` playbook. Output to `renders/<id>.mp4` inside the project directory. Follow `AGENT_GUIDE.md` on presenting available composition runtimes before locking one; for this pipeline recommend Remotion for its caption support.

### 2. Verify each render

Run `ffprobe` on every output. Width 1080, height 1920, duration strictly under 60 seconds. Look at one frame: captions inside the safe zone (bottom margin >= 280 px, sides >= 60 px).

### 3. Record

Set each video's `render` field in `artifacts/shorts_batch.json` to the path relative to the project directory, for example `renders/v1.mp4`. Write the `render_report` and `final_review` artifacts.

## Failure Handling

If one video fails to render, retry it up to 3 times, then mark it `dropped` and continue.

## Common Pitfalls

- Rendering at 1920x1080 by default; always set the vertical size.
- Narration running past 60 seconds after captions are added.
