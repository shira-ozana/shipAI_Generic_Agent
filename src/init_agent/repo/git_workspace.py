"""Git working copy for one INIT run.

Flow: clone REPO_URL (public) at REPO_BASE_REF -> create/checkout branch `shipAI`
-> INIT writes under platform/ -> commit -> push the branch.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse, urlunparse


class GitError(RuntimeError):
    pass


def _redact(text: str) -> str:
    return re.sub(r"(https?://)[^@/\s]+@", r"\1***@", text)


def _run(args: list[str], cwd: Path | None = None, timeout: int = 600) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if proc.returncode != 0:
        raise GitError(_redact(f"git {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}"))
    return proc.stdout.strip()


def _with_token(url: str, token: str) -> str:
    if not token or not url.startswith("https://"):
        return url
    parts = urlparse(url)
    return urlunparse(parts._replace(netloc=f"x-access-token:{token}@{parts.hostname}"))


@dataclass
class GitWorkspace:
    path: Path
    branch: str
    base_ref: str
    base_sha: str

    # ------------------------------------------------------------------ setup
    @classmethod
    def prepare(cls, repo_url: str, base_ref: str, branch: str, root: Path, run_id: str,
                push_url: str = "", token: str = "") -> "GitWorkspace":
        """Clone into <root>/<run_id>. Reuse remote `branch` if it already exists on the push remote."""
        if not repo_url:
            raise GitError("REPO_URL is not set (.env) and the request has no project.repo_url")
        path = (root / run_id).resolve()
        if path.exists():
            def _force(func, p, _exc):          # Windows: .git objects are read-only
                os.chmod(p, 0o700)
                func(p)
            shutil.rmtree(path, onerror=_force)
        path.parent.mkdir(parents=True, exist_ok=True)
        _run(["clone", "--no-tags", "--branch", base_ref, repo_url, str(path)])
        base_sha = _run(["rev-parse", "HEAD"], cwd=path)

        if push_url:
            _run(["remote", "add", "push", _with_token(push_url, token)], cwd=path)
        remote = "push" if push_url else "origin"
        try:
            _run(["fetch", remote, branch], cwd=path)
            _run(["checkout", "-B", branch, "FETCH_HEAD"], cwd=path)   # continue an existing shipAI branch
        except GitError:
            _run(["checkout", "-b", branch], cwd=path)                  # new branch from base_ref
        return cls(path=path, branch=branch, base_ref=base_ref, base_sha=base_sha)

    # ------------------------------------------------------------------ info
    def head_sha(self) -> str:
        return _run(["rev-parse", "HEAD"], cwd=self.path)

    # ------------------------------------------------------------------ publish
    def commit(self, paths: list[str], message: str, author_name: str, author_email: str) -> str | None:
        if not paths:
            return None
        _run(["add", "--", *paths], cwd=self.path)
        if not _run(["diff", "--cached", "--name-only"], cwd=self.path):
            return None
        _run(["-c", f"user.name={author_name}", "-c", f"user.email={author_email}",
              "commit", "-m", message], cwd=self.path)
        return self.head_sha()

    def push(self, token: str = "", push_url: str = "") -> None:
        remote = "push" if push_url else "origin"
        if not push_url and token:
            origin = _run(["remote", "get-url", "origin"], cwd=self.path)
            _run(["remote", "set-url", "--push", "origin", _with_token(origin, token)], cwd=self.path)
        _run(["push", "-u", remote, self.branch], cwd=self.path)
