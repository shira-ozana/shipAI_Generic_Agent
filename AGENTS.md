# AGENTS.md

LangGraph agent whose answers come from a local Cursor SDK run (`cursor-sdk`).
Read `docs/architecture.md` before changing the graph, and keep it in sync with
the code. Per-module guides live in `docs/modules/` (start at its `README.md`).

## Where things go

- Graph logic (`run_cursor_agent`, `build_agent`, prompt rendering):
  `src/generic_agent/graph.py`. It is the only place that calls `Agent.prompt`.
- `GenericAgent` (`run()` / `arun()` for other agents, e.g. INIT):
  `src/generic_agent/runner.py`. It builds its graphs with `build_agent`.
- Settings, `.env` loading, model listing, error messages:
  `src/generic_agent/config.py`.
- Agent instructions: `src/generic_agent/prompts/default_system.md` (package
  data, loaded with `importlib.resources`). Don't inline long prompts in Python.
- Tools: local `cursor_sdk.CustomTool`s in `src/generic_agent/tools.py`;
  register new ones in `default_tools()`. `{}` means text-only.
- Entry points: CLI in `__main__.py`, Studio factory in `studio.py` (referenced
  by `langgraph.json`), local HTTP API in `api.py` (FastAPI). All must call
  `build_agent`.
- INIT agent: `src/init_agent/` (graph, prompt pack in `prompts/`, writer,
  `/api/init` router mounted by `api.py`). Rules, guidelines and skills for the
  INIT prompt are Markdown files in `src/init_agent/prompts/`, not Python strings.
  Read `docs/modules/init_agent.md` first.
- Tests: `tests/`, offline only. Use the `mock_cursor` fixture (`conftest.py`,
  `fakes.py`), which patches `generic_agent.graph.Agent`.

## Rules

- Graph is `START -> run_cursor_agent -> END`. LangGraph checkpoints are the
  conversation history; don't resume Cursor agents or store history elsewhere.
- Instructions are rendered into each prompt, never stored in graph state.
- Cursor built-in tools stay disabled by default (`tools=[]`) and the run uses a
  scratch `cwd`; don't widen this without a reason.
- Don't pass a checkpointer in `studio.py` (Agent Server provides one).
- Tests must never call Cursor or need network/LangSmith.

## Commands

```bash
source .venv/bin/activate        # python3.11+; create with: python3 -m venv .venv
pip install -e ".[dev,studio,api]"
python -m pytest                 # offline tests, mocked Cursor SDK
python -m uvicorn generic_agent.api:app --host 127.0.0.1 --port 8000   # HTTP API, one worker
python -m generic_agent --list-models   # needs CURSOR_API_KEY
python -m generic_agent          # CLI (needs CURSOR_API_KEY and CURSOR_MODEL)
langgraph dev                    # Studio / Agent Server
```

## Credentials

- `CURSOR_API_KEY` and `CURSOR_MODEL` come from the shell or a local `.env`
  (shell wins). `.env` is git-ignored; only `.env.example` with placeholders is
  committed.
- Never ask for, read aloud, print, log, commit, or store keys in graph state.
  Don't `cat` or echo `.env`. Keep the key a `SecretStr`; call
  `get_secret_value()` only when building `AgentOptions`. Error text must not
  include secrets.
- LangSmith credentials are only for Studio and are separate from the Cursor key.
