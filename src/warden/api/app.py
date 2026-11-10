"""The FastAPI application: start a run, stream it, approve a step, and pull the kill switch.

The server holds no policy logic of its own. It builds a control plane per run through the same
factory the tests and the CLI use, then exposes start, stream, approve, and revoke over HTTP. The
stream is Server-Sent Events, one frame per thing that happened, so the page reflects the run live.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..agent import OFFBOARDING_TASK
from ..config import Settings
from ..eval import build_report, run_benchmark
from ..policy import PolicyEngine
from .manager import SessionManager
from .sse import sse_comment, sse_data

WEB_DIR = Path(__file__).resolve().parents[3] / "web"

SSE_KEEPALIVE_SECONDS = 15.0


def _repo_backend() -> dict[str, Any]:
    """Report whether the repository tool is the real GitHub connector or the synthetic one.

    The UI shows this so a live run against a real org is visibly real, and whether writes are armed
    or still a dry run is on the screen rather than taken on trust.
    """
    from ..tools.github_ops import GitHubConfig

    config = GitHubConfig.from_env()
    if config is None:
        return {"backend": "synthetic"}
    return {"backend": "github", "org": config.org, "writes_enabled": config.allow_writes}


class StartRequest(BaseModel):
    mode: str | None = None


class ApproveRequest(BaseModel):
    action_id: str
    approved: bool = True
    approver: str = "operator"


class RevokeRequest(BaseModel):
    actor: str = "operator"


def create_app(settings: Settings | None = None, *, web_dir: Path = WEB_DIR) -> FastAPI:
    """Build the application. Pass settings in tests; production reads them from the environment."""
    settings = settings or Settings.from_env()
    app = FastAPI(title="Warden", version="0.1.0")
    manager = SessionManager(settings)
    app.state.settings = settings
    app.state.manager = manager
    app.state.eval_report = None

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {"status": "ok", "mode": settings.mode, "live_ready": settings.live_ready}

    @app.get("/api/config")
    def config() -> dict[str, Any]:
        policy = PolicyEngine()
        return {
            "mode": settings.mode,
            "live_ready": settings.live_ready,
            "model": settings.model if settings.live_ready else None,
            "task": OFFBOARDING_TASK,
            "ruleset_version": policy.ruleset_version,
            "approval_timeout_seconds": settings.approval_timeout_seconds,
            "step_delay_seconds": settings.demo_step_delay_seconds,
            "repo_backend": _repo_backend(),
        }

    @app.get("/api/policy")
    def policy() -> dict[str, Any]:
        engine = PolicyEngine()
        return {"ruleset_version": engine.ruleset_version, "rules": engine.ruleset()}

    @app.post("/api/sessions")
    async def start_session(body: StartRequest) -> dict[str, Any]:
        handle = manager.start_run(mode=body.mode)
        return {"run_id": handle.run_id, "source": handle.source}

    @app.get("/api/sessions/{run_id}")
    async def session_snapshot(run_id: str) -> dict[str, Any]:
        handle = manager.get(run_id)
        if handle is None or handle.cp is None:
            raise HTTPException(status_code=404, detail="unknown run")
        return {
            "run_id": handle.run_id,
            "source": handle.source,
            "status": handle.cp.session.status.value,
            "done": handle.done.is_set(),
            "pending_action_ids": handle.cp.broker.pending_action_ids(),
            "events": [e.model_dump(mode="json") for e in handle.events],
        }

    @app.get("/api/sessions/{run_id}/events")
    async def session_events(run_id: str) -> StreamingResponse:
        handle = manager.get(run_id)
        if handle is None:
            raise HTTPException(status_code=404, detail="unknown run")

        async def stream() -> Any:
            waiter = handle.add_waiter()
            cursor = 0
            try:
                while True:
                    while cursor < len(handle.events):
                        yield sse_data(handle.events[cursor])
                        cursor += 1
                    if handle.done.is_set():
                        break
                    waiter.clear()
                    if cursor < len(handle.events):
                        continue
                    try:
                        await asyncio.wait_for(waiter.wait(), timeout=SSE_KEEPALIVE_SECONDS)
                    except TimeoutError:
                        yield sse_comment("keepalive")
            finally:
                handle.remove_waiter(waiter)

        headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        return StreamingResponse(stream(), media_type="text/event-stream", headers=headers)

    @app.post("/api/sessions/{run_id}/approve")
    async def approve(run_id: str, body: ApproveRequest) -> dict[str, Any]:
        handle = manager.get(run_id)
        if handle is None or handle.cp is None:
            raise HTTPException(status_code=404, detail="unknown run")
        applied = handle.cp.broker.resolve_approval(
            body.action_id, approved=body.approved, approver=body.approver
        )
        return {"applied": applied}

    @app.post("/api/sessions/{run_id}/revoke")
    async def revoke(run_id: str, body: RevokeRequest) -> dict[str, Any]:
        handle = manager.get(run_id)
        if handle is None or handle.cp is None:
            raise HTTPException(status_code=404, detail="unknown run")
        handle.cp.broker.revoke(handle.cp.session, handle.cp.agent_id, actor=body.actor)
        return {"revoked": True}

    @app.get("/api/benchmark")
    def benchmark() -> dict[str, Any]:
        return run_benchmark().model_dump(mode="json")

    @app.get("/api/eval")
    async def evaluation(refresh: bool = False) -> dict[str, Any]:
        if app.state.eval_report is None or refresh:
            app.state.eval_report = await build_report()
        return app.state.eval_report.model_dump(mode="json")

    if web_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(web_dir)), name="static")

        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(str(web_dir / "index.html"))

    return app


app = create_app()
