# Shorts Factory — Design Spec

Date: 2026-11-05
Status: Draft, awaiting user review

## Goal

Adapt this fork of OpenMontage for a personal YouTube channel that publishes high-volume
vertical "did you know" fact Shorts, with automation, at **$0 generation cost** and a
**single batch approval** by the owner before anything is uploaded.

## Agreed requirements

| Item | Decision |
|---|---|
| Use | Owner's own YouTube channel (AGPL-3.0 fork, personal use) |
| Format | Shorts/Reels factory, 9:16, 1080x1920, under 60 s (target 25-45 s) |
| Niche | Facts / "did you know" |
| Topic source | Owner-provided list, or agent-proposed list approved once |
| Automation | Batch: topic list -> N videos -> one review session -> approved ones publish |
| Budget | Free only: no paid API keys, no paid providers |
| Approach | New `shorts-factory` pipeline alongside existing ones (nothing existing modified) |
| Upload | Phase 2. Phase 1 exports files plus title/description/tags |

## Non-goals

- AI video or music generation (paid/GPU), scheduler daemon, other niches, rewriting existing pipelines.

## Components

1. `pipeline_defs/shorts-factory.yaml` — manifest. Stages: `topics`, `script`, `visuals`,
   `voice_captions`, `compose`, `review_queue`, `publish`. Only `topics` and `review_queue`
   (and `publish` in phase 2) have `human_approval_default: true`. Budget default 0.00 USD.
2. `skills/pipelines/shorts-factory/*.md` — executive-producer plus one director skill per
   stage, modelled on `skills/pipelines/clip-factory/` and `skills/pipelines/explainer/`.
3. `styles/` — one or two Shorts playbooks (large word-by-word captions, safe zones, pacing).
4. Phase 2: `tools/publishers/youtube_upload.py` — YouTube Data API v3 uploader, private or
   scheduled uploads only by default. The owner performs the one-time Google OAuth sign-in.
   Default quota allows roughly 6 uploads per day.

## Flow

1. **Topics** (approval): list of N topics, each with an angle and an intended hook.
2. **Per video, no stops**: script (hook in first 2 s, one fact, loop/payoff ending) ->
   fact-check against cited sources -> free footage selection (Pexels, Pixabay, NASA,
   Archive.org) -> Piper narration -> Remotion render at 1080x1920 with word-level captions.
3. **Review queue** (approval): all finished Shorts shown together on Backlot with sources;
   per-video approve / reject / redo.
4. **Publish**: only approved videos. Phase 1: export folder with metadata. Phase 2: upload.

## Guardrails

- Zero-cost enforcement: pipeline manifest forbids paid providers; preflight must show none selected.
- Anti-repetition: varied hook structures per batch; no footage clip reused within a batch.
- Fact-check gate: a video with an unsourced or failed claim is sent back, not queued.
- Licence log per video (source, licence, URL) stored with the artifacts.
- Policy: mass-produced repetitive content risks YouTube demonetization; the review step
  and variation rules are the mitigation. Uploads start private/scheduled.

## Testing

- Pipeline manifest passes `schemas/pipelines/pipeline_manifest.schema.json`.
- Existing test suite still passes (`make test`).
- Render 3 sample Shorts; verify 1080x1920, duration < 60 s, captions in sync, licence log present.

## Open questions

- Remotion install and Piper voice choice are verified during implementation.
- Whether Pexels/Pixabay free API keys are acceptable (they are free but need sign-up).
