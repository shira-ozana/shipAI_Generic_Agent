---
id: acceptance-criteria
title: Acceptance criteria
kind: skill
node: plan
priority: 40
---
Purpose: write criteria that a reviewer can verify by observation, so a task can be accepted or rejected without opinion.

Method:
1. Restate the deliverable as something that can be inspected: a file, a passing test, a response, a stored record, a log line.
2. Write two to five criteria, each a single checkable statement in the form "Given/when X, then Y" or "<artifact> contains <specific content>".
3. Name concrete values (field names, status codes, event names) from the HLS or the contract.
4. Include at least one negative or boundary case when behaviour can fail (invalid input, missing field, duplicate delivery).
5. Remove wording that cannot be checked ("fast", "clean", "robust", "properly").

Example (`NOT-1`, consume the event and send the email):
- Given a `shipment.dispatched` message with `orderId`, `customerEmail` and `trackingUrl`, one email is sent to `customerEmail` containing the `trackingUrl`.
- Given a message without `customerEmail`, no email is sent and the message is recorded as rejected.
- Given the same message delivered twice, only one email is sent.
- A unit test for each case above exists under `services/notification/tests` and passes.
