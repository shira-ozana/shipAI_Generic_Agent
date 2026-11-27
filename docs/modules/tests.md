# `tests/` - offline test suite

## Purpose

Fast, deterministic tests. They **never** call Cursor and need no network,
LangSmith or real credentials.

```bash
source .venv/bin/activate
python -m pytest
```

Task Split fixtures live in `tests/fixtures/task_split/` (excluded from collection). Two
repository tests run a real `git init`/`commit` in a temp dir.

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
| `test_runtime.py` | `AgentDefinition` validation, assembly, definition isolation, guardrails at each phase |
| `test_config.py` | `.env`/shell loading, placeholders, model listing, `describe_error` |
| `test_cli.py` | chat loop, error handling, `main` exit codes, `--list-models` |
| `test_api.py` | HTTP contract: reply, context, isolation, validation, 502, 409, CORS |
| `test_task_split_schema.py` | dependency-document validation: ownership, unique IDs, missing dependencies, cycles, citations, clarification |
| `test_task_split_repository.py` | repository guardrails: scope, traversal, symlinks, credential files, git revision |
| `test_task_split_agent.py` | prompt pack (order, rules/guidelines/skills/guardrails), the four components reaching `GenericAgent.run`, planning runs on the mock Cursor, tools, reply validation, no credentials in input, old import paths |
| `test_task_plans.py` | store/approval invariants, review view, and the `/api/task-plans` HTTP flow through the planner on the mock Cursor |
| `test_packaging.py` | Studio entry, packaged prompt assets (including `task_split_agent/prompts`), wheel and sdist config |
| `test_task_plans.py` | drafts vs approval: zero tasks unless approved, exact version, idempotent retries, rollback, HTTP flow |
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
