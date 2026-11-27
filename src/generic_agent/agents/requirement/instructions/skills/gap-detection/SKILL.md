---
name: gap-detection
description: Lists missing information, contradictions and unconfirmed assumptions as a Gap List (G#), marks each blocking or non-blocking, and decides whether there is enough information to define requirements. Used after every research pass.
---

# gap-detection

## Purpose

Decide what still stands between the request and a complete set of business
requirements, and whether the user must be asked.

## When

After every `repository-research` pass, and when `requirement-validation`
routes a failure here.

## Look for

- Missing information.
- Contradictions: within the request, between answers, or with current
  behaviour.
- Gaps between current behaviour and the requested behaviour.
- Undefined business rules: eligibility, limits, permissions, notifications.
- Missing scenarios and edge cases: failure, reversal, concurrency, empty cases.
- Unconfirmed assumptions (everything still under **Implied (to confirm)**).

## Steps

1. List each gap with its type and why it matters.
2. Mark each gap **blocking** (a requirement cannot be written correctly without
   it) or **non-blocking**.
3. Drop gaps the repository answers (they become findings).
4. Drop gaps already answered in `clarifications.md`.
5. Decide `enough_information = yes` when no blocking gaps remain; otherwise
   `no`.

## Output

```markdown
## Gap List

| G# | Type | Gap | Why it matters | Blocking |
| --- | --- | --- | --- | --- |
| G1 | Undefined rule | Can a paid but unshipped order be cancelled? | Decides the core eligibility requirement | yes |
| G2 | Edge case | What if cancellation and shipping happen at the same moment? | Changes the cut-off condition | yes |
| G3 | Notification | Should support be notified of cancellations? | Adds a side-effect requirement | no |

**enough_information:** no
```

## Rules

- Gaps are about intent and business rules, not code facts. A code fact is a
  research task, not a gap.
- Do not inflate the list with edge cases that would change no requirement.
- Keep `G#` numbers stable across passes; never reuse a number.
