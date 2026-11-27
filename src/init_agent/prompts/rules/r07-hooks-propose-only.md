---
id: R7
title: Hooks are proposed, not enabled
kind: rule
applies_to: ["assess_automation", "plan_harness", "draft_file"]
priority: 20
---
A hook that already exists in the repository has status `active`. Every new hook
has status `proposed`. Each hook states trigger, command, scope, evidence, and
failure behavior. Enabling a proposed hook is the operator's decision.
