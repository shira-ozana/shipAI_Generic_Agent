# Module guides

One short guide per piece of code, written for an agent (or person) about to
change it. Read [`../architecture.md`](../architecture.md) first for the big
picture, then the guide for the piece you are touching.

| Guide | Code | Read it when you... |
| --- | --- | --- |
| [graph.md](graph.md) | `src/generic_agent/graph.py` | change how a turn runs, how prompts are built, or which Cursor options are used |
| [graph.md](graph.md#genericagentrun) | `src/generic_agent/runner.py` | change `GenericAgent.run()`, the entry other agents (INIT) call with prompt, rules, guidelines and skills |
| [config.md](config.md) | `src/generic_agent/config.py` | touch credentials, `.env` loading, model listing or error text |
| [tools.md](tools.md) | `src/generic_agent/tools.py` | add or change a Cursor custom tool |
| [prompt.md](prompt.md) | `src/generic_agent/prompts/default_system.md` | change the agent's instructions |
| [cli.md](cli.md) | `src/generic_agent/__main__.py` | change the terminal chat or `--list-models` |
| [studio.md](studio.md) | `src/generic_agent/studio.py`, `langgraph.json` | change the LangGraph Studio / Agent Server entry |
| [api.md](api.md) | `src/generic_agent/api.py` | change the local HTTP API used by `sdlc-ui` |
| [init_agent.md](init_agent.md) | `src/init_agent/` | change the INIT agent: graph, prompt pack (rules, guidelines, skills), harness writer, `/api/init` |
| [tests.md](tests.md) | `tests/` | add or change tests or the Cursor mock |

## Rules that apply everywhere

- Dependency direction: `__main__` / `studio` / `api` -> `config`, `tools`,
  `graph`; `runner` -> `graph`, `config`; `graph` -> `config`. Never the other way around.
- `graph.py` is the only module that calls `Agent.prompt`.
- Every entry point builds its graph with `build_agent`. None of them
  re-implements the node.
- LangGraph checkpoints are the only conversation memory.
- The Cursor API key stays a `SecretStr`, never reaches state, logs, responses
  or error text, and `.env` is never printed.
- Tests are offline: mock `generic_agent.graph.Agent`, no network, no LangSmith.
- Keep `docs/architecture.md` and these guides in sync with code changes.
