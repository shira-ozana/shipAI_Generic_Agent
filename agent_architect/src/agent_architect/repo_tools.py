"""Read-only Cursor custom tools over the cloned project repository.

The generic agent runs Cursor in an empty scratch folder with built-in file tools
disabled. Instead of widening that, the architect registers these two tools: they
execute in this process and can only read inside the cloned repository.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from cursor_sdk import CustomTool, CustomToolContext

MAX_FILE_CHARS = 60_000
MAX_LIST_ENTRIES = 300
_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", "bin", "obj"}


def _resolve_inside(repo_dir: Path, relative: str) -> Path:
    root = repo_dir.resolve()
    target = (root / (relative or ".")).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"Path {relative!r} is outside the repository.")
    if ".git" in target.relative_to(root).parts:
        raise ValueError("The .git folder is not readable.")
    return target


def list_files(repo_dir: Path, relative: str = ".", max_depth: int = 3) -> str:
    start = _resolve_inside(repo_dir, relative)
    if not start.is_dir():
        raise ValueError(f"{relative!r} is not a folder.")
    root = repo_dir.resolve()
    lines: list[str] = []

    def walk(folder: Path, depth: int) -> None:
        for entry in sorted(folder.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
            if len(lines) >= MAX_LIST_ENTRIES:
                return
            if entry.is_dir() and entry.name in _SKIP_DIRS:
                continue
            rel = entry.relative_to(root).as_posix()
            lines.append(rel + ("/" if entry.is_dir() else ""))
            if entry.is_dir() and depth < max_depth:
                walk(entry, depth + 1)

    walk(start, 1)
    if len(lines) >= MAX_LIST_ENTRIES:
        lines.append(f"... (truncated at {MAX_LIST_ENTRIES} entries; list a subfolder)")
    return "\n".join(lines) or "(empty folder)"


def read_file(repo_dir: Path, relative: str) -> str:
    target = _resolve_inside(repo_dir, relative)
    if not target.is_file():
        raise ValueError(f"{relative!r} is not a file.")
    text = target.read_text(encoding="utf-8", errors="replace")
    if len(text) > MAX_FILE_CHARS:
        return text[:MAX_FILE_CHARS] + f"\n... (truncated, file has {len(text)} characters)"
    return text


def repo_tools(repo_dir: Path) -> dict[str, CustomTool]:
    def _list(args: Mapping[str, Any], context: CustomToolContext) -> str:
        path = args.get("path") or "."
        if not isinstance(path, str):
            raise ValueError("'path' must be a string.")
        return list_files(repo_dir, path)

    def _read(args: Mapping[str, Any], context: CustomToolContext) -> str:
        path = args.get("path")
        if not isinstance(path, str) or not path:
            raise ValueError("read_repo_file requires a string argument named 'path'.")
        return read_file(repo_dir, path)

    return {
        "list_repo_files": CustomTool(
            execute=_list,
            description="List files and folders in the project repository (3 levels deep).",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Folder relative to the repo root. Default '.'."}
                },
            },
        ),
        "read_repo_file": CustomTool(
            execute=_read,
            description="Read a text file from the project repository.",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string", "description": "File path relative to the repo root."}},
                "required": ["path"],
            },
        ),
    }
