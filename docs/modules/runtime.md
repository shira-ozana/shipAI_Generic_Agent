# `runtime/` - agent definitions and the `GenericAgent` runner

`src/generic_agent/runtime/` (`agent_definition.py`, `generic_agent.py`)

## Purpose

Shared foundation for agents that want guardrails run by the graph. (Task Split no
longer uses it: it calls `runner.GenericAgent` like INIT and keeps its checks in
Python; see [task-split.md](task-split.md).) An `AgentDefinition` (prompt, skills,
rules, guardrails) is passed to `GenericAgent.run(...)` on each call and reaches
the existing `run_cursor_agent` node as LangGraph runtime context. See
[`../architecture.md`](../architecture.md#runtime-interface) for the full
contract.

## Key symbols

| Symbol | What it does |
| --- | --- |
| `AgentDefinition(prompt, skills=(), rules=(), guardrails=())` | Frozen, validated definition. `prompt` must be non-blank; collections may be empty. |
| `Skill(name, content)`, `Guardrail(name, phase, check)` | Value objects. `GuardrailPhase` is `pre_run` / `post_run`; `check(text)` returns `bool` or `GuardrailResult`. |
| `AgentContext(definition)` | The runtime context passed with `graph.invoke(..., context=...)`. |
| `assemble_instructions(definition)` | Prompt, then rules, then skills; fed to `render_prompt`. |
| `run_guardrails(definition, phase, text)` | Raises `GuardrailError` on the first failed or raising check. |
| `GenericAgent(settings, tools, *, checkpointer=None, max_history_messages=None, builtin_tools=())` | Builds the graph once. `run(definition, messages, config=None)` returns the `graph.invoke()` result. |

## Rules

- `agent_definition.py` must not import `graph` (the graph imports it);
  `GenericAgent` imports `graph` lazily for the same reason.
- Definitions never go into graph state, checkpoints or the runner; only into
  per-call context.
- `runtime/` never calls `Agent.prompt`; `graph.py` does.
- Guardrail errors must not include user text, replies or secrets.
- Put concrete agents under `src/generic_agent/agents/<name>/` (first one: [task-split.md](task-split.md)).

## Tests

`tests/test_runtime.py`.
