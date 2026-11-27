"""Reusable Cursor SDK agent executed by LangGraph.

Two runners share the graph:

- ``generic_agent.GenericAgent`` (``generic_agent.runtime``): runs an ``AgentDefinition``.
- ``generic_agent.runner.GenericAgent``: the INIT agent's runner, ``run(prompt, instructions=,
  rules=, guidelines=, skills=)``. Import it from ``generic_agent.runner``.
"""

from generic_agent.graph import DEFAULT_MAX_HISTORY_MESSAGES, build_agent, load_default_prompt
from generic_agent.runtime import AgentDefinition, GenericAgent
from generic_agent.tools import default_tools

__all__ = [
    "DEFAULT_MAX_HISTORY_MESSAGES",
    "AgentDefinition",
    "GenericAgent",
    "build_agent",
    "default_tools",
    "load_default_prompt",
]
