from __future__ import annotations

import asyncio

import pytest
from fakes import FakeRunResult
from langchain_core.messages import AIMessage, HumanMessage

from generic_agent import GenericAgent, Section, SharedSkillRef, Skill, compose_instructions
from generic_agent.config import CursorRunError


def test_run_sends_prompt_and_all_material_in_one_cursor_run(mock_cursor, settings) -> None:
    cursor = mock_cursor(lambda p, o: "done")
    agent = GenericAgent(settings)

    result = agent.run(
        "Analyze src/api.",
        instructions="You are the INIT agent.",
        rules=[Section("R1", "Evidence is required", "Cite repo paths."), "Plain rule."],
        guidelines=[Section("G1", "Smallest set", "Write few files.")],
        skills=[Skill("analyze-component", "Step 1: read the inventory.", "Analyze a component")],
        shared_skills=[SharedSkillRef("lint", "Run lint", "catalog://lint")],
        metadata={"init_node": "analyze_repository"},
    )

    assert result.text == "done"
    assert result.metadata["init_node"] == "analyze_repository"
    assert result.metadata["cursor_run_id"] == "run-1"
    (prompt,) = cursor.prompts
    for expected in ("You are the INIT agent.", "# Rules (binding)", "## R1: Evidence is required\nCite repo paths.",
                     "Plain rule.", "## G1: Smallest set", "## Skill: analyze-component - Analyze a component",
                     "Step 1: read the inventory.", "`lint`: Run lint (catalog://lint)",
                     "<current_user_message>\nAnalyze src/api.\n</current_user_message>"):
        assert expected in prompt
    assert cursor.calls[0][1].tools == []          # built-in tools stay disabled


def test_runs_are_self_contained_and_history_is_explicit(mock_cursor, settings) -> None:
    cursor = mock_cursor(lambda p, o: "ok")
    agent = GenericAgent(settings)
    agent.run("first")
    agent.run("second", history=[HumanMessage(content="q0"), AIMessage(content="a0")])

    assert "first" not in cursor.prompts[1]
    assert "User: q0" in cursor.prompts[1] and "Assistant: a0" in cursor.prompts[1]


def test_graph_is_reused_for_the_same_material(mock_cursor, settings) -> None:
    mock_cursor(lambda p, o: "ok")
    agent = GenericAgent(settings)
    agent.run("a", instructions="X")
    agent.run("b", instructions="X")
    agent.run("c", instructions="Y")
    assert len(agent._graphs) == 2


def test_skill_with_own_heading_is_not_wrapped() -> None:
    text = compose_instructions("I", skills=[Skill("s", "# Skill: analyze\nbody")])
    assert "# Skill: analyze\nbody" in text and "## Skill: s" not in text


def test_slow_bridge_start_is_retried(mock_cursor, settings, monkeypatch) -> None:
    from cursor_sdk import CursorSDKError

    import generic_agent.runner as runner

    monkeypatch.setattr(runner, "BRIDGE_RETRY_DELAY_S", 0)
    calls = {"n": 0}

    def responder(p, o):
        calls["n"] += 1
        if calls["n"] == 1:
            raise CursorSDKError("Timed out waiting for bridge discovery")
        return "ok after retry"

    mock_cursor(responder)
    assert GenericAgent(settings).run("hi").text == "ok after retry" and calls["n"] == 2

    mock_cursor(lambda p, o: (_ for _ in ()).throw(CursorSDKError("Timed out waiting for bridge discovery")))
    with pytest.raises(CursorSDKError):                       # gives up after BRIDGE_START_ATTEMPTS
        GenericAgent(settings).run("hi")

    other = {"n": 0}

    def boom(p, o):
        other["n"] += 1
        raise CursorSDKError("bad request")

    mock_cursor(boom)
    with pytest.raises(CursorSDKError, match="bad request"):  # other SDK errors are not retried
        GenericAgent(settings).run("hi")
    assert other["n"] == 1


def test_arun_and_errors(mock_cursor, settings) -> None:
    mock_cursor(lambda p, o: "async ok")
    assert asyncio.run(GenericAgent(settings).arun("hi")).text == "async ok"

    mock_cursor(lambda p, o: FakeRunResult(status="error", id="run-9", error_message="overloaded"))
    with pytest.raises(CursorRunError, match="overloaded"):
        GenericAgent(settings).run("hi")
    with pytest.raises(ValueError):
        GenericAgent(settings).run("   ")
