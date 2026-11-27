"""The architect runs through the generic agent unchanged: GenericAgent + AgentDefinition (Cursor mocked)."""

from dataclasses import dataclass
from pathlib import Path

import generic_agent.graph as generic_graph
from generic_agent.config import Settings
from pydantic import SecretStr

from agent_architect.llm import ARCHITECT_RULES, architect_definition, load_architect_prompt, make_generic_llm


@dataclass
class _Result:
    result: str
    status: str = "finished"
    id: str = "run-1"
    agent_id: str = "agent-1"
    error_message: str | None = None


def test_runs_through_generic_agent(monkeypatch, tmp_path):
    calls = []

    class FakeAgent:
        @staticmethod
        def prompt(message, options, **_):
            calls.append((message, options))
            return _Result(result='{"ok": true}')

    monkeypatch.setattr(generic_graph, "Agent", FakeAgent)
    run = make_generic_llm(Settings(api_key=SecretStr("k"), model="m"))
    assert run("design please", Path(tmp_path)) == '{"ok": true}'

    message, options = calls[0]
    assert "Architect Agent" in message and "design please" in message
    assert "<rules>" in message and ARCHITECT_RULES[0] in message
    assert '<skill name="requirements-analysis">' in message and '<skill name="hld-writing">' in message
    assert set(options.local.custom_tools) == {"list_repo_files", "read_repo_file"}
    assert options.tools == [], "Cursor built-in shell/file tools stay disabled"
    assert load_architect_prompt().startswith("You are the Architect Agent")


def test_definition_is_valid():
    definition = architect_definition()
    assert definition.rules == ARCHITECT_RULES and not definition.guardrails
    assert [s.name for s in definition.skills] == [
        "requirements-analysis",
        "codebase-discovery",
        "component-decomposition",
        "interfaces-and-data",
        "quality-risks-decisions",
        "diagrams",
        "hld-writing",
    ]
