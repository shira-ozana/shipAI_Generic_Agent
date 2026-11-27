"""HTTP API for the UI.

    POST /api/init                 start a run (body: InitRequest, every field optional) -> 202 {runId}
    POST /api/init?wait=true       run and return the full result
    GET  /api/init/{runId}         status, progress per node, and the result when done
    GET  /api/init/{runId}/report  platform/init/INIT.md as text/markdown

The router is mounted into `generic_agent.api` (same port as /api/chat, so the UI's Vite
proxy already reaches it). It can also run alone: `uvicorn init_agent.api:app --port 8001`.
Runs are kept in process memory: use a single worker.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from .models import InitRequest
from .service import arun_init

logger = logging.getLogger(__name__)


class RunRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.runs: dict[str, dict[str, Any]] = {}

    def start(self, run_id: str, request: InitRequest) -> None:
        with self._lock:
            self.runs[run_id] = {"runId": run_id, "status": "running", "featureId": request.feature_id,
                                 "currentNode": None, "progress": [], "result": None}

    def progress(self, run_id: str, node: str, message: str) -> None:
        with self._lock:
            run = self.runs[run_id]
            run["currentNode"] = node
            run["progress"].append({"node": node, "message": message})

    def finish(self, run_id: str, result: dict) -> None:
        with self._lock:
            self.runs[run_id].update(status=result["status"], result=result, currentNode=None)

    def get(self, run_id: str) -> dict | None:
        with self._lock:
            run = self.runs.get(run_id)
            return None if run is None else {**run, "progress": list(run["progress"])}


registry = RunRegistry()
router = APIRouter(prefix="/api/init", tags=["init"])


async def _execute(run_id: str, request: InitRequest) -> dict:
    try:
        result = await arun_init(request, run_id=run_id,
                                 on_progress=lambda node, msg: registry.progress(run_id, node, msg))
        data = result.model_dump()
    except Exception as exc:  # noqa: BLE001
        logger.exception("INIT run %s crashed", run_id)
        data = {"stage": "init", "run_id": run_id, "status": "failed", "artifacts": [],
                "output": {"summary": f"INIT crashed: {type(exc).__name__}"}}
    registry.finish(run_id, data)
    return data


@router.post("", status_code=202)
async def start_init(request: InitRequest | None = None, wait: bool = False) -> dict:
    request = request or InitRequest()
    run_id = uuid.uuid4().hex[:12]
    registry.start(run_id, request)
    if wait:
        return await _execute(run_id, request)
    asyncio.get_running_loop().create_task(_execute(run_id, request))
    return {"runId": run_id, "status": "running", "statusUrl": f"/api/init/{run_id}"}


@router.get("/{run_id}")
def get_init(run_id: str) -> dict:
    run = registry.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Unknown INIT run.")
    return run


@router.get("/{run_id}/report", response_class=PlainTextResponse)
def get_report(run_id: str) -> PlainTextResponse:
    run = registry.get(run_id)
    if run is None or not run.get("result"):
        raise HTTPException(status_code=404, detail="Report not available yet.")
    report_file = (run["result"].get("output") or {}).get("report_file")
    if report_file and Path(report_file).is_file():
        return PlainTextResponse(Path(report_file).read_text(encoding="utf-8"), media_type="text/markdown")
    raise HTTPException(status_code=404, detail="Report file not found (local or dry run).")


def create_app() -> FastAPI:
    app = FastAPI(title="init-agent")
    app.add_middleware(CORSMiddleware, allow_origin_regex=r"^http://(localhost|127\.0\.0\.1)(:\d+)?$",
                       allow_methods=["GET", "POST"], allow_headers=["Content-Type"])
    app.include_router(router)
    return app


app = create_app()
