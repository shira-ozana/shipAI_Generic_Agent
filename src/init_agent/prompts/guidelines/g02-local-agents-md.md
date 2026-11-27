---
id: G2
title: Local AGENTS.md only for a unique contract
kind: guideline
applies_to: ["plan_harness"]
priority: 10
---
Every folder in "Required local AGENTS.md" (a folder that holds 3 or more
components) gets `<code-folder>/AGENTS.md`: it maps its components and routes the
reader to the right one. Propose other `<code-folder>/AGENTS.md` files only when that
folder has its own way of working: its own patterns, tests, rules, or a skill that
applies there. A local file
says what to read, which skill to use, which rules apply, and how to check a change.
At least one local file should link to a rule or a skill when the evidence supports it.
