"""Shared runtime foundation: agent definitions and the ``GenericAgent`` runner."""

from generic_agent.runtime.agent_definition import (
    AgentContext,
    AgentDefinition,
    Guardrail,
    GuardrailPhase,
    GuardrailResult,
    Skill,
    assemble_instructions,
)
from generic_agent.runtime.generic_agent import GenericAgent

__all__ = [
    "AgentContext",
    "AgentDefinition",
    "GenericAgent",
    "Guardrail",
    "GuardrailPhase",
    "GuardrailResult",
    "Skill",
    "assemble_instructions",
]
