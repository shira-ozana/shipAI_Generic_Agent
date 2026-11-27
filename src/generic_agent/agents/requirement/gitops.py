"""Git operations for the Requirement Agent: clone, commit the output folder, push.

Credentials are supplied to git only through the child process environment and
a credential helper given with ``git -c`` (a per-command option that is never
written to ``.git/config``). Remote URLs never contain credentials, and every
message leaving this module is scrubbed of secret values.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

from generic_agent.agents.requirement.inputs import SOURCE_BRANCH, GitCredentials

_USER_VAR = "GENERIC_AGENT_GIT_USERNAME"
_TOKEN_VAR = "GENERIC_AGENT_GIT_TOKEN"
# Runs under git's shell; reads the values from the environment, so the
# command line holds no secret.
_CREDENTIAL_HELPER = (
    f'!f() {{ test "$1" = get || exit 0; echo "username=${{{_USER_VAR}:-x-access-token}}"; '
    f'echo "password=${{{_TOKEN_VAR}}}"; }}; f'
)
COMMIT_AUTHOR = ("Requirement Agent", "requirement-agent@localhost")
GIT_TIMEOUT_SECONDS = 300


class GitError(RuntimeError):
    """A git command failed. The message never contains credentials."""


def scrub(text: str, secrets: Sequence[str]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


class GitRepo:
    """Runs git in one working directory with optional in-memory credentials."""

    def __init__(self, path: Path, credentials: GitCredentials | None = None) -> None:
        self.path = Path(path)
        self._credentials = credentials or GitCredentials()

    # -- plumbing -------------------------------------------------------------

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        env.pop(_USER_VAR, None)
        env.pop(_TOKEN_VAR, None)
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GCM_INTERACTIVE"] = "never"
        if self._credentials.present:
            env[_USER_VAR] = self._credentials.username
            env[_TOKEN_VAR] = self._credentials.token.get_secret_value()
        return env

    def _base_args(self) -> list[str]:
        name, email = COMMIT_AUTHOR
        args = ["git", "-c", f"user.name={name}", "-c", f"user.email={email}"]
        if self._credentials.present:
            # The empty helper first clears any configured helpers for this command.
            args += ["-c", "credential.helper=", "-c", f"credential.helper={_CREDENTIAL_HELPER}"]
        return args

    def run(self, *args: str, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
        command = [*self._base_args(), *args]
        try:
            proc = subprocess.run(
                command,
                cwd=str(cwd or self.path),
                env=self._env(),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=GIT_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitError(f"git {args[0]} could not run ({type(exc).__name__}).") from None
        if check and proc.returncode != 0:
            raise GitError(self._describe(args[0], proc))
        return proc

    def _describe(self, verb: str, proc: subprocess.CompletedProcess) -> str:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        tail = " ".join(detail[-3:])[:500]
        return scrub(f"git {verb} failed: {tail}" if tail else f"git {verb} failed.", self._credentials.secrets())

    # -- operations -----------------------------------------------------------

    def clone(self, repo_url: str, branch: str = SOURCE_BRANCH) -> None:
        """Clone ``branch`` of ``repo_url`` into ``self.path`` (which must not exist yet)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.run(
            "clone", "--branch", branch, "--single-branch", "--", repo_url, str(self.path),
            cwd=self.path.parent,
        )

    def commit_paths(self, paths: Sequence[str], message: str) -> bool:
        """Commit only ``paths``. Returns ``False`` when there was nothing to commit."""
        self.run("add", "--all", "--", *paths)
        staged = self.run("diff", "--cached", "--quiet", "--", *paths, check=False)
        if staged.returncode == 0:
            return False
        self.run("commit", "--no-verify", "-m", message, "--", *paths)
        return True

    def push(self, branch: str = SOURCE_BRANCH) -> int:
        """Push ``HEAD`` to ``branch``; on rejection rebase once and retry. Never forces.

        Returns the number of push attempts (1 or 2).
        """
        first = self.run("push", "origin", f"HEAD:refs/heads/{branch}", check=False)
        if first.returncode == 0:
            return 1
        self.run("pull", "--rebase", "--no-edit", "origin", branch)
        self.run("push", "origin", f"HEAD:refs/heads/{branch}")
        return 2
