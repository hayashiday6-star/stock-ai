"""Brokerage abstraction: orders, fills, positions, and the broker interface.

Trading code depends only on :class:`Broker`, so the concrete backend (a paper
simulator now, Interactive Brokers later) is swappable. Real-money execution is
never the default — see :class:`~stock_ai.broker.paper.PaperBroker`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable


class OrderSide(StrEnum):
    """Whether an order buys or sells."""

    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    """How an order is priced."""

    MARKET = "market"
    LIMIT = "limit"


@dataclass(frozen=True)
class Order:
    """A request to buy or sell a quantity of a symbol."""

    symbol: str
    side: OrderSide
    quantity: float
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = None


@dataclass(frozen=True)
class Fill:
    """A completed execution of an order."""

    symbol: str
    side: OrderSide
    quantity: float
    price: float


@dataclass(frozen=True)
class Position:
    """A held quantity of a symbol with its average entry price."""

    symbol: str
    quantity: float
    avg_price: float


@runtime_checkable
class Broker(Protocol):
    """Places orders and reports cash and positions."""

    name: str

    def place_order(self, order: Order) -> Fill:
        """Execute ``order`` and return the resulting :class:`Fill`.

        Raises:
            BrokerError: On invalid orders, insufficient funds/position, or
                connectivity failures.
        """
        ...

    def positions(self) -> list[Position]:
        """Return all currently held positions."""
        ...

    def cash(self) -> float:
        """Return available cash."""
        ...
