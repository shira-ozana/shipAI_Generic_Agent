"""Select and pack source excerpts into `<evidence>` blocks that fit the context budget."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

_PRIORITY = re.compile(r"(route|routing|router|main|app\.|index|service|api|client|store|model|component|controller|"
                       r"handler|module|config|schema|types?)", re.I)
_SECRET = re.compile(r"(?i)((?:api[_-]?key|secret|token|password|passwd|auth)[\"']?\s*[:=]\s*[\"']?)([^\s\"',]{8,})")


def redact(text: str) -> str:
    return _SECRET.sub(lambda m: m.group(1) + "***", text)


def evidence_block(path: str, text: str, truncated: bool = False) -> str:
    safe = text.replace("</evidence>", "<\\/evidence>")
    return f'<evidence path="{path}" truncated="{str(truncated).lower()}">\n{safe}\n</evidence>'


def read_excerpt(root: Path, rel: str, max_chars: int) -> tuple[str, bool]:
    try:
        text = (root / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "", False
    text = redact(text)
    if len(text) <= max_chars:
        return text, False
    head = int(max_chars * 0.8)
    return text[:head] + "\n/* ... truncated ... */\n" + text[-(max_chars - head):], True


def rank(paths: list[str]) -> list[str]:
    def key(p: str):
        name = PurePosixPath(p).name
        is_test = bool(re.search(r"(\.spec\.|\.test\.|_test\.|/tests?/|/e2e/)", "/" + p))
        return (is_test, 0 if _PRIORITY.search(name) else 1, p.count("/"), p)
    return sorted(paths, key=key)


@dataclass
class Chunk:
    paths: list[str] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)
    truncated: list[str] = field(default_factory=list)
    chars: int = 0

    @property
    def text(self) -> str:
        return "\n\n".join(self.blocks)


@dataclass
class Packing:
    chunks: list[Chunk]
    left_out: list[str]            # files that did not fit in max_chunks
    truncated: list[str]


def pack(root: Path, paths: list[str], budget: int, max_file_chars: int, max_chunks: int) -> Packing:
    chunks: list[Chunk] = [Chunk()]
    left_out: list[str] = []
    truncated: list[str] = []
    for rel in rank(paths):
        text, cut = read_excerpt(root, rel, max_file_chars)
        if not text.strip():
            continue
        block = evidence_block(rel, text, cut)
        if chunks[-1].chars + len(block) > budget and chunks[-1].blocks:
            if len(chunks) >= max_chunks:
                left_out.append(rel)
                continue
            chunks.append(Chunk())
        c = chunks[-1]
        c.paths.append(rel)
        c.blocks.append(block)
        c.chars += len(block)
        if cut:
            c.truncated.append(rel)
            truncated.append(rel)
    return Packing(chunks=[c for c in chunks if c.blocks], left_out=left_out, truncated=truncated)
