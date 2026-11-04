# Shorts Factory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `shorts-factory` pipeline that turns an approved topic list into a batch of free-to-produce, vertical "did you know" Shorts, reviewed once by the owner before anything is exported or uploaded.

**Architecture:** Purely additive. One new pipeline manifest, a set of director skills, one style playbook, and one small Python guardrail module that makes the batch rules (hook variety, footage reuse, 1080x1920 / under 60 s, zero cost, licence log, fact sources) machine-checkable. One project directory = one batch; per-video metadata lives in a supplementary `artifacts/shorts_batch.json` so the strict canonical artifact schemas are untouched. YouTube upload is phase 2 (Task 8).

**Tech Stack:** Python 3.11, pytest, PyYAML, jsonschema (already in repo); Remotion + Piper TTS + Pexels/Pixabay/NASA/Archive.org (free); ffprobe for render checks.

**Spec:** `docs/superpowers/specs/2026-11-05-shorts-factory-design.md`

## Global Constraints

- Aspect/size: 1080x1920 (9:16). Duration: strictly under 60 s (target 25-45 s).
- Cost: $0. Manifest `budget_default_usd: 0.00`; no paid provider may appear in any stage's `tools_available`.
- Approval gates: `human_approval_default: true` ONLY on `idea`, `review_queue` (and `publish` is `false` in phase 1; phase 2 sets it `true`).
- No existing file's behavior changes. New files only, except index/registry lines in `skills/INDEX.md`, `AGENT_GUIDE.md`, `docs/ARCHITECTURE.md`.
- Stage order: `idea`, `script`, `visuals`, `voice_captions`, `compose`, `review_queue`, `publish`.
- Uploads (phase 2) are `private` or scheduled only. The owner performs Google OAuth.
- All file I/O uses `encoding="utf-8"` (repo contract: `tests/contracts/test_utf8_file_io.py`).

## Review Focus

- Empty batch (zero videos) must fail every audit, never pass vacuously. Pinned in Task 1.
- Render of exactly 60.0 s, or 1920x1080 (landscape), must fail the spec check. Pinned in Task 1.
- A cost entry with a missing/`None` cost, or a paid provider name, must fail the zero-cost check. Pinned in Task 1.
- Missing Pexels/Pixabay keys must not strand the visuals stage: it must list at least one keyless source (`nasa`, `archive_org`). Pinned in Task 3.
- Non-ASCII topics (e.g. "Pokémon", "日本") must survive the batch file round-trip. Pinned in Task 2.

## File Structure

| File | Responsibility |
|---|---|
| `lib/shorts_guardrails.py` | Pure functions that check a batch; each returns `{"ok": bool, "violations": list[str]}` |
| `scripts/shorts_audit.py` | CLI: load `projects/<id>/artifacts/shorts_batch.json`, ffprobe renders, run all guardrails, exit non-zero on violations |
| `styles/shorts-captions.yaml` | Style playbook: big word-level captions, 9:16 safe zones, fast pacing |
| `pipeline_defs/shorts-factory.yaml` | Pipeline manifest |
| `skills/pipelines/shorts-factory/*.md` | Executive producer + 7 director skills |
| `tests/lib/test_shorts_guardrails.py`, `tests/pipelines/test_shorts_factory_manifest.py`, `tests/contracts/test_shorts_factory_skills.py`, `tests/lib/test_shorts_audit.py` | Tests |

---

### Task 1: Guardrail library

**Files:**
- Create: `lib/shorts_guardrails.py`
- Test: `tests/lib/test_shorts_guardrails.py`

**Interfaces:**
- Produces (all return `{"ok": bool, "violations": list[str]}`):
  - `check_hook_variety(videos: list[dict], max_share: float = 0.4) -> dict` — each video has `hook_type: str` and `hook_text: str`. Fails on empty list, a missing/blank `hook_type`, any `hook_type` used by more than `max_share` of videos (only enforced when batch has >= 3 videos), or duplicate `hook_text` (case-insensitive).
  - `check_footage_reuse(videos: list[dict]) -> dict` — each video has `clips: list[{"clip_id": str, ...}]`. Fails on empty list or any `clip_id` used in more than one place in the batch.
  - `check_short_specs(probes: dict[str, dict]) -> dict` — maps video id to `{"width": int, "height": int, "duration": float}`. Fails on empty dict, size != 1080x1920, or `duration >= 60.0`.
  - `check_zero_cost(entries: list[dict]) -> dict` — entries are `{"tool": str, "provider": str, "cost_usd": float | None}`. Fails if `cost_usd` is None/missing or `> 0`. An empty list passes only when called with `allow_empty=True` (parameter, default `False`).
  - `check_license_log(videos: list[dict]) -> dict` — every clip needs non-empty `source`, `license`, `url`.
  - `check_fact_sources(videos: list[dict]) -> dict` — each video has `claims: list[{"text": str, "sources": list[str]}]`; at least one claim, and every claim needs >= 1 `http(s)://` source.
  - `run_all(batch: dict, probes: dict, cost_entries: list[dict]) -> dict` — merges all of the above, prefixing each violation with the check name, `ok` only if all are ok.

- [ ] **Step 1: Write failing tests** in `tests/lib/test_shorts_guardrails.py`, one function per rule, with exact assertions:
  - `test_hook_variety_rejects_empty_batch`: `check_hook_variety([])["ok"] is False`.
  - `test_hook_variety_flags_dominant_type`: 4 videos, 3 with `hook_type="question"` -> `ok is False` and a violation mentioning `question`.
  - `test_hook_variety_flags_duplicate_hook_text`: two videos with `"Did you know X?"` and `"did you know x?"` -> `ok is False`.
  - `test_footage_reuse_flags_shared_clip_id`: same `clip_id` in two videos -> `ok is False`.
  - `test_specs_reject_exactly_60_seconds`: `{"a": {"width":1080,"height":1920,"duration":60.0}}` -> `ok is False`; `59.9` -> `ok is True`.
  - `test_specs_reject_landscape`: 1920x1080 -> `ok is False`.
  - `test_specs_reject_empty`: `{}` -> `ok is False`.
  - `test_zero_cost_rejects_none_and_positive`: `cost_usd=None` and `cost_usd=0.01` each -> `ok is False`; `0.0` -> `ok is True`.
  - `test_zero_cost_empty_needs_opt_in`: `[]` -> `ok is False`; `check_zero_cost([], allow_empty=True)["ok"] is True`.
  - `test_license_log_requires_all_three_fields`.
  - `test_fact_sources_requires_http_source_per_claim` and `test_fact_sources_rejects_video_with_no_claims`.
  - `test_run_all_prefixes_violations`: violation strings start with `"hook_variety:"` etc.
- [ ] **Step 2: Run** `.venv/Scripts/python.exe -m pytest tests/lib/test_shorts_guardrails.py -v`. Expected: collection error / FAIL (module missing).
- [ ] **Step 3: Implement** the functions above in `lib/shorts_guardrails.py` with the signatures given. Pure Python, no I/O, no new dependencies.
- [ ] **Step 4: Run the same command.** Expected: all PASS.
- [ ] **Step 5: Commit** `git add lib/shorts_guardrails.py tests/lib/test_shorts_guardrails.py && git commit -m "feat(shorts): add batch guardrail checks"`.

---

### Task 2: Audit CLI and batch file contract

**Files:**
- Create: `scripts/shorts_audit.py`
- Test: `tests/lib/test_shorts_audit.py`

**Interfaces:**
- Consumes: `lib.shorts_guardrails.run_all`.
- Produces:
  - `load_batch(project_dir: Path) -> dict` — reads `<project_dir>/artifacts/shorts_batch.json` (UTF-8). Shape: `{"videos": [{"id", "topic", "hook_type", "hook_text", "claims": [...], "clips": [...], "render": "renders/<id>.mp4", "cost_entries": [{"tool","provider","cost_usd"}]}]}`. Raises `FileNotFoundError` with the path in the message.
  - `probe_render(path: Path) -> dict` — runs `ffprobe -v error -select_streams v:0 -show_entries stream=width,height:format=duration -of json`, returns `{"width": int, "height": int, "duration": float}`.
  - `audit_project(project_dir: Path, probe=probe_render) -> dict` — builds probes for every video's `render` (relative to `project_dir`; a missing file is a violation `"missing render: <id>"`), flattens `cost_entries`, calls `run_all`, returns its result.
  - `main(argv: list[str] | None = None) -> int` — arg `project_dir`; prints violations one per line; returns `0` if ok else `1`.

- [ ] **Step 1: Write failing tests** using `tmp_path` and an injected fake `probe`:
  - `test_audit_passes_clean_batch` (two valid videos with distinct hooks/clips, zero costs, fake probe 1080x1920/40 s) -> `ok is True`.
  - `test_audit_reports_missing_render`.
  - `test_audit_fails_when_batch_file_missing` (`pytest.raises(FileNotFoundError)`).
  - `test_batch_file_roundtrips_non_ascii_topics`: topic `"Pokémon 日本"` written and loaded unchanged.
  - `test_main_exit_codes`: returns 1 for a violating batch, 0 for a clean one (pass probe via monkeypatching `scripts.shorts_audit.probe_render`).
- [ ] **Step 2: Run** `.venv/Scripts/python.exe -m pytest tests/lib/test_shorts_audit.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `scripts/shorts_audit.py` per the interfaces; ensure `scripts/__init__.py` import works (check how `tests` import other scripts, e.g. `scripts/backlot_simulate_run.py` uses `sys.path.insert`; mirror that).
- [ ] **Step 4: Run tests.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(shorts): add batch audit CLI`.

---

### Task 3: Style playbook and pipeline manifest

**Files:**
- Create: `styles/shorts-captions.yaml`, `pipeline_defs/shorts-factory.yaml`
- Test: `tests/pipelines/test_shorts_factory_manifest.py`

**Interfaces:**
- Consumes: `lib.pipeline_loader.load_pipeline`, `lib.checkpoint._stage_requires_approval`, `styles.playbook_loader.load_playbook`.
- Produces: pipeline name `shorts-factory`; playbook name `shorts-captions`.

- [ ] **Step 1: Write failing tests** in `tests/pipelines/test_shorts_factory_manifest.py`:
  - `test_manifest_loads_and_stage_order`: `[s["name"] for s in load_pipeline("shorts-factory")["stages"]] == ["topics","script","visuals","voice_captions","compose","review_queue","publish"]`.
  - `test_only_topics_and_review_queue_gate`: `_stage_requires_approval("shorts-factory", n)` is `True` for `idea` and `review_queue`, `False` for the other five.
  - `test_budget_is_zero`: `manifest["orchestration"]["budget_default_usd"] == 0.0`.
  - `test_no_paid_tools_listed`: no tool name in any stage's `tools_available` appears in `{"elevenlabs_tts","openai_tts","google_tts","azure_tts","heygen_video","kling_video","wan_video","image_selector","video_selector"}`.
  - `test_visuals_has_keyless_source`: `visuals` stage `tools_available` contains `nasa` or `archive_org`, and contains `pexels_video`.
  - `test_voice_stage_uses_piper`: `voice_captions` `tools_available` contains `piper_tts` and `subtitle_gen`.
  - `test_playbook_listed_and_valid`: `"shorts-captions" in list_playbooks()` and `load_playbook("shorts-captions")["identity"]["name"]`.
  - `test_required_skills_files_exist`: every entry of `required_skills` resolves to `skills/<entry>.md`.
- [ ] **Step 2: Run** `.venv/Scripts/python.exe -m pytest tests/pipelines/test_shorts_factory_manifest.py -v`. Expected: FAIL.
- [ ] **Step 3: Create `styles/shorts-captions.yaml`.** Copy the structure of `styles/clean-professional.yaml` (all seven required top-level keys). Values: bold sans font (Inter 800), high-contrast white text with dark outline, caption block kept inside the vertical safe zone (bottom margin >= 280 px, side margins >= 60 px), pace `fast`, cut every 2-4 s.
- [ ] **Step 4: Create `pipeline_defs/shorts-factory.yaml`.** Model on `pipeline_defs/clip-factory.yaml`: `category: custom`, `stability: beta`, `orchestration.mode: executive-producer`, skill `pipelines/shorts-factory/executive-producer`, `budget_default_usd: 0.00`, `compatible_playbooks.recommended: [shorts-captions]`, `required_skills` listing the 8 skills + `meta/reviewer` + `meta/checkpoint-protocol`. Stage tools: `visuals` -> `pexels_video, pixabay_video, nasa, archive_org, clip_search`; `voice_captions` -> `piper_tts, subtitle_gen, audio_mixer`; `compose` -> `video_compose, audio_mixer`. Artifacts: `idea`->`brief`; `script`->`script`; `visuals`->`scene_plan`; `voice_captions`->`asset_manifest`; `compose`->`render_report`,`final_review`; `review_queue`->`decision_log`; `publish`->`publish_log`. Set `human_approval_default` per Global Constraints and `max_wall_time_minutes: 60`.
- [ ] **Step 5: Run the new tests plus** `.venv/Scripts/python.exe -m pytest tests/pipelines tests/contracts/test_pipeline_catalog.py tests/styles -v`. Expected: PASS (the catalog tests iterate every manifest and playbook, so they now cover the new ones).
- [ ] **Step 6: Commit** `feat(shorts): add shorts-factory manifest and captions playbook`.

---

### Task 4: Director skills

**Files:**
- Create: `skills/pipelines/shorts-factory/{executive-producer,idea-director,script-director,visuals-director,voice-captions-director,compose-director,review-queue-director,publish-director}.md`
- Test: `tests/contracts/test_shorts_factory_skills.py`

**Interfaces:**
- Consumes: manifest `required_skills` and stage `skill` fields from Task 3; `scripts/shorts_audit.py` from Task 2.
- Produces: the instructions the agent follows for each stage.

- [ ] **Step 1: Write failing tests**:
  - `test_every_manifest_skill_exists_and_nonempty` for each stage `skill` and `required_skills` entry.
  - `test_skills_reference_only_real_tools`: every backticked tool name listed in a skill's "Tools" line exists in `tools/tool_registry.py` discovery (use `ToolRegistry().discover()` then `.get(name)`; mirror how `tests/contracts/test_agent_skill_pointers.py` does it).
  - `test_script_director_mentions_hook_types_and_sources` (contains `hook_type` and `sources`).
  - `test_review_queue_runs_audit` (contains `scripts/shorts_audit.py`).
- [ ] **Step 2: Run** `.venv/Scripts/python.exe -m pytest tests/contracts/test_shorts_factory_skills.py -v`. Expected: FAIL.
- [ ] **Step 3: Write the eight skills**, each following the section layout of the matching `skills/pipelines/clip-factory/*.md` file (When to Use, Prerequisites, Process, Quality checks, Common pitfalls). Required content:
  - **executive-producer:** batch state (N videos, per-video status), the stage order, only two human gates, zero-cost rule, "announce provider before generating" contract, per-video failure handling (retry up to 3, then drop the video from the batch and say so).
  - **idea-director:** propose or accept N topics; each with angle, intended `hook_type` from a fixed set (`question`, `shocking_stat`, `myth_bust`, `comparison`, `countdown`), and a sourced fact; duplicates against previous batches are rejected; the one-approval gate.
  - **script-director:** 25-45 s, hook in the first 2 s, one fact, loop/payoff ending; fills `claims` with sources; writes `artifacts/shorts_batch.json` entries (`id`, `topic`, `hook_type`, `hook_text`, `claims`).
  - **visuals-director:** choose clips per beat from `pexels_video`, `pixabay_video`, `nasa`, `archive_org`; if a key is missing, fall back to keyless sources and say so; record `clips` with `clip_id`, `source`, `license`, `url`; no `clip_id` reused in the batch.
  - **voice-captions-director:** `piper_tts` narration, `subtitle_gen` word-level captions, loudness consistent across the batch.
  - **compose-director:** render 1080x1920 with `video_compose` and the `shorts-captions` playbook to `renders/<id>.mp4`; ffprobe each; record `render`.
  - **review-queue-director:** run `python scripts/shorts_audit.py projects/<id>`; refuse to open the queue on violations; present all videos on Backlot; per-video approve / reject / redo; write the decision to `decision_log`.
  - **publish-director:** phase 1 export folder via `export_bundle` with title (<= 100 chars), description, tags per approved video; phase 2 note: uploader tool is private/scheduled only.
- [ ] **Step 4: Run tests.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(shorts): add shorts-factory director skills`.

---

### Task 5: Register the pipeline in docs and indexes

**Files:**
- Modify: `skills/INDEX.md` (add a "Shorts Factory Pipeline" table beside the Clip Factory one, line ~189), `AGENT_GUIDE.md` (pipeline table near line 251), `docs/ARCHITECTURE.md` (table near line 213).

- [ ] **Step 1:** Add one row/table per file following the existing format, status `beta`, description "Batch of vertical facts Shorts, free tools only".
- [ ] **Step 2: Run the whole fast suite** `.venv/Scripts/python.exe -m pytest tests/contracts tests/lib tests/pipelines tests/styles -q`. Expected: PASS with no new failures versus a pre-change run (record the baseline failures first with `git stash` if any exist).
- [ ] **Step 3: Commit** `docs(shorts): register shorts-factory`.

---

### Task 6: Environment for real renders

**Files:** none tracked (`.env` is git-ignored).

- [ ] **Step 1:** `cd remotion-composer && npx --yes npm install` (Windows workaround from README). Expected: exits 0.
- [ ] **Step 2:** `Copy-Item .env.example .env`. Owner adds free keys: `PEXELS_API_KEY`, `PIXABAY_API_KEY` (optional; NASA/Archive.org work keyless).
- [ ] **Step 3:** `.venv/Scripts/python.exe -m pip install piper-tts`, then run the repo preflight (`make preflight` equivalent: `.venv/Scripts/python.exe -c "from tools.tool_registry import registry; registry.discover(); print(registry.support_envelope())"` — confirm the exact entry point by reading `Makefile` target `preflight`). Expected: `piper_tts`, `video_compose`, `subtitle_gen` available.
- [ ] **Step 4:** Confirm `ffprobe` and `ffmpeg` are on PATH (`ffprobe -version`). If missing, tell the owner and stop.

---

### Task 7: End-to-end sample batch (3 Shorts)

- [ ] **Step 1:** In a fresh agent session, give: "Make 3 'did you know' Shorts about <3 topics>" and follow `AGENT_GUIDE.md` Rule Zero for pipeline `shorts-factory`. Approve the topic list at the `topics` gate.
- [ ] **Step 2:** After `compose`, run `.venv/Scripts/python.exe scripts/shorts_audit.py projects/<id>`. Expected: exit 0, no violations.
- [ ] **Step 3:** Open Backlot (`python -m backlot open <id>`); play all three; confirm captions are in sync and the licence log exists. Approve at `review_queue`.
- [ ] **Step 4:** Confirm the export folder holds three MP4s plus metadata. Fix any skill wording that caused agent stumbles, re-run the affected tests, commit `fix(shorts): tighten skills after sample run`.

---

### Task 8 (Phase 2, separate plan after owner sign-off on Task 7): YouTube uploader

Out of scope for this plan's execution. Create `tools/publishers/youtube_upload.py` (YouTube Data API v3, `privacyStatus` limited to `private`/scheduled, quota-aware), flip `publish.human_approval_default` to `true`, and add its own spec section. Write it as a new spec -> plan cycle.

---

## Self-review notes

- Spec coverage: pipeline + stages (Task 3), skills (4), playbook (3), guardrails — variety, reuse, fact sources, licence log, zero cost, specs (1, 2), testing (all + 7), phase-2 upload (8), registration (5), environment (6).
- Types: `check_*` signatures in Task 1 are the ones Task 2 calls via `run_all(batch, probes, cost_entries)`; field names (`hook_type`, `hook_text`, `clips[].clip_id`, `claims[].sources`, `render`, `cost_entries`) are identical across Tasks 1, 2 and 4.
- Known risk: exact `ToolRegistry` discovery call and the `preflight` entry point are confirmed by reading the named existing files during Tasks 4 and 6.
