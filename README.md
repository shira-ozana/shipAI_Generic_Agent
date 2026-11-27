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

## Quick start: run the project

Three parts run together on your machine:

| Part | Folder | Port | Command |
| --- | --- | --- | --- |
| Generic Agent API (chat + `/api/init`) | this repository | `3000` | `python -m uvicorn generic_agent.api:app --host 127.0.0.1 --port 3000` |
| Chat UI ([ShipAI_UI](https://github.com/shira-ozana/ShipAI_UI)), optional | `ShipAI_UI/` | `5173` | `npm run dev` |
| INIT agent (CLI) | this repository | - | `python -m init_agent` |

**1. Install (once)**

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev,api]"
cp .env.example .env                 # then edit .env (step 2)
```

**2. Fill `.env`** (never commit it, never paste keys into chat)

| Key | Needed for | Value |
| --- | --- | --- |
| `CURSOR_API_KEY` | everything | key from <https://cursor.com/dashboard/integrations> |
| `CURSOR_MODEL` | everything | an id from `python -m generic_agent --list-models` |
| `REPO_URL` | INIT | repository INIT analyzes (default: the Angular RealWorld app) |
| `GIT_TOKEN` | INIT push | optional: without it, git uses your login (`gh auth login`) |

**3. Start the API on port 3000** (terminal 1, in this repository)

```bash
python -m uvicorn generic_agent.api:app --host 127.0.0.1 --port 3000
curl http://127.0.0.1:3000/api/health      # {"status":"ok"}
```

- <http://localhost:3000/api/health>: health check
- <http://localhost:3000/docs>: interactive API docs (`/api/chat`, `/api/init`)
- `POST http://localhost:3000/api/chat` with `{"threadId": "<uuid>", "message": "..."}`

Port 8000 is left free for other services.

**4. Optional: start the chat UI** (terminal 2, in `ShipAI_UI/`)

The UI's dev proxy targets port 8000, so point it at port 3000 with `VITE_API_URL`:

```bash
npm ci
echo "VITE_API_URL=http://127.0.0.1:3000" > .env.local
npm run dev                                # http://localhost:5173
```

**5. Run INIT** (terminal 3, in this repository)

```bash
python -m init_agent --dry-run           # plan and draft only, writes nothing
INIT_PUSH=false python -m init_agent     # write + commit in workspaces/<run>/, no push
python -m init_agent                     # write, commit and push branch shipAI
```

INIT writes `AGENTS.md`, `ARCHITECTURE.md`, `RULES.md`, ... at the repository root,
`<folder>/AGENTS.md` next to the code (every folder with 3+ components), skills in
`<folder>/skills/<name>/SKILL.md`, and the report `platform/init/INIT.md`. The same run
is available from the API: `POST http://127.0.0.1:3000/api/init`.

**Windows notes**

- Smart App Control blocks a native module of `cursor-sdk`; every Cursor call fails with
  "An Application Control policy has blocked this file". Turn it off (Windows Security >
  App & browser control) or run in WSL.
- The first Cursor call can be slow (bridge cold start); `GenericAgent.run()` retries it.

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

## Task Split agent (HLS to dependency document)

`generic_agent.agents.task_split` turns HLS documents plus read-only access to one
repository revision into tasks grouped by owning component or service, with direct
prerequisites. A planning run ends with a draft; tasks are saved only when an
authorized user approves that exact draft version.

```bash
# .env (see .env.example): a checkout of the revision to plan against
TASK_SPLIT_REPOSITORIES={"shop": {"path": "/abs/path/to/shop-checkout"}}
python -m uvicorn generic_agent.api:app --host 127.0.0.1 --port 8000
```

| Request (header `X-User-Id: <user>`) | Result |
| --- | --- |
| `POST /api/task-plans` `{"repositoryId","hlsDocuments":[{"id","title","content"}]}` | pending draft grouped by owner (or clarification questions) |
| `GET /api/task-plans/{planId}` | review view of the latest version |
| `POST /api/task-plans/{planId}/versions/{n}/approve` | saves version `n`'s tasks and dependencies atomically; safe to retry |
| `POST /api/task-plans/{planId}/versions/{n}/decline` | declines it; no tasks |

The agent's prompt, rules and skills are in `src/generic_agent/prompts/task_split/`.
See [docs/modules/task-split.md](docs/modules/task-split.md),
[docs/modules/task-plans.md](docs/modules/task-plans.md) (contract, and what the
separate `sdlc-ui` still needs) and the sample in `tests/fixtures/task_split/`.

## INIT runner: `generic_agent.runner.GenericAgent.run()`

Create one instance and call `run()` with the task and the material the model must follow.
The INIT agent works this way.

```python
from generic_agent.runner import GenericAgent, Section, Skill  # not generic_agent.GenericAgent

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

## Requirement Agent

`src/generic_agent/agents/requirement/` turns a customer request into numbered
business requirements, published to `.platform/{feature_id}/requierment/` on the
repository's `ShipAi` branch. Start it from the UI with
`POST /api/requirement/start` and send answers with `POST /api/requirement/chat`,
or from the terminal:

```bash
python -m generic_agent requirement --repo-url https://github.com/org/repo \
  --feature-id ORD-42 --requirement-text "Customers can cancel an order before it ships."
```

Repository credentials for the CLI come from `REQUIREMENT_GIT_USERNAME` /
`REQUIREMENT_GIT_TOKEN` (shell or `.env`). See
[docs/modules/requirement.md](docs/modules/requirement.md).

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
