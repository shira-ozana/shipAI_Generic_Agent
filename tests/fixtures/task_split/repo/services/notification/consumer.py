"""Notification event consumers."""


def handle_order_created(event: dict) -> None:
    print("send welcome email to", event["customerEmail"])
