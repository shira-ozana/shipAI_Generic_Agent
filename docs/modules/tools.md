# `tools.py` - Cursor custom tools

`src/generic_agent/tools.py`

## Purpose

Defines the local `cursor_sdk.CustomTool`s the agent may call. Today there is
one example, `word_count`.

## Key symbols

| Symbol | What it does |
| --- | --- |
| `count_words(text)` | Pure helper, whitespace-separated word count. |
| `WORD_COUNT` | The `CustomTool`: description, JSON input schema, `execute`. |
| `default_tools()` | Name -> tool mapping used by the CLI, Studio and API. `{}` means text-only. |

## How it runs

Tools are passed through `build_agent(..., tools=...)` into
`LocalAgentOptions.custom_tools`. The SDK serves them from a loopback callback
server in this process while a run executes. Tool calls happen inside Cursor and
are not saved as `ToolMessage`s.

## Adding a tool

1. Write a pure function plus an `execute(args, context)` that validates `args`
   (raise `ValueError` for bad input) and returns a **string**.
2. Create the `CustomTool` with a clear `description` and an `input_schema`
   (`type: object`, `properties`, `required`).
3. Register it in `default_tools()`.
4. Mention it in `prompts/default_system.md` if the agent needs a nudge to use it.
5. Add tests in `tests/test_graph.py` style (call the registered tool from the
   fake Cursor responder via `options.local.custom_tools`).

## Constraints

- Cursor's built-in shell/file tools stay disabled; custom tools are the only
  way the agent acts. Keep them side-effect-free unless there is a strong reason.
- Never read or return credentials from a tool.
