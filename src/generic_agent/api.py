"""Local HTTP API: ``python -m uvicorn generic_agent.api:app --host 127.0.0.1 --port 8000``.

A thin FastAPI layer over the same graph the CLI uses. The graph is compiled
once at startup with an ``InMemorySaver``; each chat request sends only the new
``HumanMessage`` and uses the request's ``threadId`` as LangGraph's
``thread_id``, so checkpoints remain the only conversation memory.

Run it with a single worker: history and the in-flight guard live in this
process's memory.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any
from uuid import UUID

from cursor_sdk import close_default_client
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from generic_agent.config import Settings, describe_error, load_settings, load_task_split_settings
from generic_agent.graph import build_agent, load_default_prompt
from generic_agent.task_plans.routes import USER_HEADER, build_router
from generic_agent.task_plans.service import TaskPlanService, create_task_plan_service
from generic_agent.tools import default_tools

logger = logging.getLogger(__name__)

LOCAL_ORIGIN_REGEX = r"^http://(localhost|127\.0\.0\.1)(:\d+)?$"
"""Browser origins allowed to call the API (local dev servers on any port)."""

BUSY_MESSAGE = "A request for this thread is already running. Wait for it to finish."
RUN_FAILED_MESSAGE = "The assistant could not produce a reply. Please try again."


class ChatRequest(BaseModel):
    """Body of ``POST /api/chat``."""

    model_config = ConfigDict(extra="ignore")

    thread_id: Annotated[UUID, Field(alias="threadId")]
    message: StrictStr

    @field_validator("thread_id", mode="before")
    @classmethod
    def _thread_id_is_uuid_string(cls, value: Any) -> Any:
        if not isinstance(value, str):
            raise ValueError("threadId must be a UUID string")
        return value

    @field_validator("message")
    @classmethod
    def _message_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value


class ChatResponse(BaseModel):
    """Body returned by ``POST /api/chat``."""

    threadId: str  # noqa: N815 - shared HTTP contract uses camelCase
    reply: str


class ThreadGuard:
    """Tracks threads with a run in progress so a thread never runs twice at once."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: set[str] = set()

    def acquire(self, thread_id: str) -> bool:
        with self._lock:
            if thread_id in self._active:
                return False
            self._active.add(thread_id)
            return True

    def release(self, thread_id: str) -> None:
        with self._lock:
            self._active.discard(thread_id)


def create_app(
    settings: Settings | None = None, task_plans: TaskPlanService | None = None
) -> FastAPI:
    """Create the API app.

    ``settings`` is loaded from ``CURSOR_API_KEY`` / ``CURSOR_MODEL`` (shell or
    ``.env``) at startup when omitted. The graph is compiled once at startup.

    ``task_plans`` serves ``/api/task-plans``. When omitted it is built from
    ``TASK_SPLIT_REPOSITORIES`` / ``TASK_SPLIT_DB`` if configured; otherwise
    those endpoints answer ``503``.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        resolved = settings or load_settings()
        app.state.agent = build_agent(
            resolved,
            load_default_prompt(),
            default_tools(),
            checkpointer=InMemorySaver(),
        )
        app.state.guard = ThreadGuard()
        owned_plans = None
        if task_plans is None:
            task_split = load_task_split_settings()
            if task_split is not None:
                owned_plans = create_task_plan_service(resolved, task_split)
        app.state.task_plans = task_plans or owned_plans
        try:
            yield
        finally:
            if owned_plans is not None:
                owned_plans.store.close()
            close_default_client()

    app = FastAPI(title="generic-langgraph-agent", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=LOCAL_ORIGIN_REGEX,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", USER_HEADER],
    )
    app.include_router(build_router())

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    # Sync route: FastAPI runs it in a worker thread, so the blocking graph
    # invocation does not stall the event loop or other threads' requests.
    @app.post("/api/chat", response_model=ChatResponse)
    def chat(body: ChatRequest, request: Request) -> ChatResponse:
        thread_id = str(body.thread_id)
        guard: ThreadGuard = request.app.state.guard
        if not guard.acquire(thread_id):
            raise HTTPException(status_code=409, detail=BUSY_MESSAGE)
        try:
            result = request.app.state.agent.invoke(
                {"messages": [HumanMessage(content=body.message)]},
                {"configurable": {"thread_id": thread_id}},
            )
            reply = result["messages"][-1].text.strip()
        except Exception as exc:  # noqa: BLE001 - never leak raw SDK errors to clients
            # describe_error never includes credentials; the client gets a fixed message.
            logger.error("Cursor run failed for thread %s: %s", thread_id, describe_error(exc))
            raise HTTPException(status_code=502, detail=RUN_FAILED_MESSAGE) from None
        finally:
            guard.release(thread_id)
        if not reply:
            logger.error("Cursor returned empty text for thread %s", thread_id)
            raise HTTPException(status_code=502, detail=RUN_FAILED_MESSAGE)
        return ChatResponse(threadId=thread_id, reply=reply)

    return app


app = create_app()

__all__ = ["app", "create_app"]
