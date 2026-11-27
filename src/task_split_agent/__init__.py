"""Task Split agent: splits HLS documents into owner-grouped, dependency-mapped tasks.

A package of its own, like ``init_agent``: the code, the prompt pack
(``prompts/``) and the validation live here, and the planner runs the shared
``generic_agent.runner.GenericAgent``. The backend that stores drafts and tasks
is ``generic_agent.task_plans``.
"""

from .planner import HlsDocument, PlanningOutcome, TaskSplitPlanner, build_planning_request
from .prompt_pack import PromptPack
from .repository import RepositoryAccess, RepositoryAccessError
from .schema import (
    Clarification,
    DependencyDocument,
    Group,
    PlanValidationError,
    Question,
    Task,
    cross_group_dependencies,
    execution_waves,
    parse_reply,
    validate_document,
)

__all__ = [
    "Clarification",
    "DependencyDocument",
    "Group",
    "HlsDocument",
    "PlanValidationError",
    "PlanningOutcome",
    "PromptPack",
    "Question",
    "RepositoryAccess",
    "RepositoryAccessError",
    "Task",
    "TaskSplitPlanner",
    "build_planning_request",
    "cross_group_dependencies",
    "execution_waves",
    "parse_reply",
    "validate_document",
]
