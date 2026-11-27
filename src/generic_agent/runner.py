"""`GenericAgent`: one object other agents create and call with ``run()``.

A caller (for example the INIT agent) creates one instance and sends, on each call,
the task prompt plus the material the model must follow:

    agent = GenericAgent()                       # Cursor settings from .env
    result = agent.run(
        "Analyze component src/api ...",
        instructions="You are the INIT agent ...",
        rules=[Section("R1", "Evidence is required", "...")],
        guidelines=[Section("G1", "Smallest useful set", "...")],
        skills=[Skill("analyze-component", "...")],
    )
    result.text

`run()` renders the material into one system prompt, runs the Generic Agent graph
(Cursor SDK) once and returns the reply. Each call is self-contained: there is no
checkpointer, so only the `history` given to the call reaches the model.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from cursor_sdk import CursorSDKError, CustomTool
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langgraph.graph.state import CompiledStateGraph

from generic_agent.config import Settings, load_settings
from generic_agent.graph import DEFAULT_MAX_HISTORY_MESSAGES, build_agent

DEFAULT_INSTRUCTIONS = "You are a careful assistant."
BRIDGE_START_ATTEMPTS = 3
BRIDGE_RETRY_DELAY_S = 2.0
_BRIDGE_START_TIMEOUT = "Timed out waiting for bridge discovery"


def _is_bridge_start_timeout(exc: BaseException) -> bool:
    return _BRIDGE_START_TIMEOUT in str(exc)


@dataclass(frozen=True)
class Section:
    """One rule or guideline. ``id`` and ``title`` are optional labels."""

    id: str = ""
    title: str = ""
    body: str = ""

    def render(self) -> str:
        label = ": ".join(p for p in (self.id, self.title) if p)
        return f"## {label}\n{self.body}".strip() if label else self.body.strip()


@dataclass(frozen=True)
class Skill:
    """A skill the model applies for this call (the full SKILL.md body)."""

    name: str
    body: str
    description: str = ""

    def render(self) -> str:
        body = self.body.strip()
        if body.startswith("#"):                 # the SKILL.md body has its own heading
            return body
        head = f"## Skill: {self.name}" + (f" - {self.description}" if self.description else "")
        return f"{head}\n{body}"


@dataclass(frozen=True)
class SharedSkillRef:
    """A skill in a shared catalog: the model refers to it by id and does not copy it."""

    id: str
    description: str = ""
    ref: str = ""


@dataclass
class RunResult:
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


def _as_section(item: Section | str) -> Section:
    return item if isinstance(item, Section) else Section(body=str(item))


def compose_instructions(
    instructions: str = "",
    *,
    rules: Sequence[Section | str] = (),
    guidelines: Sequence[Section | str] = (),
    skills: Sequence[Skill] = (),
    shared_skills: Sequence[SharedSkillRef] = (),
) -> str:
    """Render all run material into the system prompt of one Cursor run."""
    parts = [instructions.strip() or DEFAULT_INSTRUCTIONS]
    if rules:
        parts.append("\n\n".join(["# Rules (binding)", *(_as_section(r).render() for r in rules)]))
    if guidelines:
        parts.append("\n\n".join(["# Guidelines (recommended)", *(_as_section(g).render() for g in guidelines)]))
    if skills:
        parts.append("\n\n".join(["# Skills", *(s.render() for s in skills)]))
    if shared_skills:
        lines = ["# Shared skill catalog (reuse by id; do not copy)"]
        lines += [f"- `{s.id}`: {s.description} ({s.ref})" for s in shared_skills]
        parts.append("\n".join(lines))
    return "\n\n---\n\n".join(parts)


class GenericAgent:
    """Reusable Generic Agent. Create it once; call ``run()`` / ``arun()`` per task.

    Args:
        settings: Cursor settings. ``None`` loads ``CURSOR_API_KEY`` / ``CURSOR_MODEL``
            from the environment or ``.env`` on the first run.
        tools: Local custom tools. Default ``{}``: a text-only agent.
        builtin_tools: Cursor built-in tools. Default ``()``: none (no file or shell access).
        max_history_messages: Earlier messages from ``history`` passed per run.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        tools: Mapping[str, CustomTool] | None = None,
        builtin_tools: Sequence[str] | None = (),
        max_history_messages: int = DEFAULT_MAX_HISTORY_MESSAGES,
    ) -> None:
        self._settings = settings
        self._tools = dict(tools or {})
        self._builtin_tools = builtin_tools
        self._max_history = max_history_messages
        self._graphs: dict[str, CompiledStateGraph] = {}
        self._lock = threading.Lock()

    @property
    def settings(self) -> Settings:
        with self._lock:
            if self._settings is None:
                self._settings = load_settings()
            return self._settings

    def _graph(self, system_prompt: str) -> CompiledStateGraph:
        settings = self.settings
        with self._lock:
            graph = self._graphs.get(system_prompt)
            if graph is None:
                graph = build_agent(
                    settings, system_prompt, self._tools, checkpointer=None,
                    max_history_messages=self._max_history, builtin_tools=self._builtin_tools,
                )
                self._graphs[system_prompt] = graph
            return graph

    def run(
        self,
        prompt: str,
        *,
        instructions: str = "",
        rules: Sequence[Section | str] = (),
        guidelines: Sequence[Section | str] = (),
        skills: Sequence[Skill] = (),
        shared_skills: Sequence[SharedSkillRef] = (),
        history: Sequence[AnyMessage] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> RunResult:
        """Run the model once on ``prompt`` with the given material. Blocks until done.

        ``history`` is earlier conversation (for example a previous answer and a
        correction request); ``prompt`` is the newest user message.
        """
        if not prompt.strip():
            raise ValueError("prompt must not be blank")
        system_prompt = compose_instructions(
            instructions, rules=rules, guidelines=guidelines, skills=skills, shared_skills=shared_skills,
        )
        messages = [m for m in history if isinstance(m, (HumanMessage, AIMessage))]
        graph = self._graph(system_prompt)
        state = {"messages": [*messages, HumanMessage(content=prompt)]}
        for attempt in range(1, BRIDGE_START_ATTEMPTS + 1):
            try:
                result = graph.invoke(state)
                break
            except CursorSDKError as exc:
                # The local Cursor bridge can start slowly the first time (a cold node.exe,
                # an antivirus scan) and miss the SDK's 30 s discovery window. Nothing was
                # sent to the model yet, and the SDK launches a new bridge on the next call.
                if attempt == BRIDGE_START_ATTEMPTS or not _is_bridge_start_timeout(exc):
                    raise
                time.sleep(BRIDGE_RETRY_DELAY_S)
        reply = result["messages"][-1]
        text = reply.text.strip()
        if not text:
            raise RuntimeError("Generic Agent returned an empty reply")
        return RunResult(text=text, metadata={**(reply.response_metadata or {}), **(metadata or {})})

    async def arun(self, prompt: str, **kwargs: Any) -> RunResult:
        """Async ``run()``: the Cursor call blocks, so it runs in a worker thread."""
        return await asyncio.to_thread(self.run, prompt, **kwargs)


__all__ = ["GenericAgent", "RunResult", "Section", "SharedSkillRef", "Skill", "compose_instructions"]
