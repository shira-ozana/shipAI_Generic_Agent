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

import json
from dataclasses import dataclass, field
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path, PurePosixPath

from .models import SharedSkill


@dataclass
class PromptDoc:
    id: str
    kind: str
    body: str
    title: str = ""
    applies_to: list[str] = field(default_factory=lambda: ["*"])
    node: str = ""
    files: list[str] = field(default_factory=list)
    priority: int = 100
    source: str = ""


def _parse(text: str, source: str) -> PromptDoc:
    meta: dict = {}
    body = text
    if text.startswith("---"):
        _, header, body = text.split("---", 2)
        for line in header.strip().splitlines():
            key, _, value = line.partition(":")
            value = value.strip()
            try:
                meta[key.strip()] = json.loads(value)
            except json.JSONDecodeError:
                meta[key.strip()] = value
    return PromptDoc(
        id=str(meta.get("id") or source),
        kind=str(meta.get("kind", "rule")),
        title=str(meta.get("title", "")),
        applies_to=list(meta.get("applies_to", ["*"])),
        node=str(meta.get("node", "")),
        files=list(meta.get("files", [])),
        priority=int(meta.get("priority", 100)),
        body=body.strip(),
        source=source,
    )


def _walk(root: Traversable | Path, prefix: str = ""):
    for entry in sorted(root.iterdir(), key=lambda e: e.name):
        if entry.is_dir():
            yield from _walk(entry, f"{prefix}{entry.name}/")
        elif entry.name.endswith(".md"):
            yield f"{prefix}{entry.name}", entry.read_text(encoding="utf-8")


def _matches_file(patterns: list[str], file_path: str | None) -> bool:
    if not patterns:
        return True
    if not file_path:
        return False
    p = PurePosixPath(file_path)
    return any(p.name == pat or p.match(pat) for pat in patterns)


class PromptPack:
    def __init__(self, docs: list[PromptDoc]):
        self.docs = docs

    @classmethod
    def load(cls, override_dir: str = "") -> "PromptPack":
        by_id: dict[str, PromptDoc] = {}
        for rel, text in _walk(resources.files("init_agent").joinpath("prompts")):
            doc = _parse(text, f"builtin:{rel}")
            by_id[doc.id] = doc
        if override_dir and Path(override_dir).is_dir():
            for rel, text in _walk(Path(override_dir)):
                doc = _parse(text, f"override:{rel}")
                by_id[doc.id] = doc
        return cls(sorted(by_id.values(), key=lambda d: (d.priority, d.id)))

    def _select(self, kind: str, node: str, file_path: str | None) -> list[PromptDoc]:
        out = []
        for d in self.docs:
            if d.kind != kind:
                continue
            if kind == "skill":
                if d.node != node or not _matches_file(d.files, file_path):
                    continue
            else:
                if "*" not in d.applies_to and node not in d.applies_to:
                    continue
                if d.files and node == "draft_file" and not _matches_file(d.files, file_path):
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
        parts: list[str] = []
        for d in self._select("core", node, file_path) + self._select("guardrail", node, file_path):
            parts.append(d.body)

        rules = self._select("rule", node, file_path)
        if rules or extra_rules:
            lines = ["# Rules (binding)"]
            lines += [f"## {d.id}: {d.title}\n{d.body}" for d in rules]
            lines += [f"## OP-R{i}: operator rule\n{r}" for i, r in enumerate(extra_rules or [], 1)]
            parts.append("\n\n".join(lines))

        guidelines = self._select("guideline", node, file_path)
        if guidelines or extra_guidelines:
            lines = ["# Guidelines (recommended)"]
            lines += [f"## {d.id}: {d.title}\n{d.body}" for d in guidelines]
            lines += [f"## OP-G{i}: operator guideline\n{g}" for i, g in enumerate(extra_guidelines or [], 1)]
            parts.append("\n\n".join(lines))

        for d in self._select("skill", node, file_path):
            parts.append(d.body)

        if shared_skills:
            lines = ["# Shared skill catalog (reuse by id; do not copy)"]
            lines += [f"- `{s.id}`: {s.description} ({s.ref})" for s in shared_skills]
            parts.append("\n".join(lines))

        return "\n\n---\n\n".join(parts).replace("{platform_dir}", platform_dir)
