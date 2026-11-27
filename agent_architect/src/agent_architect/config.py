"""Architect settings: Supabase, encryption key, git options (shell or ``.env``).

Cursor settings (``CURSOR_API_KEY`` / ``CURSOR_MODEL``) are loaded by the generic
agent's own ``generic_agent.config.load_settings``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values
from pydantic import SecretStr

_PLACEHOLDER_PREFIX = "your-"


class ArchitectConfigError(RuntimeError):
    """Raised when required architect configuration is missing."""


@dataclass(frozen=True)
class ArchitectSettings:
    supabase_url: str
    supabase_service_role_key: SecretStr = field(repr=False)
    encryption_key: SecretStr = field(repr=False)
    source_branch: str = "ShipAi"
    target_branch: str | None = None
    git_author_name: str = "SDLC Architect Agent"
    git_author_email: str = "architect-agent@sdlc.local"


def load_architect_settings(
    environ: Mapping[str, str] | None = None, env_file: Path | None = None
) -> ArchitectSettings:
    env = os.environ if environ is None else environ
    path = Path.cwd() / ".env" if env_file is None else env_file
    file_values = dotenv_values(path) if path.is_file() else {}

    def read(name: str) -> str:
        return (env.get(name) or file_values.get(name) or "").strip()

    required = ["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "ENCRYPTION_KEY"]
    missing = [n for n in required if not read(n) or read(n).startswith(_PLACEHOLDER_PREFIX)]
    if missing:
        raise ArchitectConfigError(
            f"Missing configuration: {', '.join(missing)}. "
            "Copy .env.example to .env and fill in the values."
        )
    return ArchitectSettings(
        supabase_url=read("SUPABASE_URL"),
        supabase_service_role_key=SecretStr(read("SUPABASE_SERVICE_ROLE_KEY")),
        encryption_key=SecretStr(read("ENCRYPTION_KEY")),
        source_branch=read("ARCHITECT_SOURCE_BRANCH") or "ShipAi",
        target_branch=read("ARCHITECT_TARGET_BRANCH") or None,
        git_author_name=read("GIT_AUTHOR_NAME") or "SDLC Architect Agent",
        git_author_email=read("GIT_AUTHOR_EMAIL") or "architect-agent@sdlc.local",
    )
