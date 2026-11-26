# `prompts/default_system.md` - agent instructions

`src/generic_agent/prompts/default_system.md`

## Purpose

The default instructions for the agent. Shipped as package data and loaded by
`graph.load_default_prompt()` through `importlib.resources`.

## Current content (summary)

- Be helpful and concise.
- Answer directly; call a matching tool (e.g. `word_count`) instead of guessing
  and then report the result in plain language.
- No file or shell access; never claim to have read or changed anything on disk.
- Say so when unsure or when a tool fails.

## Rules

- Keep long instructions in this file, not inline in Python.
- Instructions are rendered into every Cursor run's prompt (inside
  `<instructions>`) and are never stored in graph state or checkpoints.
- Keep it consistent with the tool set and the disabled built-in tools: do not
  promise capabilities the agent does not have.
- Different instructions for another agent: pass your own string to
  `build_agent(settings, "...", tools)` rather than editing this file.

## Tests

`tests/test_graph.py::test_default_prompt_is_loaded_from_package` and
`tests/test_packaging.py::test_prompt_file_is_inside_the_package` (the file must
be inside the package so it ships with the wheel).
