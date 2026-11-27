"""CLI: ``python -m generic_agent`` (add ``--list-models`` to list model IDs).

``python -m generic_agent requirement ...`` runs the Requirement Agent
(see ``generic_agent.agents.requirement.cli``).
"""

from __future__ import annotations

import argparse
import sys
import uuid
from collections.abc import Callable
from typing import Any

from cursor_sdk import close_default_client
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from generic_agent.config import (
    ConfigError,
    describe_error,
    list_model_ids,
    load_settings,
)
from generic_agent.graph import build_agent, load_default_prompt
from generic_agent.tools import default_tools

EXIT_COMMANDS = {"exit", "quit"}


def run_session(
    agent: Any,
    *,
    thread_id: str | None = None,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> None:
    """Chat loop. Each turn sends only the new user message on one thread."""
    config = {"configurable": {"thread_id": thread_id or uuid.uuid4().hex}}
    while True:
        try:
            line = input_fn("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            output_fn("")
            return
        if not line:
            continue
        if line.lower() in EXIT_COMMANDS:
            return
        try:
            result = agent.invoke({"messages": [HumanMessage(content=line)]}, config)
        except Exception as exc:  # noqa: BLE001 - keep the session alive
            output_fn(f"error: {describe_error(exc)}")
            continue
        text = result["messages"][-1].text.strip()
        output_fn(f"agent> {text or '(no text in reply)'}")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "requirement":
        from generic_agent.agents.requirement.cli import main as requirement_main

        return requirement_main(argv[1:])
    parser = argparse.ArgumentParser(prog="python -m generic_agent", description=__doc__)
    parser.add_argument(
        "--list-models", action="store_true", help="print model IDs available to your Cursor account"
    )
    args = parser.parse_args(argv)
    try:
        if args.list_models:
            for model_id, display_name in list_model_ids(load_settings(require_model=False)):
                print(f"{model_id}\t{display_name}")
            return 0
        settings = load_settings()
    except ConfigError as exc:
        print(f"error: {describe_error(exc)}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"error: {describe_error(exc)}", file=sys.stderr)
        return 1
    agent = build_agent(
        settings, load_default_prompt(), default_tools(), checkpointer=InMemorySaver()
    )
    print("generic-langgraph-agent (type 'exit' to quit)")
    try:
        run_session(agent)
    finally:
        close_default_client()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
