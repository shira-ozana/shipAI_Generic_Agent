Purpose: turn HLS prose into a map of affected components or services, their responsibilities, interfaces and required changes.

Method:
1. Skim headings, then read each section that mentions a service, module, event, API, schema, data store or UI screen.
2. List each component or service once, with its responsibility and the change the HLS requires of it. Note whether the HLS treats it as existing or new.
3. List each interface between components: who produces, who consumes, and whether the HLS defines the contract (payload, endpoint, event name).
4. Record the document ID and section for every fact, so tasks can cite it.
5. Note statements that are ambiguous or contradict each other; stage 2 and 5 decide whether they need a question.

Example (HLS `HLS-1`, "Shipment dispatch notifications"):
- Section "Order service": existing; must publish a `shipment.dispatched` event when an order moves to DISPATCHED.
- Section "Notification service": existing; must consume that event and email the customer.
- Section "Event contract": both sides agree on the `ShipmentDispatched` payload (`orderId`, `customerEmail`, `trackingUrl`).
Result: two affected services, one defined shared contract, one distinct end-to-end behaviour (dispatch leads to an email).
