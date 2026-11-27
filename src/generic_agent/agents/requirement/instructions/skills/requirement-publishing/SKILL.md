---
name: requirement-publishing
description: Writes the output files from the templates, sets status.json, commits only the output folder and pushes it to ShipAi. Used for every status or clarifications update during the run and at the end.
---

# requirement-publishing

## Purpose

Keep the repository's copy of the outputs and status current, so the UI and the
next agent can rely on it.

## When

For every status or `clarifications.md` update during the run, and at the end.

## Steps

1. Write files from the templates with `write_output` (full content each time).
2. Set the status with `set_status`; `status.json` holds only the status.
3. Write `requirement.md` only after `requirement-validation` passed. Set
   `completed` only after the user approved it (`requirement-approval`).
4. Call `publish_outputs` with label `round N questions` (after a question
   round), `approval requested` (after an approval request), `completed` (after
   the user approved), or `failed` if the run cannot continue.
   The tool commits only the output folder with the message
   `requirement-agent({feature_id}): <label>` and pushes to `ShipAi`; on a
   rejected push it pulls and retries once. It never force-pushes.
5. If publishing fails, tell the user plainly; do not claim it succeeded.

| Moment | Files written | Status |
| --- | --- | --- |
| Start (code) | `status.json` | `in_progress` |
| Question round sent | `clarifications.md`, `status.json` | `awaiting_user` |
| Answers received | `status.json` (code); then `clarifications.md` with the answers | `in_progress` |
| Validation passed, approval requested | `requirement.md`, `clarifications.md`, `status.json` | `awaiting_approval` |
| Approval reply received | `status.json` (code); then `clarifications.md` with the response | `in_progress` |
| User approved | `clarifications.md`, `status.json` (`requirement.md` unchanged) | `completed` |
| Failure | `status.json` | `failed` |

## Output

```json
{"status": "completed"}
```

Commit: `requirement-agent(ORD-42): completed`

## Rules

- Never modify anything outside `.platform/{feature_id}/requierment/`.
- Never write credentials anywhere.
- Never trigger the next agent; the UI does that.
