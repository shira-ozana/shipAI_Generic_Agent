---
name: requirement-validation
description: Checks the drafted requirements against checks V1-V9 and routes each failure to gap-detection or requirement-definition; passes only when every check passes. Used after every requirement-definition.
---

# requirement-validation

## Purpose

Make sure only complete, grounded, well-formed requirements are published.

## When

After every `requirement-definition`.

## Checks

| ID | Check |
| --- | --- |
| V1 | Coverage: every part of the customer request is covered by a requirement or listed as out of scope. |
| V2 | Grounding: every requirement has a real source (customer text, `Q#`, or `F#` with path). |
| V3 | No requirement depends on an answer that is still `pending`. |
| V4 | Consistency: no two requirements, answers or findings contradict each other. |
| V5 | No duplicates. |
| V6 | Testable and unambiguous (no vague words; roles, statuses, conditions named). |
| V7 | Atomic: one behaviour per requirement. |
| V8 | No solution: no architecture, screens, tables, endpoints, classes or technologies. |
| V9 | Template format: title, numbered `## N. {title}` sections, "The system shall …", `Source:` lines, `## Out of scope`. |

## Steps

1. Run every check on every requirement.
2. Route each failure:
   - V1, V3 or V4 caused by missing information → `gap-detection`.
   - V2 where no source is possible → remove the requirement or turn it into a
     question (via `gap-detection`).
   - Everything else → `requirement-definition`.
3. `result: passed` only when every check passes. Only then continue to
   `requirement-approval`.

## Output

```markdown
## Validation Report

result: failed

| Requirement | Check | Problem | Route |
| --- | --- | --- | --- |
| 2 | V6 | "quickly" is not testable | requirement-definition |
| 4 | V3 | Depends on Q6, still pending | gap-detection |
```

## Rules

- Do not fix problems inside this skill; route them.
- Do not publish `requirement.md` after a failed validation.
