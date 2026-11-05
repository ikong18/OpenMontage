"""Studio's local web server. Reads derive from files; writes need the per-run token."""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from lib.shorts.platforms import get_platform
from lib.shorts.spec import STAGES, load_spec, save_spec
from lib.shorts.voices import (ENGLISH_VOICES, kokoro_available, synthesize_kokoro, validate_voice)
from studio.actions import AI_STEPS, REPO, approval_argv, plan_for, redo_video
from studio.ai import AiClient, AiError
from studio.batches import batch_dir, create_batch, edit_videos, list_batches
from studio.jobs import JobBusy, JobRunner
from studio.settings import PROVIDERS, load_settings, load_writer, save_settings, save_writer
from studio.factcheck import check_view, pick_checker
from studio.footage import check_view as footage_view, render_blockers
from studio.sources import source_info
from studio.steps import STEPS, gate_status, next_action, step_states

UI_DIR = Path(__file__).resolve().parent / "ui"
SSE_HEARTBEAT_SECONDS = 15


REPO_ROOT = Path(__file__).resolve().parents[1]
CODE_ROOTS = [REPO_ROOT / "studio", REPO_ROOT / "lib" / "shorts", REPO_ROOT / "scripts" / "shorts_batch.py"]


def latest_code_mtime(roots: list[Path]) -> float:
    """Newest change among the files that make up Studio (code, pages, prompts)."""
    latest = 0.0
    for root in roots:
        for path in ([root] if root.is_file() else root.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            if path.suffix in (".py", ".js", ".html", ".css") or (path.suffix == ".md" and "prompts" in path.parts):
                latest = max(latest, path.stat().st_mtime)
    return latest


PREVIEW_TEXT = "Venus is hotter than Mercury, even though Mercury is closer to the Sun."
PIPER_DEFAULT = {"engine": "piper", "model": "en_US-lessac-medium", "length_scale": 0.95}
_ACCENT = {"af": "US female", "am": "US male", "bf": "British female", "bm": "British male"}


def voice_catalog() -> dict[str, Any]:
    kokoro = [{"id": v, "label": f"{v.split('_', 1)[1].title()} - {_ACCENT[v[:2]]}"} for v in ENGLISH_VOICES]
    return {
        "piper": {"ready": shutil.which("piper") is not None,
                  "voices": [{"id": "en_US-lessac-medium", "label": "Lessac - US (the original voice)"}]},
        "kokoro": {"ready": kokoro_available(), "voices": kokoro},
    }


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def create_app(
    projects_dir: Path,
    jobs: Optional[JobRunner] = None,
    ai: Optional[AiClient] = None,
    token: Optional[str] = None,
    providers: Optional[dict[str, AiClient]] = None,
    watch: Optional[list[Path]] = None,
) -> FastAPI:
    projects_dir = Path(projects_dir)
    clients: dict[str, AiClient] = dict(providers or {})
    if ai is not None:
        clients.setdefault("claude", ai)
    jobs = jobs or JobRunner(projects_dir)
    token = token or secrets.token_urlsafe(24)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        jobs.recover_all()
        yield

    code_roots = [Path(p) for p in (watch if watch is not None else CODE_ROOTS)]
    started_code = latest_code_mtime(code_roots)

    def code_stale() -> bool:
        """True when Studio's files changed after this copy started: the running copy is old and must be restarted."""
        return latest_code_mtime(code_roots) > started_code + 0.5

    app = FastAPI(title="Studio", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
    app.state.projects_dir, app.state.jobs, app.state.ai, app.state.token = projects_dir, jobs, ai, token
    app.state.clients = clients

    def require_token(x_studio_token: Optional[str] = Header(default=None)) -> None:
        if not x_studio_token or not secrets.compare_digest(x_studio_token, token):
            raise HTTPException(status_code=403, detail="missing or wrong Studio token")

    def project_or_404(batch_id: str) -> Path:
        try:
            return batch_dir(projects_dir, batch_id)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="no such batch") from None

    def provider_ready(name: str) -> bool:
        client = clients.get(name)
        if client is None:
            return False
        binary = getattr(client, "binary", None)
        return shutil.which(binary) is not None if binary else True

    def ai_for(project: Path) -> Optional[AiClient]:
        return clients.get(load_settings(project)["provider"])

    class StepError(Exception):
        def __init__(self, status: int, detail: str):
            super().__init__(detail)
            self.status, self.detail = status, detail

    CHAIN = {"script": "footage", "footage": "render", "render": "review"}

    def chain_after(batch_id: str, step: str, project: Path, params: dict[str, Any] | None = None, chained: bool = False):
        """(on_success, on_failure) for a step: scripts are always fact-checked; with autopilot on the chain goes on
        (never approvals, never export). A fact check the owner starts by hand ("Fact-check again") never chains."""
        params = params or {}
        if step == "script" and not params.get("factcheck"):
            return (lambda: start_step(batch_id, "script", {"factcheck": True, "_chained": True})), None
        if step == "script" and not chained:
            return None, None
        nxt = CHAIN.get(step)
        if nxt is None:
            return None, None

        def go() -> None:
            if load_settings(project)["autopilot"]:
                start_step(batch_id, nxt, {})
        # a failed fact check is shown to the owner, but it must not strand an autopilot run
        return go, (go if step == "script" else None)

    def start_step(batch_id: str, step: str, params: dict[str, Any]) -> str:
        project = project_or_404(batch_id)
        if step not in STEPS:
            raise StepError(404, f"unknown step '{step}'")
        if jobs.running(batch_id):
            raise StepError(409, "a job is already running for this batch")
        if step_states(project)[step] == "locked":
            raise StepError(422, f"step '{step}' is locked until the previous step is done")
        if step == "topics" and gate_status(project, "idea") == "approved":
            raise StepError(409, "topics are already approved; start a new batch")
        params = dict(params or {})
        chained = bool(params.pop("_chained", False))  # only the server's own chain sets this
        for forged in ("writer", "checker"):
            params.pop(forged, None)  # who wrote and who checked is decided here, never by the request
        client = ai_for(project)
        fallback = None
        if step == "script" and params.get("factcheck"):
            writer = load_writer(project) or load_settings(project)["provider"]
            checker, _same = pick_checker(writer, {name: provider_ready(name) for name in PROVIDERS})
            client = clients.get(checker)
            params.update({"writer": writer, "checker": checker})
            if checker != writer and clients.get(writer) is not None:
                fallback = (writer, clients[writer])
        elif step == "script":
            save_writer(project, load_settings(project)["provider"])
        if step in AI_STEPS and client is None:
            raise StepError(503, "no AI client is configured for this server")
        try:
            label = None
            if step == "topics" and params.get("research"):
                label = "<topics:research:" + ",".join(str(i) for i in params["research"]) + ">"
            if step == "script" and params.get("factcheck"):
                label = "<script:factcheck>"
            reviewers = None
            if step == "footage":  # the other AI looks at the finished shots; the writer's AI is the fallback
                writer = load_settings(project)["provider"]
                rname, _same = pick_checker(writer, {name: provider_ready(name) for name in PROVIDERS})
                reviewers = [(clients[rname], rname, writer)] if clients.get(rname) is not None else []
                if rname != writer and clients.get(writer) is not None:
                    reviewers.append((clients[writer], writer, writer))
            plan = plan_for(step, project, client, params, reviewer=reviewers) if reviewers else plan_for(step, project, client, params)
            if fallback is not None:
                first = plan

                def plan(ctx, _first=first, _fb=fallback, _params=dict(params)):  # noqa: F811 - the other AI failed: try the writer's
                    try:
                        _first(ctx)
                    except AiError as exc:
                        ctx.log(f"{_params['checker']} could not check ({exc}); asking {_fb[0]} instead")
                        plan_for(step, project, _fb[1], {**_params, "checker": _fb[0]})(ctx)
            on_ok, on_fail = chain_after(batch_id, step, project, params, chained)
            return jobs.run_steps(batch_id, step, plan, on_success=on_ok, on_failure=on_fail, label=label)
        except JobBusy:
            raise StepError(409, "a job is already running for this batch") from None

    def page(name: str) -> HTMLResponse:
        html = (UI_DIR / name).read_text(encoding="utf-8").replace("__STUDIO_TOKEN__", token)
        for asset in ("studio.js", "studio.css"):
            path = UI_DIR / asset
            if path.is_file():
                html = html.replace(f"/ui/{asset}", f"/ui/{asset}?v={int(path.stat().st_mtime)}")
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    def batch_view(batch_id: str) -> dict[str, Any]:
        project = project_or_404(batch_id)
        running = jobs.running(batch_id)
        recent = jobs.recent(batch_id)
        failed = None
        if not running and recent and recent[0].get("status") == "failed" and recent[0].get("step") in STEPS:
            failed = recent[0]["step"]
        try:
            spec = load_spec(project)
        except (OSError, ValueError):
            spec = {"videos": [], "sources": {}}
        steps = step_states(project, running=(running or {}).get("step"), failed=failed)
        gates = {"idea": gate_status(project, "idea"), "review_queue": gate_status(project, "review_queue")}
        platform = get_platform(spec.get("platform"))
        fact = check_view(project, spec)
        footage = footage_view(project, spec)
        fact_job = next((r for r in recent if r.get("argv") == ["<script:factcheck>"]), None)
        if fact_job and fact_job.get("status") == "failed" and not running:
            checked = fact.get("checked_at") or ""
            if not checked or (fact_job.get("ended") or "") > checked:
                try:
                    lines = [ln for ln in jobs.tail(batch_id, fact_job["id"], 20).splitlines() if ln.strip()]
                except (OSError, FileNotFoundError):
                    lines = []
                text = next((ln for ln in reversed(lines) if ln.startswith("error:")), lines[-1] if lines else "unknown error")
                fact = {**fact, "error": text.removeprefix("error: ")[:200]}
        next_info = next_action(steps, gates, running, len(spec.get("videos") or []), batch_id,
                                flagged=sum(v.get("flagged", 0) for v in fact["videos"].values() if not v.get("stale")),
                                fact_error=fact.get("error"))
        blockers = render_blockers(project, spec, [v["id"] for v in spec.get("videos") or [] if v.get("cuts")])
        if blockers and not running and steps.get("footage") == "done" and steps.get("render") != "done":
            next_info = {"kind": "todo", "step": "footage", "text": "Before the videos are made: " + blockers[0]}
        return {
            "id": batch_id,
            "platform": spec.get("platform") or "youtube_shorts",
            "spec": spec,
            "steps": steps,
            "gates": gates,
            "settings": load_settings(project),
            "providers": {name: provider_ready(name) for name in PROVIDERS},
            "code_stale": code_stale(),
            "fact_check": fact,
            "voice": {"engine": "piper", **(spec.get("voice") or PIPER_DEFAULT)},
            "voices": voice_catalog(),
            "footage_check": footage,
            "next": next_info,
            "limits": {"max_description": platform.max_description, "max_hashtags": platform.max_hashtags},
            "source_info": source_info(project, spec),
            "jobs": recent,
            "running": running,
            "renders": {str(v.get("id")): (project / "renders" / f"{v.get('id')}.mp4").is_file()
                        for v in spec.get("videos") or []},
            "renders_stamp": max([int(p.stat().st_mtime) for p in (project / "renders").glob("*.mp4")] or [0]),
            "exports": sorted(p.name for p in (project / "export").glob("*") if p.is_dir()),
        }

    app.state.batch_view = batch_view
    app.state.project_or_404 = project_or_404
    app.state.require_token = require_token

    # ---- pages ---------------------------------------------------------
    @app.get("/")
    async def index() -> HTMLResponse:
        return page("index.html")

    @app.get("/batch/{batch_id}")
    async def batch_page(batch_id: str) -> HTMLResponse:
        project_or_404(batch_id)
        return page("batch.html")

    app.mount("/ui", StaticFiles(directory=str(UI_DIR)), name="ui")

    # ---- api -----------------------------------------------------------
    @app.get("/api/health")
    async def health() -> dict:
        return {"ok": True, "app": "studio", "code_stale": code_stale()}

    @app.get("/api/batches")
    async def batches() -> list:
        return await asyncio.to_thread(list_batches, projects_dir)

    @app.post("/api/batches", status_code=201, dependencies=[Depends(require_token)])
    async def new_batch(body: dict) -> dict:
        try:
            batch_id = create_batch(projects_dir, str(body.get("name") or ""), str(body.get("platform") or ""))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return {"id": batch_id}

    @app.get("/api/batches/{batch_id}")
    async def get_batch(batch_id: str) -> dict:
        return await asyncio.to_thread(batch_view, batch_id)

    @app.put("/api/batches/{batch_id}/spec", dependencies=[Depends(require_token)])
    async def put_spec(batch_id: str, body: dict, stage: str = "idea") -> dict:
        project = project_or_404(batch_id)
        if stage not in STAGES + ("draft",):
            raise HTTPException(status_code=422, detail=f"stage must be one of {', '.join(STAGES + ('draft',))}")
        if jobs.running(batch_id):
            raise HTTPException(status_code=409, detail="a job is running; wait for it to finish")
        videos = body.get("videos")
        if not isinstance(videos, list):
            raise HTTPException(status_code=422, detail="body needs a 'videos' list")
        violations = await asyncio.to_thread(edit_videos, project, videos, stage)
        if violations:
            return JSONResponse(status_code=422, content={"violations": violations})
        return {"ok": True}

    @app.get("/api/batches/{batch_id}/jobs/{job_id}/log")
    async def job_log(batch_id: str, job_id: str, lines: int = 40) -> dict:
        project_or_404(batch_id)
        try:
            return {"log": jobs.tail(batch_id, job_id, max(1, min(lines, 400)))}
        except (FileNotFoundError, OSError):
            raise HTTPException(status_code=404, detail="no such job") from None

    @app.get("/api/batches/{batch_id}/events")
    async def events(batch_id: str, request: Request) -> StreamingResponse:
        project_or_404(batch_id)

        async def stream():
            queue = jobs.subscribe()
            try:
                yield _sse({"type": "hello", "batch_id": batch_id})
                while True:
                    if await request.is_disconnected():
                        return
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=SSE_HEARTBEAT_SECONDS)
                    except asyncio.TimeoutError:
                        yield _sse({"type": "heartbeat"})
                        continue
                    if event.get("batch_id") == batch_id:
                        yield _sse({"type": "change", **event})
            finally:
                jobs.unsubscribe(queue)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/batches/{batch_id}/steps/{step}/run", status_code=202, dependencies=[Depends(require_token)])
    async def run_step(batch_id: str, step: str, body: dict = Body(default_factory=dict)) -> dict:
        project_or_404(batch_id)
        body = {k: v for k, v in body.items() if not str(k).startswith("_")}  # internal keys are the server's own
        try:
            return {"job": start_step(batch_id, step, body)}
        except StepError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.detail) from None

    @app.put("/api/batches/{batch_id}/settings", dependencies=[Depends(require_token)])
    async def put_settings(batch_id: str, body: dict = Body(default_factory=dict)) -> dict:
        project = project_or_404(batch_id)
        # allowed while a job runs: turning autopilot off must be able to stop a chain; each step reads it when it starts
        wanted = body.get("provider")
        if wanted in PROVIDERS and not provider_ready(wanted):
            raise HTTPException(status_code=422, detail=f"{wanted} is not set up on this server (CLI not installed)")
        try:
            return save_settings(project, body)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @app.post("/api/batches/{batch_id}/gates/{gate}/approve", status_code=202, dependencies=[Depends(require_token)])
    async def approve(batch_id: str, gate: str) -> dict:
        project = project_or_404(batch_id)
        try:
            argv = await asyncio.to_thread(approval_argv, project, gate)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        try:
            after = None
            if gate == "idea":
                def after() -> None:  # approving topics starts the scripts by itself
                    start_step(batch_id, "script", {})
            job_id = jobs.start(batch_id, "topics" if gate == "idea" else "review", argv, cwd=REPO, on_success=after)
        except JobBusy:
            raise HTTPException(status_code=409, detail="a job is already running for this batch") from None
        return {"job": job_id}

    @app.post("/api/batches/{batch_id}/videos/{video_id}/redo", dependencies=[Depends(require_token)])
    async def redo(batch_id: str, video_id: str, body: dict = Body(default_factory=dict)) -> dict:
        project = project_or_404(batch_id)
        if not any(v.get("id") == video_id for v in load_spec(project).get("videos") or []):
            raise HTTPException(status_code=404, detail=f"no such video '{video_id}'")
        if jobs.running(batch_id):
            raise HTTPException(status_code=409, detail="a job is running; wait for it to finish")
        await asyncio.to_thread(redo_video, project, video_id, str(body.get("note") or "redo"))
        return {"ok": True}

    def _clean_voice(body: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
        engine = body.get("engine", "piper")
        if engine == "kokoro":
            voice: dict[str, Any] = {"engine": "kokoro", "voice": body.get("voice", "af_heart")}
            if "speed" in body:
                voice["speed"] = body["speed"]
        elif engine == "piper":
            was_piper = current.get("engine", "piper") == "piper"
            voice = {"model": body.get("model", current.get("model", "en_US-lessac-medium") if was_piper else "en_US-lessac-medium"),
                     "length_scale": body.get("length_scale", current.get("length_scale", 0.95) if was_piper else 0.95)}
        else:
            voice = {"engine": engine}
        problems = validate_voice(voice)
        if problems:
            raise HTTPException(status_code=422, detail="; ".join(problems))
        if voice.get("engine") == "kokoro" and not kokoro_available():
            raise HTTPException(status_code=422, detail="Kokoro is not set up on this computer (see studio/README.md)")
        return voice

    @app.put("/api/batches/{batch_id}/voice", dependencies=[Depends(require_token)])
    async def put_voice(batch_id: str, body: dict = Body(default_factory=dict)) -> dict:
        project = project_or_404(batch_id)
        if jobs.running(batch_id):
            raise HTTPException(status_code=409, detail="a job is running; wait for it to finish")
        spec = load_spec(project)
        spec["voice"] = _clean_voice(body, spec.get("voice") or {})
        save_spec(project, spec)
        return spec["voice"]

    @app.post("/api/batches/{batch_id}/voice/preview", dependencies=[Depends(require_token)])
    async def preview_voice(batch_id: str, body: dict = Body(default_factory=dict)) -> dict:
        project = project_or_404(batch_id)
        voice = _clean_voice(body, load_spec(project).get("voice") or {})
        engine = voice.get("engine", "piper")
        label = voice["voice"] if engine == "kokoro" else voice["model"]
        speed = voice.get("speed", voice.get("length_scale", ""))
        name = re.sub(r"[^A-Za-z0-9_.-]", "_", f"voice_preview_{engine}_{label}_{speed}") + ".wav"
        out = project / "review" / name

        def make() -> None:
            out.parent.mkdir(parents=True, exist_ok=True)
            if engine == "kokoro":
                synthesize_kokoro(voice, PREVIEW_TEXT, out)
                return
            from tools.tool_registry import registry

            registry.discover()
            tool = registry._tools["piper_tts"]
            if tool.get_status().value != "available":
                raise HTTPException(status_code=422, detail="Piper is not available on this computer")
            result = tool.execute({"text": PREVIEW_TEXT, "output_path": str(out), "model": voice["model"],
                                   "length_scale": voice["length_scale"]})
            if not result.success:
                raise HTTPException(status_code=500, detail=f"Piper failed: {(result.error or '')[:200]}")

        await asyncio.to_thread(make)
        return {"url": f"/media/{batch_id}/review/{name}"}

    @app.post("/api/batches/{batch_id}/videos/{video_id}/footage-override", dependencies=[Depends(require_token)])
    async def footage_override(batch_id: str, video_id: str) -> dict:
        """The owner accepts one video's footage although the footage check did not pass. Only a click can do this."""
        project = project_or_404(batch_id)
        spec = load_spec(project)
        video = next((v for v in spec.get("videos") or [] if v.get("id") == video_id), None)
        if video is None:
            raise HTTPException(status_code=404, detail=f"no such video '{video_id}'")
        if jobs.running(batch_id):
            raise HTTPException(status_code=409, detail="a job is running; wait for it to finish")
        video["footage_override"] = True
        save_spec(project, spec)
        return {"ok": True}

    @app.get("/media/{batch_id}/{path:path}")
    async def media(batch_id: str, path: str):
        project = project_or_404(batch_id)
        try:
            target = (project / path).resolve()
            inside = target.is_relative_to(project.resolve())
        except (OSError, ValueError):
            raise HTTPException(status_code=404) from None
        if not path or Path(path).is_absolute() or not inside or not target.is_file():
            raise HTTPException(status_code=404)
        return FileResponse(target)

    return app

