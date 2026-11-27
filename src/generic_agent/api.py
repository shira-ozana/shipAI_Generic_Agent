"""Local HTTP API: ``python -m uvicorn generic_agent.api:app --host 127.0.0.1 --port 8000``.

A thin FastAPI layer over the same graph the CLI uses. The graph is compiled
once at startup with an ``InMemorySaver``; each chat request sends only the new
``HumanMessage`` and uses the request's ``threadId`` as LangGraph's
``thread_id``, so checkpoints remain the only conversation memory.

The Requirement Agent is served on ``/api/requirement/start`` (inputs, pre-flight,
first turn) and ``/api/requirement/chat`` (answers on the same thread).

Run it with a single worker: history, per-thread clones, credentials and the
in-flight guard live in this process's memory.
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
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictStr, field_validator

from generic_agent.agents.requirement import (
    GitCredentials,
    InputError,
    PreflightError,
    RequirementAgent,
    RequirementInputs,
    RequirementSettings,
    SessionError,
)
from generic_agent.agents.requirement.agent import NO_RUN_MESSAGE
from generic_agent.config import Settings, describe_error, load_settings, load_task_split_settings
from generic_agent.graph import build_agent, load_default_prompt
from generic_agent.task_plans.routes import USER_HEADER, build_router
from generic_agent.task_plans.service import TaskPlanService, create_task_plan_service
from generic_agent.tools import default_tools
from init_agent.api import router as init_router

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


class _ThreadRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    thread_id: Annotated[UUID, Field(alias="threadId")]

    @field_validator("thread_id", mode="before")
    @classmethod
    def _thread_id_is_uuid_string(cls, value: Any) -> Any:
        if not isinstance(value, str):
            raise ValueError("threadId must be a UUID string")
        return value


class RepositoryCredentials(BaseModel):
    """Git credentials; used only by code for git and never echoed or stored."""

    model_config = ConfigDict(extra="forbid")

    username: StrictStr = ""
    token: SecretStr = SecretStr("")


class RequirementStartRequest(_ThreadRequest):
    """Body of ``POST /api/requirement/start``."""

    repo_url: Annotated[StrictStr, Field(alias="repoUrl")]
    feature_id: Annotated[StrictStr, Field(alias="featureId")]
    requirement_text: Annotated[StrictStr, Field(alias="requirementText")]
    credentials: RepositoryCredentials | None = None


class RequirementChatRequest(_ThreadRequest):
    """Body of ``POST /api/requirement/chat``."""

    message: StrictStr

    @field_validator("message")
    @classmethod
    def _message_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value


class RequirementResponse(BaseModel):
    """Body returned by both requirement endpoints."""

    threadId: str  # noqa: N815 - shared HTTP contract uses camelCase
    reply: str
    status: str | None


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
    settings: Settings | None = None,
    task_plans: TaskPlanService | None = None,
    requirement_settings: RequirementSettings | None = None,
) -> FastAPI:
    """Create the API app.

    ``settings`` is loaded from ``CURSOR_API_KEY`` / ``CURSOR_MODEL`` (shell or
    ``.env``) at startup when omitted. The graph is compiled once at startup.

    ``task_plans`` serves ``/api/task-plans``. When omitted it is built from
    ``TASK_SPLIT_REPOSITORIES`` / ``TASK_SPLIT_DB`` if configured; otherwise
    those endpoints answer ``503``.

    ``requirement_settings`` (Requirement Agent) defaults to
    ``load_requirement_settings()``.
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
        app.state.requirement = RequirementAgent(resolved, requirement_settings)
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

    # INIT agent (POST /api/init, GET /api/init/{runId}); see src/init_agent/api.py
    app.include_router(init_router)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Drop the echoed input: a request body can carry repository credentials.
        errors = [{k: v for k, v in e.items() if k not in {"input", "ctx", "url"}} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})

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

    def _requirement_turn(request: Request, thread_id: str, call) -> RequirementResponse:
        guard: ThreadGuard = request.app.state.guard
        if not guard.acquire(thread_id):
            raise HTTPException(status_code=409, detail=BUSY_MESSAGE)
        try:
            turn = call(request.app.state.requirement)
        except InputError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except PreflightError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except SessionError as exc:
            code = 404 if str(exc) == NO_RUN_MESSAGE else 409
            raise HTTPException(status_code=code, detail=str(exc)) from None
        except Exception as exc:  # noqa: BLE001 - never leak raw SDK errors to clients
            logger.error("Requirement run failed for thread %s: %s", thread_id, describe_error(exc))
            raise HTTPException(status_code=502, detail=RUN_FAILED_MESSAGE) from None
        finally:
            guard.release(thread_id)
        if not turn.reply:
            logger.error("Cursor returned empty text for requirement thread %s", thread_id)
            raise HTTPException(status_code=502, detail=RUN_FAILED_MESSAGE)
        return RequirementResponse(threadId=thread_id, reply=turn.reply, status=turn.status)

    @app.post("/api/requirement/start", response_model=RequirementResponse)
    def requirement_start(body: RequirementStartRequest, request: Request) -> RequirementResponse:
        thread_id = str(body.thread_id)
        creds = body.credentials or RepositoryCredentials()
        try:
            inputs = RequirementInputs(
                repo_url=body.repo_url,
                feature_id=body.feature_id,
                requirement_text=body.requirement_text,
                credentials=GitCredentials(username=creds.username, token=creds.token),
            )
        except InputError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return _requirement_turn(request, thread_id, lambda agent: agent.start(thread_id, inputs))

    @app.post("/api/requirement/chat", response_model=RequirementResponse)
    def requirement_chat(body: RequirementChatRequest, request: Request) -> RequirementResponse:
        thread_id = str(body.thread_id)
        return _requirement_turn(request, thread_id, lambda agent: agent.reply(thread_id, body.message))

    return app


app = create_app()

__all__ = ["app", "create_app"]
