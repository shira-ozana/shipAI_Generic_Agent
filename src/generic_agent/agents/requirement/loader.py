"""Compose the Requirement Agent's instructions from its packaged Markdown files.

Layout of an instructions folder (default: ``instructions/`` in this package)::

    AGENTS.md                    # main instructions -> AgentDefinition.prompt
    skills/<name>/SKILL.md       # one skill per folder -> Skill("<name>", ...)
    templates/<file>             # output templates -> Skill("template:<file>", ...)

Order is stable: the main file, then skills sorted by folder name, then
templates sorted by file name. Pass ``root`` to load another folder.
"""

from __future__ import annotations

from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

from generic_agent.runtime import AgentDefinition, Skill

MAIN_FILE = "AGENTS.md"
SKILLS_DIR = "skills"
SKILL_FILE = "SKILL.md"
TEMPLATES_DIR = "templates"
TEMPLATE_PREFIX = "template:"


def default_instructions_root() -> Traversable:
    """The packaged ``instructions/`` folder."""
    return resources.files(__package__).joinpath("instructions")


def load_instruction_parts(root: Traversable | Path | None = None) -> tuple[str, tuple[Skill, ...]]:
    """Return ``(main instructions, skills + templates)`` in a stable order."""
    base: Traversable = default_instructions_root() if root is None else root
    main = base.joinpath(MAIN_FILE)
    if not main.is_file():
        raise FileNotFoundError(f"{MAIN_FILE} not found in the instructions folder")
    prompt = main.read_text(encoding="utf-8").strip()

    skills: list[Skill] = []
    skills_dir = base.joinpath(SKILLS_DIR)
    if skills_dir.is_dir():
        for folder in sorted((d for d in skills_dir.iterdir() if d.is_dir()), key=lambda d: d.name):
            skill_file = folder.joinpath(SKILL_FILE)
            if skill_file.is_file():
                skills.append(Skill(folder.name, skill_file.read_text(encoding="utf-8").strip()))

    templates_dir = base.joinpath(TEMPLATES_DIR)
    if templates_dir.is_dir():
        for template in sorted((f for f in templates_dir.iterdir() if f.is_file()), key=lambda f: f.name):
            skills.append(
                Skill(f"{TEMPLATE_PREFIX}{template.name}", template.read_text(encoding="utf-8").strip())
            )
    return prompt, tuple(skills)


def load_requirement_definition(
    root: Traversable | Path | None = None, *, tools=None
) -> AgentDefinition:
    """Build the Requirement Agent's ``AgentDefinition`` (optionally with per-run tools)."""
    prompt, skills = load_instruction_parts(root)
    return AgentDefinition(prompt=prompt, skills=skills, tools=tools)
