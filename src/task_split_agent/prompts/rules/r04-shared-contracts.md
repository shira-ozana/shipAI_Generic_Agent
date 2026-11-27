---
id: R4
title: Shared contracts decouple the two sides
kind: rule
applies_to: ["*"]
priority: 40
---
When two groups share a contract that the plan defines, make both implementation tasks independent of each other and make any integration task depend on both. When a task needs an output that another task must create first, list that exact task as the dependency.
