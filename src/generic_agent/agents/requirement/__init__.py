"""Requirement Agent: turns a customer request into numbered business requirements.

An extension of the Generic Agent: its instructions and skills are packaged
Markdown (``instructions/``), its tools are bound to a per-thread clone of the
customer repository, and ``RequirementAgent`` runs it on ``GenericAgent``.
"""

from generic_agent.agents.requirement.agent import (
    PreflightError,
    RequirementAgent,
    RequirementTurn,
    SessionError,
)
from generic_agent.agents.requirement.inputs import (
    DEFAULT_INIT_OUTPUT_GLOB,
    SOURCE_BRANCH,
    GitCredentials,
    InputError,
    RequirementInputs,
    RequirementSettings,
    Status,
    load_requirement_settings,
)
from generic_agent.agents.requirement.loader import load_instruction_parts, load_requirement_definition

__all__ = [
    "DEFAULT_INIT_OUTPUT_GLOB",
    "SOURCE_BRANCH",
    "GitCredentials",
    "InputError",
    "PreflightError",
    "RequirementAgent",
    "RequirementInputs",
    "RequirementSettings",
    "RequirementTurn",
    "SessionError",
    "Status",
    "load_instruction_parts",
    "load_requirement_definition",
    "load_requirement_settings",
]
