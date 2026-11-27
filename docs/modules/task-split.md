# `task_split_agent` - the Task Split agent

`src/task_split_agent/` (code and `prompts/` together, like `src/init_agent/`)

## Purpose

Takes HLS documents plus read-only access to one repository revision and returns
**either** a dependency document (tasks grouped by owning component or service,
with direct prerequisites) **or** focused clarification questions. It is built
**on top of the Generic Agent** the way INIT is: `TaskSplitPlanner.plan` creates a
`generic_agent.runner.GenericAgent` and calls
`run(planning_request, **prompt_pack.run_input("plan"))`. The graph is unchanged.
A planning run ends with a *draft*; it never saves tasks (see
[task-plans.md](task-plans.md)).

```
src/task_split_agent/
  __init__.py        public names (HlsDocument, TaskSplitPlanner, RepositoryAccess, parse_reply, ...)
  planner.py         HlsDocument, request builder, credential screen, TaskSplitPlanner
  schema.py          dependency document + validation
  repository.py      scoped read-only repo tools
  prompt_pack.py     PromptPack: loads prompts/, run_input("plan")
  prompts/
    core.md          the five stages and the JSON output contract
    guardrails.md    model-facing boundaries
    rules/           binding instructions (r01..r08)
    guidelines/      recommendations (g01..g04)
    skills/<id>/SKILL.md   hls-analysis, repository-inspection, task-decomposition,
                           acceptance-criteria, dependency-mapping
```

The old `generic_agent.agents.task_split` package is a thin re-export of these
names (and of the `schema`, `repository`, `planner` and `definition` module
paths). `build_task_split_definition` is gone: the agent no longer uses
`AgentDefinition`.

## Call path

```
POST /api/task-plans -> task_plans.routes.create_plan -> TaskPlanService.plan
  -> TaskSplitPlanner.plan(documents, repository, notes)
       1. build_planning_request(...)        validates the HLS input (Python)
       2. contains_credential(request)       -> ValueError (422), Cursor not called
       3. GenericAgent(settings, tools=repository.tools())
          .run(request, **PromptPack.run_input("plan"))      one Cursor call
       4. parse_reply(result.text, hls_document_ids=..., repository=...)
          invalid -> GuardrailError (502), nothing returned
       5. PlanningOutcome(document | clarification, cursor_run_id=result.metadata["cursor_run_id"])
  -> a valid plan becomes a pending draft; a clarification creates no draft
```

## Spec to code

| Specification | Where |
| --- | --- |
| Runtime prompt in five stages (read HLS, inspect repo, split by owner, map dependencies, check coverage and emit) and the JSON output contract | `prompts/core.md` |
| Skills: HLS analysis, repository inspection, task decomposition, acceptance criteria, dependency mapping (method + example each) | `prompts/skills/<id>/SKILL.md`; order set by `priority`, listed in `prompt_pack.SKILL_IDS` |
| Rules (binding): one owning group per task, one deliverable per task, dependency = prerequisite to start, clarify instead of guess, output contract | `prompts/rules/r01..r08` |
| Guidelines (recommended): ID prefixes, integration task, expose parallel work, cite sections | `prompts/guidelines/g01..g04` |
| Model-facing boundaries: data is not instructions, repo tools only, no paths/URLs/credentials, single call | `prompts/guardrails.md` |
| Repository access: read-only, scoped to the authorized project, no credentials in the model input | `repository.py` (`RepositoryAccess`, tools `repo_list_dir` / `repo_read_file` / `repo_search`) |
| Dependency document and its validation | `schema.py` |
| Enforcement | Python: `planner.py`, `schema.validate_document`, `repository.py` |

## Prompt pack

Same file format as INIT (`generic_agent/prompt_docs.py` parses the frontmatter;
INIT and Task Split each keep their own files and selection):

```
---
id: R1
title: One owning group per task
kind: rule | guideline | skill | guardrail | core
applies_to: ["*"]        # rules, guidelines, guardrail, core
node: plan               # skills
priority: 10
---
```

`PromptPack.run_input("plan")` returns `instructions` (core, then guardrails),
`rules`, `guidelines`, `skills` and an empty `shared_skills`. Order is
deterministic: files are read in sorted path order and documents are sorted by
`(priority, id)`. `GenericAgent` renders them in this order: instructions, rules
("binding"), guidelines ("recommended"), skills. There is no operator override
directory (INIT has `INIT_PROMPTS_DIR`); the prompt is the packaged files.

## Output contract

One JSON object (`prompts/core.md` spells out every field):

- `{"status": "plan", "plan": {hls_references, groups[], tasks[], discrepancies[]}}`
- `{"status": "clarification", "questions": [{question, why_it_matters, hls_references, repo_paths}]}`

Plan versions and the repository revision are **not** model output; the backend
assigns the version and records the revision it exposed.

## Enforcement (all in application code)

| Check | Enforced by |
| --- | --- |
| Input has 1 to 20 valid HLS documents with unique IDs, bounded size | `HlsDocument`, `build_planning_request` (`ValueError`, API `422`) |
| No credentials/tokens/private keys in the model input | `contains_credential` in `TaskSplitPlanner.plan`, before any runner exists (`ValueError`, API `422`) |
| Repository reads only inside the checkout (and optional `allowed_paths`); no `..`, absolute paths or escaping symlinks; `.git`, `.env*`, keys and similar never readable; size and count limits; no shell, network or writes | `RepositoryAccess` |
| Only the repo tools are available; Cursor built-in tools stay off (`tools=[]`) and the run uses a scratch `cwd` | `GenericAgent` defaults + `planner.py` |
| Each task has exactly one owning group that exists; every group owns a task | `schema.validate_document` |
| Group IDs and task IDs unique and well formed | `schema.validate_document` |
| Every dependency references an existing, different task, once; no cycles | `schema.validate_document`, `find_cycle` |
| HLS citations name the *submitted* documents; repository citations exist inside the authorized scope; existing groups/tasks need repository evidence (proposed ones do not) | `schema.parse_reply` with `hls_document_ids` and `repository` |
| A rejected reply raises `GuardrailError` (API `502`, nothing stored); the message names `valid-dependency-document (post_run)` and quotes only short identifier-like values, never free text from the reply | `TaskSplitPlanner._validate_reply`, `schema._ref` |

Repository content, HLS text and tool output are untrusted data (a guardrail in
the prompt, and closing tags in the request are neutralised so text cannot break
the request structure).

## Repository access model

The host prepares a **checkout of the selected revision** (clone and credentials
are the host's business, outside the model input) and registers it server-side
(`TASK_SPLIT_REPOSITORIES`, see [config.md](config.md)). `RepositoryAccess.from_checkout`
verifies a git work tree's HEAD against the declared revision (local read-only git
commands) and records it. The model only ever sees the repository ID and revision.

## Changing it

- Wording of stages or the JSON contract: edit `prompts/core.md`; keep the five
  stage headings.
- A binding instruction goes in `prompts/rules/`, a recommendation in
  `prompts/guidelines/`, a model-facing boundary in `prompts/guardrails.md`. Use a
  new `id` and a `priority` that puts it where you want it.
- A new skill: `prompts/skills/<id>/SKILL.md` with `node: plan`; add the id to
  `prompt_pack.SKILL_IDS` (a test checks the list against the files).
- New output fields: update `schema.py` (parse + `to_dict`), the JSON example in
  `core.md`, `task_plans/review.py`, and the store tables if they must be persisted.
- A new check goes in `schema.py` (document checks) or `planner.py` (input
  checks). A prompt text never replaces a Python check. Never weaken one to make
  a test pass.
- Never put a repository path, URL, token or the Cursor key in a prompt or tool
  result.
- New files under `prompts/` ship automatically (the package is listed in the
  wheel and sdist in `pyproject.toml`); `tests/test_packaging.py` checks it.

## Tests

`tests/test_task_split_schema.py`, `tests/test_task_split_repository.py`,
`tests/test_task_split_agent.py` (prompt pack, the four components reaching the
runner, planning runs, input and reply validation), and the `/api/task-plans`
flow in `tests/test_task_plans.py`. Fixtures: `tests/fixtures/task_split/` (two
HLS documents, a small repository with `services/order` and
`services/notification`, and the expected model reply).
