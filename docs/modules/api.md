# `api.py` - local HTTP API

`src/generic_agent/api.py`

## Purpose

A FastAPI layer that lets the separate `sdlc-ui` React app chat with the same
graph. Launch (one worker, loopback only; needs `pip install -e ".[api]"`):

```bash
python -m uvicorn generic_agent.api:app --host 127.0.0.1 --port 8000
```

## Contract

| Endpoint | Request | Success | Errors |
| --- | --- | --- | --- |
| `GET /api/health` | - | `200 {"status":"ok"}` (never calls Cursor) | - |
| `POST /api/chat` | `{"threadId":"<UUID>","message":"<text>"}` | `200 {"threadId":"<same UUID>","reply":"<text>"}` | `422` invalid input, `409` thread busy, `502` failed or empty run |

Error bodies are `{"detail": ...}`. Changing field names or status codes breaks
`sdlc-ui`; update `README.md` and `docs/architecture.md` too.

## Key symbols

| Symbol | What it does |
| --- | --- |
| `create_app(settings=None)` | App factory. Lifespan loads settings (unless given), compiles the graph once with `InMemorySaver`, creates the `ThreadGuard`, closes the SDK client on shutdown. |
| `app` | Module-level `create_app()` used by uvicorn. |
| `ChatRequest` / `ChatResponse` | Pydantic models. `threadId` must be a UUID string; `message` must be a non-blank string. |
| `ThreadGuard` | Lock-protected set of in-flight thread IDs (`acquire` / `release`). |
| `BUSY_MESSAGE`, `RUN_FAILED_MESSAGE` | The fixed `409` / `502` texts. |

## Behaviour to preserve

- `chat` is a **sync** route (runs in a worker thread) because the graph call
  blocks.
- Each request sends only the new `HumanMessage`, with `thread_id =
  str(threadId)` (canonical lowercase UUID).
- `guard.release` is in a `finally`; a thread can never stay locked after an
  error.
- All graph exceptions and empty reply text map to `502` with the fixed message.
  Never return `describe_error`, SDK messages or run error text to the client;
  log them server-side only.
- CORS allows only `http://localhost:<port>` and `http://127.0.0.1:<port>`.
- Single process only: history and the guard are in memory.

## Tests

`tests/test_api.py` (uses `TestClient` and the `mock_cursor` fixture): health,
reply, follow-up context, isolation, validation table, safe `502`s, guard
release, `409` with a threaded request, CORS.
