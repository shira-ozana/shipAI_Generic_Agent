---
id: write-agents-md
kind: skill
node: draft_file
files: ["AGENTS.md"]
priority: 50
---
# Skill: write AGENTS.md

Root `AGENTS.md` (the entry point for agents), in this order:
1. One paragraph: what the system is, and that local `AGENTS.md` files sit in the code
   folders and the INIT report is `{platform_dir}/init/INIT.md`.
2. `## Component map`: a table with component, path, responsibility, and a link
   to the local `AGENTS.md` when one exists.
3. `## Commands`: setup, start, build, test, each marked documented or executed.
4. `## Rules`: a short list that links to `RULES.md`.
5. `## Skills`: a list that links to every skill in the link table (root
   `skills/` and `<code-folder>/skills/`), grouped by folder, with one line on when to
   use it. Include shared skills by reference.
6. `## Hooks`: one line per hook with its status, or a link to `HOOKS.md`.
7. `## Existing guidance`: links to README, CLAUDE.md, docs and other guidance.

Local `<code-folder>/AGENTS.md` (inside the code folder):
1. `## Scope`: the code folder and what it does.
2. When the task lists "Components inside this folder": `## Components`, a table
   with each component, its responsibility, and a link to its local `AGENTS.md` when
   one is in the link table.
3. `## Read first`: the source files that define the folder's behavior.
4. `## Use these skills`: first the skills that live in this folder
   (`skills/<name>/SKILL.md` next to this file), then root skills that apply here.
   Then `## Rules that apply`, with links.
5. `## Check a change`: the narrowest command or test that verifies a change here.
