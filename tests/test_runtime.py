from __future__ import annotations

import threading

import pytest
from fakes import SECRET, FakeRunResult
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from generic_agent import GenericAgent, build_agent, default_tools
from generic_agent.config import CursorRunError, GuardrailError, describe_error
from generic_agent.runtime import (
    AgentDefinition,
    Guardrail,
    GuardrailPhase,
    GuardrailResult,
    Skill,
    assemble_instructions,
)


def human(text: str) -> list[HumanMessage]:
    return [HumanMessage(content=text)]


def thread(name: str) -> dict:
    return {"configurable": {"thread_id": name}}


# --- AgentDefinition ---------------------------------------------------------


@pytest.mark.parametrize("prompt", ["", "   \n", None, 5])
def test_prompt_must_be_a_non_blank_string(prompt) -> None:
    with pytest.raises(ValueError, match="prompt"):
        AgentDefinition(prompt=prompt)


def test_empty_collections_are_accepted_and_normalised_to_tuples() -> None:
    definition = AgentDefinition(prompt="p", skills=[], rules=[], guardrails=[])
    assert definition.skills == () and definition.rules == () and definition.guardrails == ()
    assert assemble_instructions(definition) == "p"


def test_skills_accept_a_name_to_text_mapping_and_reject_bad_input() -> None:
    definition = AgentDefinition(prompt="p", skills={"a": "text a"})
    assert definition.skills == (Skill("a", "text a"),)
    with pytest.raises(ValueError, match="unique"):
        AgentDefinition(prompt="p", skills=[Skill("a", "x"), Skill("a", "y")])
    with pytest.raises(ValueError, match="skill name"):
        Skill("", "x")
    with pytest.raises(ValueError, match="rule"):
        AgentDefinition(prompt="p", rules=["  "])
    with pytest.raises(TypeError):
        AgentDefinition(prompt="p", rules="a single string")
    with pytest.raises(ValueError, match="phase"):
        Guardrail("g", "sometime", lambda text: True)


def test_assembly_order_is_prompt_then_rules_then_skills() -> None:
    definition = AgentDefinition(
        prompt="MAIN",
        rules=["rule one", "rule two"],
        skills=[Skill("alpha", "alpha text"), Skill("beta", "beta text")],
    )
    text = assemble_instructions(definition)
    order = ["MAIN", "1. rule one", "2. rule two", 'skill name="alpha"', "alpha text", "beta text"]
    positions = [text.index(part) for part in order]
    assert positions == sorted(positions)


# --- GenericAgent ------------------------------------------------------------


def test_prompt_rules_and_skills_reach_the_sdk(mock_cursor, settings) -> None:
    cursor = mock_cursor(lambda p, o: FakeRunResult(result="hello", id="run-5"))
    definition = AgentDefinition(
        prompt="You are the MAIN agent.",
        rules=["Always be brief."],
        skills=[Skill("haiku", "Write replies as haiku.")],
    )
    runner = GenericAgent(settings=settings, tools=default_tools())

    result = runner.run(definition, messages=human("hi there"))

    (prompt, options), = cursor.calls
    assert prompt.startswith("<instructions>\nYou are the MAIN agent.")
    assert "Always be brief." in prompt and "Write replies as haiku." in prompt
    assert prompt.index("MAIN") < prompt.index("Always be brief.") < prompt.index("haiku")
    assert "<current_user_message>\nhi there\n</current_user_message>" in prompt
    # Existing SDK configuration is preserved.
    assert options.model == "test-model" and options.api_key == SECRET
    assert options.tools == [] and options.local.setting_sources is None
    assert set(options.local.custom_tools) == {"word_count"}
    # graph.invoke() shape.
    human_message, reply = result["messages"]
    assert isinstance(human_message, HumanMessage)
    assert isinstance(reply, AIMessage) and reply.content == "hello"
    assert reply.response_metadata["cursor_run_id"] == "run-5"


def test_definitions_are_isolated_across_runs_on_one_runner(mock_cursor, settings) -> None:
    cursor = mock_cursor(lambda p, o: "ok")
    runner = GenericAgent(settings=settings, tools={}, checkpointer=InMemorySaver())
    first = AgentDefinition(prompt="PROMPT-ONE", rules=["RULE-ONE"], skills={"s1": "SKILL-ONE"})
    second = AgentDefinition(prompt="PROMPT-TWO")

    runner.run(first, human("a"), thread("t"))
    result = runner.run(second, human("b"), thread("t"))  # same thread, other definition
    runner.run(first, human("c"), thread("other"))

    one, two, three = cursor.prompts
    assert "PROMPT-ONE" in one and "RULE-ONE" in one and "SKILL-ONE" in one
    assert "PROMPT-TWO" in two
    assert not any(leak in two for leak in ("PROMPT-ONE", "RULE-ONE", "SKILL-ONE"))
    assert "PROMPT-ONE" in three and "PROMPT-TWO" not in three
    assert two.count("<instructions>") == 1
    assert "User: a" in two and "Assistant: ok" in two  # history still comes from the checkpoint
    assert [type(m) for m in result["messages"]] == [HumanMessage, AIMessage] * 2


def test_definitions_do_not_enter_state_or_checkpoints(mock_cursor, settings) -> None:
    mock_cursor(lambda p, o: "ok")
    saver = InMemorySaver()
    runner = GenericAgent(settings=settings, tools={}, checkpointer=saver)
    runner.run(AgentDefinition(prompt="DEFINITION-MARKER", rules=["RULE-MARKER"]), human("hi"), thread("t"))

    assert "DEFINITION-MARKER" not in repr(saver.storage)
    assert "RULE-MARKER" not in repr(saver.storage)
    assert SECRET not in repr(saver.storage)


def test_concurrent_runs_keep_their_own_definitions(mock_cursor, settings) -> None:
    both_started = threading.Barrier(2, timeout=5)

    def responder(prompt: str, options) -> str:
        both_started.wait()  # each run is mid-flight while the other starts
        return "A" if "PROMPT-A" in prompt else "B"

    cursor = mock_cursor(responder)
    runner = GenericAgent(settings=settings, tools={})
    replies: dict[str, str] = {}

    def go(name: str) -> None:
        out = runner.run(AgentDefinition(prompt=f"PROMPT-{name}"), human("hi"))
        replies[name] = out["messages"][-1].content

    workers = [threading.Thread(target=go, args=(n,)) for n in "AB"]
    [w.start() for w in workers]
    [w.join(timeout=10) for w in workers]

    assert replies == {"A": "A", "B": "B"}
    assert len(cursor.calls) == 2


def test_run_requires_an_agent_definition(mock_cursor, settings) -> None:
    mock_cursor(lambda p, o: "ok")
    with pytest.raises(TypeError, match="AgentDefinition"):
        GenericAgent(settings=settings, tools={}).run("just a string", human("hi"))


def test_build_agent_without_context_still_uses_its_system_prompt(mock_cursor, settings) -> None:
    cursor = mock_cursor(lambda p, o: "ok")
    build_agent(settings, "LEGACY PROMPT", {}).invoke({"messages": human("hi")})
    assert "LEGACY PROMPT" in cursor.prompts[0]


# --- Guardrails --------------------------------------------------------------


def test_passing_guardrails_run_in_order_around_the_sdk_call(mock_cursor, settings) -> None:
    events: list[str] = []

    def responder(prompt: str, options) -> str:
        events.append("sdk")
        return "good reply"

    mock_cursor(responder)

    def recorder(name: str):
        def check(text: str) -> bool:
            events.append(f"{name}:{text}")
            return True

        return check

    definition = AgentDefinition(
        prompt="p",
        guardrails=[
            Guardrail("post1", GuardrailPhase.POST_RUN, recorder("post1")),
            Guardrail("pre1", GuardrailPhase.PRE_RUN, recorder("pre1")),
            Guardrail("pre2", "pre_run", recorder("pre2")),
        ],
    )
    GenericAgent(settings=settings, tools={}).run(definition, human("question"))

    assert events == ["pre1:question", "pre2:question", "sdk", "post1:good reply"]


def test_failed_pre_run_guardrail_blocks_the_sdk_call(mock_cursor, settings) -> None:
    cursor = mock_cursor(lambda p, o: "never used")
    saver = InMemorySaver()
    definition = AgentDefinition(
        prompt="p",
        guardrails=[
            Guardrail(
                "no-secrets",
                GuardrailPhase.PRE_RUN,
                lambda text: GuardrailResult("password" not in text, "message looks sensitive"),
            )
        ],
    )
    runner = GenericAgent(settings=settings, tools={}, checkpointer=saver)

    with pytest.raises(GuardrailError, match=r"no-secrets.*pre_run.*failed: message looks sensitive") as info:
        runner.run(definition, human("my password is hunter2"), thread("t"))

    assert cursor.calls == []
    assert isinstance(info.value, CursorRunError)  # existing run error handling applies
    assert "hunter2" not in str(info.value)
    assert describe_error(info.value) == str(info.value)


def test_failed_post_run_guardrail_does_not_write_an_assistant_message(mock_cursor, settings) -> None:
    cursor = mock_cursor(lambda p, o: "this reply has BANNED-WORD")
    definition = AgentDefinition(
        prompt="p",
        guardrails=[Guardrail("no-forbidden", GuardrailPhase.POST_RUN, lambda text: "BANNED-WORD" not in text)],
    )
    runner = GenericAgent(settings=settings, tools={}, checkpointer=InMemorySaver())

    with pytest.raises(GuardrailError, match=r"no-forbidden.*post_run.*failed") as info:
        runner.run(definition, human("hi"), thread("t"))

    assert len(cursor.calls) == 1  # the SDK did run
    assert "BANNED-WORD" not in str(info.value)  # the rejected reply is not echoed
    saved = runner._graph.get_state(thread("t")).values["messages"]
    assert not any(isinstance(m, AIMessage) for m in saved)

    # A later reply that passes is the only assistant message written.
    cursor.responder = lambda p, o: "all good"
    result = runner.run(definition, human("again"), thread("t"))
    assert [m.content for m in result["messages"] if isinstance(m, AIMessage)] == ["all good"]


def test_post_run_guardrail_is_skipped_when_the_run_fails(mock_cursor, settings) -> None:
    mock_cursor(lambda p, o: FakeRunResult(status="error", id="run-3", error_message="boom"))
    called: list[str] = []
    definition = AgentDefinition(
        prompt="p",
        guardrails=[Guardrail("g", GuardrailPhase.POST_RUN, lambda text: called.append(text) or True)],
    )
    with pytest.raises(CursorRunError, match="run-3") as info:
        GenericAgent(settings=settings, tools={}).run(definition, human("hi"))
    assert not isinstance(info.value, GuardrailError) and called == []


def test_guardrail_that_raises_is_reported_as_a_failed_check(mock_cursor, settings) -> None:
    mock_cursor(lambda p, o: "ok")

    def broken(text: str) -> bool:
        raise RuntimeError("internal detail with " + SECRET)

    definition = AgentDefinition(prompt="p", guardrails=[Guardrail("broken", GuardrailPhase.PRE_RUN, broken)])
    with pytest.raises(GuardrailError, match="broken.*raised RuntimeError") as info:
        GenericAgent(settings=settings, tools={}).run(definition, human("hi"))
    assert SECRET not in str(info.value)
