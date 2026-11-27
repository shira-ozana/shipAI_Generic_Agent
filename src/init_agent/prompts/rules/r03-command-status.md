---
id: R3
title: Command status is explicit
kind: rule
applies_to: ["*"]
priority: 10
---
Every command has a status. Use `documented` when it comes from README, package
scripts, CI or docs. Use `executed_passed` or `executed_failed` only when the
caller supplied an execution result. Never state or imply that a command ran when
no execution result exists.
