# `graph.py` - the graph and the Cursor call

`src/generic_agent/graph.py`

## Purpose

Builds the LangGraph `StateGraph` (`START -> run_cursor_agent -> END`) over
`MessagesState` and runs one local Cursor agent per turn. It is the **only**
place that calls `Agent.prompt`.

## Key symbols

| Symbol | What it does |
| --- | --- |
| `build_agent(settings, system_prompt, tools, *, checkpointer=None, max_history_messages=40, builtin_tools=())` | Factory every entry point uses. Returns a compiled graph. |
| `run_cursor_agent(state)` (closure inside `build_agent`) | The single node: render prompt -> `Agent.prompt` -> check status -> return one `AIMessage`. |
| `render_prompt(instructions, messages, max_history_messages)` | Builds `<instructions>`, optional `<conversation_history>`, `<current_user_message>` and a closing line. Raises `ValueError` without a `HumanMessage`. |
| `load_default_prompt()` | Reads `prompts/default_system.md` via `importlib.resources`. |
| `DEFAULT_MAX_HISTORY_MESSAGES` | 40 earlier human/assistant messages per run. |

## Behaviour to preserve

- A turn creates a fresh Cursor agent (create, send, wait, close). Cursor agents
  are never resumed; history comes from the checkpoint and is rendered into the
  prompt.
- Instructions are rendered per run and never stored in state.
- `AgentOptions`: `model` and `api_key` from `Settings`; `local.cwd` is a
  temporary scratch dir deleted after the run; `local.custom_tools` is the
  `tools` mapping (`None` if empty); `tools=[]` disables Cursor's built-in
  shell/file tools (`builtin_tools=None` re-enables the default set); no
  `setting_sources`.
- `result.status != "finished"` raises `CursorRunError`. Success saves
  `result.result` as an `AIMessage` with `cursor_run_id`, `cursor_agent_id` and
  `model` in `response_metadata`.
- `get_secret_value()` is called only when building `AgentOptions`.
- Blank history messages are skipped; `max_history_messages <= 0` means no
  history.

## Changing it

- New per-turn behaviour belongs in the node or `render_prompt`, not in an
  entry point.
- Don't widen built-in tools or `cwd` without a clear reason.
- Update `docs/architecture.md` (node transition section) when the graph
  changes.

## Tests

`tests/test_graph.py`: shape, reply and options, tool round trip, text-only mode,
checkpoint history, isolation, history trimming, custom instructions,
built-in-tool opt-in, failed runs, no secrets in state, `render_prompt` edge
cases.
