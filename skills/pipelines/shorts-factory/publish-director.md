# Publish Director — Shorts Factory Pipeline

## When to Use

Export the approved Shorts with upload-ready metadata. Phase 1 exports files; it does not upload.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/publish_log.schema.json` | Artifact validation |
| Prior artifacts | `render_report`, `final_review`, `decision_log` | Approved videos |

**Tools:** `export_bundle`

## Process

### 1. Export only approved videos

Read the review decisions. Rejected and dropped videos are not exported.

### 2. Metadata per video

For each approved video write: a title of 100 characters or fewer, a description (one-sentence fact, source links, and `#Shorts`), and 5-10 tags. Order the videos for posting so hook types alternate.

### 3. Export

Use `export_bundle` to place `renders/<id>.mp4` and its metadata in one export folder. Write the `publish_log` artifact.

### 4. Upload (phase 2, not yet built)

Direct YouTube upload is a later phase. When added it will be limited to `private` or scheduled uploads. Until then tell the owner the export folder is ready to upload by hand.

## Common Pitfalls

- Exporting a rejected video.
- Titles that overpromise compared with the fact in the video.

## Batch runner command

Fill each approved video's `title`, `description` and `tags` in `batch_spec.json`, then run
`python scripts/shorts_batch.py export projects/<id> --approved v1,v3` (ids in posting order). It re-runs the
audit, exports only those ids as private and writes the publish stage. Nothing is uploaded.

### Platform

The export follows the spec's `"platform"` (`youtube_shorts` or `facebook_reels`): it sets the platform label and
the default hashtag (`#Shorts` / `#Reels`). `validate` checks each description against the platform's length
and hashtag limits (conservative: 2,000 characters and 30 hashtags for `facebook_reels`). Exports are always
marked private; upload by hand until an uploader exists.
