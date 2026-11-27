# Architecture

`generic-langgraph-agent` is a small, reusable agent. The Cursor SDK
(`cursor-sdk`, running a *local* agent) produces the answers and LangGraph owns
the graph, state and checkpoints.

## Boundaries

In scope:

- One explicit `StateGraph` over `MessagesState` with a single node.
- A factory, `build_agent(...)`, taking Cursor settings, instructions and a tool
  mapping.
- Cursor configuration from `CURSOR_API_KEY` / `CURSOR_MODEL` (shell or `.env`).
- Three entry points that run the *same* graph: a CLI, LangGraph Studio and a
  local HTTP API.

Out of scope: persistence beyond in-memory checkpointing, cloud agents,
retrieval, multi-agent orchestration, and anything that requires LangSmith for
the CLI or the tests.

## Modules (`src/generic_agent/`)

| Module | Responsibility |
| --- | --- |
| `graph.py` | `build_agent` factory, the `run_cursor_agent` node, prompt rendering (`render_prompt`), default-prompt loading. The only module that calls `Agent.prompt`. |
| `config.py` | `.env` loading, `Settings`, `list_model_ids()` (`Cursor.models.list()`), error translation (`ConfigError`, `CursorRunError`, `describe_error`). |
| `tools.py` | Local Cursor SDK custom tools (`word_count`) and `default_tools()`. |
| `runtime/agent_definition.py` | `AgentDefinition` (prompt, skills, rules, guardrails), `Skill`, `Guardrail`, `GuardrailPhase`, `GuardrailResult`, `AgentContext` (runtime context), `assemble_instructions`, `run_guardrails`. Plain data and helpers; never imports `graph`. |
| `runtime/generic_agent.py` | `GenericAgent`: synchronous runner. `run(definition, messages, config=None)` invokes the graph with the definition as runtime context. |
| `agents/requirement/` | The Requirement Agent: packaged instructions and skills, loader, per-thread clone tools, git publishing, `RequirementAgent` runner and its CLI. See [modules/requirement.md](modules/requirement.md). |
| `studio.py` | `make_graph()` factory referenced by `langgraph.json`. No checkpointer. |
| `api.py` | FastAPI app (`app`, `create_app`): compiles the graph once at startup with `InMemorySaver` and serves `/api/health`, `/api/chat` and the Requirement Agent's `/api/requirement/start` and `/api/requirement/chat`. |
| `__main__.py` | CLI (`python -m generic_agent`, `--list-models`): compiles once, runs the chat loop, renders replies and errors. |
| `prompts/default_system.md` | Default agent instructions, shipped as package data. |

Dependency direction: `__main__` / `studio` / `api` -> `config`, `tools`, `graph`;
`runtime.generic_agent` -> `graph`, `runtime.agent_definition`;
`graph` -> `config` (for `Settings` and `CursorRunError`) and
`runtime.agent_definition`; `runtime.agent_definition` -> `config`. (`GenericAgent`
imports `graph` inside `__init__` to avoid a circular import.)

Per-module guides for changing each piece: [`modules/`](modules/README.md).

## Graph state

`MessagesState`: a single `messages` list with the `add_messages` reducer,
holding only `HumanMessage`s and the `AIMessage`s saved by the node.

- **LangGraph checkpoints are the only conversation memory.** Cursor agents are
  not resumed; each graph run creates a fresh local Cursor agent via
  `Agent.prompt(...)` (create, send, wait, close).
- Instructions are **not** stored in state. They are rendered into each run's
  prompt, so history never accumulates copies.
- The API key and model live in a closure (`Settings`, `SecretStr`), never in
  state or checkpoints, and are not logged.
- Tool calls happen inside Cursor and are not recorded as `ToolMessage`s; only
  the final text is saved.

## Node transition

```mermaid
flowchart LR
    START([START]) --> run_cursor_agent
    run_cursor_agent --> END([END])
```

`run_cursor_agent(state)`:

0. Instructions are `system_prompt` (the `build_agent` argument) unless the
   invocation carries an `AgentContext`; then they are
   `assemble_instructions(context.definition)` (see
   [Runtime interface](#runtime-interface)).
1. `render_prompt(instructions, messages, max_history_messages)` builds one text
   prompt: `<instructions>`, `<conversation_history>` (the most recent
   `max_history_messages` = 40 earlier human/assistant messages, omitted when
   empty) and `<current_user_message>` (the newest `HumanMessage`).
   With a definition, **pre-run guardrails** then run on the latest
   `HumanMessage` text; a failure raises `GuardrailError` and `Agent.prompt` is
   never called.
2. `Agent.prompt(prompt, AgentOptions(...))` runs locally with:
   - `model` and `api_key` from `Settings`;
   - `local.cwd` = a temporary scratch directory deleted after the run;
   - `local.custom_tools` = the registered tools (`None` for a text-only agent);
   - `tools=[]`: Cursor's built-in shell/file tools are disabled by default
     (`build_agent(..., builtin_tools=None)` restores Cursor's default toolset);
   - no `setting_sources`, so no ambient project/user rules are loaded.
3. If `result.status != "finished"` the node raises `CursorRunError` (status,
   run id, error message). Otherwise, with a definition, **post-run guardrails**
   check `result.result` (failure raises `GuardrailError`, so nothing is saved).
   A reply that passes is saved as an `AIMessage` with `response_metadata`
   holding the Cursor run id, agent id and model.

SDK errors that mean the run never started (`CursorAgentError` subclasses)
propagate unchanged.

## Runtime interface

`runtime/` is the shared foundation for future agents. It adds no nodes: the
graph is still `START -> run_cursor_agent -> END`.

```python
runner = GenericAgent(settings=settings, tools=tools)   # sync; optional checkpointer=...
result = runner.run(
    definition,                                          # AgentDefinition, per call
    messages=[HumanMessage(content=user_message)],
    config={"configurable": {"thread_id": "..."}},       # optional, needs a checkpointer
)                                                        # same shape as graph.invoke()
```

`AgentDefinition` is a frozen dataclass:

| Field | Meaning |
| --- | --- |
| `prompt` | Main instruction; must be a non-blank string. |
| `skills` | Named instructional text, `Skill(name, content)` (or a `{name: text}` mapping). Names are unique. May be empty. |
| `rules` | Instructions included on every run of the definition. May be empty. |
| `guardrails` | `Guardrail(name, phase, check)`; `phase` is `pre_run` or `post_run`. May be empty. |
| `tools` | Optional `{name: CustomTool}`. `None` (default) uses the runner's tools; a mapping (`{}` = text-only) replaces them for this run only, e.g. tools bound to a per-thread working directory. Not compared, not checkpointed. |

- **Isolation**: `GenericAgent.run` builds the graph once (in `__init__`) and
  passes the definition as invocation-scoped LangGraph runtime context
  (`context=AgentContext(definition)`, schema set via
  `StateGraph(..., context_schema=AgentContext)`). Context is not state and is
  not checkpointed, so state stays `MessagesState` and checkpoints hold only
  messages. Each call uses its own definition, including concurrent calls and
  calls that share a thread.
- **Assembly order** (`assemble_instructions`): `prompt`, then
  `<rules>` (numbered), then `<skills>` (`<skill name="...">`), in declaration
  order; empty sections are omitted. The result is passed as `instructions` to
  `render_prompt`, so history and `<current_user_message>` keep their format.
- **Guardrails**: `check(text)` returns `True` / `GuardrailResult(True)` to pass;
  `False` / `GuardrailResult(False, message)` fails. Pre-run checks receive the
  latest user message and run before `Agent.prompt`; post-run checks receive the
  reply and run only after a `finished` result, before the `AIMessage` is
  returned. They run in declaration order and the first failure stops the run. A
  check that raises also fails. `GuardrailError` subclasses `CursorRunError`, so
  the CLI (`describe_error`) and the API (`502`) handle it as any failed run. Its
  text names the guardrail and phase (plus the check's own `message`) but never
  the user text or the rejected reply.
- **Compatibility**: `build_agent(settings, system_prompt, tools, ...)` is
  unchanged. Without a context (CLI, API, Studio) the node uses `system_prompt`
  from `prompts/default_system.md` and runs no guardrails.
- Cursor options (settings, custom tools, scratch `cwd`, `tools=[]` by default)
  are exactly those of `build_agent`; `GenericAgent` forwards `builtin_tools`,
  `checkpointer` and `max_history_messages`.

## Custom tools

`word_count` is a `cursor_sdk.CustomTool` (description, JSON input schema,
`execute(args, context)` returning text) registered through
`LocalAgentOptions.custom_tools`. The SDK serves it from a loopback callback
server in this process while the run executes. `build_agent(..., tools={})`
gives a text-only configuration.

## One graph, three runners

| | CLI | Studio (`langgraph dev`) | HTTP API |
| --- | --- | --- | --- |
| Entry point | `python -m generic_agent` | `langgraph.json` -> `studio.py:make_graph` | `uvicorn generic_agent.api:app` |
| Settings | `config.load_settings()` | `config.load_settings()` | `config.load_settings()` at startup |
| Instructions / tools | default prompt, `default_tools()` | same | same |
| Checkpointer | `InMemorySaver`, one `thread_id` (uuid4) per session | none passed; Agent Server injects its own | one `InMemorySaver`; `threadId` is the `thread_id` |
| Input per turn | only the new `HumanMessage` | the Studio client | only the new `HumanMessage` |

Studio additionally needs a LangSmith login in the browser for the hosted UI;
that is separate from the Cursor key, and the CLI and tests never need it.

## HTTP API

`api.py` serves the graph to local front ends (the separate `sdlc-ui` React
app). Launch (one worker, loopback only):

```bash
pip install -e ".[api]"
python -m uvicorn generic_agent.api:app --host 127.0.0.1 --port 8000
```

Shared contract:

| Endpoint | Request | Success | Errors |
| --- | --- | --- | --- |
| `GET /api/health` | - | `200 {"status":"ok"}`; never touches the graph or Cursor | - |
| `POST /api/chat` | `{"threadId":"<UUID>","message":"<text>"}` | `200 {"threadId":"<same UUID>","reply":"<assistant text>"}` | `422` missing/invalid UUID, non-string or blank message; `409` a run for this thread is in flight; `502` run failed or empty reply |

```bash
curl -s -X POST http://127.0.0.1:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"threadId":"3f2b8c1e-6a4d-4e0b-9d57-1c2a7e5f9b10","message":"Hello"}'
```

Behaviour:

- **Startup**: a FastAPI lifespan loads `Settings` (`CURSOR_API_KEY` /
  `CURSOR_MODEL`, same rules as the CLI; missing config fails startup), calls
  `build_agent(settings, load_default_prompt(), default_tools(),
  checkpointer=InMemorySaver())` once, and closes the SDK's default client on
  shutdown. `create_app(settings)` lets tests inject settings.
- **Per request**: `chat` is a sync route (FastAPI runs it in a worker thread).
  It invokes the graph with only the new `HumanMessage` and
  `{"configurable": {"thread_id": str(threadId)}}`. Different IDs have separate
  checkpoints and histories. `threadId` is parsed as a UUID and echoed in
  canonical lowercase form, so different spellings of one UUID share a thread.
- **Concurrency**: `ThreadGuard` (a lock-protected set) rejects a second request
  for a thread that is still running with `409`, and always releases the thread
  in a `finally`, on success or failure. Different threads run in parallel.
- **Errors**: any exception from the graph (failed run, SDK error) and empty
  reply text become `502 {"detail": "<fixed message>"}`. Raw SDK/run messages are
  never returned; the server logs `describe_error(exc)` (which excludes
  credentials). A failed turn leaves its user message in the thread's
  checkpoint, as in the CLI.
- **CORS**: only `http://localhost:<port>` and `http://127.0.0.1:<port>` origins,
  `GET`/`POST`, `Content-Type` header.
- **Single worker**: the checkpointer and the in-flight guard are in-process
  memory, so run exactly one uvicorn worker; history is lost on restart.

## Requirement Agent

`agents/requirement/` is the first concrete agent. Code runs a pre-flight
(validate inputs, clone branch `ShipAi` per thread, check Init output under
`.platform/`) before the first model call, then every turn runs on
`GenericAgent` with an `AgentDefinition` built from packaged Markdown
(`AGENTS.md` + 8 `SKILL.md` + templates) and tools bound to that thread's
clone (read-only repository access, writes only to
`.platform/{feature_id}/requierment/`, publishing via git in code). The graph is
unchanged. Human-in-the-loop is turn-based on one thread. Full guide:
[modules/requirement.md](modules/requirement.md).

## Configuration and errors

Settings come from non-empty shell variables, falling back to `./.env` in the
working directory. `CURSOR_MODEL` has no default; model IDs come from
`Cursor.models.list()` (`python -m generic_agent --list-models`).

`describe_error` maps failures to actionable text without echoing secrets:
missing configuration, authentication, permission, unknown model, network or
timeout, rate limits, bad requests, other SDK errors and failed runs.

## Tests (`tests/`)

Offline and deterministic: a fake `Agent.prompt` replaces the Cursor SDK (no
network, no bridge process). Covered: graph shape, direct reply, `word_count`
round trip through the registered custom tool, text-only mode, history from
checkpoints within a thread, thread isolation, history trimming, failed runs,
no secrets in state/checkpoints, config loading, `--list-models`, error mapping,
CLI loop, the HTTP API (`tests/test_api.py`: reply, same-thread follow-up
context, thread isolation, validation, `502` safety and guard release, `409`,
CORS), the runtime (`tests/test_runtime.py`: definition validation, assembly
order, prompt/rules/skills reaching the SDK, isolation across runs and threads,
no definition in checkpoints, a failed guardrail at each phase), the
Requirement Agent (`tests/test_requirement_agent.py`, local bare git remote),
and packaging / `langgraph.json`.
