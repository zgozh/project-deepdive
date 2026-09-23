"""Small in-memory order service used by the Phase 2 scanner fixture.

The fixture exists to provide realistic repository paths, so it deliberately has
no external dependency and no framework import.
"""

from dataclasses import dataclass


@dataclass
class Order:
    """One placed order with its amount in cents."""

    order_id: str
    amount_cents: int


class OrderService:
    """Store orders in memory so the fixture never touches a network or database."""

    def __init__(self) -> None:
        self._orders: dict[str, Order] = {}

    def create(self, order_id: str, amount_cents: int) -> Order:
        if amount_cents < 0:
            raise ValueError("amount_cents must not be negative")
        order = Order(order_id=order_id, amount_cents=amount_cents)
        self._orders[order_id] = order
        return order

    def total_cents(self) -> int:
        return sum(order.amount_cents for order in self._orders.values())
