---
name: clarification-questions
description: Asks the user 2-3 business questions per round for the most important blocking gaps, records the round in clarifications.md, sets awaiting_user and stops; records the answers when they arrive. Used whenever gap-detection returns enough_information = no.
---

# clarification-questions

## Purpose

Close blocking gaps by asking the user about intent, with as few questions as
possible.

## When

Whenever `gap-detection` returns `enough_information = no`, and when the user's
answers to a round arrive.

## Steps — asking

1. Prioritize blocking gaps that affect the most requirements or could change
   research.
2. Pick 2–3 of them. Never more than 3.
3. Write each question in business language, in the user's language, with one
   subject per question.
4. Where the repository shows current behaviour, offer it as an option
   ("Today X happens (F2). Keep it?").
5. Give concrete options and always allow a free answer.
6. Never repeat a question that is already answered in `clarifications.md`.
7. Append the round to `clarifications.md` (English, from the template) with
   every answer `pending`, using `write_output` with the full file content.
8. Follow `requirement-publishing`: `set_status awaiting_user`, then
   `publish_outputs` with label `round N questions`.
9. Reply to the user with the questions and stop. Do not continue the flow in
   this turn.

## Steps — receiving

1. Code has already set status `in_progress` when the answers arrive.
2. Record each answer under its `Q#` in `clarifications.md` (in English), with
   the gaps it closes.
3. If an answer is unclear or partial, carry it into the next round, rephrased.
4. Hand back to `requirement-understanding`, and to `repository-research` if an
   answer changes what to look for.

## Output

Reply to the user (their language):

```markdown
To write the requirements I need 2 answers:

1. **Q3.** Can a customer cancel an order that is already paid but not yet shipped?
   a) Yes, with a full refund  b) No, only unpaid orders  c) Other: ...
2. **Q4.** Today, support is not notified when an order changes (F5). Should support be notified of cancellations?
   a) No, keep as today  b) Yes, by email  c) Other: ...
```

Round appended to `clarifications.md`: see the clarifications template.

## Rules

- No technical questions (no databases, APIs, screens, frameworks).
- `Q#` numbers are global across rounds and never reused.
- One subject per question; 2–3 questions per round.
