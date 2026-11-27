"""Offline fixtures: a local bare git repo stands in for GitHub; a fake LLM for Cursor."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

FEATURE_ID = "82441f43-1747-4ffc-8581-8e4fcefaa453"
REPO_URL = "https://github.com/acme/shop"


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout.strip()


def seed_remote(
    tmp_path: Path,
    requirement_status: str | None = "completed",
    with_requirement: bool = True,
    folder: str = "requierment",
    file_name: str = "requirement.md",
    branch: str = "ShipAi",
    feature_id: str = FEATURE_ID,
) -> Path:
    remote = tmp_path / "remote.git"
    remote.mkdir()
    git("init", "--bare", cwd=remote)
    work = tmp_path / "seed"
    work.mkdir()
    git("init", cwd=work)
    (work / "README.md").write_text("# Shop\nA FastAPI backend and an Angular frontend.\n")
    (work / "backend").mkdir()
    (work / "backend" / "main.py").write_text("app = None\n")
    req_dir = work / ".platform" / feature_id / folder
    if with_requirement:
        req_dir.mkdir(parents=True)
        (req_dir / file_name).write_text("# Requirement\nUsers can reset their password.\n")
        (req_dir / "clarifications.md").write_text("Q: Which users? A: Registered users only.\n")
        if requirement_status is not None:
            (req_dir / "status.json").write_text(json.dumps({"status": requirement_status}))
    git("add", ".", cwd=work)
    git("commit", "-m", "seed", cwd=work)
    git("branch", "-M", "main", cwd=work)
    git("remote", "add", "origin", str(remote), cwd=work)
    git("push", "origin", "main", cwd=work)
    git("symbolic-ref", "HEAD", "refs/heads/main", cwd=remote)
    if branch != "main":
        # Like the real project repo: .platform lives on the agents' branch, not main.
        git("checkout", "-b", branch, cwd=work)
        (work / "agents-branch.txt").write_text("ShipAi\n")
        git("add", ".", cwd=work)
        git("commit", "-m", "agents branch", cwd=work)
        git("push", "origin", branch, cwd=work)
        git("checkout", "main", cwd=work)
        if (work / ".platform").exists():
            git("rm", "-r", "--quiet", ".platform", cwd=work)
            git("commit", "-m", "main has no .platform", cwd=work)
            git("push", "origin", "main", cwd=work)
    return remote


def read_remote(tmp_path: Path, remote: Path, branch: str = "ShipAi") -> Path:
    out = tmp_path / f"check-{branch}"
    subprocess.run(["git", "clone", "--branch", branch, str(remote), str(out)], check=True, capture_output=True)
    return out


def design_json(paths: tuple[str, ...] = ("backend/api", "frontend/web"), overall: str = "# Feature HLD") -> str:
    return json.dumps(
        {
            "overall_hld": overall,
            "components": [{"architecture_path": p, "name": p.split("/")[-1], "hld": f"# {p} HLD"} for p in paths],
        }
    )


def analysis_json(questions: tuple[str, ...] = ("Which email provider?",)) -> str:
    return json.dumps(
        {
            "summary": "Password reset by email.",
            "functional_requirements": ["FR-1: A user can request a reset link."],
            "non_functional_requirements": ["NFR-1: Link expires in 30 minutes."],
            "assumptions": ["Emails are sent by the backend."],
            "questions": list(questions),
        }
    )


class FakeLlm:
    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.messages: list[str] = []
        self.repo_dirs: list[Path] = []

    def __call__(self, message: str, repo_dir: Path) -> str:
        self.messages.append(message)
        self.repo_dirs.append(repo_dir)
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


@pytest.fixture
def remote(tmp_path: Path) -> Path:
    return seed_remote(tmp_path)
