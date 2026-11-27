"""Run the Task Split agent: HLS documents + repository in, draft or questions out.

``TaskSplitPlanner.plan`` follows the INIT pattern. It validates the request in
Python, creates a ``generic_agent.runner.GenericAgent`` that has only the
read-only repository tools of one checkout, and calls ``run(planning_request,
**prompt_pack.run_input("plan"))`` once. The five planning stages are explicit
in that one call (``prompts/core.md``). Python then parses the reply and
validates it against the submitted HLS document IDs and the authorized
repository scope, so a reply that fails never leaves this module.

The run ends here. It never saves tasks; the caller stores the returned
document as a *draft* and a human approves a specific version later.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from generic_agent.config import GuardrailError, Settings
from generic_agent.runner import GenericAgent

from .prompt_pack import NODE, PromptPack
from .repository import RepositoryAccess
from .schema import ID_PATTERN, Clarification, DependencyDocument, PlanValidationError, parse_reply

MAX_HLS_DOCUMENTS = 20
MAX_HLS_CHARS = 200_000
MAX_NOTES_CHARS = 20_000
MAX_TITLE_CHARS = 200

REPLY_GUARDRAIL = "valid-dependency-document"
"""Name used in the error when the model's reply is rejected."""

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
CREDENTIAL_MESSAGE = "input appears to contain a credential; remove it and retry"


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


@dataclass(frozen=True)
class PlanningOutcome:
    """Exactly one of ``document`` (a validated draft) or ``clarification`` is set."""

    document: DependencyDocument | None = None
    clarification: Clarification | None = None
    cursor_run_id: str | None = None


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


def contains_credential(text: str) -> bool:
    """True when ``text`` looks like a repository credential, token or private key."""
    return any(pattern.search(text) for pattern in _CREDENTIAL_PATTERNS)


class TaskSplitPlanner:
    """Creates a draft dependency document (or clarification questions) from HLS input."""

    def __init__(self, settings: Settings, prompt_pack: PromptPack | None = None) -> None:
        self._settings = settings
        self._prompts = prompt_pack or PromptPack.load()

    def plan(
        self,
        documents: Sequence[HlsDocument],
        repository: RepositoryAccess,
        notes: str | None = None,
    ) -> PlanningOutcome:
        """Run one planning call.

        Raises:
            ValueError: the input is invalid (blank or duplicate documents, too large,
                or it appears to contain a credential). Cursor is not called.
            GuardrailError: the model's reply is not a valid plan or clarification
                for these HLS documents and this repository (for example a missing
                dependency or a cycle). Nothing is returned.
            CursorRunError: the Cursor run did not finish.
        """
        request = build_planning_request(documents, repository, notes)
        if contains_credential(request):
            raise ValueError(CREDENTIAL_MESSAGE)

        # A fresh agent per run: the repository tools are scoped to this checkout, and
        # Cursor's built-in tools stay disabled (GenericAgent default).
        agent = GenericAgent(self._settings, tools=repository.tools())
        result = agent.run(request, **self._prompts.run_input(NODE))

        parsed = self._validate_reply(result.text, [d.id for d in documents], repository)
        run_id = result.metadata.get("cursor_run_id")
        if isinstance(parsed, Clarification):
            return PlanningOutcome(clarification=parsed, cursor_run_id=run_id)
        return PlanningOutcome(document=parsed, cursor_run_id=run_id)

    @staticmethod
    def _validate_reply(
        text: str, hls_document_ids: Sequence[str], repository: RepositoryAccess
    ) -> DependencyDocument | Clarification:
        """Parse and validate the reply. The error never includes the reply text."""
        label = f"Guardrail {REPLY_GUARDRAIL!r} (post_run)"
        try:
            return parse_reply(text, hls_document_ids=hls_document_ids, repository=repository)
        except PlanValidationError as exc:
            raise GuardrailError(f"{label} failed: {exc}") from None
        except Exception as exc:
            raise GuardrailError(f"{label} raised {type(exc).__name__}.") from None
