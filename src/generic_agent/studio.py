"""LangGraph Studio / Agent Server entry point (see ``langgraph.json``).

No checkpointer is passed: Agent Server provides persistence in this mode.
"""

from __future__ import annotations

from langgraph.graph.state import CompiledStateGraph

from generic_agent.config import load_settings
from generic_agent.graph import build_agent, load_default_prompt
from generic_agent.tools import default_tools


def make_graph() -> CompiledStateGraph:
    """Build the same graph as the CLI, using Cursor settings from the environment."""
    return build_agent(load_settings(), load_default_prompt(), default_tools())
