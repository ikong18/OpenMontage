# Studio: a local UI for the shorts-factory batch flow

Date: 2026-11-05. Status: draft for owner review.

## Purpose

The owner makes high-volume vertical Shorts/Reels ("did you know" space facts) for personal use, free tools only.
Today a batch is driven from a terminal or chat session. Studio is a local web UI where the owner creates a batch,
chooses topics, watches each step run, reviews the finished videos and exports them, with the same two approvals
as now (topics at the `idea` gate, finished batch at the `review_queue` gate).

Success means: one batch can go from "New batch" to a private export folder entirely from the browser, the terminal
and the UI never disagree about state, nothing is approved except by an owner click, and nothing costs money.

## Decisions already made with the owner

- Studio is a new package, separate from Backlot. Backlot stays read-only; Studio links to it for storyboards.
- The thinking steps (suggest topics, write scripts, pick footage cuts) run the owner's local `claude` CLI
  headlessly (`claude -p`) on their subscription. No API keys. Codex support is out of scope for version 1.
- Version 1 covers the whole batch flow. A clip-browsing footage picker, uploading and Codex are out of scope.

## Architecture

New package `studio/`, mirroring Backlot's stack (FastAPI, plain JavaScript pages, bound to 127.0.0.1).

| File | Responsibility |
|---|---|
| `studio/__main__.py` | `python -m studio open [batch-id]` / `serve --port 4760` |
| `studio/server.py` | `create_app(projects_dir, jobs, ai)`; routes, host check, token check |
| `studio/batches.py` | list batches, create a batch, load/save the spec, derive step state |
| `studio/steps.py` | pure functions: step state from files (spec, checkpoints, renders) |
| `studio/jobs.py` | background job runner: subprocess, log file, status, SSE feed |
| `studio/ai.py` | `AiClient` interface; `ClaudeCli` implementation; prompt files in `studio/prompts/` |
| `studio/ui/` | `index.html` (batches), `batch.html` (steps), `studio.js`, `studio.css` |

Studio holds no state of its own beyond a job log. All batch state lives where it lives today:
`projects/<id>/artifacts/batch_spec.json`, `shorts_batch.json`, and the `checkpoint_*.json` files. Every action
that changes a batch goes through the existing code (`lib/shorts/*`, `scripts/shorts_batch.py`), never around it.

## Steps and state

A batch moves through six steps. Each step's state (`locked`, `ready`, `running`, `done`, `failed`, `stale`) is
computed from files on every request, so a page reload or a terminal run changes nothing about correctness.

| Step | What the owner does | What runs | Done when |
|---|---|---|---|
| 1 Topics | Click Suggest topics; edit, add, remove; click Approve topics | AI suggests topics with hook type and source URLs; `validate --stage idea`; approval runs `checkpoints --idea-only --approve idea` | idea checkpoint approved |
| 2 Script | Click Write scripts; edit narration and claims | AI writes narration and claims per video; `validate --stage script` | every video has narration and claims that validate |
| 3 Footage | Click Find footage; adjust the cuts table | AI proposes search queries; runner `fetch` (size cap) ; contact sheets made; AI proposes cuts from the sheets; `validate --stage visuals` | every video has cuts that validate |
| 4 Voice and render | Click Make videos | `voice`, then `render`; progress per video | renders exist and are current (fingerprints) |
| 5 Review | Watch each video; Approve or Redo with a note; click Approve batch | `checkpoints` writes records; audit runs; approval runs `checkpoints --approve review_queue` | review_queue approved |
| 6 Export | Click Export | `export --approved <ids>`; private only; nothing uploaded | export folders exist |

Rules the UI enforces (and the server re-checks, never trusts the browser):
- A step is `locked` until the previous step is `done`.
- Editing an earlier step after later ones are done marks later steps `stale` (the existing fingerprints decide;
  Studio only displays it) and requires a re-run.
- Approvals happen only through the two Approve buttons. The AI process cannot reach them.
- Export is offered only for approved videos.

## AI steps

`ClaudeCli.run(prompt_file, inputs, schema, tools)` runs `claude -p` with `--output-format json`, `--json-schema`,
and a tool allowlist. It never writes project files; it returns JSON and Studio validates and saves it.

| Task | Allowed tools | Output |
|---|---|---|
| suggest topics | WebFetch, WebSearch | list of {topic, hook_type, hook_text, sources[]} |
| write scripts | WebFetch | per video {narration, claims[{text, sources[]}], title, description, tags} |
| footage queries | none | per video list of {source, query} |
| footage cuts | Read (limited to the batch's `review/` folder) | per video list of {source, in, dur} |

Prompt files hold the rules already in the director skills (hook-type variety, claims must cite a source, arithmetic
labelled as calculated, no clip reuse, 18 to 60 s). AI output that fails validation is shown with the validation
messages and can be rerun; it is never saved silently. Bash, Edit and Write tools are disallowed for these runs. The
exact CLI flags are confirmed against the installed `claude` version when the plan is written.

## Jobs

One job at a time per batch. A job is a subprocess (a `shorts_batch.py` command or an AI call) with a log file at
`projects/<id>/studio/jobs/<job-id>.log` and a record in `projects/<id>/studio/jobs.jsonl` (id, step, command,
started, ended, exit status). The page follows a batch's jobs over SSE (`/api/batches/<id>/events`). If the server
restarts, any job still marked running is marked failed. Long steps (render) show per-video progress by reading the
runner's one-line outputs.

## API

All under `127.0.0.1`. Reads are GET; every POST/PUT needs the `X-Studio-Token` header.

- `GET /api/batches`, `POST /api/batches {name, platform}` (creates `projects/<id>` with `project.json` and an empty spec)
- `GET /api/batches/<id>` returns spec, step states, recent jobs
- `PUT /api/batches/<id>/spec` edits video fields (topic, narration, claims, cuts, title, description, tags); validated
- `POST /api/batches/<id>/steps/<step>/run`
- `POST /api/batches/<id>/gates/<idea|review_queue>/approve`
- `POST /api/batches/<id>/videos/<vid>/redo {note}` clears that video's cuts/render and reruns steps 3 and 4 for it
- `GET /api/batches/<id>/events` (SSE), `GET /media/<id>/<path>` (renders, contact sheets; Range requests for the player)

## Security and cost

- Binds to 127.0.0.1; `TrustedHostMiddleware` allows only 127.0.0.1 and localhost on the chosen port.
- A random token is generated at server start and embedded in the served page; mutating routes require it
  (guards against other websites posting to the local server).
- Batch ids use the existing safe-id rule; media paths are resolved and must stay inside the batch folder.
- No provider outside the existing free allowlist; `check_zero_cost`, the size cap and the licence log run exactly as
  in the CLI. Studio never uploads anything and never sets a visibility other than private.

## Error handling

Every failure shows the job's last log lines and the validation messages in the step panel and offers Retry. A
failed AI call, a refused download (size cap), a missing ffmpeg or Piper, and a stale render each have a specific
message taken from the existing runner errors rather than a generic one.

## Testing

- Unit tests: `steps.py` state derivation from fixture batches; spec edits and their validation; token and host checks;
  path safety; job record handling.
- Server tests with FastAPI's test client and a `FakeAi` and a fake command runner, so no real Claude call, download
  or render runs in the test suite.
- One real end-to-end batch through the browser, with the owner approving at both gates, run and shown at the end.
- The existing suite must stay green.

## Out of scope for version 1

Clip-browsing footage picker; Codex or API-key providers; uploading to Facebook or YouTube; multi-user or remote
access; editing caption styling beyond the existing `caption_style` field; replacing Backlot.

## Risks

- AI footage picking from contact sheets is the weakest step; the cuts table is the manual fallback and is always editable.
- `claude -p` flags and output format can change between CLI versions; the plan pins what was verified on the installed version.
- Rendering is slow (about 3 minutes per video here); the UI must make progress and the ability to leave the page obvious.
