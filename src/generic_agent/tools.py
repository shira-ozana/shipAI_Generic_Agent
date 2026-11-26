"""Local Cursor SDK custom tools. Register new tools in ``default_tools``."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from cursor_sdk import CustomTool, CustomToolContext


def count_words(text: str) -> int:
    """Count whitespace-separated words."""
    return len(text.split())


def _run_word_count(args: Mapping[str, Any], context: CustomToolContext) -> str:
    text = args.get("text")
    if not isinstance(text, str):
        raise ValueError("word_count requires a string argument named 'text'.")
    return str(count_words(text))


WORD_COUNT = CustomTool(
    execute=_run_word_count,
    description="Count the whitespace-separated words in a piece of text.",
    input_schema={
        "type": "object",
        "properties": {"text": {"type": "string", "description": "The text to count."}},
        "required": ["text"],
    },
)


def default_tools() -> dict[str, CustomTool]:
    """Tools used by the CLI and Studio. Pass ``{}`` for a text-only agent."""
    return {"word_count": WORD_COUNT}
