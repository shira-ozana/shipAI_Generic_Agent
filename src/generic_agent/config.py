"""Cursor configuration and error translation (reads the environment and `.env`)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import cursor_sdk
from cursor_sdk import Cursor
from dotenv import dotenv_values
from pydantic import SecretStr

API_KEY_VAR = "CURSOR_API_KEY"
MODEL_VAR = "CURSOR_MODEL"
_PLACEHOLDER_PREFIX = "your-"


class ConfigError(RuntimeError):
    """Raised when required Cursor configuration is missing or still a placeholder."""


class CursorRunError(RuntimeError):
    """Raised when a Cursor run started but did not finish successfully."""


@dataclass(frozen=True)
class Settings:
    """Cursor settings. The key is a ``SecretStr`` and hidden from ``repr``."""

    api_key: SecretStr = field(repr=False)
    model: str


def load_settings(
    environ: Mapping[str, str] | None = None,
    env_file: Path | None = None,
    *,
    require_model: bool = True,
) -> Settings:
    """Read settings from the environment, falling back to a local ``.env``.

    Non-empty shell variables take precedence over ``.env`` values. Without
    ``env_file``, ``./.env`` in the current directory is used if it exists.
    """
    env = os.environ if environ is None else environ
    path = Path.cwd() / ".env" if env_file is None else env_file
    file_values = dotenv_values(path) if path.is_file() else {}

    def read(name: str) -> str:
        return (env.get(name) or file_values.get(name) or "").strip()

    def missing(value: str) -> bool:
        return not value or value.startswith(_PLACEHOLDER_PREFIX)

    api_key, model = read(API_KEY_VAR), read(MODEL_VAR)
    problems = []
    if missing(api_key):
        problems.append(
            f"{API_KEY_VAR} is not set. Create a key at https://cursor.com/dashboard/integrations"
        )
    if require_model and missing(model):
        problems.append(
            f"{MODEL_VAR} is not set. Run `python -m generic_agent --list-models` "
            "to see the model IDs available to your account"
        )
    if problems:
        raise ConfigError(
            "; ".join(problems)
            + ". Put them in a local .env file (copy .env.example to .env) "
            "or export them in your shell."
        )
    return Settings(api_key=SecretStr(api_key), model="" if missing(model) else model)


def list_model_ids(settings: Settings) -> list[tuple[str, str]]:
    """Return ``(model_id, display_name)`` pairs from ``Cursor.models.list()``."""
    models = Cursor.models.list(api_key=settings.api_key.get_secret_value())
    return [(m.id, m.display_name) for m in models]


def describe_error(exc: BaseException) -> str:
    """Turn a failure into an actionable message. Never includes credentials."""
    if isinstance(exc, (ConfigError, CursorRunError)):
        return str(exc)
    if isinstance(exc, cursor_sdk.AuthenticationError):
        return (
            f"Cursor rejected the API key. Check {API_KEY_VAR} (a valid Cursor user "
            "or service-account key from https://cursor.com/dashboard/integrations)."
        )
    if isinstance(exc, cursor_sdk.PermissionDeniedError):
        return (
            f"Your Cursor account is not allowed to use this model or feature. "
            f"Check {MODEL_VAR} and your plan or team permissions."
        )
    if isinstance(exc, cursor_sdk.NotFoundError):
        return (
            f"Cursor model or resource not found. Check {MODEL_VAR} against "
            "`python -m generic_agent --list-models`."
        )
    if isinstance(exc, cursor_sdk.NetworkError):
        return (
            "Could not reach Cursor (network, proxy or timeout). Check your "
            "internet connection and try again."
        )
    if isinstance(exc, cursor_sdk.RateLimitError):
        return "Cursor rate limit reached. Wait a moment and try again."
    if isinstance(exc, cursor_sdk.BadRequestError):
        return f"Cursor rejected the request: {exc.message}"
    if isinstance(exc, cursor_sdk.CursorAgentError):
        hint = " It is safe to retry." if exc.is_retryable else ""
        return f"Cursor SDK error ({type(exc).__name__}): {exc.message}.{hint}"
    return f"{type(exc).__name__}: {exc}"
