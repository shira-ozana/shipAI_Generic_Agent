"""Paths inside the project repository's ``.platform`` folder."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

PLATFORM_DIR = ".platform"
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class InvalidPathError(ValueError):
    """An identifier or architecture path cannot be used as a folder name."""


# Same rule as the Requirement Agent (generic_agent.agents.requirement.inputs).
_FEATURE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")


def validate_feature_id(feature_id: str) -> str:
    if not isinstance(feature_id, str) or not _FEATURE_ID.fullmatch(feature_id) or ".." in feature_id:
        raise InvalidPathError(
            f"feature_id must be 1-100 letters, digits, '.', '_' or '-', got {feature_id!r}"
        )
    return feature_id


def validate_architecture_path(path: str) -> str:
    """Normalise ``backend/api``-style paths; reject anything that could escape the folder."""
    cleaned = path.strip().replace("\\", "/").strip("/")
    segments = cleaned.split("/") if cleaned else []
    if not segments:
        raise InvalidPathError("architecture_path is empty")
    for segment in segments:
        if segment in {".", ".."} or segment.lower() == ".git" or not _SAFE_SEGMENT.match(segment):
            raise InvalidPathError(f"Invalid architecture_path segment {segment!r} in {path!r}")
    return "/".join(segments)


def feature_dir(feature_id: str) -> PurePosixPath:
    return PurePosixPath(PLATFORM_DIR, feature_id)


# The Requirements Agent's spec writes ".platform/{feature_id}/requierment/requierment.md"
# (sic). Accept that spelling and the correct one, case-insensitively.
REQUIREMENT_DIR_NAMES = ("requierment", "requirement", "requirements")
REQUIREMENT_FILE_NAMES = ("requierment.md", "requirement.md", "requirements.md")


def requirement_dir_candidates(feature_id: str) -> list[PurePosixPath]:
    return [feature_dir(feature_id) / name for name in REQUIREMENT_DIR_NAMES]


CLARIFICATIONS_FILE = "clarifications.md"


def find_requirement_files(repo_dir: Path, feature_id: str) -> tuple[Path, Path] | None:
    """Return ``(requirement.md, status.json)`` written by the Requirements Agent, or None."""
    base = repo_dir / feature_dir(feature_id)
    if not base.is_dir():
        return None
    dirs = {d.name.lower(): d for d in base.iterdir() if d.is_dir()}
    for dir_name in REQUIREMENT_DIR_NAMES:
        folder = dirs.get(dir_name)
        if folder is None:
            continue
        files = {f.name.lower(): f for f in folder.iterdir() if f.is_file()}
        for file_name in REQUIREMENT_FILE_NAMES:
            if file_name in files:
                return files[file_name], folder / "status.json"
    return None


def init_file() -> PurePosixPath:
    return PurePosixPath(PLATFORM_DIR, "init", "init.md")


def architect_dir(feature_id: str) -> PurePosixPath:
    return feature_dir(feature_id) / "architect"


def architect_hld_file(feature_id: str) -> PurePosixPath:
    return architect_dir(feature_id) / "HLD.md"


def architect_status_file(feature_id: str) -> PurePosixPath:
    return architect_dir(feature_id) / "status.json"


def component_hld_file(feature_id: str, architecture_path: str) -> PurePosixPath:
    return architect_dir(feature_id) / "components" / validate_architecture_path(architecture_path) / "HLD.md"
