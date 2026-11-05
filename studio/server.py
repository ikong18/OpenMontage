"""Studio's local web server. Reads derive from files; writes need the per-run token."""

from __future__ import annotations

import asyncio
import json
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from lib.shorts.platforms import get_platform
from lib.shorts.spec import STAGES, load_spec
from studio.actions import AI_STEPS, REPO, approval_argv, plan_for, redo_video
from studio.ai import AiClient
from studio.batches import batch_dir, create_batch, edit_videos, list_batches
from studio.jobs import JobBusy, JobRunner
from studio.sources import source_info
from studio.steps import STEPS, gate_status, next_action, step_states

UI_DIR = Path(__file__).resolve().parent / "ui"
SSE_HEARTBEAT_SECONDS = 15


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def create_app(
    projects_dir: Path,
    jobs: Optional[JobRunner] = None,
    ai: Optional[AiClient] = None,
    token: Optional[str] = None,
) -> FastAPI:
    projects_dir = Path(projects_dir)
    jobs = jobs or JobRunner(projects_dir)
    token = token or secrets.token_urlsafe(24)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        jobs.recover_all()
        yield

    app = FastAPI(title="Studio", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
    app.state.projects_dir, app.state.jobs, app.state.ai, app.state.token = projects_dir, jobs, ai, token

    def require_token(x_studio_token: Optional[str] = Header(default=None)) -> None:
        if not x_studio_token or not secrets.compare_digest(x_studio_token, token):
            raise HTTPException(status_code=403, detail="missing or wrong Studio token")

    def project_or_404(batch_id: str) -> Path:
        try:
            return batch_dir(projects_dir, batch_id)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="no such batch") from None

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
        return {
            "id": batch_id,
            "platform": spec.get("platform") or "youtube_shorts",
            "spec": spec,
            "steps": steps,
            "gates": gates,
            "next": next_action(steps, gates, running, len(spec.get("videos") or []), batch_id),
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
        return {"ok": True, "app": "studio"}

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
        if stage not in STAGES:
            raise HTTPException(status_code=422, detail=f"stage must be one of {', '.join(STAGES)}")
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
        project = project_or_404(batch_id)
        if step not in STEPS:
            raise HTTPException(status_code=404, detail=f"unknown step '{step}'")
        if jobs.running(batch_id):
            raise HTTPException(status_code=409, detail="a job is already running for this batch")
        if step_states(project)[step] == "locked":
            raise HTTPException(status_code=422, detail=f"step '{step}' is locked until the previous step is done")
        if step == "topics" and gate_status(project, "idea") == "approved":
            raise HTTPException(status_code=409, detail="topics are already approved; start a new batch")
        if step in AI_STEPS and ai is None:
            raise HTTPException(status_code=503, detail="no AI client is configured for this server")
        try:
            job_id = jobs.run_steps(batch_id, step, plan_for(step, project, ai, body))
        except JobBusy:
            raise HTTPException(status_code=409, detail="a job is already running for this batch") from None
        return {"job": job_id}

    @app.post("/api/batches/{batch_id}/gates/{gate}/approve", status_code=202, dependencies=[Depends(require_token)])
    async def approve(batch_id: str, gate: str) -> dict:
        project = project_or_404(batch_id)
        try:
            argv = await asyncio.to_thread(approval_argv, project, gate)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        try:
            job_id = jobs.start(batch_id, "topics" if gate == "idea" else "review", argv, cwd=REPO)
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

