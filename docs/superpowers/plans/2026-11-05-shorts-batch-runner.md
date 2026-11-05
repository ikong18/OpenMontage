# Shorts Batch Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the throwaway batch scripts with committed, tested tools behind one command, `scripts/shorts_batch.py`, driven by a single batch spec file.

**Architecture:** Small pure-ish modules in `lib/shorts/` (spec, batch file, fetch, align, segments, props, checkpoints, export); a thin argparse CLI wraps them; the director skills are updated to call the CLI. Existing tools (`piper_tts`, `subtitle_gen`, `video_compose`, `export_bundle`) are called through the tool registry, never reimplemented.

**Tech Stack:** Python 3.11, pytest, ffmpeg/ffprobe (tests that need them skip when missing), Remotion via `video_compose`, existing `lib.checkpoint`.

**Spec:** `docs/superpowers/specs/2026-11-05-shorts-batch-runner-design.md`

## Global Constraints

- Cost is $0: no subcommand may select a paid provider; providers must be in `lib.shorts_guardrails.FREE_PROVIDERS`.
- Gated stages `idea` and `review_queue` are written `completed` only when the CLI is given `--approve <stage>`; otherwise `awaiting_human`.
- Output is 1080x1920, 30 fps, H.264, strictly under 60 s per video.
- `fetch` refuses files over `--max-mb` (default `400`) and files of unknown size unless `--allow-unknown-size`.
- `render` refuses when total cut length is shorter than the narration (tolerance `0.1` s).
- All file I/O uses `encoding="utf-8"` (spec files are read with `utf-8-sig`); no test opens a network connection.
- Hook types are exactly `question`, `shocking_stat`, `myth_bust`, `comparison`, `countdown`.
- Expected problems print one line and exit non-zero; no tracebacks for missing ffmpeg, missing files, malformed spec.
- Existing manifest stages, gates and tools do not change.

## Review Focus

- Cuts shorter than the narration (black tail) must be refused before rendering. Pinned in Task 4.
- Oversized or unknown-size footage must be refused before downloading. Pinned in Task 2.
- The same source range used by two videos (overlapping, not just identical) must be flagged. Pinned in Task 1.
- Spec file with a UTF-8 BOM, malformed JSON, or non-ASCII topics must give a clear message or load correctly. Pinned in Task 1.
- `--approve` of a stage whose predecessors are not complete, and missing ffmpeg/Piper, must give a one-line error. Pinned in Tasks 5 and 7.

## File Structure

| File | Responsibility |
|---|---|
| `lib/shorts/__init__.py` | Package marker |
| `lib/shorts/spec.py` | Load and validate `batch_spec.json` |
| `lib/shorts/batch.py` | Derive and update the audited `shorts_batch.json` |
| `lib/shorts/fetch.py` | Size-capped clip download |
| `lib/shorts/align.py` | Word timings from audio pauses; Remotion caption words |
| `lib/shorts/segments.py` | Cut a segment to 1080x1920 (fill / blurpad) |
| `lib/shorts/props.py` | Remotion props and the cut-coverage guard |
| `lib/shorts/checkpoints.py` | Stage records for `script` .. `review_queue` |
| `lib/shorts/export.py` | Approved-only export and `publish_log` |
| `scripts/shorts_batch.py` | CLI: `validate fetch voice render checkpoints export` |
| `tests/shorts/` | One test file per module plus CLI |

---

### Task 1: Spec and batch file modules

**Files:**
- Create: `lib/shorts/__init__.py`, `lib/shorts/spec.py`, `lib/shorts/batch.py`
- Test: `tests/shorts/__init__.py`, `tests/shorts/test_spec.py`, `tests/shorts/test_batch.py`

**Interfaces:**
- Produces:
  - `HOOK_TYPES: tuple[str, ...]` in `spec.py`.
  - `load_spec(project_dir: Path) -> dict` — reads `<project_dir>/artifacts/batch_spec.json` with `utf-8-sig`; raises `FileNotFoundError` (path in message) or `ValueError` (invalid JSON, or top level not an object).
  - `validate_spec(spec: dict, project_dir: Path) -> list[str]` — returns violations; checks per the spec's `validate` row: required fields (`project_id`, `sources`, `videos`, and per video `id`, `topic`, `hook_type`, `hook_text`, `narration`, `claims`, `cuts`, `title`, `description`, `tags`), unique ids, `hook_type` in `HOOK_TYPES`, `title` length <= 100, each cut's `source` key exists in `sources` and its `path` file exists under `project_dir`, `dur > 0`, claims each have an http(s) source, and **no two cuts anywhere in the batch overlap on the same source** (half-open intervals `[in, in+dur)`).
  - `derive_batch(spec: dict) -> dict` in `batch.py` — `{"videos": [...]}` with per video `id, topic, hook_type, hook_text, claims, narration, render="renders/<id>.mp4", cost_entries=[]`, and `clips` as `{"clip_id": f"{source.clip_id}@{in:.1f}-{in+dur:.1f}", "source": provider, "license", "url"}` per cut.
  - `read_batch(project_dir: Path) -> dict`, `write_batch(project_dir: Path, batch: dict) -> None` (UTF-8, `ensure_ascii=False`), `record_cost(batch: dict, video_id: str, tool: str, provider: str, cost_usd: float) -> None` (replaces an existing entry for the same tool).

- [ ] **Step 1: Write failing tests.** In `test_spec.py`: `test_load_spec_accepts_bom`, `test_load_spec_malformed_json_names_the_file` (`ValueError` message contains `batch_spec.json`), `test_load_spec_rejects_non_object`, `test_validate_flags_duplicate_ids`, `test_validate_flags_unknown_hook_type`, `test_validate_flags_missing_source_file`, `test_validate_flags_overlapping_cuts_across_videos` (v1 cut `56.0` dur `4.0`, v2 cut `58.0` dur `4.0` on the same source -> violation; `60.0` dur `4.0` -> no violation), `test_validate_flags_claim_without_http_source`, `test_validate_accepts_non_ascii_topic` (`"Pokémon 日本"`), `test_validate_clean_spec_has_no_violations`. In `test_batch.py`: `test_derive_batch_builds_clip_ids_and_empty_costs` (clip id `nasa_X@56.0-60.0`), `test_write_then_read_roundtrips_non_ascii`, `test_record_cost_replaces_same_tool`.
- [ ] **Step 2: Run** `.venv/Scripts/python.exe -m pytest tests/shorts -q -p no:cacheprovider`. Expected: collection error (modules missing).
- [ ] **Step 3: Implement** the functions above. Overlap check: group cuts by source key, sort by `in`, compare neighbours.
- [ ] **Step 4: Run the same command.** Expected: all pass.
- [ ] **Step 5: Commit** `feat(shorts): add batch spec and batch file modules`.

---

### Task 2: Size-capped fetch, and a per-video cost audit rule

**Files:**
- Create: `lib/shorts/fetch.py`
- Modify: `lib/shorts_guardrails.py` (add `check_video_costs`, include in `run_all`), `tests/lib/test_shorts_guardrails.py`, `tests/lib/test_shorts_audit.py` (the `_video` helpers gain `cost_entries`)
- Test: `tests/shorts/test_fetch.py`

**Interfaces:**
- Produces:
  - `class SizeCapError(RuntimeError)` in `fetch.py`.
  - `check_size(size_bytes: int | None, max_mb: float = 400, allow_unknown_size: bool = False) -> None` — raises `SizeCapError` whose message names the size and the cap, or says the size is unknown.
  - `fetch_clip(candidate, dest: Path, *, max_mb: float = 400, allow_unknown_size: bool = False, head=_head_size, download=None) -> Path` — `candidate` has `download_url`; `head(url) -> int | None` returns content-length; `download(candidate, dest)` defaults to the source adapter's own `download`. Calls `check_size` **before** downloading.
  - `check_video_costs(videos: list[dict]) -> dict` in guardrails — a video with no `cost_entries` is a violation naming the video id; empty batch fails.

- [ ] **Step 1: Write failing tests.** `test_check_size_rejects_over_cap`, `test_check_size_rejects_unknown_by_default`, `test_check_size_allows_unknown_when_opted_in`, `test_fetch_does_not_download_when_over_cap` (fake `head` returns 5 GB, fake `download` raises if called), `test_fetch_downloads_when_under_cap` (fake files, returns `dest`). Guardrails: `test_check_video_costs_flags_video_without_entries`, `test_run_all_includes_video_costs`.
- [ ] **Step 2: Run** `.venv/Scripts/python.exe -m pytest tests/shorts/test_fetch.py tests/lib/test_shorts_guardrails.py -q -p no:cacheprovider`. Expected: FAIL.
- [ ] **Step 3: Implement** as specified. `_head_size(url) -> int | None` uses `requests.head(..., allow_redirects=True, timeout=15)` and returns `None` when `content-length` is absent or the call fails.
- [ ] **Step 4: Run** the two test files plus `tests/lib/test_shorts_audit.py`. Expected: pass.
- [ ] **Step 5: Commit** `feat(shorts): size-capped fetch and per-video cost rule`.

---

### Task 3: Word aligner

**Files:**
- Create: `lib/shorts/align.py`
- Test: `tests/shorts/test_align.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) WordTiming(word: str, start_ms: int, end_ms: int)`.
  - `split_sentences(text: str) -> list[str]` — split after `.`, `?`, `!` followed by whitespace.
  - `distribute_words(sentences: list[str], spans: list[tuple[float, float]], duration: float) -> list[WordTiming]` — pure. If `len(spans) == len(sentences)` each sentence's words fill its span; otherwise fall back to one region from the first span start to the last span end with sentence gaps weighted `4`. Word weight = letters/digits count (min 1) plus `2` when the word ends in `,` or `;`.
  - `detect_speech_spans(wav_path: Path, noise_db: float = -38, min_silence: float = 0.18) -> tuple[list[tuple[float, float]], float]` — runs ffmpeg `silencedetect`; returns spans and total duration. Raises `RuntimeError("ffmpeg not found ...")` when ffmpeg is missing.
  - `class SilenceAligner: align(self, text: str, wav_path: Path) -> list[WordTiming]` and a `WordAligner` `typing.Protocol` with the same method.
  - `to_remotion_captions(timings: list[WordTiming]) -> list[dict]` — `{"word", "startMs", "endMs"}`.

- [ ] **Step 1: Write failing tests.** Pure: `test_distribute_words_covers_every_word_in_order` (timings monotonic, first start >= span start, last end <= span end), `test_distribute_words_matching_spans_keeps_sentences_inside_their_span`, `test_distribute_words_fallback_when_counts_differ`, `test_distribute_words_handles_single_word_sentence`, `test_to_remotion_captions_keys`. ffmpeg-backed (marked `skipif(shutil.which("ffmpeg") is None)`): `test_detect_speech_spans_finds_two_bursts` (generate with ffmpeg: 1 s sine, 0.6 s silence, 1 s sine -> 2 spans, duration ~2.6 s), `test_silence_aligner_end_to_end` (same wav, text `"Hello there. Second sentence here."` -> 5 words, monotonic).
- [ ] **Step 2: Run** `.venv/Scripts/python.exe -m pytest tests/shorts/test_align.py -q -p no:cacheprovider`. Expected: FAIL.
- [ ] **Step 3: Implement.** Reuse the algorithm proven in batch 01: weights as defined above; inside a span words get `(end-start) * weight / total_weight`.
- [ ] **Step 4: Run.** Expected: pass (ffmpeg tests pass or skip).
- [ ] **Step 5: Commit** `feat(shorts): add pause-based word aligner`.

---

### Task 4: Segments and render props

**Files:**
- Create: `lib/shorts/segments.py`, `lib/shorts/props.py`
- Test: `tests/shorts/test_segments.py`, `tests/shorts/test_props.py`

**Interfaces:**
- Produces:
  - `segment_ffmpeg_args(mode: str) -> list[str]` — for `"fill"`: `["-vf", "scale=-2:1920,crop=1080:1920,fps=30,format=yuv420p"]`; for `"blurpad"`: `["-filter_complex", "<split / boxblur=40:6 / overlay chain ending in fps=30,format=yuv420p>"]`; any other mode raises `ValueError`.
  - `cut_segment(src: Path, in_s: float, dur: float, out: Path, mode: str) -> Path` — runs ffmpeg (`-ss in -i src -t dur -an`, libx264 `-crf 20 -g 30`); raises `RuntimeError` with a one-line message if ffmpeg is missing or fails.
  - `CAPTION_STYLE: dict` in `props.py` = `{"fontSize": 84, "wordsPerPage": 4, "bottomOffset": 380, "color": "#FFFFFF", "highlightColor": "#FACC15", "backgroundColor": "rgba(0,0,0,0.45)"}`.
  - `check_cut_coverage(total_cut_seconds: float, audio_seconds: float, tolerance: float = 0.1) -> None` — raises `ValueError` naming both lengths when `total_cut_seconds < audio_seconds - tolerance`.
  - `build_props(segments: list[Path], durations: list[float], captions: list[dict], narration_path: Path) -> dict` — `renderer_family "explainer-data"`, `render_runtime "remotion"`, `playbook "shorts-captions"`, `cuts` (consecutive `in_seconds`/`out_seconds`, `source_in_seconds 0`, `layer "primary"`, ids `c1..cN`), `captions`, `captionStyle` = `CAPTION_STYLE`, `audio.narration.src`.
  - `render_video(registry, props: dict, output_path: Path) -> None` — calls `video_compose` with `operation "remotion_render"` and `profile "youtube_shorts"`; raises `RuntimeError(one-line error)` on failure.

- [ ] **Step 1: Write failing tests.** `test_fill_args_crop_to_vertical`, `test_blurpad_args_use_filter_complex_and_blur`, `test_unknown_mode_raises`, `test_cut_segment_produces_1080x1920_30fps` (ffmpeg-skipped; generated 640x360 source), `test_cut_segment_missing_ffmpeg_message` (monkeypatch `shutil.which` -> `None`; message mentions ffmpeg), `test_check_cut_coverage_rejects_short_cuts` (cuts 24.15 vs audio 24.21 passes within tolerance, 23.0 vs 24.21 raises), `test_build_props_cuts_are_consecutive`, `test_build_props_carries_caption_style_and_narration`, `test_render_video_passes_profile` (fake registry records the inputs).
- [ ] **Step 2: Run** `.venv/Scripts/python.exe -m pytest tests/shorts/test_segments.py tests/shorts/test_props.py -q -p no:cacheprovider`. Expected: FAIL.
- [ ] **Step 3: Implement** per the signatures. The blurpad chain: `[0:v]split[a][b];[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=40:6[bg];[b]scale=1080:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,fps=30,format=yuv420p`.
- [ ] **Step 4: Run.** Expected: pass.
- [ ] **Step 5: Commit** `feat(shorts): add segment cutting and render props`.

---

### Task 5: Stage checkpoints

**Files:**
- Create: `lib/shorts/checkpoints.py`
- Test: `tests/shorts/test_checkpoints.py`

**Interfaces:**
- Consumes: `lib.checkpoint.write_checkpoint(pipeline_dir, project_id, stage, status, artifacts, *, pipeline_type, style_playbook, human_approved, ...)`, `lib.shorts.spec.load_spec`, `lib.shorts.batch.read_batch`.
- Produces: `write_stage_records(projects_dir: Path, spec: dict, batch: dict, approve: frozenset[str] = frozenset(), probe=probe_render) -> list[str]` — writes, in order, `script` (aggregate `script` artifact from narration), `visuals` (`scene_plan`, one `broll` scene per cut, `required_assets` as objects `{type, description, source}`), `voice_captions` (`asset_manifest` with narration, subtitle and video assets, `total_cost_usd 0.0`), `compose` (`render_report` + `final_review`, using `probe(path)` for size/duration), `review_queue` (`decision_log`); returns the stages written. `review_queue` status is `completed` with `human_approved=True` only if `"review_queue"` is in `approve`, otherwise `awaiting_human`. The `idea` stage is **not** written here: `write_idea_record(projects_dir, spec, approve: bool) -> None` writes it (`brief` + `decision_log` with `render_runtime_selection` = remotion), `completed` only when `approve` is true. Raises `ValueError` (one line) if a stage is requested while a predecessor is incomplete (wrapping `CheckpointValidationError`).

- [ ] **Step 1: Write failing tests** with a tmp projects dir and a fake `probe`: `test_idea_awaits_human_without_approval`, `test_idea_completes_with_approval`, `test_stage_records_validate_against_schemas` (all five artifacts accepted by `write_checkpoint`), `test_review_queue_awaits_human_by_default`, `test_review_queue_completes_only_with_approve`, `test_records_refuse_when_idea_not_approved` (`ValueError`, message names `idea`), `test_scene_plan_has_one_scene_per_cut`.
- [ ] **Step 2: Run** `.venv/Scripts/python.exe -m pytest tests/shorts/test_checkpoints.py -q -p no:cacheprovider`. Expected: FAIL.
- [ ] **Step 3: Implement.** Mirror batch 01's artifact shapes (they already validate against `schemas/artifacts/*`).
- [ ] **Step 4: Run.** Expected: pass.
- [ ] **Step 5: Commit** `feat(shorts): add stage checkpoint writer`.

---

### Task 6: Export

**Files:**
- Create: `lib/shorts/export.py`
- Test: `tests/shorts/test_export.py`

**Interfaces:**
- Produces: `export_videos(projects_dir: Path, spec: dict, approved_ids: list[str], registry) -> list[dict]` — for ids in `approved_ids` only, in the given order, calls `export_bundle` with the spec's `title`, `description`, `tags`, `hashtags ["#Shorts"]`, `platform "youtube_shorts"`, `visibility "private"`, `export_dir projects/<id>/export/<NN>-<vid>`, and the SRT path; returns the `publish_log` entries; then writes the `publish` stage (`publish_log`, `completed`). Raises `ValueError` if an approved id is not in the spec or `review_queue` is not completed.

- [ ] **Step 1: Write failing tests** with a fake registry: `test_export_skips_unapproved_videos`, `test_export_order_follows_approved_ids`, `test_export_rejects_unknown_id`, `test_export_requires_completed_review_queue`, `test_export_marks_private_and_shorts_hashtag`.
- [ ] **Step 2: Run** `.venv/Scripts/python.exe -m pytest tests/shorts/test_export.py -q -p no:cacheprovider`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: pass.
- [ ] **Step 5: Commit** `feat(shorts): add approved-only export`.

---

### Task 7: CLI

**Files:**
- Create: `scripts/shorts_batch.py`
- Test: `tests/shorts/test_cli.py`

**Interfaces:**
- Consumes: every module above; `scripts/shorts_audit.audit_project`.
- Produces: `main(argv: list[str] | None = None) -> int` with subcommands (all take `project_dir` positionally unless noted):
  - `validate` — prints violations, exit `1` if any.
  - `fetch <project_dir> --source nasa|wikimedia|archive_org --query Q --clip-id ID --key KEY [--max-mb N] [--allow-unknown-size]` — resolves the candidate by `clip_id`, calls `fetch_clip`, adds/updates `sources[KEY]` in the spec with path, provider, licence and URL.
  - `voice [--video ID]` — `piper_tts` with the spec's `voice`, `SilenceAligner`, `subtitle_gen` (JSON + SRT), writes `assets/audio/<id>.wav|.captions.json|.srt`, records `piper_tts` and `subtitle_gen` costs (`openmontage`) in the batch file.
  - `render [--video ID]` — requires `validate` clean; cuts segments, `check_cut_coverage`, `build_props`, `render_video`, records the `video_compose` / `remotion` cost.
  - `checkpoints [--approve idea|review_queue]...` — runs the audit first for `review_queue`; calls the Task 5 functions.
  - `export --approved v1,v3` — refuses unless `audit` passes.
  - Missing ffmpeg/ffprobe/Piper voice, `FileNotFoundError`, `ValueError`, `RuntimeError`, `SizeCapError` all print one line (no traceback) and return `1`.

- [ ] **Step 1: Write failing tests** (monkeypatch module functions; no ffmpeg needed): `test_validate_exit_codes`, `test_fetch_refuses_oversized_without_downloading`, `test_render_refuses_when_spec_invalid`, `test_render_refuses_short_cuts`, `test_checkpoints_without_approve_leaves_gates_open`, `test_checkpoints_pass_approve_flags_through`, `test_export_refuses_when_audit_fails`, `test_missing_ffmpeg_prints_one_line_and_returns_1` (output has no `Traceback` and exactly one non-empty line).
- [ ] **Step 2: Run** `.venv/Scripts/python.exe -m pytest tests/shorts/test_cli.py -q -p no:cacheprovider`. Expected: FAIL.
- [ ] **Step 3: Implement** with `argparse` subparsers; each handler is a function returning an exit code, wrapped by one `try/except (FileNotFoundError, ValueError, RuntimeError, SizeCapError)`.
- [ ] **Step 4: Run** `tests/shorts`. Expected: pass.
- [ ] **Step 5: Commit** `feat(shorts): add shorts_batch CLI`.

---

### Task 8: Skills and docs

**Files:**
- Modify: `skills/pipelines/shorts-factory/{idea,script,visuals,voice-captions,compose,review-queue,publish}-director.md`, `skills/INDEX.md`
- Test: `tests/contracts/test_shorts_factory_skills.py`

- [ ] **Step 1: Write failing tests** appended to the existing contract file: `test_directors_name_their_batch_command` (idea/script -> `validate`, visuals -> `fetch`, voice-captions -> `voice`, compose -> `render`, review-queue -> `checkpoints`, publish -> `export`; each contains `scripts/shorts_batch.py`), `test_skills_forbid_adhoc_scripts` (executive-producer contains `ad-hoc`), `test_script_director_names_batch_spec_fields` (`batch_spec.json`, `cuts`, `narration`).
- [ ] **Step 2: Run** `.venv/Scripts/python.exe -m pytest tests/contracts/test_shorts_factory_skills.py -q -p no:cacheprovider`. Expected: FAIL.
- [ ] **Step 3: Update the skills.** Each tells the agent which subcommand to run and which spec fields to fill; the executive producer states that the agent never writes ad-hoc scripts and passes `--approve` only after the owner says yes in chat. Add one line to `skills/INDEX.md`.
- [ ] **Step 4: Run** `tests/contracts` + `tests/shorts`. Expected: pass.
- [ ] **Step 5: Commit** `docs(shorts): point directors at the batch runner`.

---

### Task 9: End-to-end check on batch 01

- [ ] **Step 1:** Write `projects/shorts-space-batch-01-e2e/artifacts/batch_spec.json` for video v3 only, reusing the downloaded NASA SVS clip (copy it into the new project) and v3's narration, claims and cut list from batch 01 (`.superpowers/compose.py` has the numbers).
- [ ] **Step 2:** With ffmpeg on PATH and the venv active, run in order: `validate`, `voice`, `render`, `checkpoints --approve idea`, then `.venv/Scripts/python.exe scripts/shorts_audit.py projects/shorts-space-batch-01-e2e`. Expected: every command exits `0`, the render is 1080x1920 and under 60 s, audit prints `shorts audit: OK`.
- [ ] **Step 3:** Open one frame of the render and confirm captions show at the large size inside the safe zone. If anything differs from batch 01, fix the owning module test-first.
- [ ] **Step 4:** Run the broad suite `.venv/Scripts/python.exe -m pytest tests/contracts tests/lib tests/pipelines tests/styles tests/shorts -q -p no:cacheprovider`. Expected: all pass.
- [ ] **Step 5: Commit** any fixes `fix(shorts): ...`.

---

## Self-review notes

- Spec coverage: validate (T1), fetch + size cap (T2), voice/align (T3), render/segments/props + coverage guard (T4), checkpoints + gates (T5), export approved-only (T6), CLI + one-line errors (T7), skills (T8), per-video cost rule (T2), end-to-end (T9).
- Type consistency: `WordTiming`/`to_remotion_captions` (T3) are consumed by the CLI `voice` (T7); `build_props` takes captions as `list[dict]` produced by `to_remotion_captions`; `derive_batch`/`read_batch` field names (`cost_entries`, `render`, `clips`) match `scripts/shorts_audit.py`.
- Known risk: the CLI's `voice` and `render` need ffmpeg, Piper and Remotion; unit tests monkeypatch those and Task 9 exercises them for real.
