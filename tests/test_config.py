from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import cursor_sdk
import pytest

from generic_agent.config import (
    ConfigError,
    CursorRunError,
    describe_error,
    RepositoryConfig,
    list_model_ids,
    load_settings,
    load_task_split_settings,
)

SECRET = "cursor-test-secret-value"


def env_file(tmp_path: Path, text: str) -> Path:
    path = tmp_path / ".env"
    path.write_text(text)
    return path


def test_env_file_is_used_when_shell_is_empty(tmp_path: Path) -> None:
    path = env_file(tmp_path, f"CURSOR_API_KEY={SECRET}\nCURSOR_MODEL=model-a\n")
    settings = load_settings({}, path)
    assert settings.api_key.get_secret_value() == SECRET
    assert settings.model == "model-a"


def test_shell_variables_take_precedence_over_env_file(tmp_path: Path) -> None:
    path = env_file(tmp_path, "CURSOR_API_KEY=from-file\nCURSOR_MODEL=file-model\n")
    settings = load_settings({"CURSOR_API_KEY": SECRET, "CURSOR_MODEL": "shell-model"}, path)
    assert settings.api_key.get_secret_value() == SECRET
    assert settings.model == "shell-model"


def test_missing_values_raise_actionable_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as info:
        load_settings({}, tmp_path / "missing.env")
    message = str(info.value)
    assert "CURSOR_API_KEY" in message and "CURSOR_MODEL" in message
    assert "--list-models" in message and ".env" in message


def test_placeholders_from_example_file_are_rejected() -> None:
    example = Path(__file__).parent.parent / ".env.example"
    with pytest.raises(ConfigError):
        load_settings({}, example)


def test_model_is_optional_when_listing_models(tmp_path: Path) -> None:
    settings = load_settings({"CURSOR_API_KEY": SECRET}, tmp_path / "x", require_model=False)
    assert settings.model == ""


def test_secret_is_not_in_repr(tmp_path: Path) -> None:
    settings = load_settings({"CURSOR_API_KEY": SECRET, "CURSOR_MODEL": "m"}, tmp_path / "x")
    assert SECRET not in repr(settings)


def test_list_model_ids_uses_cursor_models_list(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []

    def fake_list(*, api_key: str):
        calls.append(api_key)
        return [SimpleNamespace(id="model-a", display_name="Model A")]

    monkeypatch.setattr("generic_agent.config.Cursor", SimpleNamespace(models=SimpleNamespace(list=fake_list)))
    settings = load_settings({"CURSOR_API_KEY": SECRET}, tmp_path / "x", require_model=False)

    assert list_model_ids(settings) == [("model-a", "Model A")]
    assert calls == [SECRET]


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (cursor_sdk.AuthenticationError("bad key"), "CURSOR_API_KEY"),
        (cursor_sdk.PermissionDeniedError("nope"), "CURSOR_MODEL"),
        (cursor_sdk.NotFoundError("no model"), "--list-models"),
        (cursor_sdk.NetworkError("down"), "Could not reach"),
        (cursor_sdk.APITimeoutError("slow"), "Could not reach"),
        (cursor_sdk.RateLimitError("slow down"), "rate limit"),
        (cursor_sdk.ConfigurationError("odd"), "odd"),
        (cursor_sdk.InternalServerError("boom", is_retryable=True), "safe to retry"),
        (ConfigError("missing thing"), "missing thing"),
        (CursorRunError("run failed"), "run failed"),
    ],
)
def test_describe_error_is_actionable(exc: BaseException, expected: str) -> None:
    assert expected in describe_error(exc)


# --- Task Split backend settings -------------------------------------------------


def test_task_split_is_disabled_without_repositories(tmp_path: Path) -> None:
    assert load_task_split_settings({}, tmp_path / "missing.env") is None


def test_task_split_settings_are_read_from_the_environment_or_env_file(tmp_path: Path) -> None:
    repos = '{"shop": {"path": "/srv/shop", "revision": "abc123", "allowed_paths": ["services"]}, "docs": {"path": "/srv/docs"}}'
    path = env_file(tmp_path, f"TASK_SPLIT_REPOSITORIES={repos}\nTASK_SPLIT_DB=/var/plans.sqlite3\n")

    settings = load_task_split_settings({}, path)
    assert settings is not None and settings.database_path == "/var/plans.sqlite3"
    assert settings.repositories == {
        "shop": RepositoryConfig("/srv/shop", "abc123", ("services",)),
        "docs": RepositoryConfig("/srv/docs", None, ()),
    }

    shell = load_task_split_settings({"TASK_SPLIT_REPOSITORIES": '{"x": {"path": "/x"}}'}, path)
    assert shell is not None and list(shell.repositories) == ["x"]  # shell wins; DB comes from the file
    assert shell.database_path == "/var/plans.sqlite3"
    assert load_task_split_settings({"TASK_SPLIT_REPOSITORIES": '{"x": {"path": "/x"}}'}, tmp_path / "none").database_path == "task_plans.sqlite3"


@pytest.mark.parametrize(
    "raw",
    ["not json", "[]", "{}", '{"a": "/path"}', '{"a": {}}', '{"a": {"path": ""}}',
     '{"a": {"path": "/p", "allowed_paths": "src"}}', '{"a": {"path": "/p", "revision": 3}}'],
)
def test_invalid_task_split_repositories_raise_without_echoing_values(raw: str) -> None:
    with pytest.raises(ConfigError, match="TASK_SPLIT_REPOSITORIES") as info:
        load_task_split_settings({"TASK_SPLIT_REPOSITORIES": raw}, Path("/nonexistent/.env"))
    assert raw not in str(info.value)
