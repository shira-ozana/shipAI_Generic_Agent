"""Deterministic offline stand-ins for the Cursor SDK."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from cursor_sdk import AgentOptions

SECRET = "cursor-test-secret-value"


@dataclass
class FakeRunResult:
    """Subset of ``cursor_sdk.RunResult`` used by the graph."""

    result: str = ""
    status: str = "finished"
    id: str = "run-1"
    agent_id: str = "agent-1"
    error_message: str | None = None


Responder = Callable[[str, AgentOptions], "str | FakeRunResult"]


class FakeCursor:
    """Replaces ``generic_agent.graph.Agent``; records every ``Agent.prompt`` call."""

    def __init__(self, responder: Responder) -> None:
        self.responder = responder
        self.calls: list[tuple[str, AgentOptions]] = []

    def prompt(self, message: str, options: AgentOptions, **_: Any) -> FakeRunResult:
        self.calls.append((message, options))
        reply = self.responder(message, options)
        return reply if isinstance(reply, FakeRunResult) else FakeRunResult(result=reply)

    @property
    def prompts(self) -> list[str]:
        return [message for message, _ in self.calls]
