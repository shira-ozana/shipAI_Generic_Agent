# `agents/requirement/` - the Requirement Agent

`src/generic_agent/agents/requirement/`

## What it is

An agent built on the Generic Agent runtime that turns an unstructured customer
request into a short list of numbered, unambiguous **business** requirements
for the Architect Agent. It researches a clone of the customer repository
(branch `ShipAi`, starting from the Init Agent's Markdown under `.platform/`),
asks the user 2–3 clarification questions per round, asks the user to approve
the validated requirements, and publishes:

```
.platform/{feature_id}/requierment/      # "requierment" is the intended spelling
  requirement.md  clarifications.md  status.json   # status.json = {"status": "<value>"}
```

committed and pushed to `ShipAi`. It never hands off; the UI moves on.

| Status | Meaning for the UI |
| --- | --- |
| `in_progress` | The agent is working (set by code at start and when a user reply arrives) |
| `awaiting_user` | The reply holds clarification questions; send the answers |
| `awaiting_approval` | The reply holds the requirements; send approval or the changes wanted |
| `completed` | The user approved; `requirement.md` is final and published |
| `failed` | Pre-flight failed or the run cannot continue |

`awaiting_approval` is an addition to the original specification (which went
straight from validation to `completed`).

## Inputs

| Input | API field (`/api/requirement/start`) | CLI |
| --- | --- | --- |
| `repo_url` | `repoUrl` (no credentials inside the URL) | `--repo-url` |
| credentials | `credentials: {"username", "token"}` (optional) | env / `.env`: `REQUIREMENT_GIT_USERNAME`, `REQUIREMENT_GIT_TOKEN` |
| `feature_id` | `featureId` (one safe path segment: `[A-Za-z0-9][A-Za-z0-9._-]*`, max 100) | `--feature-id` |
| `requirement_text` | `requirementText` | `--requirement-text` or `--requirement-file` |

Credentials are used only for git: they live in the process's memory
(`SecretStr`) and reach git only through the child-process environment and a
`git -c credential.helper=...` option, which is never written to `.git/config`.
They never enter graph state, checkpoints, prompts, tool output, logs, errors,
commit messages or remote URLs. API validation errors never echo request input.

## How to run it

```bash
# HTTP API (same server as /api/chat)
python -m uvicorn generic_agent.api:app --host 127.0.0.1 --port 8000

# CLI: first turn, then type the answers to each question round
python -m generic_agent requirement --repo-url https://github.com/org/repo \
  --feature-id ORD-42 --requirement-text "Customers can cancel an order before it ships."
```

| Request | Success | Errors |
| --- | --- | --- |
| `POST /api/requirement/start` `{"threadId","repoUrl","featureId","requirementText","credentials"?}` | `200 {"threadId","reply","status"}` | `422` invalid input; `400` pre-flight failed (clear message, model not called); `409` thread busy or already started; `502` run failed |
| `POST /api/requirement/chat` `{"threadId","message"}` | `200 {"threadId","reply","status"}` | `422`; `404` no run on this thread; `409`; `502` |

The reply of a question round contains the questions and `status` is
`awaiting_user`; the user's next message on the same `threadId` (via
`/api/requirement/chat`) contains the answers. When the requirements are ready,
the reply lists them and `status` is `awaiting_approval`; the user's next
message approves them or says what to change.

Optional settings (shell environment):

| Variable | Default | Meaning |
| --- | --- | --- |
| `REQUIREMENT_AGENT_WORKDIR` | `<temp>/generic-agent-requirement` | Parent of the per-thread clones (`<workdir>/<threadId>`) |
| `REQUIREMENT_AGENT_INIT_GLOB` | `.platform/**/*.md` | The Init-output check: at least one match, ignoring this feature's own output folder |

## How it works

| File | Responsibility |
| --- | --- |
| `instructions/AGENTS.md` | Main instructions (role, inputs, outputs, flow, skills table, global rules). |
| `instructions/skills/<name>/SKILL.md` | The 8 skills: understanding, research, gap detection, questions, definition, validation, approval, publishing. |
| `instructions/templates/` | `requirement-template.md`, `clarifications-template.md`, `status-template.json`. |
| `loader.py` | `load_instruction_parts(root=None)`: main file, then skills sorted by folder name, then templates sorted by file name. `root` points at another folder. |
| `inputs.py` | `RequirementInputs`, `GitCredentials`, `Status`, `RequirementSettings`, validation. |
| `gitops.py` | `GitRepo`: clone, commit given paths, push with one rebase-and-retry, never force; scrubs secrets from errors. |
| `workspace.py` | `Workspace`: confined read (`list_files`, `read_file`, `search_files`; no absolute paths, `..`, `.git` or escaping symlinks), restricted writes (`requirement.md` / `clarifications.md`, `status.json` via an enum), `publish(label)`. |
| `tools.py` | `requirement_tools(workspace)`: the six Cursor custom tools. No shell, no other writes. |
| `agent.py` | `RequirementAgent`: pre-flight in code, then each turn on `GenericAgent` with an `AgentDefinition` whose `tools` are bound to the thread's clone. |
| `cli.py` | `python -m generic_agent requirement ...`. |

Turn sequence:

1. **Pre-flight (code, first turn only)**: validate inputs; clone `ShipAi` into
   `<workdir>/<threadId>`; check Init output. On failure: return a clear
   message, write and publish `failed` if the clone exists, don't call the model.
2. Code sets `in_progress` and publishes (`requirement-agent({feature_id}): started`).
3. Model turn: the instructions drive the flow through the tools. A question
   round ends with `awaiting_user` published (`round N questions`).
4. Next user message: if status is `awaiting_user`, code sets `in_progress` and
   publishes (`answers received`), then runs the model again on the same clone.
5. After validation passes, the model writes `requirement.md`, sets
   `awaiting_approval` and publishes (`approval requested`).
6. The user's reply: code sets `in_progress` and publishes (`approval response
   received`). Approved → the model sets `completed` and publishes
   (`completed`); changes requested → back into the flow and a new approval
   round.

Approval guard (code): `set_status completed` is rejected unless an approval
request was sent, the user replied after it, and `requirement.md` is
byte-identical to the version sent for approval. Whether the reply is a "yes" is
decided by the model (`requirement-approval` skill).

The clone and the credentials are kept per thread in process memory (like the
API's checkpointer): run one worker; after a restart, start a new thread.

## Changing the instructions or skills

Edit the Markdown under `instructions/`; no Python change is needed. A new
skill is a new `skills/<name>/SKILL.md` (frontmatter `name`, `description`;
sections Purpose, When, Steps, Output, Rules) and is picked up automatically;
mention it in `AGENTS.md`'s flow and skills table. Tool names used in the
instructions must match `tools.py`. `tests/test_requirement_agent.py` checks
that every skill and template is loaded.

## Tests

`tests/test_requirement_agent.py` (local bare git remote, mocked Cursor):
loader, the `AgentDefinition.tools` extension, pre-flight failures, tool path
confinement, write restrictions, `status.json`, publishing (only the output
folder, `ShipAi`, one retry, never force), the approval guard, questions,
answers, approval and requested changes on the same clone, credential leaks,
the API and the CLI.
