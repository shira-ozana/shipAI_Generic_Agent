"""Task Split prompt pack: core prompt + guardrails + rules + guidelines + skills.

Files live in ``task_split_agent/prompts/`` (package data) and use the same
frontmatter as the INIT pack (see ``generic_agent.prompt_docs``):

    prompts/core.md                    kind: core        the five stages and the JSON contract
    prompts/guardrails.md              kind: guardrail   model-facing boundaries
    prompts/rules/*.md                 kind: rule        binding instructions
    prompts/guidelines/*.md            kind: guideline   recommendations
    prompts/skills/<id>/SKILL.md       kind: skill       method + example, ``node: plan``

Task Split makes one model call per planning request, so there is one node,
``plan``. Selection and ordering are this pack's own: documents are ordered by
``(priority, id)``, and the instructions are core, then guardrails. Enforcement
is not here: Python validates the input, the reply and the repository scope
(see ``planner.py``).
"""

from __future__ import annotations

from importlib import resources

from generic_agent.prompt_docs import PromptDoc, parse_prompt_doc, walk_markdown
from generic_agent.runner import Section, Skill, compose_instructions

NODE = "plan"
"""The single node of the Task Split agent; skills are tagged ``node: plan``."""

SKILL_IDS = (
    "hls-analysis",
    "repository-inspection",
    "task-decomposition",
    "acceptance-criteria",
    "dependency-mapping",
)
"""The skills of the pack, in the order they are rendered (set by each file's ``priority``)."""


class PromptPackError(RuntimeError):
    """The packaged prompt files are inconsistent (a packaging or editing mistake)."""


class PromptPack:
    def __init__(self, docs: list[PromptDoc]):
        self.docs = docs

    @classmethod
    def load(cls) -> PromptPack:
        by_id: dict[str, PromptDoc] = {}
        for rel, text in walk_markdown(resources.files("task_split_agent").joinpath("prompts")):
            doc = parse_prompt_doc(text, f"builtin:{rel}")
            if doc.id in by_id:
                raise PromptPackError(f"duplicate prompt id {doc.id!r}")
            by_id[doc.id] = doc
        pack = cls(sorted(by_id.values(), key=lambda d: (d.priority, d.id)))
        if not pack._select("core", NODE):
            raise PromptPackError("the prompt pack has no core prompt")
        return pack

    def _select(self, kind: str, node: str) -> list[PromptDoc]:
        out = []
        for d in self.docs:
            if d.kind != kind:
                continue
            if kind == "skill":
                if d.node != node:
                    continue
            elif "*" not in d.applies_to and node not in d.applies_to:
                continue
            out.append(d)
        return out

    def ids_for(self, node: str = NODE) -> list[str]:
        """Prompt ids in the order they reach the model."""
        return [d.id for k in ("core", "guardrail", "rule", "guideline", "skill") for d in self._select(k, node)]

    def run_input(self, node: str = NODE) -> dict:
        """Keyword arguments for ``generic_agent.runner.GenericAgent.run()``."""
        core = self._select("core", node) + self._select("guardrail", node)
        return {
            "instructions": "\n\n---\n\n".join(d.body for d in core),
            "rules": [Section(d.id, d.title, d.body) for d in self._select("rule", node)],
            "guidelines": [Section(d.id, d.title, d.body) for d in self._select("guideline", node)],
            "skills": [Skill(name=d.id, body=d.body, description=d.title) for d in self._select("skill", node)],
            "shared_skills": [],
        }

    def compose(self, node: str = NODE) -> str:
        """The system prompt text that ``GenericAgent.run(**run_input(node))`` sends."""
        return compose_instructions(**self.run_input(node))
