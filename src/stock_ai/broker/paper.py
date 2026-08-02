"""An in-memory paper-trading broker: simulated fills, no real money.

Fills market orders at a price from an injected price source; limit orders fill
only when marketable, at the limit price. State lives in memory and is never
sent anywhere, so this is safe for testing and dry runs.
"""

from __future__ import annotations

from collections.abc import Callable

from stock_ai.broker.base import (
    Broker,
    Fill,
    Order,
    OrderSide,
    OrderType,
    Position,
)
from stock_ai.core.exceptions import BrokerError
from stock_ai.core.logging import get_logger

logger = get_logger(__name__)

PriceSource = Callable[[str], float]


class PaperBroker(Broker):
    """Simulate order execution against an in-memory cash and position ledger."""

    name = "paper"

    def __init__(self, cash: float, price_source: PriceSource) -> None:
        """Create the broker.

        Args:
            cash: Starting cash balance.
            price_source: Callable returning the current market price for a symbol.
        """
        self._cash = cash
        self._price = price_source
        self._positions: dict[str, Position] = {}

    def cash(self) -> float:
        """Return available cash."""
        return self._cash

    def positions(self) -> list[Position]:
        """Return held positions with a non-zero quantity."""
        return [p for p in self._positions.values() if p.quantity != 0]

    def place_order(self, order: Order) -> Fill:
        """Execute ``order`` against the ledger and return the fill."""
        if order.quantity <= 0:
            raise BrokerError(f"Order quantity must be positive, got {order.quantity}.")

        price = self._fill_price(order)
        if order.side is OrderSide.BUY:
            self._buy(order.symbol, order.quantity, price)
        else:
            self._sell(order.symbol, order.quantity, price)

        logger.info("Filled %s %g %s @ %.4f", order.side.value, order.quantity, order.symbol, price)
        return Fill(order.symbol, order.side, order.quantity, price)

    def _fill_price(self, order: Order) -> float:
        """Determine the execution price, enforcing limit marketability."""
        market = self._price(order.symbol)
        if order.order_type is OrderType.MARKET:
            return market
        if order.limit_price is None:
            raise BrokerError("Limit order requires a limit_price.")
        marketable = (
            market <= order.limit_price
            if order.side is OrderSide.BUY
            else market >= order.limit_price
        )
        if not marketable:
            raise BrokerError(
                f"Limit {order.side.value} at {order.limit_price} not marketable (market {market})."
            )
        return order.limit_price

    def _buy(self, symbol: str, quantity: float, price: float) -> None:
        """Apply a buy: debit cash and increase (or open) the position."""
        cost = price * quantity
        if cost > self._cash:
            raise BrokerError(f"Insufficient cash: need {cost:.2f}, have {self._cash:.2f}.")
        self._cash -= cost
        existing = self._positions.get(symbol)
        if existing is None:
            self._positions[symbol] = Position(symbol, quantity, price)
        else:
            total_qty = existing.quantity + quantity
            avg = (existing.avg_price * existing.quantity + price * quantity) / total_qty
            self._positions[symbol] = Position(symbol, total_qty, avg)

    def _sell(self, symbol: str, quantity: float, price: float) -> None:
        """Apply a sell: credit cash and reduce (or close) the position."""
        existing = self._positions.get(symbol)
        if existing is None or existing.quantity < quantity:
            held = existing.quantity if existing else 0.0
            raise BrokerError(f"Insufficient position in {symbol}: need {quantity}, have {held}.")
        self._cash += price * quantity
        remaining = existing.quantity - quantity
        if remaining == 0:
            del self._positions[symbol]
        else:
            self._positions[symbol] = Position(symbol, remaining, existing.avg_price)
