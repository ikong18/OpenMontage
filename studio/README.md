# Studio - a local UI for the shorts-factory batch flow

Create a batch, choose topics, watch each step run, review the videos and export them, all from the browser.
Studio keeps no state of its own: everything is read from `projects/<id>/`, so the terminal and the page always agree.

```bash
python -m studio open            # start the server if needed and open the browser
python -m studio open <batch-id> # jump straight to one batch
python -m studio serve           # run in the foreground (default port 4760, STUDIO_PORT to change)
```

Start it from a normal terminal, not from inside a Claude Code session: the `claude` CLI refuses to start inside another one.

## The six steps

1. **Topics** - Claude (or Codex) suggests topics, or you type your own and click **Research this topic**: the AI finds source pages, picks the hook type and writes the first sentence. You edit, then click **Approve topics**, which also starts the scripts by itself.
2. **Script** - written automatically after you approve the topics: narration, claims (each with a source the AI opened) and posting text. You read and edit. Tick **Keep going automatically** to also run footage, voice and render, stopping before Review.
   Right after the scripts, the *other* AI (Codex if Claude wrote them, and the reverse) opens every cited page and marks each claim supported, partly, not supported or could-not-check, with the quote it found. You see this next to each claim; it never blocks you, you decide. If only one AI is installed, the same one checks and Studio says so.
3. **Footage** - clips are downloaded (size-capped), Claude proposes cuts from contact sheets; you can change any cut.
4. **Voice and render** - narration and 1080x1920 renders (a few minutes per video).
5. **Review** - watch each video, Redo any you do not like, then click **Approve batch**.
6. **Export** - writes each video with title, description and hashtag, always private. Nothing is uploaded.

## What the AI can and cannot do

The thinking steps run your own `claude -p` or `codex exec` (your subscriptions, no API keys; pick one per batch in the page header) in a separate work folder outside the repo.
Claude gets a short tool allowlist (web pages for facts, file read for contact sheets) and none of your MCP servers. Codex runs in its read-only sandbox with your MCP servers switched off, but it has no tool allowlist: it can still read files and run read-only commands, so only point Studio at topics and pages you trust. It returns JSON; Studio validates it and saves it.
Nothing it returns is executed, and it cannot approve anything: only your two button clicks run the approve commands.

## Safety

- Listens on `127.0.0.1` only; other `Host` headers get HTTP 400.
- Every change needs the `X-Studio-Token` header; the token is random per server start and embedded in the pages.
- One job at a time per batch. Edits are refused while a job runs.
- Free only: the existing zero-cost, size-cap and licence checks apply unchanged.

Design: `docs/superpowers/specs/2026-11-05-studio-ui-design.md`.

## Real-AI tests

`tests/real_ai` runs the real CLIs and the real web (minutes, uses your subscriptions). They are skipped normally. From a normal terminal:

```bash
$env:STUDIO_REAL_AI = "claude,codex"; $env:OPENMONTAGE_ALLOW_NETWORK = "1"
python -m pytest tests/real_ai -s -p no:cacheprovider
```
