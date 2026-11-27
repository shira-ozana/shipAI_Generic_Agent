"""Read-only, scoped access to one repository checkout, exposed as Cursor custom tools.

The host prepares a checkout of the selected revision (cloning with whatever
credentials it needs, outside this process's model input). The agent only gets
three tools that read that directory:

* ``repo_list_dir``, ``repo_read_file`` and ``repo_search``.

Guardrails enforced here, in application code:

* paths are relative to the checkout root; absolute paths, ``..`` and symlinks
  that resolve outside the root are refused;
* an optional ``allowed_paths`` list narrows the readable areas further;
* ``.git``, ``.env*``, keys and similar credential files are never readable;
* no shell, no network, no writes; reads are size- and count-limited.

Error messages never include absolute paths.
"""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from cursor_sdk import CustomTool, CustomToolContext

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_SAFE_REVISION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")

DENIED_DIR_NAMES = frozenset({".git"})
DENIED_FILE_GLOBS = (
    ".env",
    ".env.*",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "id_rsa*",
    "id_dsa*",
    "id_ecdsa*",
    "id_ed25519*",
    "credentials",
    "credentials.*",
    "*.kdbx",
)
SKIPPED_SEARCH_DIRS = frozenset(
    {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".mypy_cache", ".pytest_cache"}
)

MAX_LIST_ENTRIES = 200
MAX_READ_LINES = 400
MAX_READ_BYTES = 64 * 1024
MAX_FILE_BYTES = 1024 * 1024
MAX_SEARCH_FILE_BYTES = 256 * 1024
MAX_SEARCH_MATCHES = 50
MAX_QUERY_LENGTH = 200


class RepositoryAccessError(ValueError):
    """A repository read was refused (outside scope, denied, missing or too large)."""


class RepositoryAccess:
    """One authorized repository revision, readable only through scoped helpers."""

    def __init__(
        self,
        repository_id: str,
        root: str | os.PathLike[str],
        revision: str,
        allowed_paths: Sequence[str] = (),
    ) -> None:
        if not _SAFE_ID.match(repository_id or ""):
            raise ValueError("repository_id must be a short identifier (letters, digits, . _ -)")
        if not _SAFE_REVISION.match(revision or ""):
            raise ValueError("revision must be a short commit, tag or branch identifier")
        resolved = Path(root).resolve()
        if not resolved.is_dir():
            raise ValueError("repository root must be an existing directory")
        self.repository_id = repository_id
        self.revision = revision
        self._root = resolved
        self._allowed: tuple[Path, ...] = ()  # set below; _resolve(scoped=False) ignores it
        self._allowed = tuple(self._resolve(p, scoped=False) for p in allowed_paths)
        for allowed in self._allowed:
            if not allowed.exists():
                raise ValueError("every allowed path must exist in the checkout")

    @classmethod
    def from_checkout(
        cls,
        repository_id: str,
        root: str | os.PathLike[str],
        revision: str | None = None,
        allowed_paths: Sequence[str] = (),
    ) -> RepositoryAccess:
        """Open a checkout. If it is a git work tree, its HEAD must match ``revision``.

        ``revision`` may be omitted for a git checkout (HEAD is recorded). For a
        plain directory it is required and trusted as declared.
        """
        head = detect_git_head(Path(root))
        if revision is None:
            if head is None:
                raise ValueError("revision is required for a checkout that is not a git work tree")
            revision = head
        elif head is not None and not head.startswith(revision) and not revision.startswith(head):
            raise RepositoryAccessError("the checkout is not at the selected revision")
        return cls(repository_id, root, revision, allowed_paths)

    # --- scoping ---------------------------------------------------------------

    def _normalize(self, path: str) -> Path:
        if not isinstance(path, str) or "\x00" in path:
            raise RepositoryAccessError("path must be a string")
        text = path.strip().replace("\\", "/")
        if text in ("", ".", "./"):
            return Path()
        if text.startswith("/") or re.match(r"^[A-Za-z]:", text):
            raise RepositoryAccessError("path must be relative to the repository root")
        parts = [p for p in text.split("/") if p not in ("", ".")]
        if ".." in parts:
            raise RepositoryAccessError("path must stay inside the repository")
        return Path(*parts)

    def _resolve(self, path: str, *, scoped: bool = True) -> Path:
        relative = self._normalize(path)
        resolved = (self._root / relative).resolve()
        if resolved != self._root and self._root not in resolved.parents:
            raise RepositoryAccessError("path is outside the repository")
        shown = resolved.relative_to(self._root)
        if _is_denied(relative) or _is_denied(shown):
            raise RepositoryAccessError("path is not readable")
        if scoped and not self._within_scope(resolved) and not self._ancestor_of_scope(resolved):
            raise RepositoryAccessError("path is outside the authorized area")
        return resolved

    def _within_scope(self, path: Path) -> bool:
        return not self._allowed or any(path == a or a in path.parents for a in self._allowed)

    def _ancestor_of_scope(self, path: Path) -> bool:
        return any(path in a.parents for a in self._allowed)

    def contains(self, path: str) -> bool:
        """True when ``path`` exists inside the authorized, readable scope."""
        try:
            resolved = self._resolve(path)
        except RepositoryAccessError:
            return False
        return resolved.exists() and (self._within_scope(resolved) or resolved.is_dir())

    def _rel(self, path: Path) -> str:
        return "." if path == self._root else path.relative_to(self._root).as_posix()

    # --- operations ------------------------------------------------------------

    def list_dir(self, path: str = "") -> str:
        directory = self._resolve(path)
        if not directory.is_dir():
            raise RepositoryAccessError("not a directory")
        lines: list[str] = []
        for entry in sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            try:
                target = self._resolve(self._rel(entry))
            except RepositoryAccessError:
                continue
            if target.is_dir():
                if self._within_scope(target) or self._ancestor_of_scope(target):
                    lines.append(f"dir   {entry.name}/")
            elif self._within_scope(target):
                lines.append(f"file  {entry.name} ({target.stat().st_size} bytes)")
        shown = lines[:MAX_LIST_ENTRIES]
        suffix = [f"... {len(lines) - len(shown)} more entries"] if len(lines) > len(shown) else []
        return "\n".join([f"# {self._rel(directory)}/", *shown, *suffix])

    def read_file(self, path: str, start_line: int = 1, end_line: int | None = None) -> str:
        target = self._resolve(path)
        if not target.is_file() or not self._within_scope(target):
            raise RepositoryAccessError("not a readable file")
        if target.stat().st_size > MAX_FILE_BYTES:
            raise RepositoryAccessError("file is too large to read")
        data = target.read_bytes()
        if b"\x00" in data[:8000]:
            raise RepositoryAccessError("binary files cannot be read")
        lines = data.decode("utf-8", errors="replace").splitlines()
        total = len(lines)
        start = max(1, start_line)
        stop = min(total, start + MAX_READ_LINES - 1, end_line if end_line is not None else total)
        selected: list[str] = []
        size = 0
        for number in range(start, stop + 1):
            row = f"{number:>5}| {lines[number - 1]}"
            size += len(row) + 1
            if size > MAX_READ_BYTES:
                break
            selected.append(row)
        last = start + len(selected) - 1
        more = " - call again with start_line to continue" if last < min(total, end_line or total) else ""
        return "\n".join([f"# {self._rel(target)} (lines {start}-{max(last, start - 1)} of {total}{more})", *selected])

    def search(self, query: str, path: str = "") -> str:
        if not isinstance(query, str) or not query.strip():
            raise RepositoryAccessError("query must be a non-blank string")
        if len(query) > MAX_QUERY_LENGTH:
            raise RepositoryAccessError("query is too long")
        needle = query.lower()
        start = self._resolve(path)
        files: list[Path] = []
        if start.is_file():
            files.append(start)
        else:
            for current, dirs, names in os.walk(start, followlinks=False):
                dirs[:] = sorted(d for d in dirs if d not in SKIPPED_SEARCH_DIRS and not _is_denied(Path(d)))
                files.extend(Path(current) / name for name in sorted(names))
        matches: list[str] = []
        for file in files:
            if len(matches) >= MAX_SEARCH_MATCHES:
                break
            try:
                # Re-resolve through the guardrails: a symlink may point outside or at a denied file.
                target = self._resolve(self._rel(file))
                if not target.is_file() or not self._within_scope(target):
                    continue
                if target.stat().st_size > MAX_SEARCH_FILE_BYTES:
                    continue
                data = target.read_bytes()
            except (RepositoryAccessError, OSError):
                continue
            if b"\x00" in data[:8000]:
                continue
            for number, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
                if needle in line.lower():
                    matches.append(f"{self._rel(target)}:{number}: {line.strip()[:200]}")
                    if len(matches) >= MAX_SEARCH_MATCHES:
                        break
        if not matches:
            return "No matches."
        capped = " (limit reached)" if len(matches) >= MAX_SEARCH_MATCHES else ""
        return "\n".join([f"# {len(matches)} matches{capped}", *matches])

    # --- Cursor tools ----------------------------------------------------------

    def tools(self) -> dict[str, CustomTool]:
        """The only repository capabilities the agent receives."""

        def list_dir(args: Mapping[str, Any], context: CustomToolContext) -> str:
            return self.list_dir(str(args.get("path", "")))

        def read_file(args: Mapping[str, Any], context: CustomToolContext) -> str:
            path = args.get("path")
            if not isinstance(path, str):
                raise RepositoryAccessError("path is required")
            end = args.get("end_line")
            return self.read_file(path, _as_int(args.get("start_line", 1)), None if end is None else _as_int(end))

        def search(args: Mapping[str, Any], context: CustomToolContext) -> str:
            query = args.get("query")
            if not isinstance(query, str):
                raise RepositoryAccessError("query is required")
            return self.search(query, str(args.get("path", "")))

        return {
            "repo_list_dir": CustomTool(
                execute=list_dir,
                description=(
                    "List a directory of the read-only repository checkout. Paths are relative to "
                    "the repository root; an empty path lists the root."
                ),
                input_schema={
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "Relative directory path."}},
                },
            ),
            "repo_read_file": CustomTool(
                execute=read_file,
                description=(
                    "Read a text file from the read-only repository checkout with line numbers. "
                    f"Returns at most {MAX_READ_LINES} lines per call; use start_line/end_line to page."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative file path."},
                        "start_line": {"type": "integer", "description": "First line (1-based)."},
                        "end_line": {"type": "integer", "description": "Last line (inclusive)."},
                    },
                    "required": ["path"],
                },
            ),
            "repo_search": CustomTool(
                execute=search,
                description=(
                    "Case-insensitive literal text search across the read-only repository checkout. "
                    f"Returns up to {MAX_SEARCH_MATCHES} 'path:line: text' matches."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Text to find (not a regex)."},
                        "path": {"type": "string", "description": "Optional relative directory or file to search in."},
                    },
                    "required": ["query"],
                },
            ),
        }


def _as_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RepositoryAccessError("line numbers must be integers")
    return value


def _is_denied(relative: Path) -> bool:
    for part in relative.parts:
        if part in DENIED_DIR_NAMES:
            return True
    name = relative.name
    return bool(name) and any(fnmatch.fnmatch(name.lower(), glob) for glob in DENIED_FILE_GLOBS)


def detect_git_head(root: Path) -> str | None:
    """HEAD of ``root`` if it is exactly the top level of a git work tree, else ``None``.

    Runs two local, read-only git commands. No network, no prompts.
    """
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}

    def git(*args: str) -> str | None:
        try:
            done = subprocess.run(
                ["git", "-C", str(root), *args],
                capture_output=True,
                text=True,
                timeout=10,
                env=env,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout.strip() if done.returncode == 0 else None

    top = git("rev-parse", "--show-toplevel")
    if top is None or Path(top).resolve() != Path(root).resolve():
        return None
    return git("rev-parse", "HEAD")
