"""Graph construction: START -> run_cursor_agent -> END.

The Cursor SDK runs locally inside the single node. LangGraph checkpoints are
the source of truth for conversation history; every run gets a fresh Cursor
agent and receives the relevant history in its prompt.
"""

from __future__ import annotations

import tempfile
from collections.abc import Mapping, Sequence
from importlib import resources

from cursor_sdk import Agent, AgentOptions, CustomTool, LocalAgentOptions
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph

from generic_agent.config import CursorRunError, Settings

DEFAULT_MAX_HISTORY_MESSAGES = 40
"""Most recent prior messages passed to each Cursor run."""


def load_default_prompt() -> str:
    """Return the packaged default instructions (``prompts/default_system.md``)."""
    return (
        resources.files("generic_agent")
        .joinpath("prompts", "default_system.md")
        .read_text(encoding="utf-8")
        .strip()
    )


def render_prompt(
    instructions: str,
    messages: Sequence[AnyMessage],
    max_history_messages: int = DEFAULT_MAX_HISTORY_MESSAGES,
) -> str:
    """Build the text sent to Cursor: instructions, recent history, newest message.

    The newest ``HumanMessage`` is the one to answer; earlier human and
    assistant messages (most recent ``max_history_messages``) are history.
    """
    last_human = next(
        (i for i in range(len(messages) - 1, -1, -1) if isinstance(messages[i], HumanMessage)),
        None,
    )
    if last_human is None:
        raise ValueError("The graph input must contain a HumanMessage.")

    history = [
        f"{'User' if isinstance(m, HumanMessage) else 'Assistant'}: {m.text}"
        for m in messages[:last_human]
        if isinstance(m, (HumanMessage, AIMessage)) and m.text.strip()
    ]
    history = history[-max_history_messages:] if max_history_messages > 0 else []

    sections = [f"<instructions>\n{instructions}\n</instructions>"]
    if history:
        sections.append("<conversation_history>\n" + "\n\n".join(history) + "\n</conversation_history>")
    sections.append(f"<current_user_message>\n{messages[last_human].text}\n</current_user_message>")
    sections.append("Reply to the current user message, following the instructions.")
    return "\n\n".join(sections)


def build_agent(
    settings: Settings,
    system_prompt: str,
    tools: Mapping[str, CustomTool],
    *,
    checkpointer: BaseCheckpointSaver | bool | None = None,
    max_history_messages: int = DEFAULT_MAX_HISTORY_MESSAGES,
    builtin_tools: Sequence[str] | None = (),
) -> CompiledStateGraph:
    """Build and compile the agent graph.

    Args:
        settings: Cursor API key and model ID (never stored in graph state).
        system_prompt: Instructions rendered into each Cursor run's prompt.
        tools: Local custom tools by name. Pass ``{}`` for a text-only agent.
        checkpointer: Optional checkpointer (e.g. ``InMemorySaver``). Leave unset
            when a host such as Agent Server manages persistence.
        max_history_messages: How many earlier messages to include per run.
        builtin_tools: Cursor built-in tools to allow. The default ``()`` disables
            shell/file tools so only ``tools`` are available; ``None`` keeps
            Cursor's full default toolset.
    """
    custom_tools = dict(tools) or None

    def run_cursor_agent(state: MessagesState) -> dict[str, list[AnyMessage]]:
        prompt = render_prompt(system_prompt, state["messages"], max_history_messages)
        with tempfile.TemporaryDirectory(prefix="generic-agent-") as scratch_dir:
            result = Agent.prompt(
                prompt,
                AgentOptions(
                    model=settings.model,
                    api_key=settings.api_key.get_secret_value(),
                    local=LocalAgentOptions(cwd=scratch_dir, custom_tools=custom_tools),
                    tools=None if builtin_tools is None else list(builtin_tools),
                ),
            )
        if result.status != "finished":
            detail = f": {result.error_message}" if result.error_message else ""
            raise CursorRunError(f"Cursor run {result.status} (run id {result.id}){detail}")
        reply = AIMessage(
            content=result.result,
            response_metadata={
                "cursor_run_id": result.id,
                "cursor_agent_id": result.agent_id,
                "model": settings.model,
            },
        )
        return {"messages": [reply]}

    builder = StateGraph(MessagesState)
    builder.add_node("run_cursor_agent", run_cursor_agent)
    builder.add_edge(START, "run_cursor_agent")
    builder.add_edge("run_cursor_agent", END)
    return builder.compile(checkpointer=checkpointer)


__all__ = [
    "DEFAULT_MAX_HISTORY_MESSAGES",
    "build_agent",
    "load_default_prompt",
    "render_prompt",
]
