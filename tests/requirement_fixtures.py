"""Local git fixtures for Requirement Agent tests: a bare remote with a ShipAi branch."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from cursor_sdk import CustomToolContext

GIT_TOKEN = "ghp-test-token-value-4815162342"


def git(*args: str, cwd: Path) -> str:
    proc = subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@localhost", *args],
        cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", check=True,
    )
    return proc.stdout


def make_remote(tmp_path: Path, *, with_init: bool = True, branch: str = "ShipAi") -> Path:
    """Create a bare repository whose ``branch`` holds a small system."""
    seed = tmp_path / "seed"
    seed.mkdir()
    git("init", "-q", "-b", branch, cwd=seed)
    (seed / "src").mkdir()
    (seed / "src" / "orders.py").write_text("STATES = ['placed', 'paid', 'shipped']\n", encoding="utf-8")
    if with_init:
        (seed / ".platform").mkdir()
        (seed / ".platform" / "overview.md").write_text(
            "# System\n\nOrders move through placed, paid, shipped.\n", encoding="utf-8"
        )
    git("add", "-A", cwd=seed)
    git("commit", "-q", "-m", "seed", cwd=seed)
    remote = tmp_path / "remote.git"
    git("clone", "-q", "--bare", str(seed), str(remote), cwd=tmp_path)
    return remote


def remote_file(remote: Path, path: str, branch: str = "ShipAi") -> str:
    return git("show", f"{branch}:{path}", cwd=remote)


def remote_status(remote: Path, feature_id: str) -> str:
    return json.loads(remote_file(remote, f".platform/{feature_id}/requierment/status.json"))["status"]


def call(options, name: str, **args) -> str:
    """Call a registered custom tool the way Cursor would."""
    return options.local.custom_tools[name].execute(args, CustomToolContext(tool_call_id="call"))
