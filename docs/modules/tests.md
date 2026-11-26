# `tests/` - offline test suite

## Purpose

Fast, deterministic tests. They **never** call Cursor and need no network,
LangSmith or real credentials.

```bash
source .venv/bin/activate
python -m pytest
```

Needs `pip install -e ".[dev,studio,api]"` (the API tests use FastAPI's
`TestClient`).

## Test doubles

| Piece | File | Role |
| --- | --- | --- |
| `SECRET` | `fakes.py` | A fake key; tests assert it never leaks into state, checkpoints or responses. |
| `FakeRunResult` | `fakes.py` | Subset of `cursor_sdk.RunResult` (`result`, `status`, `id`, `agent_id`, `error_message`). |
| `FakeCursor` | `fakes.py` | Records every `Agent.prompt` call (`calls`, `prompts`) and answers via a *responder*. |
| `Responder` | `fakes.py` | `(prompt, AgentOptions) -> str \| FakeRunResult`; raise an exception to simulate SDK errors. |
| `settings` | `conftest.py` | `Settings` with the fake key and model `test-model`. |
| `mock_cursor` | `conftest.py` | Patches `generic_agent.graph.Agent` with a `FakeCursor`; call it with a responder. |

## Files

| File | Covers |
| --- | --- |
| `test_graph.py` | graph shape, replies, tools, history, isolation, trimming, errors, no secrets |
| `test_config.py` | `.env`/shell loading, placeholders, model listing, `describe_error` |
| `test_cli.py` | chat loop, error handling, `main` exit codes, `--list-models` |
| `test_api.py` | HTTP contract: reply, context, isolation, validation, 502, 409, CORS |
| `test_packaging.py` | `langgraph.json`, Studio factory, packaged prompt, dependencies |

## Writing a test

```python
def test_example(mock_cursor, settings):
    fake = mock_cursor(lambda prompt, options: "hello")
    agent = build_agent(settings, "Be brief.", {}, checkpointer=InMemorySaver())
    ...
    assert "expected text" in fake.prompts[0]
```

- Patch `generic_agent.graph.Agent` (via `mock_cursor`), never the network.
- For concurrency, block inside the responder with a `threading.Event` (see the
  `409` test) rather than sleeping.
- Don't read `.env` in tests; pass `Settings`, `environ` or `env_file`
  explicitly.
