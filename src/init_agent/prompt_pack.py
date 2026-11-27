"""Prompt pack: core prompt + guardrails + rules + guidelines + skills -> one system prompt per node.

Files live in `init_agent/prompts/` (package data) and may be extended or overridden by a
folder set in INIT_PROMPTS_DIR with the same layout (a file with the same `id` replaces
the built-in one). Each file starts with a small frontmatter block:

    ---
    id: R1
    title: Evidence is required
    kind: rule | guideline | skill | guardrail | core
    applies_to: ["*"] | ["plan_harness", "draft_file"]   # rules, guidelines
    node: analyze_repository                              # skills
    files: ["ARCHITECTURE.md"]                            # optional, draft_file only
    priority: 10
    ---
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from generic_agent.prompt_docs import PromptDoc, matches_file, parse_prompt_doc, walk_markdown
from generic_agent.runner import Section, SharedSkillRef, Skill, compose_instructions

from .models import SharedSkill


class PromptPack:
    def __init__(self, docs: list[PromptDoc]):
        self.docs = docs

    @classmethod
    def load(cls, override_dir: str = "") -> "PromptPack":
        by_id: dict[str, PromptDoc] = {}
        for rel, text in walk_markdown(resources.files("init_agent").joinpath("prompts")):
            doc = parse_prompt_doc(text, f"builtin:{rel}")
            by_id[doc.id] = doc
        if override_dir and Path(override_dir).is_dir():
            for rel, text in walk_markdown(Path(override_dir)):
                doc = parse_prompt_doc(text, f"override:{rel}")
                by_id[doc.id] = doc
        return cls(sorted(by_id.values(), key=lambda d: (d.priority, d.id)))

    def _select(self, kind: str, node: str, file_path: str | None) -> list[PromptDoc]:
        out = []
        for d in self.docs:
            if d.kind != kind:
                continue
            if kind == "skill":
                if d.node != node or not matches_file(d.files, file_path):
                    continue
            else:
                if "*" not in d.applies_to and node not in d.applies_to:
                    continue
                if d.files and node == "draft_file" and not matches_file(d.files, file_path):
                    continue
            out.append(d)
        return out

    def ids_for(self, node: str, file_path: str | None = None) -> list[str]:
        return [d.id for k in ("core", "guardrail", "rule", "guideline", "skill") for d in self._select(k, node, file_path)]

    def compose(
        self,
        node: str,
        *,
        platform_dir: str = "platform",
        file_path: str | None = None,
        extra_rules: list[str] | None = None,
        extra_guidelines: list[str] | None = None,
        shared_skills: list[SharedSkill] | None = None,
    ) -> str:
        """The system prompt text that `GenericAgent.run(**run_input(...))` sends."""
        return compose_instructions(**self.run_input(
            node, platform_dir=platform_dir, file_path=file_path, extra_rules=extra_rules,
            extra_guidelines=extra_guidelines, shared_skills=shared_skills,
        ))

    def run_input(
        self,
        node: str,
        *,
        platform_dir: str = "platform",
        file_path: str | None = None,
        extra_rules: list[str] | None = None,
        extra_guidelines: list[str] | None = None,
        shared_skills: list[SharedSkill] | None = None,
    ) -> dict:
        """Keyword arguments for `GenericAgent.run()`: instructions, rules, guidelines, skills."""
        def sub(text: str) -> str:
            return text.replace("{platform_dir}", platform_dir)

        core = self._select("core", node, file_path) + self._select("guardrail", node, file_path)
        rules = [Section(d.id, d.title, sub(d.body)) for d in self._select("rule", node, file_path)]
        rules += [Section(f"OP-R{i}", "operator rule", sub(r)) for i, r in enumerate(extra_rules or [], 1)]
        guidelines = [Section(d.id, d.title, sub(d.body)) for d in self._select("guideline", node, file_path)]
        guidelines += [Section(f"OP-G{i}", "operator guideline", sub(g))
                       for i, g in enumerate(extra_guidelines or [], 1)]
        return {
            "instructions": sub("\n\n---\n\n".join(d.body for d in core)),
            "rules": rules,
            "guidelines": guidelines,
            "skills": [Skill(name=d.id, body=sub(d.body), description=d.title)
                       for d in self._select("skill", node, file_path)],
            "shared_skills": [SharedSkillRef(id=s.id, description=s.description, ref=s.ref)
                              for s in shared_skills or []],
        }
