"""HTTP routes for the Task Split review and approval flow (``/api/task-plans``).

Contract for the UI (see ``docs/modules/task-plans.md``):

* ``POST /api/task-plans``: run the agent, store a *pending draft*, return the
  review view (or clarification questions). Creates no tasks.
* ``GET  /api/task-plans/{planId}[?version=N]``: the review view of a version.
* ``POST /api/task-plans/{planId}/versions/{version}/approve``: save that exact
  version's tasks and dependencies in one transaction. Safe to retry.
* ``POST /api/task-plans/{planId}/versions/{version}/decline``: decline it.

The caller is identified by the ``X-User-Id`` header. This module trusts it, so
the deployment must put real authentication in front (see the docs).
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictStr

from generic_agent.agents.task_split import HlsDocument
from generic_agent.agents.task_split.definition import MAX_HLS_DOCUMENTS
from generic_agent.config import describe_error
from generic_agent.task_plans.review import build_clarification, build_review
from generic_agent.task_plans.service import TaskPlanService, UnknownRepositoryError
from generic_agent.task_plans.store import (
    DraftNotFoundError,
    DraftNotPendingError,
    NotAuthorizedError,
    PlanAlreadyApprovedError,
)

logger = logging.getLogger(__name__)

USER_HEADER = "X-User-Id"
NOT_CONFIGURED_MESSAGE = "Task plans are not configured on this server."
PLAN_FAILED_MESSAGE = "The planner could not produce a valid plan. Please try again."
NOT_FOUND_MESSAGE = "Draft not found."
FORBIDDEN_MESSAGE = "You are not allowed to act on this plan."


class HlsDocumentIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: StrictStr
    title: StrictStr
    content: StrictStr


class PlanRequest(BaseModel):
    """Body of ``POST /api/task-plans``."""

    model_config = ConfigDict(extra="ignore")

    planId: Annotated[StrictStr | None, Field(max_length=64)] = None  # noqa: N815
    repositoryId: StrictStr  # noqa: N815
    hlsDocuments: Annotated[list[HlsDocumentIn], Field(min_length=1, max_length=MAX_HLS_DOCUMENTS)]  # noqa: N815
    notes: StrictStr | None = None


def current_user(x_user_id: Annotated[str | None, Header(alias=USER_HEADER)] = None) -> str:
    user = (x_user_id or "").strip()
    if not user or len(user) > 200:
        raise HTTPException(status_code=401, detail=f"The {USER_HEADER} header is required.")
    return user


def get_service(request: Request) -> TaskPlanService:
    service: TaskPlanService | None = getattr(request.app.state, "task_plans", None)
    if service is None:
        raise HTTPException(status_code=503, detail=NOT_CONFIGURED_MESSAGE)
    return service


User = Annotated[str, Depends(current_user)]
Service = Annotated[TaskPlanService, Depends(get_service)]


def _translate(exc: Exception) -> HTTPException:
    if isinstance(exc, NotAuthorizedError):
        return HTTPException(status_code=403, detail=FORBIDDEN_MESSAGE)
    if isinstance(exc, (DraftNotFoundError, UnknownRepositoryError)):
        return HTTPException(status_code=404, detail=NOT_FOUND_MESSAGE if isinstance(exc, DraftNotFoundError) else str(exc))
    if isinstance(exc, DraftNotPendingError):
        return HTTPException(
            status_code=409,
            detail=f"This draft version is {exc.status} and cannot be approved. Review the latest version.",
        )
    if isinstance(exc, PlanAlreadyApprovedError):
        return HTTPException(status_code=409, detail="This plan already has an approved version.")
    raise exc


def build_router() -> APIRouter:
    router = APIRouter(prefix="/api/task-plans", tags=["task-plans"])

    @router.post("")
    def create_plan(body: PlanRequest, user: User, service: Service) -> dict:
        try:
            documents = [HlsDocument(d.id, d.title, d.content) for d in body.hlsDocuments]
            result = service.plan(
                user, documents, body.repositoryId, plan_id=body.planId, notes=body.notes
            )
        except (NotAuthorizedError, DraftNotFoundError, UnknownRepositoryError, PlanAlreadyApprovedError) as exc:
            raise _translate(exc) from None
        except ValueError as exc:
            # Bad input (blank/duplicate HLS documents, credential in input): our own message.
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except Exception as exc:  # noqa: BLE001 - never leak raw SDK or model output to clients
            logger.error("Task split run failed: %s", describe_error(exc))
            raise HTTPException(status_code=502, detail=PLAN_FAILED_MESSAGE) from None
        if result.clarification is not None:
            return {
                "status": "clarification",
                "planId": result.plan_id,
                "questions": build_clarification(result.clarification),
            }
        assert result.draft is not None
        return {"status": "draft", "planId": result.plan_id, "draft": build_review(result.draft)}

    @router.get("/{plan_id}")
    def get_plan(plan_id: str, user: User, service: Service, version: int | None = None) -> dict:
        try:
            return build_review(service.get_draft(user, plan_id, version))
        except (DraftNotFoundError, NotAuthorizedError) as exc:
            raise _translate(exc) from None

    @router.post("/{plan_id}/versions/{version}/approve")
    def approve(plan_id: str, version: int, user: User, service: Service) -> dict:
        try:
            result = service.approve(user, plan_id, version)
        except (NotAuthorizedError, DraftNotFoundError, DraftNotPendingError) as exc:
            raise _translate(exc) from None
        except Exception as exc:  # noqa: BLE001 - includes InvalidDraftError and database errors
            logger.error("Approval of %s v%s failed: %s", plan_id, version, describe_error(exc))
            raise HTTPException(status_code=500, detail="The plan could not be approved.") from None
        return {
            "status": "approved",
            "planId": result.plan_id,
            "version": result.version,
            "taskCount": result.task_count,
            "dependencyCount": result.dependency_count,
            "alreadyApproved": result.already_approved,
        }

    @router.post("/{plan_id}/versions/{version}/decline")
    def decline(plan_id: str, version: int, user: User, service: Service) -> dict:
        try:
            return build_review(service.decline(user, plan_id, version))
        except (NotAuthorizedError, DraftNotFoundError, DraftNotPendingError) as exc:
            raise _translate(exc) from None

    return router
