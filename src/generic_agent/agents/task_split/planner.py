"""Run the Task Split agent: HLS documents + repository in, draft or questions out.

The planning run ends here. It never saves tasks; the caller stores the returned
document as a *draft* and a human approves a specific version later.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from langchain_core.messages import HumanMessage

from generic_agent.agents.task_split.definition import (
    HlsDocument,
    build_planning_request,
    build_task_split_definition,
    request_has_no_credentials,
)
from generic_agent.agents.task_split.repository import RepositoryAccess
from generic_agent.agents.task_split.schema import Clarification, DependencyDocument, parse_reply
from generic_agent.config import Settings
from generic_agent.runtime import GenericAgent


@dataclass(frozen=True)
class PlanningOutcome:
    """Exactly one of ``document`` (a validated draft) or ``clarification`` is set."""

    document: DependencyDocument | None = None
    clarification: Clarification | None = None
    cursor_run_id: str | None = None


class TaskSplitPlanner:
    """Creates a draft dependency document (or clarification questions) from HLS input."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def plan(
        self,
        documents: Sequence[HlsDocument],
        repository: RepositoryAccess,
        notes: str | None = None,
    ) -> PlanningOutcome:
        """Run one planning turn.

        Raises:
            ValueError: the input is invalid (blank or duplicate documents, too large,
                or it appears to contain a credential).
            GuardrailError: a guardrail rejected the input or the model's reply
                (for example a missing dependency or a cycle). Nothing is returned.
            CursorRunError: the Cursor run did not finish.
        """
        request = build_planning_request(documents, repository, notes)
        screened = request_has_no_credentials(request)
        if not screened.passed:  # also a pre-run guardrail; checked here to report it as bad input
            raise ValueError(screened.message)
        definition = build_task_split_definition(
            hls_document_ids=[d.id for d in documents], repository=repository
        )
        # A fresh runner per run: the repository tools are scoped to this checkout.
        runner = GenericAgent(settings=self._settings, tools=repository.tools())
        result = runner.run(definition, messages=[HumanMessage(content=request)])
        reply = result["messages"][-1]
        # The post-run guardrail already validated the reply; parse again for the typed value.
        parsed = parse_reply(
            reply.text,
            hls_document_ids=[d.id for d in documents],
            repository=repository,
        )
        run_id = reply.response_metadata.get("cursor_run_id")
        if isinstance(parsed, Clarification):
            return PlanningOutcome(clarification=parsed, cursor_run_id=run_id)
        return PlanningOutcome(document=parsed, cursor_run_id=run_id)
