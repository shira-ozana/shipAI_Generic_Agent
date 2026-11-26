---
id: write-agents-md
kind: skill
node: draft_file
files: ["AGENTS.md"]
priority: 50
---
# Skill: write AGENTS.md

Root `AGENTS.md` (the entry point for agents), in this order:
1. One paragraph: what the system is, and that harness files live in `{platform_dir}/`.
2. `## Component map`: a table with component, path, responsibility, and a link
   to the local `AGENTS.md` when one exists.
3. `## Commands`: setup, start, build, test, each marked documented or executed.
4. `## Rules`: a short list that links to `RULES.md`.
5. `## Skills`: a list that links to each `skills/<name>/SKILL.md` with one line on
   when to use it. Include shared skills by reference.
6. `## Hooks`: one line per hook with its status, or a link to `HOOKS.md`.
7. `## Existing guidance`: links to README, CLAUDE.md, docs and other guidance.

Local `areas/<code-folder>/AGENTS.md`:
1. `## Scope`: the code folder and what it does.
2. `## Read first`: the source files that define the folder's behavior.
3. `## Use these skills` and `## Rules that apply`, with links.
4. `## Check a change`: the narrowest command or test that verifies a change here.
