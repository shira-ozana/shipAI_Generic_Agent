"""Clone, commit and push with the system ``git``.

Credentials go in a per-command ``http.extraHeader`` so they are never written to
the clone's ``.git/config`` or remote URL, and are redacted from error messages.
"""

from __future__ import annotations

import base64
import subprocess
from collections.abc import Sequence
from pathlib import Path

from agent_architect.credentials import GitHubCredentials


class GitError(RuntimeError):
    """A git command failed."""


def _auth_args(creds: GitHubCredentials | None) -> tuple[list[str], list[str]]:
    if creds is None:
        return [], []
    token = creds.token.get_secret_value()
    basic = base64.b64encode(f"{creds.github_user}:{token}".encode()).decode()
    return ["-c", f"http.extraHeader=Authorization: Basic {basic}"], [token, basic]


def run_git(args: Sequence[str], cwd: Path | None = None, creds: GitHubCredentials | None = None) -> str:
    auth, secrets = _auth_args(creds)
    proc = subprocess.run(
        ["git", *auth, *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if proc.returncode != 0:
        message = (proc.stderr or proc.stdout).strip()
        for secret in secrets:
            message = message.replace(secret, "***")
        raise GitError(f"git {args[0]} failed: {message}")
    return proc.stdout.strip()


def clone(url: str, dest: Path, creds: GitHubCredentials | None, branch: str | None = None) -> None:
    args = ["clone", "--depth", "1"] + (["--branch", branch] if branch else []) + [url, str(dest)]
    run_git(args, creds=creds)


def commit_and_push(
    repo_dir: Path,
    paths: Sequence[str],
    message: str,
    creds: GitHubCredentials | None,
    author_name: str,
    author_email: str,
    target_branch: str | None = None,
) -> str | None:
    """Commit ``paths`` and push. Returns the commit SHA, or None if nothing changed."""
    if target_branch:
        run_git(["checkout", "-B", target_branch], cwd=repo_dir)
    run_git(["add", "--all", "--", *paths], cwd=repo_dir)
    staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=repo_dir)
    if staged.returncode == 0:
        return None
    run_git(
        ["-c", f"user.name={author_name}", "-c", f"user.email={author_email}", "commit", "-m", message],
        cwd=repo_dir,
    )
    branch = target_branch or run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_dir)
    run_git(["push", "origin", f"HEAD:refs/heads/{branch}"], cwd=repo_dir, creds=creds)
    return run_git(["rev-parse", "HEAD"], cwd=repo_dir)
