---
name: requirement-approval
description: Presents the validated requirements to the user for approval, sets awaiting_approval and stops; on the user's reply either finishes the run (approved) or routes the requested changes back into the flow. Used after requirement-validation passes.
---

# requirement-approval

## Purpose

Make sure the customer agrees with the requirements before they are marked
`completed` and handed to the Architect Agent.

## When

- After `requirement-validation` passes (asking).
- When the user's reply to an approval request arrives (receiving).

## Steps — asking

1. Write the validated `requirement.md` with `write_output`.
2. Append an `## Approval N` section to `clarifications.md` (see the
   clarifications template) with `Response: pending`.
3. `set_status awaiting_approval`, then `publish_outputs` with label
   `approval requested`.
4. Reply to the user, in their language: the numbered requirements (title and
   "The system shall …" statement for each) and the Out of scope list, then ask
   them to approve or to say what to change. Stop; do not continue in this turn.

## Steps — receiving

Code has already set status `in_progress` when the reply arrives.

1. Decide what the reply means:
   - **Approved** (a clear yes, with no changes): record `Response: approved`
     in the `## Approval N` section, then continue with `requirement-publishing`
     to finish (`completed`). Do not change `requirement.md`.
   - **Changes requested**: record the requested changes under the
     `## Approval N` section. A change of intent or scope →
     `requirement-understanding` (and `repository-research` if it changes what
     to look for), then `gap-detection`. A wording-only change →
     `requirement-definition`. Then validate and ask for approval again
     (`## Approval N+1`).
   - **Unclear**: ask one short follow-up question and set `awaiting_approval`
     again; do not treat it as approval.
2. A requirement the user rejects without a replacement moves to **Out of
   scope**, with the approval round as its source.

## Output

Reply to the user (their language):

```markdown
These are the requirements for ORD-42:

1. **Customer cancels an unshipped order**: The system shall allow a customer to cancel their own order while its status is placed or paid.
2. **Refund on cancellation**: The system shall refund the full amount of a cancelled paid order to the original payment method.

Out of scope: cancelling part of an order.

Do you approve these requirements, or would you like to change something?
```

## Rules

- Never set `completed` without an explicit approval of the current
  `requirement.md`. Code also rejects `completed` unless an approval request
  was sent, the user replied, and `requirement.md` is unchanged since that
  request.
- Silence, thanks, or a question is not approval.
- Any change to `requirement.md` needs a new approval round.
