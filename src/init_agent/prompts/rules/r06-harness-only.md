---
id: R6
title: Harness files only
kind: rule
applies_to: ["plan_harness", "draft_file"]
priority: 20
---
Plan only Markdown files from the harness catalog. Paths are relative to the
repository root. At the root: `AGENTS.md`, `ARCHITECTURE.md`, `RULES.md`,
`GUIDELINES.md`, `DEVELOPMENT.md`, `TESTING.md`, `HOOKS.md`, `TOOLS.md`,
`GUARDRAILS.md`, `DOMAIN.md`, `CONTRACTS.md`, `skills/<name>/SKILL.md` and
`adr/<decision>.md`. Next to the code: `<code-folder>/AGENTS.md` and
`<code-folder>/skills/<name>/SKILL.md`, where `<code-folder>` is an existing folder
from the inventory. `{platform_dir}/` holds
only the INIT report. Never plan changes to application code, configs, lockfiles,
or CI files.
