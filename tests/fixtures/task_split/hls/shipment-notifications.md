# HLS-1: Shipment dispatch notifications

## Goal
Customers receive an email as soon as their order is dispatched.

## Order service
The order service (`services/order`) owns order status. When an order moves to
DISPATCHED it must publish a `shipment.dispatched` event. It publishes no
shipment events today.

## Notification service
The notification service (`services/notification`) sends customer email. It must
consume `shipment.dispatched` events and email the customer a message that
contains the tracking link. A duplicate delivery of the same event must not send
a second email. Events without a customer email are rejected and recorded.

## Event contract
Both services agree on the `ShipmentDispatched` payload: `orderId`,
`customerEmail`, `trackingUrl`. Event schemas are kept in `contracts/`. The new
schema is defined once and used by both services.

## Verification
An end-to-end test dispatches an order and checks that exactly one email with
the tracking link is produced.
