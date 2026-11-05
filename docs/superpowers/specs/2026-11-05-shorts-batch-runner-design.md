# Shorts Batch Runner — Design Spec

Date: 2026-11-05
Status: Draft, awaiting user review
Builds on: `2026-11-05-shorts-factory-design.md` (the `shorts-factory` pipeline, merged to `main` at `b4b63dd`)

## Goal

Replace the throwaway scripts used to run the first Shorts batch with committed, tested tools, so the
next batch is produced by repeatable commands that follow the pipeline's rules instead of an agent
improvising code. The owner's two gates (the `idea` topic list and the `review_queue` batch review) and
the $0 / free-only rule stay exactly as they are.

## Background

Batch 01 (`projects/shorts-space-batch-01`) was produced with ad-hoc scripts kept outside git:
narration + word timing, segment cutting + Remotion render, stage checkpoint writing, and export. They
worked, but they are not in the repo, are not tested, and conflict with `AGENT_GUIDE.md` Rule Zero
("do not write ad-hoc scripts"). One of them also pulled several gigabytes of unneeded footage because
the stock-search tool downloads NASA originals before knowing their length.

## Requirements

| Item | Decision |
|---|---|
| Interface | One command-line entry point, `scripts/shorts_batch.py`, with subcommands |
| Logic | Small tested modules in `lib/shorts/`; the CLI is a thin wrapper |
| Input | One batch spec file, `projects/<id>/artifacts/batch_spec.json`, written by the agent during the idea, script and visuals stages |
| Cost | $0. No paid provider can be selected by any subcommand |
| Gates | No subcommand marks a gated stage (`idea`, `review_queue`) completed unless the caller passes an explicit `--approve <stage>`; the agent does that only after the owner says yes in chat |
| Tests | No network. Tests needing ffmpeg/ffprobe skip cleanly when they are missing |
| Skills | The director skills tell the agent to call these commands instead of writing scripts |

## Non-goals

- YouTube upload (separate phase, own spec).
- A speech-recognition model. Word timing keeps the pause-based method, behind a small interface so a
  better aligner can replace it later.
- New topics, scheduling, or changes to the pipeline manifest's stages or gates.

## Batch spec file

`projects/<id>/artifacts/batch_spec.json` (UTF-8, BOM tolerated):

```json
{
  "project_id": "shorts-space-batch-01",
  "voice": {"model": "en_US-lessac-medium", "length_scale": 0.95},
  "sources": {
    "venus_hot": {"path": "assets/video/raw_v1/nasa_Why_is_Venus_so_Hot.mp4",
                  "clip_id": "nasa_Why_is_Venus_so_Hot", "provider": "nasa", "mode": "fill",
                  "license": "NASA Media Usage Guidelines (public domain with caveats)",
                  "url": "https://images.nasa.gov/details/Why%20is%20Venus%20so%20Hot"}
  },
  "videos": [{
    "id": "v1", "topic": "...", "hook_type": "comparison", "hook_text": "...",
    "narration": "full spoken text",
    "claims": [{"text": "...", "sources": ["https://..."]}],
    "cuts": [{"source": "venus_hot", "in": 56.0, "dur": 4.0}],
    "title": "<= 100 characters", "description": "...", "tags": ["..."]
  }]
}
```

`mode` is `fill` (centre crop to 9:16) or `blurpad` (fit the width over a blurred background; for 4:3
or pillarboxed footage). The existing `artifacts/shorts_batch.json` stays the audited, derived result.

## Subcommands and modules

| Command | Module(s) | What it does |
|---|---|---|
| `validate` | `lib/shorts/spec.py` | Loads and checks the spec: required fields, unique video ids, `hook_type` in the fixed set, every cut's source exists, **no `clip_id`+range reused across the batch**, every claim has an http(s) source |
| `fetch` | `lib/shorts/fetch.py` | Downloads one named clip from a free source adapter (`nasa`, `wikimedia`, `archive_org`) into `assets/video/`. Checks the file size first and **refuses anything over `--max-mb` (default 400)**, or of unknown size unless `--allow-unknown-size`. Writes the source URL and licence into the spec's `sources` entry |
| `voice` | `lib/shorts/align.py` | Runs `piper_tts` per video, then derives word timings from the audio's pauses and word lengths (`WordAligner` interface, `SilenceAligner` implementation); runs `subtitle_gen`; records zero-cost entries in `shorts_batch.json` |
| `render` | `lib/shorts/segments.py`, `lib/shorts/props.py` | Cuts each segment to a 1080x1920, 30 fps H.264 file (fill or blurpad), builds the Remotion props (`profile: youtube_shorts`, `playbook: shorts-captions`, the `captionStyle` values, narration, captions), calls `video_compose`. **Refuses to render if the cuts' total length is shorter than the narration** |
| `checkpoints` | `lib/shorts/checkpoints.py` | Writes the `script`, `visuals`, `voice_captions`, `compose` and `review_queue` stage records from the spec and render results, schema-valid. Writes `awaiting_human` for `idea` and `review_queue`; writes them `completed` only with `--approve idea` / `--approve review_queue` |
| `export` | `lib/shorts/export.py` | For approved videos only, calls `export_bundle` in posting order and writes the `publish_log` |
| `audit` | existing `scripts/shorts_audit.py` | Unchanged; `render` and `export` refuse to proceed if it fails |

All subcommands exit non-zero with a one-line reason on failure and never print a traceback for
expected problems (missing ffmpeg, missing file, malformed spec).

## Skills

`idea-director`, `script-director`, `visuals-director`, `voice-captions-director`, `compose-director`,
`review-queue-director` and `publish-director` are updated to name the matching subcommand and the spec
fields the agent must fill, and to say that writing ad-hoc scripts is not allowed. The manifest's stages,
gates and tools do not change.

## Error handling

- ffmpeg / ffprobe / Piper voice missing: a clear message naming the missing piece and how it was
  installed on this machine, never a stack trace.
- A failed segment or render: report the video id and stop that video; other videos continue (the
  pipeline's retry-then-drop policy stays with the agent).
- `fetch` over the size cap: refuses, names the size and suggests a smaller clip or a higher `--max-mb`.

## Testing

- `spec.validate`: duplicate ids, reused clip range, missing source, bad `hook_type`, claim without a
  source, non-ASCII topics.
- `fetch`: size cap and unknown-size handling using an injected fake downloader (no network).
- `align`: generated audio with known pauses (ffmpeg-generated, skipped without ffmpeg); word timings are
  monotonic, within the audio length, and cover every word.
- `segments` / `props`: filter strings for fill vs blurpad; props contain `profile`, playbook, the
  `captionStyle` values and the narration path; cuts shorter than narration are rejected.
- `checkpoints`: written records validate against the artifact schemas; gated stages are not completed
  without `--approve`.
- `export`: layout and `publish_log` validity; rejected/unapproved videos are skipped.
- CLI: exit codes and one-line errors for each failure above.
- Full existing suite still passes.

## Risks

- Pause-based word timing is an estimate; captions can drift. Kept behind `WordAligner` so an ASR-based
  aligner can be dropped in without touching callers.
- Remotion first run downloads a headless browser; the `render` command prints a one-line note when it
  is likely to do so.
- Footage licences: `fetch` records what the source adapter reports, but the owner remains responsible
  for checking terms (for example NASA's media guidelines on logos and endorsement).

## Open questions

- None blocking. A per-video cost check (reviewer minor) is included here as a `validate` rule.
