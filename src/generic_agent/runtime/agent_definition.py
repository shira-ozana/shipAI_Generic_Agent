"""Agent definitions: the prompt, skills, rules and guardrails for one run.

A definition is plain, immutable data. It never enters graph state or
checkpoints; ``GenericAgent`` passes it to the graph through invocation-scoped
runtime context, so one runner can serve many definitions.

This module must not import ``generic_agent.graph`` (the graph imports it).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

from cursor_sdk import CustomTool

from generic_agent.config import GuardrailError


class GuardrailPhase(StrEnum):
    """When a guardrail runs relative to the Cursor SDK call."""

    PRE_RUN = "pre_run"
    """Before ``Agent.prompt``; the check receives the latest user message text."""
    POST_RUN = "post_run"
    """After a successful SDK result; the check receives the reply text."""


@dataclass(frozen=True)
class GuardrailResult:
    """Outcome of a guardrail check. ``message`` explains a failure."""

    passed: bool
    message: str = ""


GuardrailCheck = Callable[[str], "GuardrailResult | bool"]
"""``check(text)`` returns ``True``/``GuardrailResult(True)`` to pass."""


@dataclass(frozen=True)
class Guardrail:
    """An executable check run before or after the Cursor run."""

    name: str
    phase: GuardrailPhase
    check: GuardrailCheck

    def __post_init__(self) -> None:
        _require_text(self.name, "guardrail name")
        try:
            object.__setattr__(self, "phase", GuardrailPhase(self.phase))
        except ValueError:
            allowed = ", ".join(p.value for p in GuardrailPhase)
            raise ValueError(f"guardrail {self.name!r} phase must be one of: {allowed}") from None
        if not callable(self.check):
            raise TypeError(f"guardrail {self.name!r} check must be callable")


@dataclass(frozen=True)
class Skill:
    """A named instructional resource supplied as text."""

    name: str
    content: str

    def __post_init__(self) -> None:
        _require_text(self.name, "skill name")
        _require_text(self.content, f"skill {self.name!r} content")


@dataclass(frozen=True)
class AgentDefinition:
    """Everything that distinguishes one agent from another.

    Attributes:
        prompt: Main instruction; must be a non-blank string.
        skills: Named instructional resources. A ``{name: text}`` mapping is
            also accepted and normalised to ``Skill`` objects.
        rules: Instructions included on every run of this definition.
        guardrails: Pre-run and post-run checks.
        tools: Optional custom tools for runs of this definition. ``None`` (the
            default) uses the tools the graph was built with; a mapping (``{}``
            for text-only) replaces them for this run only. Lets an agent bind
            tools to per-run resources such as a working directory.

    Collections are normalised to tuples (and may be empty), so a definition
    cannot change after it has been validated.
    """

    prompt: str
    skills: tuple[Skill, ...] = field(default=())
    rules: tuple[str, ...] = field(default=())
    guardrails: tuple[Guardrail, ...] = field(default=())
    tools: Mapping[str, CustomTool] | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        _require_text(self.prompt, "prompt")
        if self.tools is not None:
            if not isinstance(self.tools, Mapping):
                raise TypeError("tools must be a mapping of name to CustomTool, or None")
            for name, tool in self.tools.items():
                _require_text(name, "tool name")
                if not isinstance(tool, CustomTool):
                    raise TypeError(f"tool {name!r} must be a CustomTool")
            object.__setattr__(self, "tools", MappingProxyType(dict(self.tools)))

        skills = self.skills
        if isinstance(skills, Mapping):
            skills = tuple(Skill(name, content) for name, content in skills.items())
        skills = _as_tuple(skills, Skill, "skills")
        names = [s.name.strip() for s in skills]
        if len(set(names)) != len(names):
            raise ValueError("skill names must be unique")

        rules = _as_tuple(self.rules, str, "rules")
        for rule in rules:
            _require_text(rule, "rule")

        object.__setattr__(self, "skills", skills)
        object.__setattr__(self, "rules", rules)
        object.__setattr__(self, "guardrails", _as_tuple(self.guardrails, Guardrail, "guardrails"))

    def guardrails_for(self, phase: GuardrailPhase) -> tuple[Guardrail, ...]:
        """Guardrails of ``phase`` in declaration order."""
        return tuple(g for g in self.guardrails if g.phase is phase)


@dataclass(frozen=True)
class AgentContext:
    """Invocation-scoped LangGraph runtime context (not state, not checkpointed)."""

    definition: AgentDefinition


def assemble_instructions(definition: AgentDefinition) -> str:
    """Combine prompt, rules and skills into the text given to ``render_prompt``.

    Order is fixed: the prompt, then ``<rules>`` (numbered, declaration order),
    then ``<skills>`` (declaration order). Empty sections are omitted.
    """
    sections = [definition.prompt.strip()]
    if definition.rules:
        numbered = "\n".join(f"{i}. {rule.strip()}" for i, rule in enumerate(definition.rules, 1))
        sections.append(f"<rules>\n{numbered}\n</rules>")
    if definition.skills:
        skills = "\n\n".join(
            f'<skill name="{skill.name.strip()}">\n{skill.content.strip()}\n</skill>'
            for skill in definition.skills
        )
        sections.append(f"<skills>\n{skills}\n</skills>")
    return "\n\n".join(sections)


def run_guardrails(definition: AgentDefinition, phase: GuardrailPhase, text: str) -> None:
    """Run the definition's guardrails for ``phase`` in order.

    Raises:
        GuardrailError: the first check that fails or raises. The error names
            the guardrail and phase but never includes ``text``.
    """
    for guardrail in definition.guardrails_for(phase):
        label = f"Guardrail {guardrail.name!r} ({phase.value})"
        try:
            outcome = guardrail.check(text)
        except Exception as exc:
            raise GuardrailError(f"{label} raised {type(exc).__name__}.") from exc
        if isinstance(outcome, GuardrailResult):
            passed, message = outcome.passed, outcome.message
        else:
            passed, message = bool(outcome), ""
        if not passed:
            detail = f": {message}" if message else "."
            raise GuardrailError(f"{label} failed{detail}")


def _require_text(value: object, what: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{what} must be a non-blank string")


def _as_tuple(values: Iterable[object], kind: type, what: str) -> tuple:
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        raise TypeError(f"{what} must be a collection of {kind.__name__}")
    items = tuple(values)
    for item in items:
        if not isinstance(item, kind):
            raise TypeError(f"{what} must contain only {kind.__name__} objects")
    return items
