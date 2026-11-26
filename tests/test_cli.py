from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace

import cursor_sdk
from fakes import FakeRunResult
from langgraph.checkpoint.memory import InMemorySaver

from generic_agent import build_agent, default_tools
from generic_agent.__main__ import main, run_session


def drive(agent, *lines: str) -> list[str]:
    inputs: Iterator[str] = iter(lines)
    out: list[str] = []

    def read(_prompt: str) -> str:
        try:
            return next(inputs)
        except StopIteration:
            raise EOFError from None

    run_session(agent, input_fn=read, output_fn=out.append)
    return [line for line in out if line]  # drop the blank line printed on EOF


def test_session_sends_only_new_message_and_keeps_history(mock_cursor, settings) -> None:
    cursor = mock_cursor(lambda p, o: f"saw {len(cursor.calls)}")
    agent = build_agent(settings, "p", {}, checkpointer=InMemorySaver())
    sent: list[int] = []
    original = agent.invoke

    class Spy:
        def invoke(self, payload, config):
            sent.append(len(payload["messages"]))
            return original(payload, config)

    out = drive(Spy(), "one", "", "two", "exit", "never reached")

    assert out == ["agent> saw 1", "agent> saw 2"]
    assert sent == [1, 1]
    assert "User: one" in cursor.prompts[1]


def test_session_runs_word_count_tool(mock_cursor, settings) -> None:
    def responder(prompt, options):
        tool = options.local.custom_tools["word_count"]
        return f"Words: {tool.execute({'text': 'a b c d'}, cursor_sdk.CustomToolContext())}"

    mock_cursor(responder)
    agent = build_agent(settings, "p", default_tools(), checkpointer=InMemorySaver())
    assert drive(agent, "count: a b c d") == ["agent> Words: 4"]


def test_session_translates_sdk_errors_and_survives(mock_cursor, settings) -> None:
    def boom(prompt, options):
        raise cursor_sdk.AuthenticationError("bad key")

    mock_cursor(boom)
    agent = build_agent(settings, "p", {}, checkpointer=InMemorySaver())
    out = drive(agent, "hi", "again")
    assert len(out) == 2 and all(line.startswith("error:") and "CURSOR_API_KEY" in line for line in out)


def test_session_reports_failed_runs(mock_cursor, settings) -> None:
    mock_cursor(lambda p, o: FakeRunResult(status="cancelled", id="run-3"))
    agent = build_agent(settings, "p", {}, checkpointer=InMemorySaver())
    (line,) = drive(agent, "hi")
    assert "cancelled" in line and "run-3" in line


def test_main_without_credentials_exits_with_guidance(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    monkeypatch.delenv("CURSOR_MODEL", raising=False)
    monkeypatch.chdir(tmp_path)
    assert main([]) == 2
    assert "CURSOR_API_KEY" in capsys.readouterr().err


def test_main_list_models_prints_ids_without_needing_a_model(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CURSOR_MODEL", raising=False)
    monkeypatch.setenv("CURSOR_API_KEY", "cursor-test-secret-value")
    models = [SimpleNamespace(id="model-a", display_name="Model A")]
    monkeypatch.setattr(
        "generic_agent.config.Cursor",
        SimpleNamespace(models=SimpleNamespace(list=lambda *, api_key: models)),
    )

    assert main(["--list-models"]) == 0
    out = capsys.readouterr()
    assert "model-a\tModel A" in out.out
    assert "cursor-test-secret-value" not in out.out + out.err


def test_main_list_models_reports_sdk_errors(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CURSOR_API_KEY", "cursor-test-secret-value")

    def fail(*, api_key):
        raise cursor_sdk.AuthenticationError("bad key")

    monkeypatch.setattr("generic_agent.config.Cursor", SimpleNamespace(models=SimpleNamespace(list=fail)))
    assert main(["--list-models"]) == 1
    assert "CURSOR_API_KEY" in capsys.readouterr().err
