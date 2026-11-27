# `agents/task_split/` - the Task Split agent

`src/generic_agent/agents/task_split/` and `src/generic_agent/prompts/task_split/`

## Purpose

Takes HLS documents plus read-only access to one repository revision and returns
**either** a dependency document (tasks grouped by owning component or service,
with direct prerequisites) **or** focused clarification questions. It runs on the
existing runtime (`GenericAgent` + `AgentDefinition`); the graph is unchanged.
A planning run ends with a *draft*; it never saves tasks (see
[task-plans.md](task-plans.md)).

## Spec to code

| Specification | Where |
| --- | --- |
| Runtime prompt in five stages (read HLS, inspect repo, split by owner, map dependencies, check coverage and emit) | `prompts/task_split/prompt.md` |
| Skills: HLS analysis, repository inspection, task decomposition, acceptance criteria, dependency mapping (method + example each) | `prompts/task_split/skills/*.md`, order in `definition.SKILL_NAMES` |
| Rules: one owning group per task, one deliverable per task, dependency = prerequisite to start, clarify instead of guess | `prompts/task_split/rules.md` (one `- ` item per rule) |
| Repository access: read-only, scoped to the authorized project, no credentials in the model input | `repository.py` (`RepositoryAccess`, tools `repo_list_dir` / `repo_read_file` / `repo_search`) |
| Dependency document and its validation | `schema.py` |
| Guardrails enforced in the application | `definition.py` (pre/post-run guardrails), `schema.validate_document`, `repository.py` |
| Run: request in, draft or questions out | `planner.py` (`TaskSplitPlanner.plan`) |

## Output contract

One JSON object (the prompt spells out every field):

- `{"status": "plan", "plan": {hls_references, groups[], tasks[], discrepancies[]}}`
- `{"status": "clarification", "questions": [{question, why_it_matters, hls_references, repo_paths}]}`

Plan versions and the repository revision are **not** model output; the backend
assigns the version and records the revision it exposed.

## Guardrails (all in application code)

| Guardrail | Enforced by |
| --- | --- |
| Input is a planning request with at least one HLS document | pre-run `planning-request-well-formed` |
| No credentials/tokens/private keys in the model input (also checked in `TaskSplitPlanner.plan`, so it surfaces as bad input) | pre-run `no-credentials-in-input` |
| Repository reads only inside the checkout (and optional `allowed_paths`); no `..`, absolute paths or escaping symlinks; `.git`, `.env*`, keys and similar never readable; size and count limits; no shell, network or writes | `RepositoryAccess` |
| Only the repo tools are available; Cursor built-in tools stay off (`tools=[]`) and the run uses a scratch `cwd` | `GenericAgent` defaults + `planner.py` |
| Each task has exactly one owning group that exists; every group owns a task | `schema.validate_document` |
| Group IDs and task IDs unique and well formed | `schema.validate_document` |
| Every dependency references an existing, different task, once; no cycles | `schema.validate_document`, `find_cycle` |
| HLS citations name supplied documents; repository citations exist inside the authorized scope; existing groups/tasks need repository evidence (proposed ones do not) | `schema.validate_document` with `hls_document_ids` and `repository` |
| Validation messages quote only short identifier-like values, never free text from the reply | `schema._ref` |

Repository content, HLS text and tool output are untrusted data (a rule in the
prompt, and closing tags in the request are neutralised so text cannot break the
request structure).

## Repository access model

The host prepares a **checkout of the selected revision** (clone and credentials
are the host's business, outside the model input) and registers it server-side
(`TASK_SPLIT_REPOSITORIES`, see [config.md](config.md)). `RepositoryAccess.from_checkout`
verifies a git work tree's HEAD against the declared revision (local read-only git
commands) and records it. The model only ever sees the repository ID and revision.

## Changing it

- Wording of stages, rules or skills: edit the `.md` files; keep a stage-numbered
  prompt and add new skills to `SKILL_NAMES`.
- New output fields: update `schema.py` (parse + `to_dict`), the prompt's JSON
  example, `task_plans/review.py`, and the store tables if they must be persisted.
- A new guardrail goes in `definition.py` (text checks) or `schema.py`
  (document checks). Never weaken one to make a test pass.
- Never put a repository path, URL, token or the Cursor key in a prompt or tool
  result.

## Tests

`tests/test_task_split_schema.py`, `tests/test_task_split_repository.py`,
`tests/test_task_split_agent.py`. Fixtures: `tests/fixtures/task_split/` (two HLS
documents, a small repository with `services/order` and `services/notification`,
and the expected model reply).
