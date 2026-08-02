"""Interactive Brokers adapter — intentionally a non-executing skeleton.

Live order routing to a real account is a deliberate, operator-gated step: it
requires ``ib_insync``, a running TWS/Gateway session, and explicit account
configuration. This project never enables real-money trading by default, so the
methods here raise rather than place live orders. Implement them behind an
explicit, reviewed opt-in when you are ready to go live; until then, use
:class:`~stock_ai.broker.paper.PaperBroker`.
"""

from __future__ import annotations

from stock_ai.broker.base import Fill, Order, Position
from stock_ai.core.exceptions import BrokerError

_NOT_ENABLED = (
    "IBKR live trading is not enabled. Configure ib_insync and a TWS/Gateway "
    "session and implement this adapter deliberately; use PaperBroker for dry runs."
)


class IBKRBroker:
    """Placeholder for an Interactive Brokers backend (not wired to live orders)."""

    name = "ibkr"

    def place_order(self, order: Order) -> Fill:
        """Raise — live order placement is intentionally not implemented."""
        raise BrokerError(_NOT_ENABLED)

    def positions(self) -> list[Position]:
        """Raise — live account access is intentionally not implemented."""
        raise BrokerError(_NOT_ENABLED)

    def cash(self) -> float:
        """Raise — live account access is intentionally not implemented."""
        raise BrokerError(_NOT_ENABLED)
