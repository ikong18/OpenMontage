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

1. **Topics** - Claude suggests topics with sources; you edit them and click **Approve topics**.
2. **Script** - Claude writes narration, claims (each with a source) and posting text; you edit.
3. **Footage** - clips are downloaded (size-capped), Claude proposes cuts from contact sheets; you can change any cut.
4. **Voice and render** - narration and 1080x1920 renders (a few minutes per video).
5. **Review** - watch each video, Redo any you do not like, then click **Approve batch**.
6. **Export** - writes each video with title, description and hashtag, always private. Nothing is uploaded.

## What the AI can and cannot do

The thinking steps run your own `claude -p` (your subscription, no API keys) in a separate work folder with a short
tool allowlist (web pages for facts, file read for contact sheets). It returns JSON; Studio validates it and saves it.
It cannot write files, run commands, or approve anything: only your two button clicks run the approve commands.

## Safety

- Listens on `127.0.0.1` only; other `Host` headers get HTTP 400.
- Every change needs the `X-Studio-Token` header; the token is random per server start and embedded in the pages.
- One job at a time per batch. Edits are refused while a job runs.
- Free only: the existing zero-cost, size-cap and licence checks apply unchanged.

Design: `docs/superpowers/specs/2026-11-05-studio-ui-design.md`.
