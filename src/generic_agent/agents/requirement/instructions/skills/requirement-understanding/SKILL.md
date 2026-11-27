---
name: requirement-understanding
description: Builds and updates the Requirement Context (goal, actors, entities, Explicit / Implied / Unknown) from the customer request. Used at the start of a run, after every clarification round, and after an approval reply that changes intent or scope.
---

# requirement-understanding

## Purpose

Understand what the customer is asking for, in business terms, before any
research or questions.

## When

- At the start, with `requirement_text` as input.
- After every clarification round, and after an approval reply that changes
  intent or scope: update the existing context with the new
  answers. Do not restart from scratch.

## Steps

1. Restate the request in 1–2 sentences.
2. Identify the business goal and who benefits.
3. Identify the actors: user roles, external systems, scheduled processes.
4. List the business entities and terms in the customer's own words. These are
   your search terms for `repository-research`.
5. Name the system areas likely involved. Mark them unconfirmed until research
   confirms them.
6. Sort everything into **Explicit** (stated by the customer or confirmed by an
   answer), **Implied (to confirm)** and **Unknown**.

## Output

```markdown
## Requirement Context

**Request:** Customers can cancel an order before it ships.
**Goal:** Fewer support calls about unwanted orders. **Benefits:** customers, support team.

**Actors:** Customer; Support agent; Warehouse system (external)
**Entities / terms:** order, "cancel", "shipped", refund
**Likely areas (unconfirmed):** order management, payments

### Explicit
- A customer can cancel their own order before it ships.
- Cancellation sends a confirmation email (Q2).

### Implied (to confirm)
- A cancelled order is refunded in full.

### Unknown
- Whether partially shipped orders can be cancelled.
```

## Rules

- Describe the problem; propose no solutions.
- When an answer moves an item to **Explicit**, record its `Q#` next to it.
- Keep the customer's own terms; do not rename entities.
