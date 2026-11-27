---
id: dependency-mapping
title: Dependency mapping
kind: skill
node: plan
priority: 50
---
Purpose: record which task deliverables are real prerequisites for starting other tasks, across groups as well as inside them, and expose what can run in parallel.

Method:
1. For each task, ask: "What must already exist before someone can start this?" Name the producing task, not a topic.
2. Add an entry to `depends_on` only for a real prerequisite. Ordering preference, similar subject matter or shared owner are not reasons.
3. Keep only direct prerequisites. If A needs B and B needs C, A lists B only.
4. If a shared contract is defined by a task, both implementation tasks depend on that contract task and not on each other.
5. An integration task depends on every implementation task whose output it exercises.
6. Check for cycles and for tasks with no prerequisites: those can start in parallel, and so can tasks that share the same prerequisites.

Example (HLS-1):
- `ORD-1` (event schema): no prerequisites.
- `ORD-2` (publish event): `depends_on: ["ORD-1"]`.
- `NOT-1` (consume event): `depends_on: ["ORD-1"]`. This crosses groups, because the consumer needs the exact schema produced by `ORD-1`. It does not depend on `ORD-2`, so `ORD-2` and `NOT-1` can run in parallel.
- `NOT-2` (end-to-end test): `depends_on: ["ORD-2", "NOT-1"]`.
