"""LangChain chat model on top of `generic_agent.build_agent`.

* The INIT prompt pack (core + guardrails + rules + guidelines + skill) is passed as the
  graph's `system_prompt`, so it is rendered into `<instructions>` of every Cursor run.
* No checkpointer: every call is self-contained; history never accumulates.
* `tools={}` and Cursor built-in tools stay disabled (generic_agent default): the model
  only sees what INIT sends and cannot touch files.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr

from generic_agent.config import Settings, load_settings
from generic_agent.graph import build_agent


class GenericAgentModel(BaseChatModel):
    """`BaseChatModel` whose every call is one Generic Agent graph invocation."""

    thread_id: str | None = None          # one per INIT run, for tracing only
    _settings: Settings | None = PrivateAttr(default=None)
    _graphs: dict[str, Any] = PrivateAttr(default_factory=dict)
    _lock: threading.Lock = PrivateAttr(default_factory=threading.Lock)

    def __init__(self, settings: Settings | None = None, **data: Any) -> None:
        super().__init__(**data)
        self._settings = settings

    @property
    def _llm_type(self) -> str:
        return "generic-agent"

    def _graph(self, system_prompt: str):
        with self._lock:
            if self._settings is None:
                self._settings = load_settings()      # CURSOR_API_KEY / CURSOR_MODEL
            graph = self._graphs.get(system_prompt)
            if graph is None:
                graph = build_agent(self._settings, system_prompt, {}, checkpointer=None)
                self._graphs[system_prompt] = graph
            return graph

    @staticmethod
    def _split(messages: list[BaseMessage]) -> tuple[str, list[BaseMessage]]:
        system = "\n\n".join(str(m.content) for m in messages if isinstance(m, SystemMessage))
        convo = [m for m in messages if isinstance(m, (HumanMessage, AIMessage))]
        if not convo or not isinstance(convo[-1], HumanMessage):
            raise ValueError("the last message must be a HumanMessage")
        return system or "You are a careful assistant.", convo

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        system, convo = self._split(messages)
        result = self._graph(system).invoke({"messages": convo})
        reply = result["messages"][-1]
        text = reply.text.strip()
        if not text:
            raise RuntimeError("Generic Agent returned an empty reply")
        meta = {**(reply.response_metadata or {}), "init_thread_id": self.thread_id}
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text, response_metadata=meta))])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        # Agent.prompt blocks; run it in a worker thread.
        return await asyncio.to_thread(self._generate, messages, stop, None, **kwargs)
