# generic-langgraph-agent

A small, reusable agent: an explicit LangGraph `StateGraph` whose single node runs
a local [Cursor SDK](https://cursor.com/docs/sdk/python) agent, one example custom
tool (`word_count`), a CLI and LangGraph Studio support. See
[docs/architecture.md](docs/architecture.md).

```
START -> run_cursor_agent -> END
```

LangGraph checkpoints hold the conversation; each run passes the relevant history
to a fresh Cursor agent. Cursor's built-in shell/file tools are disabled, so the
agent can only use the tools you register.

## Setup

Requires Python 3.11+ (the commands below use `python3`; substitute
`python3.11`, `python3.12` or `python3.13` if `python3` is older).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,studio,api]"
```

## Set local credentials

Create a key at [Cursor Dashboard -> Integrations](https://cursor.com/dashboard/integrations),
then edit the local, git-ignored `.env` file yourself (do not paste keys into chat
or commit them):

```bash
cp .env.example .env
# edit .env and set:
#   CURSOR_API_KEY=<your Cursor API key>
#   CURSOR_MODEL=<a model ID available to your account>
```

Variables already exported in your shell take precedence over `.env`.

### List the model IDs available to your account

Set only `CURSOR_API_KEY` first, then:

```bash
python -m generic_agent --list-models
```

This calls `Cursor.models.list()` and prints `model_id<TAB>display name`. Copy an
ID into `CURSOR_MODEL`. Equivalent SDK code (the SDK reads `CURSOR_API_KEY` from
the process environment, not from `.env`):

```bash
set -a; source .env; set +a    # export the variables from .env into this shell
python -c "from cursor_sdk import Cursor; [print(m.id, m.display_name) for m in Cursor.models.list()]"
```

## Run the tests (offline, mocked Cursor SDK)

```bash
python -m pytest
```

## Run the CLI

```bash
python -m generic_agent
```

Type `exit` to quit. Try a prompt that uses the tool:

```
How many words are in this sentence: "the quick brown fox jumps over the lazy dog"?
```

The agent should call `word_count` and answer with 9. Each CLI session uses one
in-memory thread, so follow-up questions use earlier turns from the checkpoint.

## Run in LangGraph Studio

```bash
langgraph dev
```

This starts a local Agent Server (default `http://127.0.0.1:2024`) and prints a
Studio URL. Open it to chat with the `agent` graph; Agent Server manages
checkpointing.

Credentials, kept separate:

- **Cursor**: `CURSOR_API_KEY` / `CURSOR_MODEL` as above (loaded from `.env` in the
  directory where you run `langgraph dev`, or from your shell).
- **LangSmith** (Studio only): the hosted Studio UI at `smith.langchain.com` asks
  you to sign in with a LangSmith account in the browser. `LANGSMITH_API_KEY` in
  `.env` is optional and only enables tracing. The CLI and tests never need it.

## Run the local HTTP API

A FastAPI app (`src/generic_agent/api.py`) exposes the same graph to local
front ends such as the separate `sdlc-ui` React app.

```bash
pip install -e ".[api]"
python -m uvicorn generic_agent.api:app --host 127.0.0.1 --port 8000
```

It reads `CURSOR_API_KEY` / `CURSOR_MODEL` from the shell or `.env` (the same
configuration as the CLI) and fails at startup if they are missing. Keep it on
`127.0.0.1` with a single worker: history is held in memory by that process.

Shared HTTP contract:

| Request | Success | Errors |
| --- | --- | --- |
| `GET /api/health` | `200 {"status":"ok"}` (never calls Cursor) | |
| `POST /api/chat` with `{"threadId":"<UUID>","message":"<text>"}` | `200 {"threadId":"<same UUID>","reply":"<assistant text>"}` | `422` missing/invalid UUID or blank message; `409` a request for this thread is still running; `502` Cursor run failed or returned empty text |

Error bodies are `{"detail": ...}`; the `409` and `502` details are fixed, safe
messages (no credentials or raw SDK errors; those are logged by the server
only). `threadId` selects the conversation: each ID keeps its own history, and
the UUID is returned in lowercase canonical form. The browser origins
`http://localhost:<port>` and `http://127.0.0.1:<port>` are allowed by CORS.

```bash
curl -s http://127.0.0.1:8000/api/health

curl -s -X POST http://127.0.0.1:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"threadId":"3f2b8c1e-6a4d-4e0b-9d57-1c2a7e5f9b10","message":"Say hello in five words."}'
# {"threadId":"3f2b8c1e-6a4d-4e0b-9d57-1c2a7e5f9b10","reply":"..."}
```

## Use it from another agent: `GenericAgent.run()`

Create one instance and call `run()` with the task and the material the model must follow.
The INIT agent works this way.

```python
from generic_agent import GenericAgent, Section, Skill

agent = GenericAgent()  # CURSOR_API_KEY / CURSOR_MODEL from .env on first run
result = agent.run(
    "Summarize src/api.",
    instructions="You are a code analyst.",
    rules=[Section("R1", "Evidence is required", "Cite repo paths.")],
    guidelines=["Keep it short."],
    skills=[Skill("analyze-component", "1. Read the inventory. 2. ...")],
)
print(result.text, result.metadata["cursor_run_id"])
```

Each call is self-contained (no checkpointer); pass `history=[HumanMessage(...), AIMessage(...)]`
for earlier turns. `await agent.arun(...)` is the async form.

## Use it from Python

`build_agent(settings, system_prompt, tools, *, checkpointer=None)` returns a
compiled graph. Build as many agents as you like:

```python
from langchain_core.messages import HumanMessage

from generic_agent import build_agent, default_tools, load_default_prompt
from generic_agent.config import load_settings

settings = load_settings()  # CURSOR_API_KEY / CURSOR_MODEL

default = build_agent(settings, load_default_prompt(), default_tools())
pirate = build_agent(
    settings,
    "You are a pirate. Always answer in pirate speak and keep it short.",
    default_tools(),  # or {} for a text-only agent
)

result = pirate.invoke({"messages": [HumanMessage(content="How many words in 'ahoy there matey'?")]})
print(result["messages"][-1].text)
```

Pass `checkpointer=InMemorySaver()` and `config={"configurable": {"thread_id": "..."}}`
to keep conversation history per thread. `max_history_messages=` (default 40)
limits how much earlier conversation goes into each run. A run that does not
finish raises `generic_agent.config.CursorRunError`.

## INIT agent

`src/init_agent/` builds an agent harness for any repository. Set `REPO_URL` (and
`GIT_TOKEN` to push) in `.env`, then:

```bash
python -m init_agent             # clone REPO_URL -> branch shipAI -> harness in root + code folders -> push
python -m init_agent --dry-run   # plan and draft only
```

or `POST /api/init` on the running API. Details: [docs/modules/init_agent.md](docs/modules/init_agent.md).

## Add a tool

Define a `cursor_sdk.CustomTool` (description, JSON input schema, `execute`) in
`src/generic_agent/tools.py`, add it to `default_tools()`, and add a test in
`tests/`.
