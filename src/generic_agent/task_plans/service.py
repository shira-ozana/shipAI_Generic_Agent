"""Application service: plan (draft) -> review -> approve or decline."""

from __future__ import annotations

import uuid
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from task_split_agent import (
    Clarification,
    HlsDocument,
    RepositoryAccess,
    TaskSplitPlanner,
)
from generic_agent.config import Settings, TaskSplitSettings
from generic_agent.task_plans.store import (
    ApprovalResult,
    DraftNotFoundError,
    DraftRecord,
    NotAuthorizedError,
    PlanAlreadyApprovedError,
    TaskPlanError,
    TaskPlanStore,
)


class UnknownRepositoryError(TaskPlanError):
    """The requested repository ID is not registered with this backend."""


@dataclass(frozen=True)
class PlanResult:
    """A planning run ends in a pending draft or in clarification questions, never in tasks."""

    plan_id: str
    draft: DraftRecord | None = None
    clarification: Clarification | None = None


class TaskPlanService:
    """Owns the planning run and the approval operations.

    ``repositories`` maps a public repository ID to server-side access (a
    checkout path and revision the operator configured). Clients select a
    repository by ID only; they never supply paths or credentials.
    """

    def __init__(
        self,
        store: TaskPlanStore,
        planner: TaskSplitPlanner,
        repositories: Mapping[str, RepositoryAccess],
    ) -> None:
        self.store = store
        self._planner = planner
        self._repositories = dict(repositories)

    @property
    def repository_ids(self) -> list[str]:
        return sorted(self._repositories)

    def plan(
        self,
        user: str,
        hls_documents: Sequence[HlsDocument],
        repository_id: str,
        *,
        plan_id: str | None = None,
        notes: str | None = None,
        approvers: Collection[str] | None = None,
    ) -> PlanResult:
        """Run the Task Split agent and store its output as a pending draft.

        Passing an existing ``plan_id`` creates the next version and supersedes
        the previous pending one. No task records are created.
        """
        repository = self._repositories.get(repository_id)
        if repository is None:
            raise UnknownRepositoryError("unknown repository")
        plan_id = plan_id or str(uuid.uuid4())
        # Fail fast, before spending a model run, if this user cannot revise the plan.
        try:
            self.store.get_draft(plan_id)
        except TaskPlanError:
            pass  # new plan
        else:
            if not self.store.is_authorized(plan_id, user):
                raise NotAuthorizedError("user may not revise this plan")

        outcome = self._planner.plan(hls_documents, repository, notes)
        if outcome.clarification is not None:
            return PlanResult(plan_id, clarification=outcome.clarification)
        assert outcome.document is not None
        draft = self.store.create_draft(
            plan_id,
            outcome.document,
            repository_id=repository.repository_id,
            revision=repository.revision,
            created_by=user,
            approvers=approvers,
        )
        return PlanResult(plan_id, draft=draft)

    def get_draft(self, user: str, plan_id: str, version: int | None = None) -> DraftRecord:
        if not self.store.is_authorized(plan_id, user):
            raise DraftNotFoundError("draft not found")  # same answer as "no such plan"
        return self.store.get_draft(plan_id, version)

    def approve(self, user: str, plan_id: str, version: int) -> ApprovalResult:
        return self.store.approve(plan_id, version, user)

    def decline(self, user: str, plan_id: str, version: int) -> DraftRecord:
        return self.store.decline(plan_id, version, user)


def create_task_plan_service(settings: Settings, task_split: TaskSplitSettings) -> TaskPlanService:
    """Wire the store, planner and authorized repositories from configuration."""
    repositories = {
        repo_id: RepositoryAccess.from_checkout(
            repo_id, config.path, config.revision, config.allowed_paths
        )
        for repo_id, config in task_split.repositories.items()
    }
    return TaskPlanService(
        TaskPlanStore(task_split.database_path), TaskSplitPlanner(settings), repositories
    )


__all__ = [
    "PlanAlreadyApprovedError",
    "PlanResult",
    "TaskPlanService",
    "UnknownRepositoryError",
    "create_task_plan_service",
]
