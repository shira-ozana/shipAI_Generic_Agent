# `init_agent` - INIT agent (repository harness builder)

`src/init_agent/`

## Purpose

INIT takes an existing repository (`REPO_URL` in `.env`), analyzes it, and writes an
agent harness into the repository itself on a new branch `shipAI`:

- at the root: `AGENTS.md`, `ARCHITECTURE.md`, `RULES.md`, `HOOKS.md` and the other
  catalog files, `skills/<name>/SKILL.md`, `adr/<decision>.md`;
- next to the code: `<code-folder>/AGENTS.md`, only in folders that already exist. Every
  folder that holds `INIT_LOCAL_AGENTS_MIN_COMPONENTS` (default 3) or more components gets one,
  even when the model leaves it out of the plan;
- skills next to their code: `<code-folder>/skills/<name>/SKILL.md` in the deepest folder with
  a local `AGENTS.md` that holds the skill's whole scope, else `skills/<name>/SKILL.md` at the
  root. A skill INIT wrote elsewhere in an earlier run is removed when its file holds only the
  INIT block (action `removed`); a file with human content is kept;
- the report only under `platform/init/` (`INIT.md`, `status.json`).

Agent tools (Cursor, Claude Code, Codex) find `AGENTS.md` at the root and in the folder
they work in. Requirements and Architect start from the report. The writer
(`repo/writer.py`) still accepts only catalog Markdown names, never touches code, configs
or CI, and still reads the older `platform/...` and `areas/<folder>/AGENTS.md` forms.

It is built **on top of the Generic Agent**: `arun_init` creates one
`generic_agent.GenericAgent` instance (or uses `arun_init(..., agent=...)`), and every LLM
node calls `agent.arun(task, **prompt_pack.run_input(node, ...))`. `run_input` returns the
node's `instructions` (core + guardrails), `rules`, `guidelines`, `skills` and
`shared_skills`; `GenericAgent` renders them into `<instructions>` of one Cursor run with
no checkpointer. JSON repair retries go back through `run(..., history=[...])`. Cursor built-in tools stay disabled: the model only reads what INIT sends
and never touches files. Python reads, validates and writes everything.

## Graph

```
START -> prepare_workspace -> scan_repository -> analyze_repository -> derive_skills
      -> assess_automation -> plan_harness -> assess_coverage -> apply_harness
      -> repair_links -> write_init_report -> publish_branch -> END
```

| Node | LLM | What it does |
| --- | --- | --- |
| `prepare_workspace` | - | `git clone REPO_URL` at `REPO_BASE_REF`, checkout `shipAI` (reuses it if it already exists on the remote). With `project.repo_path` it uses a local copy (no clone, no push). |
| `scan_repository` | - | Inventory: manifests, lockfiles, build config, entry points, tests, CI, hooks, guidance (`AGENTS.md`, `CLAUDE.md`, `.cursor/rules`...), package scripts, README commands, components. |
| `analyze_repository` | yes | One overview call, then per component calls; excerpts packed into the context budget (`INIT_CONTEXT_BUDGET_CHARS`). |
| `derive_skills` | yes | Local skills with evidence, shared skills by reference, `needs_evidence` otherwise. |
| `assess_automation` | yes | Active and proposed hooks, verification commands. Runs only `inputs.verify_commands`. Code forces new hooks to `proposed` and unexecuted commands to `documented`. |
| `plan_harness` | yes | File manifest, then one Markdown draft call per file. Each draft gets a link table with the exact relative target of every file that will exist (skill `link-harness-files`). `SKILL.md` and `HOOKS.md` are rendered from structured data. |
| `assess_coverage` | - | Gaps, components not analyzed, files left out of the budget, RAG alert. |
| `apply_harness` | - | Catalog allowlist (root files, `skills/`, `adr/`, `<existing-code-folder>/AGENTS.md`), no `..`, nothing under `platform/` except the report, managed block `<!-- init-agent:begin/end -->` keeps human content. |
| `repair_links` | yes | Checks the links in each generated block. A block with broken links goes back to the Generic Agent once (skill `repair-links`, with the link table and candidates found on disk). Links still broken become plain text and are reported as `info` alerts. Human content outside the block is never changed. Skipped on dry run. |
| `write_init_report` | - | `INIT.md` from the actual write results, link check, unknown evidence, STE100 sentence-length check, `status.json`. |
| `publish_branch` | - | Commit the written harness files and `platform/init/`, and push `shipAI` (`GIT_TOKEN`, optional `PUSH_URL`). |

## Prompt pack (`src/init_agent/prompts/`)

| Kind | Files | Selected by |
| --- | --- | --- |
| core | `core.md` (the INIT core prompt) | always |
| guardrails | `guardrails.md` | always |
| rules (binding) | `rules/r01..r10` | `applies_to` node; `files` for drafts |
| guidelines | `guidelines/g01..g06` | `applies_to` node |
| skills | `skills/<id>/SKILL.md` | `node`, and `files` for `draft_file` |

Order in the system prompt: core, guardrails, rules, guidelines, the node's skill, operator
`extra_rules` / `extra_guidelines` from the request, then the shared skill catalog. The JSON
schema goes in the task message. `INIT_PROMPTS_DIR` adds files or replaces them by `id`
without code changes. The result lists the prompt ids used per node (`output.prompt_pack`).

## HTTP API (mounted into `generic_agent.api`, same port as `/api/chat`)

| Endpoint | Result |
| --- | --- |
| `POST /api/init` (body: `InitRequest`, all optional) | `202 {"runId","status":"running","statusUrl"}` |
| `POST /api/init?wait=true` | the full `InitResult` |
| `GET /api/init/{runId}` | `status`, `currentNode`, `progress[]`, `result` |
| `GET /api/init/{runId}/report` | `platform/init/INIT.md` (text/markdown) |

`InitResult` follows `01-init/expected.json` (`stage`, `feature_id`, `status`, `artifacts`,
`output.project_context`) and adds `output.files`, `alerts`, `rag_alert`, `branch`, `commit`,
`pushed`, `progress`. It returns paths only, not file contents.

## Run

```bash
python -m init_agent                         # clone REPO_URL, write, push shipAI
python -m init_agent --dry-run               # plan and draft, write nothing
python -m init_agent --repo-path ../some/repo --request 01-init/request.json
```

## Tests

`tests/test_init_agent.py`: offline, through the shared `mock_cursor` fixture. Covers the
full run, path rejection, human content preservation, RAG alert, dry run, LLM failure,
clone, commit and push to a local bare repo, prompt-pack overrides, and the API.
