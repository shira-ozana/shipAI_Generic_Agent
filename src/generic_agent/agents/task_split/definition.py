"""The Task Split ``AgentDefinition``: prompt, skills, rules and guardrails.

Assets live in ``prompts/task_split/`` (package data, loaded with
``importlib.resources``): ``prompt.md``, ``rules.md`` (one ``- `` item per rule)
and ``skills/<name>.md``. Guardrails are Python and run in the application:

* pre-run: the planning request is well formed and carries no credentials;
* post-run: the reply parses as a plan or a clarification and satisfies the
  dependency-document rules (ownership, unique IDs, valid dependencies, no
  cycles, citations inside the authorized repository scope).
"""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from importlib import resources

from generic_agent.agents.task_split.repository import RepositoryAccess
from generic_agent.agents.task_split.schema import ID_PATTERN, PlanValidationError, parse_reply
from generic_agent.runtime.agent_definition import (
    AgentDefinition,
    Guardrail,
    GuardrailPhase,
    GuardrailResult,
    Skill,
)

PROMPT_DIR = "task_split"
SKILL_NAMES = (
    "hls-analysis",
    "repository-inspection",
    "task-decomposition",
    "acceptance-criteria",
    "dependency-mapping",
)
"""Skill files in ``prompts/task_split/skills/``, in the order they are rendered."""

MAX_HLS_DOCUMENTS = 20
MAX_HLS_CHARS = 200_000
MAX_NOTES_CHARS = 20_000
MAX_TITLE_CHARS = 200

_CREDENTIAL_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"://[^/\s:@]+:[^/\s@]+@"),  # user:password@host
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
)
_CLOSING_TAGS = re.compile(r"</(planning_request|hls_documents?|reviewer_notes|repository)\b", re.IGNORECASE)


@dataclass(frozen=True)
class HlsDocument:
    """One HLS document supplied to the agent."""

    id: str
    title: str
    content: str

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not ID_PATTERN.match(self.id):
            raise ValueError(f"HLS document ID must match {ID_PATTERN.pattern}")
        if not isinstance(self.title, str) or not self.title.strip() or len(self.title) > MAX_TITLE_CHARS:
            raise ValueError(f"HLS document {self.id!r} needs a title of at most {MAX_TITLE_CHARS} characters")
        if not isinstance(self.content, str) or not self.content.strip():
            raise ValueError(f"HLS document {self.id!r} content must not be blank")
        if len(self.content) > MAX_HLS_CHARS:
            raise ValueError(f"HLS document {self.id!r} exceeds {MAX_HLS_CHARS} characters")


def load_text(*parts: str) -> str:
    """Read a packaged asset from ``generic_agent/prompts/``."""
    node = resources.files("generic_agent").joinpath("prompts", *parts)
    return node.read_text(encoding="utf-8").strip()


def load_rules() -> tuple[str, ...]:
    """Parse ``rules.md``: each ``- `` item (with indented continuation lines) is one rule."""
    rules: list[str] = []
    for line in load_text(PROMPT_DIR, "rules.md").splitlines():
        if line.startswith("- "):
            rules.append(line[2:].strip())
        elif rules and line.startswith((" ", "\t")) and line.strip():
            rules[-1] += " " + line.strip()
    return tuple(rules)


def load_skills() -> tuple[Skill, ...]:
    return tuple(Skill(name, load_text(PROMPT_DIR, "skills", f"{name}.md")) for name in SKILL_NAMES)


def build_planning_request(
    documents: Sequence[HlsDocument],
    repository: RepositoryAccess,
    notes: str | None = None,
) -> str:
    """Render the single user message for a planning run.

    Only the repository ID and revision appear. No path, URL or credential of
    the checkout is part of the model input.
    """
    if not documents:
        raise ValueError("at least one HLS document is required")
    if len(documents) > MAX_HLS_DOCUMENTS:
        raise ValueError(f"at most {MAX_HLS_DOCUMENTS} HLS documents are allowed")
    ids = [d.id for d in documents]
    if len(set(ids)) != len(ids):
        raise ValueError("HLS document IDs must be unique")
    if notes is not None and len(notes) > MAX_NOTES_CHARS:
        raise ValueError(f"notes exceed {MAX_NOTES_CHARS} characters")

    parts = [
        "<planning_request>",
        f'<repository id="{repository.repository_id}" revision="{repository.revision}" />',
        "<hls_documents>",
    ]
    for doc in documents:
        title = doc.title.strip().replace('"', "'").replace("<", "(").replace(">", ")")
        parts.append(f'<hls_document id="{doc.id}" title="{title}">')
        parts.append(_neutralize(doc.content.strip()))
        parts.append("</hls_document>")
    parts.append("</hls_documents>")
    if notes and notes.strip():
        parts += ["<reviewer_notes>", _neutralize(notes.strip()), "</reviewer_notes>"]
    parts.append("</planning_request>")
    return "\n".join(parts)


def _neutralize(text: str) -> str:
    """Stop supplied text from closing the request's own tags."""
    return _CLOSING_TAGS.sub(lambda m: "<\\/" + m.group(1), text)


# --- Guardrails ----------------------------------------------------------------


def request_is_well_formed(text: str) -> GuardrailResult:
    """Pre-run: the message is a planning request with at least one HLS document."""
    if not text.lstrip().startswith("<planning_request>") or "<hls_document id=" not in text:
        return GuardrailResult(False, "input must be a planning request with at least one HLS document")
    return GuardrailResult(True)


def request_has_no_credentials(text: str) -> GuardrailResult:
    """Pre-run: repository credentials and tokens must never reach the model."""
    if any(pattern.search(text) for pattern in _CREDENTIAL_PATTERNS):
        return GuardrailResult(False, "input appears to contain a credential; remove it and retry")
    return GuardrailResult(True)


def reply_is_valid_document(
    hls_document_ids: Collection[str], repository: RepositoryAccess
) -> Guardrail:
    """Post-run: the reply is a valid dependency document or clarification request."""
    known = tuple(hls_document_ids)

    def check(text: str) -> GuardrailResult:
        try:
            parse_reply(text, hls_document_ids=known, repository=repository)
        except PlanValidationError as exc:
            return GuardrailResult(False, str(exc))
        return GuardrailResult(True)

    return Guardrail("valid-dependency-document", GuardrailPhase.POST_RUN, check)


def build_task_split_definition(
    *, hls_document_ids: Collection[str], repository: RepositoryAccess
) -> AgentDefinition:
    """Assemble the Task Split definition for one planning run.

    The post-run guardrail is bound to this run's HLS document IDs and
    repository scope, so build a fresh definition per run.
    """
    return AgentDefinition(
        prompt=load_text(PROMPT_DIR, "prompt.md"),
        skills=load_skills(),
        rules=load_rules(),
        guardrails=(
            Guardrail("planning-request-well-formed", GuardrailPhase.PRE_RUN, request_is_well_formed),
            Guardrail("no-credentials-in-input", GuardrailPhase.PRE_RUN, request_has_no_credentials),
            reply_is_valid_document(hls_document_ids, repository),
        ),
    )
