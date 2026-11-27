---
id: task-decomposition
title: Task decomposition
kind: skill
node: plan
priority: 30
---
Purpose: split the required changes into tasks that one owner can pick up and verify independently.

Method:
1. Take one group at a time. List the distinct deliverables its change requires (a schema, an endpoint, a consumer, a migration, a test suite).
2. One deliverable per task. If a task has "and" between two verifiable outputs, split it.
3. Where work crosses a boundary, create a task on each affected side. If the end-to-end check is its own piece of work, add an integration task owned by one group, listing the other side as a dependency later.
4. Write the title as an imperative, the scope as in/out of scope, and the deliverable as a single artifact.
5. Give each task a stable ID with the group's short prefix.

Example (HLS-1):
- Group `order-service` (prefix `ORD`): `ORD-1` Define the `ShipmentDispatched` event schema (deliverable: `contracts/shipment_dispatched.json`); `ORD-2` Publish the event on DISPATCHED (deliverable: publisher in `services/order` with a unit test).
- Group `notification-service` (prefix `NOT`): `NOT-1` Consume `shipment.dispatched` and send the email (deliverable: consumer and template in `services/notification`); `NOT-2` Verify dispatch-to-email end to end (deliverable: an integration test that dispatches an order and finds the email).
Each task is owned by exactly one group; the end-to-end check is its own task.
