"""Requirement Agent inputs, settings and their validation (no I/O)."""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import SecretStr

SOURCE_BRANCH = "ShipAi"
"""Branch the repository is cloned from and pushed to."""

OUTPUT_DIR_TEMPLATE = ".platform/{feature_id}/requierment"
"""Output folder inside the clone (``requierment`` is the intended spelling)."""

DEFAULT_INIT_OUTPUT_GLOB = ".platform/**/*.md"
"""Default Init-output check: at least one Markdown file under ``.platform/``
(excluding this feature's own output folder)."""

WORKDIR_VAR = "REQUIREMENT_AGENT_WORKDIR"
INIT_GLOB_VAR = "REQUIREMENT_AGENT_INIT_GLOB"

_FEATURE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
_THREAD_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


class Status(StrEnum):
    IN_PROGRESS = "in_progress"
    AWAITING_USER = "awaiting_user"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"


class InputError(ValueError):
    """An input is missing or invalid. The message is safe to show to the UI."""


@dataclass(frozen=True)
class GitCredentials:
    """Repository credentials. Never stored in graph state, files or logs."""

    username: str = field(default="", repr=False)
    token: SecretStr = field(default_factory=lambda: SecretStr(""), repr=False)

    @property
    def present(self) -> bool:
        return bool(self.token.get_secret_value())

    def secrets(self) -> tuple[str, ...]:
        """Values that must be scrubbed from any text leaving the git layer."""
        return tuple(v for v in (self.token.get_secret_value(),) if v)


@dataclass(frozen=True)
class RequirementInputs:
    repo_url: str
    feature_id: str
    requirement_text: str
    credentials: GitCredentials = field(default_factory=GitCredentials, repr=False)

    def __post_init__(self) -> None:
        validate_repo_url(self.repo_url)
        validate_feature_id(self.feature_id)
        if not isinstance(self.requirement_text, str) or not self.requirement_text.strip():
            raise InputError("requirement_text is required.")

    @property
    def output_dir(self) -> str:
        return OUTPUT_DIR_TEMPLATE.format(feature_id=self.feature_id)


@dataclass(frozen=True)
class RequirementSettings:
    """Where per-thread clones live and how Init output is detected."""

    workdir: Path
    init_output_glob: str = DEFAULT_INIT_OUTPUT_GLOB


def load_requirement_settings(environ: Mapping[str, str] | None = None) -> RequirementSettings:
    env = os.environ if environ is None else environ
    workdir = (env.get(WORKDIR_VAR) or "").strip()
    glob = (env.get(INIT_GLOB_VAR) or "").strip() or DEFAULT_INIT_OUTPUT_GLOB
    return RequirementSettings(
        workdir=Path(workdir) if workdir else Path(tempfile.gettempdir()) / "generic-agent-requirement",
        init_output_glob=glob,
    )


def validate_feature_id(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InputError("feature_id is required.")
    if not _FEATURE_ID.fullmatch(value) or value.endswith(".") or ".." in value:
        raise InputError(
            "feature_id must be a single path segment: letters, digits, '.', '_' or '-', "
            "starting with a letter or digit (max 100 characters)."
        )
    return value


def validate_thread_id(value: object) -> str:
    if not isinstance(value, str) or not _THREAD_ID.fullmatch(value) or ".." in value:
        raise InputError("thread id must be letters, digits, '.', '_' or '-'.")
    return value


def validate_repo_url(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InputError("repo_url is required.")
    # git receives an argument list (after "--"), so spaces are harmless; control
    # characters and a leading "-" (option injection) are not.
    if value != value.strip() or value.startswith("-") or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise InputError("repo_url is not a valid repository URL.")
    parts = urlsplit(value)
    if parts.scheme in {"http", "https"} and (parts.username or parts.password):
        raise InputError(
            "repo_url must not contain credentials; pass them separately as repository credentials."
        )
    if parts.scheme in {"http", "https"} and not parts.netloc:
        raise InputError("repo_url is not a valid repository URL.")
    return value
