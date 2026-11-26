"""INIT agent: prepares an existing repository for agent-assisted development.

Built on the Generic Agent: every LLM call is a `generic_agent.build_agent` run whose
system prompt is assembled from the INIT prompt pack (rules, guidelines, skills).
"""

from .service import arun_init, run_init

__all__ = ["arun_init", "run_init"]
