---
id: plan-harness
kind: skill
node: plan_harness
priority: 50
---
# Skill: plan the harness

When to use: you receive the analysis, skills, automation results, the existing
guidance files and the list of harness files already present in the repository.

Steps:
1. Always include `AGENTS.md`, `ARCHITECTURE.md` and `RULES.md`. Paths in the plan
   are relative to the repository root (`AGENTS.md` is the root file).
2. Add `GUIDELINES.md` when the repository shows more than one valid pattern for
   the same task.
3. Add `<code-folder>/AGENTS.md` for every folder in "Required local AGENTS.md", and
   for other folders with a unique contract. The file goes inside the folder.
   `<code-folder>` is the exact repository path of an existing folder.
4. Do not plan `SKILL.md` files or `HOOKS.md`. The caller renders them. A skill goes to
   `<code-folder>/skills/<name>/SKILL.md` of the deepest folder with a local
   `AGENTS.md` that holds its whole scope, else to `skills/<name>/SKILL.md` at the root.
   You may link to them.
5. For a file that already exists, use `update` and keep its human content, or
   `keep` when it is already correct.
6. For each file give `scope`, `purpose` and `sources`. Leave `content` empty.
7. Fill `conflicts`, `recommendations` and `coverage_gaps`. Set `rag.recommended`
   to true only when relevant evidence stays hard to find after the split by
   component, or when a large scattered documentation set needs repeated retrieval.
