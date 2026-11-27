"""The architect as an ``AgentDefinition`` run by the shared ``GenericAgent``.

The generic agent is used unchanged: this module only supplies the architect's
prompt and rules (its definition) and the read-only repository tools.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib import resources
from pathlib import Path

from generic_agent import AgentDefinition, GenericAgent
from generic_agent.runtime.agent_definition import Skill
from generic_agent.config import Settings as CursorSettings
from langchain_core.messages import HumanMessage

from agent_architect.repo_tools import repo_tools

LlmRunner = Callable[[str, Path], str]
"""(message, cloned repo folder) -> the model's text reply."""

ARCHITECT_RULES = (
    "Base every statement about the existing system on files you read with the repository tools.",
    "You cannot change files. Never claim that you created, changed or pushed anything.",
    "Reply with only the JSON object that the current message asks for, written in English.",
)


def load_architect_prompt() -> str:
    return (
        resources.files("agent_architect")
        .joinpath("prompts", "architect_system.md")
        .read_text(encoding="utf-8")
        .strip()
    )


def load_architect_skills() -> tuple[Skill, ...]:
    """Skills from ``prompts/skills/NN-name.md``, in file-name order; the name drops ``NN-``."""
    folder = resources.files("agent_architect").joinpath("prompts", "skills")
    files = sorted((f for f in folder.iterdir() if f.name.endswith(".md")), key=lambda f: f.name)
    return tuple(
        Skill(name=f.name.removesuffix(".md").split("-", 1)[-1], content=f.read_text(encoding="utf-8").strip())
        for f in files
    )


def architect_definition() -> AgentDefinition:
    """The Architect Agent's definition for ``GenericAgent.run``: prompt, rules and skills."""
    return AgentDefinition(
        prompt=load_architect_prompt(), rules=ARCHITECT_RULES, skills=load_architect_skills()
    )


def make_generic_llm(cursor_settings: CursorSettings, definition: AgentDefinition | None = None) -> LlmRunner:
    """Build an ``LlmRunner`` on top of ``generic_agent.GenericAgent``.

    Tools are bound to one cloned repo folder, so a runner is created per call.
    """
    agent_definition = definition or architect_definition()

    def run(message: str, repo_dir: Path) -> str:
        runner = GenericAgent(settings=cursor_settings, tools=repo_tools(repo_dir))
        result = runner.run(agent_definition, messages=[HumanMessage(content=message)])
        return result["messages"][-1].text

    return run
