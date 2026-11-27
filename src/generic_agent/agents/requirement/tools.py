"""Cursor custom tools bound to one thread's ``Workspace``.

Read tools are confined to the clone; write tools only touch the output folder;
publishing runs git in code. There is no shell tool.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from cursor_sdk import CustomTool, CustomToolContext

from generic_agent.agents.requirement.inputs import Status
from generic_agent.agents.requirement.workspace import OUTPUT_FILES, Workspace

TOOL_NAMES = ("list_files", "read_file", "search_files", "write_output", "set_status", "publish_outputs")


def _string(args: Mapping[str, Any], name: str, default: str | None = None) -> str:
    value = args.get(name, default)
    if not isinstance(value, str):
        raise ValueError(f"'{name}' must be a string.")
    return value


def _optional_int(args: Mapping[str, Any], name: str) -> int | None:
    value = args.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"'{name}' must be an integer.")
    return value


def _tool(execute: Callable[[Mapping[str, Any]], str], description: str, properties: dict, required: list[str]) -> CustomTool:
    def run(args: Mapping[str, Any], context: CustomToolContext) -> str:
        return execute(args or {})

    return CustomTool(
        execute=run,
        description=description,
        input_schema={"type": "object", "properties": properties, "required": required, "additionalProperties": False},
    )


def requirement_tools(workspace: Workspace) -> dict[str, CustomTool]:
    """The Requirement Agent's tools for one workspace."""
    path_prop = {"type": "string", "description": "Path relative to the repository root, e.g. '.platform' or 'src/orders'."}
    return {
        "list_files": _tool(
            lambda a: workspace.list_files(_string(a, "path", "."), bool(a.get("recursive", False))),
            "List a directory of the cloned repository (read-only). Directories end with '/'.",
            {"path": path_prop, "recursive": {"type": "boolean", "description": "List all files below the path."}},
            [],
        ),
        "read_file": _tool(
            lambda a: workspace.read_file(_string(a, "path"), _optional_int(a, "start_line"), _optional_int(a, "max_lines")),
            "Read a text file of the cloned repository (read-only), optionally a line range.",
            {
                "path": path_prop,
                "start_line": {"type": "integer", "minimum": 1},
                "max_lines": {"type": "integer", "minimum": 1},
            },
            ["path"],
        ),
        "search_files": _tool(
            lambda a: workspace.search_files(_string(a, "query"), _string(a, "path", ".")),
            "Case-insensitive text search in the cloned repository (read-only). Returns path:line: text.",
            {"query": {"type": "string"}, "path": path_prop},
            ["query"],
        ),
        "write_output": _tool(
            lambda a: workspace.write_output(_string(a, "file"), _string(a, "content")),
            "Write the full content of requirement.md or clarifications.md in this feature's output folder.",
            {"file": {"type": "string", "enum": list(OUTPUT_FILES)}, "content": {"type": "string"}},
            ["file", "content"],
        ),
        "set_status": _tool(
            lambda a: workspace.set_status(_string(a, "status")),
            "Set this feature's status.json to one of the allowed status values.",
            {"status": {"type": "string", "enum": [s.value for s in Status]}},
            ["status"],
        ),
        "publish_outputs": _tool(
            lambda a: workspace.publish(_string(a, "label")),
            "Commit only this feature's output folder and push it to the ShipAi branch. "
            "label is 'round N questions', 'approval requested', 'completed' or 'failed'.",
            {"label": {"type": "string", "pattern": "^(round [1-9][0-9]{0,2} questions|approval requested|completed|failed)$"}},
            ["label"],
        ),
    }
