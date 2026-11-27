"""Order status transitions."""


def mark_dispatched(order: dict) -> dict:
    order["status"] = "DISPATCHED"
    return order
