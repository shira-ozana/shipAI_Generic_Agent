"""CLI: ``python -m generic_agent requirement --repo-url URL --feature-id ID --requirement-text TEXT``.

Repository credentials are read from ``REQUIREMENT_GIT_USERNAME`` /
``REQUIREMENT_GIT_TOKEN`` (shell or ``.env``), never from command-line
arguments, so they do not appear in shell history or process listings.
After the first turn, type the answers to each question round; ``exit`` quits.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path

from cursor_sdk import close_default_client
from dotenv import dotenv_values
from pydantic import SecretStr

from generic_agent.agents.requirement.agent import PreflightError, RequirementAgent, RequirementTurn
from generic_agent.agents.requirement.inputs import GitCredentials, InputError, RequirementInputs
from generic_agent.config import ConfigError, describe_error, load_settings

USERNAME_VAR = "REQUIREMENT_GIT_USERNAME"
TOKEN_VAR = "REQUIREMENT_GIT_TOKEN"
EXIT_COMMANDS = {"exit", "quit"}


def load_git_credentials(environ: Mapping[str, str] | None = None, env_file: Path | None = None) -> GitCredentials:
    env = os.environ if environ is None else environ
    path = Path.cwd() / ".env" if env_file is None else env_file
    file_values = dotenv_values(path) if path.is_file() else {}

    def read(name: str) -> str:
        return (env.get(name) or file_values.get(name) or "").strip()

    return GitCredentials(username=read(USERNAME_VAR), token=SecretStr(read(TOKEN_VAR)))


def _show(turn: RequirementTurn, output_fn: Callable[[str], None]) -> None:
    output_fn(f"agent> {turn.reply or '(no text in reply)'}")
    output_fn(f"[status: {turn.status or 'unknown'}]")


def run_requirement_session(
    agent: RequirementAgent,
    inputs: RequirementInputs,
    *,
    thread_id: str | None = None,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> int:
    thread_id = thread_id or uuid.uuid4().hex
    try:
        _show(agent.start(thread_id, inputs), output_fn)
    except PreflightError as exc:
        output_fn(f"error: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001
        output_fn(f"error: {describe_error(exc)}")
        return 1
    while True:
        try:
            line = input_fn("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            output_fn("")
            return 0
        if not line:
            continue
        if line.lower() in EXIT_COMMANDS:
            return 0
        try:
            _show(agent.reply(thread_id, line), output_fn)
        except Exception as exc:  # noqa: BLE001 - keep the session alive
            output_fn(f"error: {describe_error(exc)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m generic_agent requirement", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo-url", required=True)
    parser.add_argument("--feature-id", required=True)
    text = parser.add_mutually_exclusive_group(required=True)
    text.add_argument("--requirement-text")
    text.add_argument("--requirement-file", type=Path, help="read requirement_text from a UTF-8 file")
    args = parser.parse_args(argv)
    try:
        requirement_text = (
            args.requirement_file.read_text(encoding="utf-8") if args.requirement_file else args.requirement_text
        )
        inputs = RequirementInputs(
            repo_url=args.repo_url,
            feature_id=args.feature_id,
            requirement_text=requirement_text,
            credentials=load_git_credentials(),
        )
        settings = load_settings()
    except (ConfigError, InputError, OSError) as exc:
        print(f"error: {describe_error(exc) if isinstance(exc, ConfigError) else exc}", file=sys.stderr)
        return 2
    agent = RequirementAgent(settings)
    print("Requirement Agent (type 'exit' to quit)")
    try:
        return run_requirement_session(agent, inputs)
    finally:
        close_default_client()
