---
id: R6
title: Harness files only
kind: rule
applies_to: ["plan_harness", "draft_file"]
priority: 20
---
Plan only Markdown files from the harness catalog, under `{platform_dir}/`:
`AGENTS.md`, `ARCHITECTURE.md`, `RULES.md`, `GUIDELINES.md`, `DEVELOPMENT.md`,
`TESTING.md`, `HOOKS.md`, `TOOLS.md`, `GUARDRAILS.md`, `DOMAIN.md`, `CONTRACTS.md`,
`skills/<name>/SKILL.md`, `adr/<decision>.md`, and local
`areas/<code-folder>/AGENTS.md`. Never plan changes to application code, configs,
lockfiles, or CI files.
