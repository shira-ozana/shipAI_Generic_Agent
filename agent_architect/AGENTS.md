# AGENTS.md

Architect Agent of the SDLC platform. Read `README.md` first.

## Rules

- The generic agent (`generic_agent`, at the repository root) is a dependency.
  Never copy or modify it from this package; architect behaviour goes in this package
  (prompt, tools, graph). Other SDLC agents reuse the same generic agent.
- The architect is an `AgentDefinition` (prompt + rules + skills) in `llm.py`,
  run by `generic_agent.GenericAgent`. Only `llm.py` touches the generic agent.
- Behaviour lives in Markdown: `prompts/architect_system.md` (role, two steps)
  and `prompts/skills/NN-name.md` (one skill per file, loaded in name order).
- Two human checkpoints (`interrupt()`): `clarify_requirement` (stage
  `clarify`, resume `{"answers": str}`) and `human_review` (stage `review`,
  resume `{"approved": bool, "feedback": str}`).
- The model never writes files: it returns JSON (`design.py`), and
  `graph.write_outputs` writes the `.platform` files. Validate every
  `architecture_path` with `platform_paths.validate_architecture_path`.
- Repository tools are read-only and confined to the clone (`repo_tools.py`).
  Cursor built-in shell/file tools stay disabled.
- Credentials: never log, print or store the GitHub token or keys in graph state.
  The token is used only via `git -c http.extraHeader` and redacted from errors.
- Inputs follow the Requirement Agent: same `feature_id` rule, reads
  `.platform/{feature_id}/requierment/` on the `ShipAi` branch.
- Tests are offline: fake LLM, local bare git repos, mocked Supabase.

## Commands

```powershell
.venv\Scripts\activate
pip install -e ..                 # generic agent (repo root)
pip install -e ".[dev]"
python -m pytest
python -m agent_architect --username <u> --repo-url <url> --feature-id <uuid>
```
