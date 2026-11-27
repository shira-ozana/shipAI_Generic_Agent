"""The per-thread clone: confined reads, restricted output writes, publishing."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from generic_agent.agents.requirement.gitops import GitError, GitRepo
from generic_agent.agents.requirement.inputs import (
    OUTPUT_DIR_TEMPLATE,
    SOURCE_BRANCH,
    GitCredentials,
    Status,
)

OUTPUT_FILES = ("requirement.md", "clarifications.md")
STATUS_FILE = "status.json"
MAX_READ_BYTES = 200_000
MAX_SEARCH_FILE_BYTES = 1_000_000
MAX_LIST_ENTRIES = 500
MAX_SEARCH_MATCHES = 100
_HIDDEN_DIRS = {".git"}
_PUBLISH_LABEL = re.compile(r"round [1-9][0-9]{0,2} questions|approval requested|completed|failed")


class PathError(ValueError):
    """A tool path is outside the clone or otherwise not allowed."""


class WorkspaceError(RuntimeError):
    """A workspace operation failed. The message is safe for the model and UI."""


@dataclass
class Workspace:
    """One thread's clone of the repository and its output folder."""

    root: Path
    feature_id: str
    credentials: GitCredentials
    # Approval guard (in memory, per thread): the digest of requirement.md shown in
    # the last approval request, and whether the user has replied since.
    approval_digest: str | None = field(default=None, repr=False)
    approval_replied: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        self.root = Path(self.root)

    def _requirement_digest(self) -> str | None:
        path = self.output_dir / "requirement.md"
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None

    def record_approval_reply(self) -> None:
        """Called by code when a user message arrives while awaiting approval."""
        if self.approval_digest is not None:
            self.approval_replied = True

    @property
    def output_rel(self) -> str:
        return OUTPUT_DIR_TEMPLATE.format(feature_id=self.feature_id)

    @property
    def output_dir(self) -> Path:
        return self.root.joinpath(*PurePosixPath(self.output_rel).parts)

    @property
    def git(self) -> GitRepo:
        return GitRepo(self.root, self.credentials)

    # -- reading (confined to the clone) ------------------------------------

    def resolve(self, path: str) -> Path:
        """Resolve a clone-relative path; reject absolute paths, '..', escapes and .git."""
        if not isinstance(path, str):
            raise PathError("path must be a string.")
        raw = path.strip().replace("\\", "/") or "."
        pure = PurePosixPath(raw)
        if pure.is_absolute() or re.match(r"^[A-Za-z]:", raw) or raw.startswith("//"):
            raise PathError("path must be relative to the repository root.")
        if ".." in pure.parts:
            raise PathError("path must not contain '..'.")
        if any(part in _HIDDEN_DIRS for part in pure.parts):
            raise PathError("the .git folder is not accessible.")
        root = self.root.resolve()
        target = root.joinpath(*pure.parts).resolve()
        if target != root and not target.is_relative_to(root):
            raise PathError("path resolves outside the repository.")
        return target

    def list_files(self, path: str = ".", recursive: bool = False) -> str:
        target = self.resolve(path)
        if not target.is_dir():
            raise PathError(f"not a directory: {path}")
        root = self.root.resolve()
        entries: list[str] = []
        truncated = False
        if recursive:
            for dirpath, dirnames, filenames in os.walk(target, followlinks=False):
                dirnames[:] = sorted(d for d in dirnames if d not in _HIDDEN_DIRS)
                for name in sorted(filenames):
                    candidate = Path(dirpath, name)
                    if not candidate.resolve().is_relative_to(root):
                        continue
                    entries.append(candidate.relative_to(root).as_posix())
                    if len(entries) >= MAX_LIST_ENTRIES:
                        truncated = True
                        break
                if truncated:
                    break
        else:
            for child in sorted(target.iterdir(), key=lambda p: p.name):
                if child.name in _HIDDEN_DIRS or not child.resolve().is_relative_to(root):
                    continue
                entries.append(child.name + ("/" if child.is_dir() else ""))
                if len(entries) >= MAX_LIST_ENTRIES:
                    truncated = True
                    break
        text = "\n".join(entries) or "(empty)"
        return text + (f"\n... truncated at {MAX_LIST_ENTRIES} entries" if truncated else "")

    def read_file(self, path: str, start_line: int | None = None, max_lines: int | None = None) -> str:
        target = self.resolve(path)
        if not target.is_file():
            raise PathError(f"not a file: {path}")
        data = target.read_bytes()[:MAX_READ_BYTES]
        if b"\x00" in data[:8000]:
            return "(binary file, not shown)"
        text = data.decode("utf-8", errors="replace")
        if start_line is not None or max_lines is not None:
            lines = text.splitlines()
            start = max(int(start_line or 1), 1) - 1
            count = int(max_lines) if max_lines else len(lines)
            text = "\n".join(lines[start : start + max(count, 0)])
        elif target.stat().st_size > MAX_READ_BYTES:
            text += f"\n... truncated at {MAX_READ_BYTES} bytes; use start_line/max_lines"
        return text

    def search_files(self, query: str, path: str = ".") -> str:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-blank string.")
        needle = query.lower()
        target = self.resolve(path)
        root = self.root.resolve()
        files = [target] if target.is_file() else []
        if target.is_dir():
            for dirpath, dirnames, filenames in os.walk(target, followlinks=False):
                dirnames[:] = sorted(d for d in dirnames if d not in _HIDDEN_DIRS)
                files.extend(Path(dirpath, n) for n in sorted(filenames))
        matches: list[str] = []
        for file in files:
            real = file.resolve()
            if not real.is_relative_to(root) or not real.is_file():
                continue
            if real.stat().st_size > MAX_SEARCH_FILE_BYTES:
                continue
            data = real.read_bytes()
            if b"\x00" in data[:8000]:
                continue
            rel = file.relative_to(root).as_posix()
            for number, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
                if needle in line.lower():
                    matches.append(f"{rel}:{number}: {line.strip()[:300]}")
                    if len(matches) >= MAX_SEARCH_MATCHES:
                        return "\n".join(matches) + f"\n... stopped at {MAX_SEARCH_MATCHES} matches"
        return "\n".join(matches) or "(no matches)"

    # -- writing (restricted to the output folder) ----------------------------

    def write_output(self, file: str, content: str) -> str:
        if file not in OUTPUT_FILES:
            raise PathError(f"file must be one of: {', '.join(OUTPUT_FILES)}.")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("content must be a non-blank string.")
        self._ensure_output_dir()
        (self.output_dir / file).write_text(content.rstrip() + "\n", encoding="utf-8")
        return f"wrote {self.output_rel}/{file}"

    def set_status(self, status: str) -> str:
        try:
            value = Status(status)
        except ValueError:
            allowed = ", ".join(s.value for s in Status)
            raise ValueError(f"status must be one of: {allowed}.") from None
        digest = self._requirement_digest()
        if value in (Status.AWAITING_APPROVAL, Status.COMPLETED) and digest is None:
            raise ValueError(f"write requirement.md before setting status {value.value}.")
        if value is Status.COMPLETED:
            if self.approval_digest is None or not self.approval_replied:
                raise ValueError(
                    "request the user's approval (status awaiting_approval) and wait for their "
                    "reply before setting status completed."
                )
            if digest != self.approval_digest:
                raise ValueError(
                    "requirement.md changed after the approval request; ask for approval again."
                )
        self._ensure_output_dir()
        (self.output_dir / STATUS_FILE).write_text(json.dumps({"status": value.value}) + "\n", encoding="utf-8")
        if value is Status.AWAITING_APPROVAL:
            self.approval_digest, self.approval_replied = digest, False
        return f"status set to {value.value}"

    def read_status(self) -> str | None:
        try:
            return json.loads((self.output_dir / STATUS_FILE).read_text(encoding="utf-8")).get("status")
        except (OSError, ValueError, AttributeError):
            return None

    def _ensure_output_dir(self) -> None:
        out, root = self.output_dir, self.root.resolve()
        # Checked before and after creating it: a symlinked .platform must not lead outside.
        if not out.resolve().is_relative_to(root):
            raise PathError("output folder resolves outside the repository.")
        out.mkdir(parents=True, exist_ok=True)
        if not out.resolve().is_relative_to(root):
            raise PathError("output folder resolves outside the repository.")

    # -- publishing ------------------------------------------------------------

    def publish(self, label: str) -> str:
        """Commit only the output folder and push it to ``ShipAi``."""
        if not isinstance(label, str) or not _PUBLISH_LABEL.fullmatch(label):
            raise ValueError("label must be 'round N questions', 'approval requested', 'completed' or 'failed'.")
        return self.commit_and_push(label)

    def commit_and_push(self, label: str) -> str:
        message = f"requirement-agent({self.feature_id}): {label}"
        try:
            committed = self.git.commit_paths([self.output_rel], message)
            attempts = self.git.push(SOURCE_BRANCH)
        except GitError as exc:
            raise WorkspaceError(f"publishing failed: {exc}") from None
        what = "committed and pushed" if committed else "nothing new to commit; pushed"
        retry = " after one rebase retry" if attempts == 2 else ""
        return f"{what} to {SOURCE_BRANCH}{retry}: {message}"
