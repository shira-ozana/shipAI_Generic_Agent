"""``status.json`` files: the architect writes its own and reads the requirement agent's."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

AGENT_NAME = "architect"

# Values the Requirements Agent may write when it finished successfully.
# Keep in sync with the Requirements Agent's status contract.
REQUIREMENT_DONE_STATUSES = {"completed", "done", "approved", "success"}


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def read_status(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must contain a JSON object.")
    return data


def build_architect_status(
    *,
    feature_id: str,
    status: str,
    started_at: str,
    model: str,
    revision: int,
    components: list[dict[str, str]],
    requirement_path: str = "",
) -> dict[str, Any]:
    return {
        "agent": AGENT_NAME,
        "feature_id": feature_id,
        "requirement": requirement_path,
        "status": status,
        "started_at": started_at,
        "completed_at": now_iso(),
        "model": model,
        "revision": revision,
        "hld": "HLD.md",
        "components": components,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
