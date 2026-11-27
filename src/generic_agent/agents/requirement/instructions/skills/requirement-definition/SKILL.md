---
name: requirement-definition
description: Writes the numbered, sourced "The system shall ..." business requirements and the Out of scope list from Explicit items, findings and answers. Used once there is enough information, and again when validation reports wording problems.
---

# requirement-definition

## Purpose

Turn the confirmed context into a short list of atomic, testable business
requirements.

## When

- Once `gap-detection` returns `enough_information = yes`.
- Again when `requirement-validation` routes wording problems here, or when
  the user asks for changes during `requirement-approval`.

## Steps

1. Collect the **Explicit** items, the relevant findings (`F#`) and the user's
   answers (`Q#`).
2. Write one requirement per behaviour.
3. Give each requirement a short title, one **"The system shall …"** statement,
   optional condition bullets, and a **Source** line (customer request, `Q#`,
   or `F#` with its path).
4. Number them `1, 2, 3…`: the main behaviour first, then rules, then side
   effects.
5. List anything the user excluded under **Out of scope**.

## Output

Follows the requirement template:

```markdown
# Requirements — ORD-42

## 1. Customer cancels an unshipped order
The system shall allow a customer to cancel their own order while its status is placed or paid.
- The order's status is not shipped or delivered.
Source: customer request; Q3; F1 (`.platform/domain/orders.md`)

## 2. Refund on cancellation
The system shall refund the full amount of a cancelled paid order to the original payment method.
Source: Q3; F3 (`src/payments/refunds.py`)

## Out of scope
- Cancelling part of an order (Q5).
```

## Rules

Writing rules for every requirement:

- **Unambiguous**: no "fast", "appropriate", "user-friendly", "etc.", "and/or".
  Name the roles, statuses and conditions.
- **Testable**: each statement can be judged true or false.
- **Atomic**: one behaviour per requirement.
- **No solution**: no screens, tables, endpoints, classes or technologies.
- **Sourced**: every requirement has a `Source:` line.
- Written in English.
