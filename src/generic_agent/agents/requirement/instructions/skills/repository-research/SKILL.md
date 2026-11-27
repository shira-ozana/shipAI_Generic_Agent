---
name: repository-research
description: Establishes current system behaviour and business rules from the Init Agent's Markdown under .platform/, then code, producing sourced findings F#. Used after requirement-understanding and again whenever an answer changes what to check.
---

# repository-research

## Purpose

Find out what the system already does, so requirements fit it and the user is
never asked about facts the repository answers.

## When

- After `requirement-understanding`.
- Again whenever a user answer changes what to look for.

## Sources, in order

1. The Init Agent's Markdown under `.platform/` (the system map). Start here.
2. Code, only to confirm a point or fill a gap the docs leave.

## Steps

1. Locate the context's entities and terms in the Init docs (`search_files`,
   `list_files .platform`, `read_file`).
2. For each entity, establish: does it exist; its relevant states or fields (in
   business terms); what users can already do with it.
3. Find similar existing behaviour. It shows the conventions the new behaviour
   should follow.
4. Find existing business rules: who may do it, when, and what happens after.
5. Go to code only when the docs do not answer a question you need answered.
6. If docs and code differ, record both and treat the code as current.
7. Stop when your questions are answered or shown to be unanswerable from the
   repository.

## Output

```markdown
## Research Findings

- **F1.** Orders have the states placed, paid, shipped, delivered. — `.platform/domain/orders.md`
- **F2.** Only the order's owner and support agents can view an order. — `.platform/security.md`
- **F3.** Refunds already exist for returned items, issued to the original payment method. — `src/payments/refunds.py`

**Similar behaviour:** Returns (F3): customer starts it, support approves, email sent.
**Existing rules:** Shipped orders cannot be edited (F1, `.platform/domain/orders.md`).
**Docs vs code:** Docs list no "cancelled" state; code has `CANCELLED` (`src/orders/model.py`). Code treated as current.

### Not found in the repository
- Any rule about cancelling after payment.
```

## Rules

- No source, no finding. Every `F#` names a file path.
- Describe behaviour in business terms, not implementation.
- Read-only: you only read the repository.
- Number findings globally (`F1`, `F2`, …) and keep numbers stable across passes.
- The "Not found" list feeds `gap-detection`.
