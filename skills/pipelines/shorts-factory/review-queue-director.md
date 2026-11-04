# Review Queue Director — Shorts Factory Pipeline

## When to Use

The batch's second and last human gate. Audit the batch, then let the owner review every finished Short in one sitting.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Prior artifacts | `render_report`, `final_review` | Finished renders |
| Audit | `scripts/shorts_audit.py` | Machine check of the batch rules |
| Viewer | Backlot (`python -m backlot open <project-id>`) | Visual review |

**Tools:** none.

## Process

### 1. Run the audit first

```bash
python scripts/shorts_audit.py projects/<project-id>
```

Exit code 0 is required before the queue opens. On violations, fix them (re-pick clips, rewrite hooks, re-render) and re-run. Do not present a batch the audit rejects, and do not edit the audit to make it pass.

### 2. Present the whole batch

Open Backlot and list every video with: title/topic, hook text, duration, claims with source links, and clip licence log. The owner watches them together.

### 3. Collect a decision per video

For each video the owner chooses **approve**, **reject** or **redo** (with a note). Redo means send the video back to the earliest stage the note affects, then re-run the audit. Record every decision in `decision_log`.

### 4. Gate

Complete the stage with human approval only when every video is approved or rejected. Rejected videos are excluded from `publish`.

## Common Pitfalls

- Showing the owner a batch with audit warnings.
- Approving the batch while a redo is still pending.
