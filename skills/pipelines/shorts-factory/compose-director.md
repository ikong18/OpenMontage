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

### 0. Runtime

Read `render_runtime` from the approved `render_runtime_selection` decision (locked to `"remotion"` for this pipeline). If a run arrives with `render_runtime == "hyperframes"`, stop and re-open the `idea` stage so the owner can be shown the constraint: HyperFrames lacks word-level caption burn parity here.

### 1. Render

Use `video_compose` with `operation: "remotion_render"` and `profile: "youtube_shorts"` (this is what makes the render 1080x1920; the Explainer composition defaults to landscape). Set `playbook: "shorts-captions"` in the composition props and apply the playbook's caption look with `captionStyle: {"fontSize": 84, "wordsPerPage": 4, "bottomOffset": 380, "highlightColor": "#FACC15"}` (without it captions fall back to 42 px, 6 words and 80 px from the bottom, which breaks the safe zone). Output to `renders/<id>.mp4` inside the project directory. The runtime was already presented and locked at the `idea` gate.

### 2. Verify each render

Run `ffprobe` on every output. Width 1080, height 1920, duration strictly under 60 seconds. Look at one frame: captions inside the safe zone (bottom margin >= 280 px, sides >= 60 px).

### 3. Record

Set each video's `render` field in `artifacts/shorts_batch.json` to the path relative to the project directory, for example `renders/v1.mp4`. Write the `render_report` and `final_review` artifacts.

## Failure Handling

If one video fails to render, retry it up to 3 times, then mark it `dropped` and continue.

## Common Pitfalls

- Rendering at 1920x1080 by default; always set the vertical size.
- Narration running past 60 seconds after captions are added.
