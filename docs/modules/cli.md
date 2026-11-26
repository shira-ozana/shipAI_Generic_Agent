# `__main__.py` - the CLI

`src/generic_agent/__main__.py`

## Purpose

Terminal chat (`python -m generic_agent`) and `--list-models`.

## Key symbols

| Symbol | What it does |
| --- | --- |
| `main(argv=None) -> int` | Parses args, loads settings, builds the agent, runs the session, closes the SDK client. |
| `run_session(agent, *, thread_id=None, input_fn=input, output_fn=print)` | The chat loop; injectable I/O for tests. |
| `EXIT_COMMANDS` | `exit`, `quit`. |

## Behaviour to preserve

- Builds the graph with `build_agent(settings, load_default_prompt(),
  default_tools(), checkpointer=InMemorySaver())`.
- One `thread_id` (uuid4 hex by default) per session; each turn sends **only**
  the new `HumanMessage`.
- A failing turn prints `error: <describe_error>` and the loop continues.
- Exit codes: `0` ok, `2` configuration error (`ConfigError`), `1` other startup
  error. `--list-models` uses `load_settings(require_model=False)`.
- `close_default_client()` runs in a `finally`.

## Tests

`tests/test_cli.py`: history across turns, tool use, SDK errors and failed runs
keep the session alive, missing credentials, `--list-models` output and errors.
