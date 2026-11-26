# `studio.py` and `langgraph.json` - Studio / Agent Server

`src/generic_agent/studio.py`, `langgraph.json`

## Purpose

Lets `langgraph dev` (LangGraph Studio / Agent Server) serve the same graph as
the CLI.

## How it fits together

`langgraph.json` registers the graph `agent` as
`./src/generic_agent/studio.py:make_graph`. `make_graph()` returns
`build_agent(load_settings(), load_default_prompt(), default_tools())`.

## Rules

- **Do not pass a checkpointer.** Agent Server injects persistence; adding one
  would conflict with it.
- Settings come from the shell or the `.env` in the directory where
  `langgraph dev` runs.
- Studio's hosted UI needs a LangSmith login in the browser. That is separate
  from the Cursor key; the CLI, API and tests never need it.
- Keep `langgraph.json` pointing at `make_graph`; changing the function name or
  path breaks Studio and the packaging test.

## Tests

`tests/test_packaging.py`: `langgraph.json` points at the factory, and the
factory builds a graph without a checkpointer.
