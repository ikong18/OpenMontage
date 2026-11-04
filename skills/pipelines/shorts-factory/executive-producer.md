# Executive Producer — Shorts Factory Pipeline

## When to Use

You are the **Executive Producer (EP)** for a `shorts-factory` project: one project is one **batch** of N vertical "did you know" Shorts (1080x1920, under 60 seconds), built with **free tools only**. You run the whole batch with two human gates and no other stops.

## Hard Rules

- **$0 generation cost.** Budget is `0.00`. Never call a paid provider (ElevenLabs, OpenAI, Google TTS, any AI video/image generator). Narration is `piper_tts`; footage comes from stock sources.
- **Two human gates only:** `idea` (owner approves the topic list and the render runtime) and `review_queue` (owner approves the finished batch). Do not stop the owner between those two points.
- **Announce before executing:** before each tool family runs for the first time in a batch, say the tool, provider, and why (see `AGENT_GUIDE.md` Decision Communication Contract).
- **Batch metadata** lives in `artifacts/shorts_batch.json` (not a canonical artifact). Every video has `id`, `topic`, `hook_type`, `hook_text`, `claims`, `clips`, `render`, `cost_entries`.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Pipeline | `pipeline_defs/shorts-factory.yaml` | Stage order, gates, tools |
| Playbook | `styles/shorts-captions.yaml` | Caption and pacing rules |
| Audit | `scripts/shorts_audit.py` | Machine check before the review queue |
| Skills | the seven stage directors in this folder | Stage execution |

## Cumulative State

```
EP_STATE:
  pipeline: shorts-factory
  playbook: shorts-captions
  budget_total_usd: 0.00
  videos: { <id>: { status: pending|scripted|footage|voiced|rendered|dropped|approved|rejected, retries: 0 } }
  issues_log: []
```

## Flow

1. `idea` -> owner approves the list.
2. For every video, run `script` -> `visuals` -> `voice_captions` -> `compose` without stopping. Work stage by stage across the batch (all scripts, then all footage, and so on) so batch-wide rules can be checked.
3. `review_queue` -> run the audit, then present everything on Backlot.
4. `publish` -> export approved videos only.

## Cross-Stage Checks

- After `script`: hook types are varied; every claim has an http(s) source.
- After `visuals`: no `clip_id` appears twice in the batch; every clip logs `source`, `license`, `url`.
- After `voice_captions`: every `cost_usd` is `0.0`.
- After `compose`: every render probes at 1080x1920 and under 60 s.

## Failure Handling

Retry a failing video up to 3 times (a different clip, a rewritten hook). If it still fails, mark it `dropped`, tell the owner which video and why, and continue with the rest. Never lower a rule to save a video.

## Common Pitfalls

- Reusing a favorite clip across videos.
- Five videos that all open with a question.
- Letting a paid fallback slip in because a key was missing; fall back to keyless sources instead.
