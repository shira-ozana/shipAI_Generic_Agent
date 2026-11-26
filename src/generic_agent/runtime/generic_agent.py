"""``GenericAgent``: a synchronous runner that takes the definition per call."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from cursor_sdk import CustomTool
from langchain_core.messages import AnyMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver

from generic_agent.config import Settings
from generic_agent.runtime.agent_definition import AgentContext, AgentDefinition


class GenericAgent:
    """Runs any ``AgentDefinition`` on the shared graph.

    The runner owns the Cursor settings, tools and optional checkpointer; the
    definition is supplied on each ``run`` and travels as invocation-scoped
    runtime context. Graph state and checkpoints hold only conversation
    messages, so one runner can safely serve different definitions (even
    concurrently or on the same thread).

        runner = GenericAgent(settings=settings, tools=tools)
        result = runner.run(definition, messages=[HumanMessage(content="hi")])
    """

    def __init__(
        self,
        settings: Settings,
        tools: Mapping[str, CustomTool],
        *,
        checkpointer: BaseCheckpointSaver | bool | None = None,
        max_history_messages: int | None = None,
        builtin_tools: Sequence[str] | None = (),
    ) -> None:
        # Imported here: graph.py imports runtime.agent_definition, so a module-level
        # import would be circular when ``generic_agent.graph`` is imported first.
        from generic_agent.graph import DEFAULT_MAX_HISTORY_MESSAGES, build_agent

        self._graph = build_agent(
            settings,
            "",  # unused: every run supplies its definition as context
            tools,
            checkpointer=checkpointer,
            max_history_messages=(
                DEFAULT_MAX_HISTORY_MESSAGES if max_history_messages is None else max_history_messages
            ),
            builtin_tools=builtin_tools,
        )

    def run(
        self,
        definition: AgentDefinition,
        messages: Sequence[AnyMessage],
        config: RunnableConfig | None = None,
    ) -> dict[str, Any]:
        """Run ``definition`` over ``messages`` and return ``graph.invoke()``'s result.

        Args:
            definition: Prompt, rules, skills and guardrails for this run only.
            messages: Conversation input; the newest ``HumanMessage`` is answered.
            config: Optional LangGraph config, e.g.
                ``{"configurable": {"thread_id": "..."}}`` (needs a checkpointer).

        Returns:
            ``{"messages": [...]}`` as returned by the graph.

        Raises:
            GuardrailError: a pre-run or post-run guardrail failed.
            CursorRunError: the Cursor run did not finish.
        """
        if not isinstance(definition, AgentDefinition):
            raise TypeError("definition must be an AgentDefinition")
        return self._graph.invoke(
            {"messages": list(messages)},
            config,
            context=AgentContext(definition=definition),
        )
