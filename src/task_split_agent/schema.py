"""The Task Split dependency document: types, parsing and application-side validation.

The model returns one JSON object (see ``prompts/core.md``). Nothing
in it is trusted: ``parse_reply`` turns it into frozen dataclasses and
``validate_document`` enforces the guardrails in application code:

* every task has exactly one owning group that exists,
* task IDs (and group IDs) are unique,
* every dependency points at an existing, different task,
* the dependency graph is acyclic,
* citations point at supplied HLS documents and at repository paths that exist
  inside the authorized repository scope.

Plan *versions* and the repository revision are never part of the model output;
the backend assigns them when it stores a draft.

This module has no dependency on the graph, the SDK or the database.
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

GROUP_KINDS = ("component", "service")

ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
"""Shape of group and task IDs (and HLS document IDs)."""

_SAFE_TEXT = re.compile(r"^[\w.\-#/: ]{1,80}$")
_FENCE = re.compile(r"^```[A-Za-z0-9]*\s*\n(.*?)\n?```\s*$", re.DOTALL)
MAX_REPORTED_ERRORS = 10


class PlanValidationError(ValueError):
    """The model reply (or a stored document) breaks the dependency-document rules.

    ``errors`` lists every problem found. Messages only quote short, identifier-like
    values taken from the reply, never free text from it.
    """

    def __init__(self, errors: Iterable[str]) -> None:
        self.errors = tuple(errors)
        shown = self.errors[:MAX_REPORTED_ERRORS]
        extra = len(self.errors) - len(shown)
        text = "; ".join(shown) + (f"; and {extra} more" if extra > 0 else "")
        super().__init__(text or "invalid dependency document")


class RepositoryVerifier(Protocol):
    """What validation needs from repository access: does this path exist in scope?"""

    def contains(self, path: str) -> bool: ...


@dataclass(frozen=True)
class Group:
    """A component or service that owns tasks."""

    id: str
    name: str
    kind: str
    proposed: bool
    hls_references: tuple[str, ...]
    repo_paths: tuple[str, ...]


@dataclass(frozen=True)
class Task:
    """One independently verifiable unit of work, owned by exactly one group."""

    id: str
    group_id: str
    title: str
    scope: str
    deliverable: str
    acceptance_criteria: tuple[str, ...]
    hls_references: tuple[str, ...]
    repo_references: tuple[str, ...]
    depends_on: tuple[str, ...]


@dataclass(frozen=True)
class Discrepancy:
    """A non-blocking mismatch between the HLS and the repository."""

    description: str
    hls_references: tuple[str, ...]
    repo_paths: tuple[str, ...]


@dataclass(frozen=True)
class Question:
    """A focused clarification question."""

    question: str
    why_it_matters: str
    hls_references: tuple[str, ...]
    repo_paths: tuple[str, ...]


@dataclass(frozen=True)
class DependencyDocument:
    """Tasks grouped by owning component or service, with direct dependencies."""

    hls_references: tuple[str, ...]
    groups: tuple[Group, ...]
    tasks: tuple[Task, ...]
    discrepancies: tuple[Discrepancy, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Canonical JSON-compatible form (what the backend stores)."""
        return {
            "hls_references": list(self.hls_references),
            "groups": [
                {
                    "id": g.id,
                    "name": g.name,
                    "kind": g.kind,
                    "proposed": g.proposed,
                    "hls_references": list(g.hls_references),
                    "repo_paths": list(g.repo_paths),
                }
                for g in self.groups
            ],
            "tasks": [
                {
                    "id": t.id,
                    "group_id": t.group_id,
                    "title": t.title,
                    "scope": t.scope,
                    "deliverable": t.deliverable,
                    "acceptance_criteria": list(t.acceptance_criteria),
                    "hls_references": list(t.hls_references),
                    "repo_references": list(t.repo_references),
                    "depends_on": list(t.depends_on),
                }
                for t in self.tasks
            ],
            "discrepancies": [
                {
                    "description": d.description,
                    "hls_references": list(d.hls_references),
                    "repo_paths": list(d.repo_paths),
                }
                for d in self.discrepancies
            ],
        }

    @classmethod
    def from_dict(cls, data: object) -> DependencyDocument:
        """Parse the canonical form. Raises ``PlanValidationError`` on a bad shape."""
        errors: list[str] = []
        document = _parse_document(data, errors)
        if errors or document is None:
            raise PlanValidationError(errors)
        return document

    def group_of(self, task_id: str) -> str:
        return next(t.group_id for t in self.tasks if t.id == task_id)


@dataclass(frozen=True)
class Clarification:
    """The agent needs answers before it can produce a reliable plan."""

    questions: tuple[Question, ...]


PlanningReply = DependencyDocument | Clarification


# --- Parsing -----------------------------------------------------------------


def extract_json(text: str) -> object:
    """Decode the reply as one JSON value, tolerating a single ``` fence."""
    body = text.strip()
    fenced = _FENCE.match(body)
    if fenced:
        body = fenced.group(1).strip()
    try:
        return json.loads(body)
    except (json.JSONDecodeError, RecursionError):
        raise PlanValidationError(["the reply is not a single valid JSON object"]) from None


def parse_reply(
    text: str,
    *,
    hls_document_ids: Collection[str] | None = None,
    repository: RepositoryVerifier | None = None,
) -> PlanningReply:
    """Parse and validate the model's reply.

    Returns a validated ``DependencyDocument`` (``"status": "plan"``) or a
    ``Clarification``. Raises ``PlanValidationError`` otherwise.
    """
    data = extract_json(text)
    if not isinstance(data, Mapping):
        raise PlanValidationError(["the reply must be a JSON object"])
    status = data.get("status")
    if status == "clarification":
        return _parse_clarification(data)
    if status != "plan":
        raise PlanValidationError(['"status" must be "plan" or "clarification"'])
    document = DependencyDocument.from_dict(data.get("plan"))
    validate_document(document, hls_document_ids=hls_document_ids, repository=repository)
    return document


def _parse_clarification(data: Mapping[str, Any]) -> Clarification:
    errors: list[str] = []
    raw = data.get("questions")
    questions: list[Question] = []
    if not isinstance(raw, list) or not raw:
        errors.append('"questions" must be a non-empty list')
    else:
        for index, item in enumerate(raw, 1):
            where = f"question {index}"
            if not isinstance(item, Mapping):
                errors.append(f"{where} must be an object")
                continue
            text = _text(item, "question", where, errors)
            why = item.get("why_it_matters", "")
            if not isinstance(why, str):
                errors.append(f"{where}: why_it_matters must be a string")
                why = ""
            questions.append(
                Question(
                    text,
                    why.strip(),
                    _text_list(item, "hls_references", where, errors, required=False),
                    _text_list(item, "repo_paths", where, errors, required=False),
                )
            )
    if errors:
        raise PlanValidationError(errors)
    return Clarification(tuple(questions))


def _parse_document(data: object, errors: list[str]) -> DependencyDocument | None:
    if not isinstance(data, Mapping):
        errors.append('"plan" must be an object')
        return None
    hls_references = _text_list(data, "hls_references", "plan", errors)

    groups: list[Group] = []
    raw_groups = data.get("groups")
    if not isinstance(raw_groups, list) or not raw_groups:
        errors.append('"groups" must be a non-empty list')
    else:
        for index, item in enumerate(raw_groups, 1):
            where = f"group {index}"
            if not isinstance(item, Mapping):
                errors.append(f"{where} must be an object")
                continue
            proposed = item.get("proposed", False)
            if not isinstance(proposed, bool):
                errors.append(f"{where}: proposed must be true or false")
                proposed = False
            groups.append(
                Group(
                    id=_text(item, "id", where, errors),
                    name=_text(item, "name", where, errors),
                    kind=_text(item, "kind", where, errors),
                    proposed=proposed,
                    hls_references=_text_list(item, "hls_references", where, errors),
                    repo_paths=_text_list(item, "repo_paths", where, errors, required=False),
                )
            )

    tasks: list[Task] = []
    raw_tasks = data.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        errors.append('"tasks" must be a non-empty list')
    else:
        for index, item in enumerate(raw_tasks, 1):
            where = f"task {index}"
            if not isinstance(item, Mapping):
                errors.append(f"{where} must be an object")
                continue
            tasks.append(
                Task(
                    id=_text(item, "id", where, errors),
                    # A list here ("several owners") fails the string check below.
                    group_id=_text(item, "group_id", where, errors),
                    title=_text(item, "title", where, errors),
                    scope=_text(item, "scope", where, errors),
                    deliverable=_text(item, "deliverable", where, errors),
                    acceptance_criteria=_text_list(item, "acceptance_criteria", where, errors),
                    hls_references=_text_list(item, "hls_references", where, errors),
                    repo_references=_text_list(item, "repo_references", where, errors, required=False),
                    depends_on=_text_list(item, "depends_on", where, errors, required=False),
                )
            )

    discrepancies: list[Discrepancy] = []
    raw_discrepancies = data.get("discrepancies", [])
    if not isinstance(raw_discrepancies, list):
        errors.append('"discrepancies" must be a list')
    else:
        for index, item in enumerate(raw_discrepancies, 1):
            where = f"discrepancy {index}"
            if not isinstance(item, Mapping):
                errors.append(f"{where} must be an object")
                continue
            discrepancies.append(
                Discrepancy(
                    _text(item, "description", where, errors),
                    _text_list(item, "hls_references", where, errors, required=False),
                    _text_list(item, "repo_paths", where, errors, required=False),
                )
            )

    if errors:
        return None
    return DependencyDocument(
        hls_references=hls_references,
        groups=tuple(groups),
        tasks=tuple(tasks),
        discrepancies=tuple(discrepancies),
    )


def _text(item: Mapping[str, Any], key: str, where: str, errors: list[str]) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{where}: {key} must be a non-blank string")
        return ""
    return value.strip()


def _text_list(
    item: Mapping[str, Any], key: str, where: str, errors: list[str], *, required: bool = True
) -> tuple[str, ...]:
    value = item.get(key, [])
    if not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value):
        errors.append(f"{where}: {key} must be a list of non-blank strings")
        return ()
    if required and not value:
        errors.append(f"{where}: {key} must not be empty")
    return tuple(v.strip() for v in value)


# --- Validation --------------------------------------------------------------


def validate_document(
    document: DependencyDocument,
    *,
    hls_document_ids: Collection[str] | None = None,
    repository: RepositoryVerifier | None = None,
) -> None:
    """Enforce the application guardrails; raise ``PlanValidationError`` listing all breaches.

    ``hls_document_ids`` and ``repository`` are optional so the backend can
    re-check the structural rules (ownership, IDs, dependencies, cycles) on a
    stored document without the original run context.
    """
    errors: list[str] = []

    group_ids = _unique_ids((g.id for g in document.groups), "group", errors)
    task_ids = _unique_ids((t.id for t in document.tasks), "task", errors)

    for group in document.groups:
        if group.kind not in GROUP_KINDS:
            errors.append(f"group {_ref(group.id)}: kind must be one of {', '.join(GROUP_KINDS)}")

    # Ownership: exactly one group per task, and no empty groups in review.
    owners: dict[str, int] = {}
    for task in document.tasks:
        if task.group_id not in group_ids:
            errors.append(f"task {_ref(task.id)} is owned by unknown group {_ref(task.group_id)}")
        else:
            owners[task.group_id] = owners.get(task.group_id, 0) + 1
    for group in document.groups:
        if group.id in group_ids and group.id not in owners:
            errors.append(f"group {_ref(group.id)} owns no tasks")

    # Dependencies reference existing, different tasks, once each.
    graph: dict[str, tuple[str, ...]] = {}
    for task in document.tasks:
        seen: set[str] = set()
        for dep in task.depends_on:
            if dep == task.id:
                errors.append(f"task {_ref(task.id)} depends on itself")
            elif dep not in task_ids:
                errors.append(f"task {_ref(task.id)} depends on unknown task {_ref(dep)}")
            elif dep in seen:
                errors.append(f"task {_ref(task.id)} lists dependency {_ref(dep)} twice")
            seen.add(dep)
        graph.setdefault(task.id, tuple(d for d in task.depends_on if d in task_ids and d != task.id))
    cycle = find_cycle(graph)
    if cycle:
        errors.append("dependency cycle: " + " -> ".join(_ref(t) for t in cycle))

    # Evidence and scope.
    if hls_document_ids is not None:
        known = set(hls_document_ids)

        def check_hls(owner: str, refs: Iterable[str]) -> None:
            for ref in refs:
                if ref.split("#", 1)[0] not in known:
                    errors.append(f"{owner} cites unknown HLS document {_ref(ref.split('#', 1)[0])}")

        check_hls("plan", document.hls_references)
        for group in document.groups:
            check_hls(f"group {_ref(group.id)}", group.hls_references)
        for task in document.tasks:
            check_hls(f"task {_ref(task.id)}", task.hls_references)
        for item in document.discrepancies:
            check_hls("a discrepancy", item.hls_references)

    if repository is not None:
        for group in document.groups:
            if not group.proposed and not group.repo_paths:
                errors.append(f"existing group {_ref(group.id)} needs a verified repository path")
            for path in group.repo_paths:
                if not repository.contains(path):
                    errors.append(f"group {_ref(group.id)} cites unverified repository path {_ref(path)}")
        proposed = {g.id for g in document.groups if g.proposed}
        for task in document.tasks:
            if task.group_id not in proposed and not task.repo_references:
                errors.append(f"task {_ref(task.id)} needs repository evidence")
            for path in task.repo_references:
                if not repository.contains(path):
                    errors.append(f"task {_ref(task.id)} cites unverified repository path {_ref(path)}")
        for item in document.discrepancies:
            for path in item.repo_paths:
                if not repository.contains(path):
                    errors.append(f"a discrepancy cites unverified repository path {_ref(path)}")

    if errors:
        raise PlanValidationError(errors)


def _unique_ids(ids: Iterable[str], noun: str, errors: list[str]) -> set[str]:
    seen: set[str] = set()
    for value in ids:
        if not ID_PATTERN.match(value):
            errors.append(f"{noun} ID {_ref(value)} must match {ID_PATTERN.pattern}")
        if value in seen:
            errors.append(f"duplicate {noun} ID {_ref(value)}")
        seen.add(value)
    return seen


def _ref(value: str) -> str:
    """Quote an identifier-like value from the reply; hide anything else."""
    return f"'{value}'" if _SAFE_TEXT.match(value) else "'<invalid>'"


def find_cycle(graph: Mapping[str, Collection[str]]) -> list[str] | None:
    """Return one cycle as ``[a, b, ..., a]`` (edges are task -> prerequisite), or ``None``."""
    white, grey, black = 0, 1, 2
    color = dict.fromkeys(graph, white)
    for start in graph:
        if color[start] != white:
            continue
        path = [start]
        stack = [iter(graph[start])]
        color[start] = grey
        while stack:
            for nxt in stack[-1]:
                state = color.get(nxt, white)
                if state == grey:
                    return path[path.index(nxt) :] + [nxt]
                if state == white and nxt in graph:
                    color[nxt] = grey
                    path.append(nxt)
                    stack.append(iter(graph[nxt]))
                    break
            else:
                color[path.pop()] = black
                stack.pop()
    return None


# --- Analysis ------------------------------------------------------------------


def execution_waves(document: DependencyDocument) -> tuple[tuple[str, ...], ...]:
    """Group task IDs into waves; every task in a wave can start in parallel.

    Wave 0 holds tasks without prerequisites. A task sits one wave after its
    latest prerequisite. The document must already be validated (acyclic).
    """
    depth: dict[str, int] = {}
    by_id = {t.id: t for t in document.tasks}

    def resolve(task_id: str) -> int:
        if task_id not in depth:
            depth[task_id] = 1 + max((resolve(d) for d in by_id[task_id].depends_on), default=-1)
        return depth[task_id]

    for task in document.tasks:
        resolve(task.id)
    waves: dict[int, list[str]] = {}
    for task in document.tasks:
        waves.setdefault(depth[task.id], []).append(task.id)
    return tuple(tuple(waves[level]) for level in sorted(waves))


def cross_group_dependencies(document: DependencyDocument) -> tuple[tuple[str, str], ...]:
    """``(task_id, prerequisite_task_id)`` pairs whose tasks have different owners."""
    owner = {t.id: t.group_id for t in document.tasks}
    return tuple(
        (t.id, dep) for t in document.tasks for dep in t.depends_on if owner.get(dep) not in (None, t.group_id)
    )
