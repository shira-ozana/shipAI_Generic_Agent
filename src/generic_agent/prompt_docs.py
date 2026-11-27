"""Shared loading of Markdown prompt files that start with a frontmatter block.

Agents with a prompt pack (INIT, Task Split) keep their own files and their own
selection logic. Only the file format is shared:

    ---
    id: R1
    title: Evidence is required
    kind: rule | guideline | skill | guardrail | core
    applies_to: ["*"] | ["plan_harness", "draft_file"]   # rules, guidelines
    node: analyze_repository                              # skills
    files: ["ARCHITECTURE.md"]                            # optional
    priority: 10
    ---

Values are JSON when they parse as JSON, plain text otherwise. Files are read in
sorted path order, so loading is deterministic.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from importlib.resources.abc import Traversable
from pathlib import Path, PurePosixPath


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


def parse_prompt_doc(text: str, source: str) -> PromptDoc:
    """Parse one Markdown file; a file without frontmatter becomes a rule named after ``source``."""
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


def walk_markdown(root: Traversable | Path, prefix: str = "") -> Iterator[tuple[str, str]]:
    """Yield ``(relative path, text)`` for every ``.md`` file under ``root``, in sorted order."""
    for entry in sorted(root.iterdir(), key=lambda e: e.name):
        if entry.is_dir():
            yield from walk_markdown(entry, f"{prefix}{entry.name}/")
        elif entry.name.endswith(".md"):
            yield f"{prefix}{entry.name}", entry.read_text(encoding="utf-8")


def matches_file(patterns: list[str], file_path: str | None) -> bool:
    """True when ``patterns`` is empty, or ``file_path`` matches one by name or glob."""
    if not patterns:
        return True
    if not file_path:
        return False
    p = PurePosixPath(file_path)
    return any(p.name == pat or p.match(pat) for pat in patterns)
