from __future__ import annotations

import os

import pytest
from cursor_sdk import AgentOptions, CustomToolContext
from fakes import SECRET, FakeRunResult
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.memory import InMemorySaver

from generic_agent import build_agent, default_tools, load_default_prompt
from generic_agent.config import CursorRunError
from generic_agent.graph import render_prompt
from generic_agent.tools import count_words

INSTRUCTIONS = "You are a test agent."


def thread(name: str) -> dict:
    return {"configurable": {"thread_id": name}}


def ask(agent, text: str, name: str | None = None) -> dict:
    return agent.invoke({"messages": [HumanMessage(content=text)]}, thread(name) if name else None)


def test_graph_shape_is_start_run_cursor_agent_end(settings) -> None:
    graph = build_agent(settings, INSTRUCTIONS, default_tools()).get_graph()
    assert set(graph.nodes) == {"__start__", "run_cursor_agent", "__end__"}
    assert {(e.source, e.target) for e in graph.edges} == {
        ("__start__", "run_cursor_agent"),
        ("run_cursor_agent", "__end__"),
    }


def test_direct_reply_is_saved_as_ai_message_and_options_are_local(mock_cursor, settings) -> None:
    seen_cwd: list[str] = []

    def responder(prompt: str, options: AgentOptions) -> FakeRunResult:
        seen_cwd.append(options.local.cwd)
        assert os.path.isdir(options.local.cwd)  # scratch dir exists during the run
        return FakeRunResult(result="hello", id="run-42", agent_id="agent-7")

    cursor = mock_cursor(responder)
    agent = build_agent(settings, INSTRUCTIONS, default_tools())

    result = ask(agent, "hi")

    human, reply = result["messages"]
    assert isinstance(human, HumanMessage)
    assert isinstance(reply, AIMessage) and reply.content == "hello"
    assert reply.response_metadata["cursor_run_id"] == "run-42"
    assert not any(isinstance(m, SystemMessage) for m in result["messages"])

    (prompt, options), = cursor.calls
    assert INSTRUCTIONS in prompt and "hi" in prompt
    assert options.model == "test-model"
    assert options.api_key == SECRET
    assert options.tools == []  # no Cursor built-in shell/file tools
    assert options.local.setting_sources is None  # no ambient settings
    assert set(options.local.custom_tools) == {"word_count"}
    assert not os.path.exists(seen_cwd[0])  # scratch dir cleaned up


def test_word_count_custom_tool_round_trip(mock_cursor, settings) -> None:
    def responder(prompt: str, options: AgentOptions) -> str:
        tool = options.local.custom_tools["word_count"]
        output = tool.execute({"text": "one two three"}, CustomToolContext(tool_call_id="call_1"))
        return f"The text has {output} words."

    mock_cursor(responder)
    agent = build_agent(settings, INSTRUCTIONS, default_tools())

    assert ask(agent, "count words")["messages"][-1].content == "The text has 3 words."


def test_text_only_configuration_registers_no_tools(mock_cursor, settings) -> None:
    cursor = mock_cursor(lambda p, o: "ok")
    agent = build_agent(settings, INSTRUCTIONS, {})

    assert ask(agent, "hi")["messages"][-1].content == "ok"
    assert cursor.calls[0][1].local.custom_tools is None


def test_history_comes_from_the_checkpoint_within_a_thread(mock_cursor, settings) -> None:
    cursor = mock_cursor(lambda p, o: f"reply {len(cursor.calls)}")
    agent = build_agent(settings, INSTRUCTIONS, {}, checkpointer=InMemorySaver())

    ask(agent, "my name is Dana", "t")
    result = ask(agent, "what is my name?", "t")

    second = cursor.prompts[1]
    assert "User: my name is Dana" in second
    assert "Assistant: reply 1" in second
    assert "<current_user_message>\nwhat is my name?\n</current_user_message>" in second
    assert second.count("<instructions>") == 1  # prompt is not accumulated
    assert len(result["messages"]) == 4
    assert not any(isinstance(m, SystemMessage) for m in result["messages"])


def test_threads_are_isolated(mock_cursor, settings) -> None:
    cursor = mock_cursor(lambda p, o: "ok")
    agent = build_agent(settings, INSTRUCTIONS, {}, checkpointer=InMemorySaver())

    ask(agent, "secret of thread a", "a")
    other = ask(agent, "hello from b", "b")

    assert "secret of thread a" not in cursor.prompts[1]
    assert "<conversation_history>" not in cursor.prompts[1]
    assert len(other["messages"]) == 2


def test_only_recent_history_is_passed(mock_cursor, settings) -> None:
    cursor = mock_cursor(lambda p, o: "ok")
    agent = build_agent(settings, INSTRUCTIONS, {}, checkpointer=InMemorySaver(), max_history_messages=2)

    for text in ("first", "second", "third"):
        ask(agent, text, "t")

    last = cursor.prompts[-1]
    assert "User: first" not in last
    assert "User: third" not in last  # current message is not duplicated as history
    assert "User: second" in last and "Assistant: ok" in last


def test_custom_instructions_without_editing_the_graph(mock_cursor, settings) -> None:
    cursor = mock_cursor(lambda p, o: "ok")
    ask(build_agent(settings, "Prompt one.", default_tools()), "x")
    ask(build_agent(settings, "Prompt two.", {}), "x")

    assert "Prompt one." in cursor.prompts[0] and "Prompt two." not in cursor.prompts[0]
    assert "Prompt two." in cursor.prompts[1]


def test_builtin_tools_can_be_opted_back_in(mock_cursor, settings) -> None:
    cursor = mock_cursor(lambda p, o: "ok")
    ask(build_agent(settings, INSTRUCTIONS, {}, builtin_tools=None), "x")
    assert cursor.calls[0][1].tools is None


def test_failed_run_raises_readable_error(mock_cursor, settings) -> None:
    mock_cursor(lambda p, o: FakeRunResult(status="error", id="run-9", error_message="model overloaded"))
    agent = build_agent(settings, INSTRUCTIONS, {})

    with pytest.raises(CursorRunError, match="error.*run-9.*model overloaded"):
        ask(agent, "hi")


def test_api_key_never_enters_graph_state_or_checkpoints(mock_cursor, settings) -> None:
    mock_cursor(lambda p, o: "ok")
    saver = InMemorySaver()
    agent = build_agent(settings, INSTRUCTIONS, default_tools(), checkpointer=saver)
    ask(agent, "hi", "t")

    state = agent.get_state(thread("t"))
    assert SECRET not in repr(state)
    assert SECRET not in repr(saver.storage)
    assert SECRET not in repr(settings)


def test_render_prompt_handles_content_blocks_and_requires_a_human_message() -> None:
    blocks = [{"type": "text", "text": "part one "}, {"type": "text", "text": "part two"}]
    prompt = render_prompt(
        "inst", [HumanMessage(content="q1"), AIMessage(content=blocks), HumanMessage(content="q2")]
    )
    assert "Assistant: part one part two" in prompt

    with pytest.raises(ValueError, match="HumanMessage"):
        render_prompt("inst", [AIMessage(content="orphan")])


def test_word_count_tool_is_deterministic_and_validates_input() -> None:
    tool = default_tools()["word_count"]
    context = CustomToolContext(tool_call_id="c")
    assert count_words("  a  b\nc ") == 3
    assert tool.execute({"text": ""}, context) == "0"
    assert tool.input_schema["required"] == ["text"]
    with pytest.raises(ValueError):
        tool.execute({"text": 5}, context)


def test_default_prompt_is_loaded_from_package() -> None:
    prompt = load_default_prompt()
    assert "word_count" in prompt
