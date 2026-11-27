"""The review payload shown in the UI: a draft grouped by component or service.

Pure function of a stored draft. It carries the ``planId`` and ``version`` the
UI must send back when the user approves or declines, so approval is bound to
exactly what was displayed.
"""

from __future__ import annotations

from typing import Any

from task_split_agent.schema import (
    Clarification,
    DependencyDocument,
    cross_group_dependencies,
    execution_waves,
)
from generic_agent.task_plans.store import DraftRecord


def build_review(draft: DraftRecord) -> dict[str, Any]:
    """JSON-ready (camelCase) review view of ``draft``."""
    document = draft.document
    owner = {t.id: t.group_id for t in document.tasks}
    waves = execution_waves(document)
    wave_of = {task_id: index for index, wave in enumerate(waves) for task_id in wave}
    cross = cross_group_dependencies(document)
    cross_ids = set(cross)

    groups = []
    for group in document.groups:
        tasks = []
        for task in document.tasks:
            if task.group_id != group.id:
                continue
            tasks.append(
                {
                    "id": task.id,
                    "title": task.title,
                    "scope": task.scope,
                    "deliverable": task.deliverable,
                    "acceptanceCriteria": list(task.acceptance_criteria),
                    "hlsReferences": list(task.hls_references),
                    "repoReferences": list(task.repo_references),
                    "wave": wave_of[task.id],
                    "canStartImmediately": not task.depends_on,
                    "dependsOn": [
                        {
                            "taskId": dep,
                            "groupId": owner[dep],
                            "crossGroup": (task.id, dep) in cross_ids,
                        }
                        for dep in task.depends_on
                    ],
                }
            )
        groups.append(
            {
                "id": group.id,
                "name": group.name,
                "kind": group.kind,
                "proposed": group.proposed,
                "hlsReferences": list(group.hls_references),
                "repoPaths": list(group.repo_paths),
                "tasks": tasks,
            }
        )

    return {
        "planId": draft.plan_id,
        "version": draft.version,
        "status": draft.status,
        "repository": {"id": draft.repository_id, "revision": draft.revision},
        "createdBy": draft.created_by,
        "createdAt": draft.created_at,
        "decidedBy": draft.decided_by,
        "decidedAt": draft.decided_at,
        "hlsReferences": list(document.hls_references),
        "groups": groups,
        "crossGroupDependencies": [
            {
                "taskId": task_id,
                "groupId": owner[task_id],
                "dependsOnTaskId": dep,
                "dependsOnGroupId": owner[dep],
            }
            for task_id, dep in cross
        ],
        "executionWaves": [list(wave) for wave in waves],
        "discrepancies": [
            {
                "description": d.description,
                "hlsReferences": list(d.hls_references),
                "repoPaths": list(d.repo_paths),
            }
            for d in document.discrepancies
        ],
        "warnings": _warnings(document),
    }


def build_clarification(clarification: Clarification) -> list[dict[str, Any]]:
    return [
        {
            "question": q.question,
            "whyItMatters": q.why_it_matters,
            "hlsReferences": list(q.hls_references),
            "repoPaths": list(q.repo_paths),
        }
        for q in clarification.questions
    ]


def _warnings(document: DependencyDocument) -> list[str]:
    """Non-blocking review hints (the draft is already valid)."""
    cited = {ref.split("#", 1)[0] for t in document.tasks for ref in t.hls_references}
    return [
        f"No task cites HLS document {doc_id}." for doc_id in document.hls_references if doc_id.split("#", 1)[0] not in cited
    ]
