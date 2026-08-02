"""Screening engine: apply a condition across stored securities.

Builds a :class:`~stock_ai.screening.base.ScreeningContext` per symbol from the
database and returns those that satisfy the condition.
"""

from __future__ import annotations

from stock_ai.core.logging import get_logger
from stock_ai.database.engine import Database
from stock_ai.database.repository import (
    FundamentalsRepository,
    PriceRepository,
    list_symbols,
)
from stock_ai.screening.base import Condition, ScreeningContext

logger = get_logger(__name__)


class ScreeningEngine:
    """Evaluate a condition over many symbols and collect the passers."""

    def __init__(self, database: Database, load_prices: bool = False) -> None:
        """Create the engine.

        Args:
            database: Source of fundamentals (and prices, if enabled).
            load_prices: Whether to attach price history to each context.
                Leave ``False`` for fundamentals-only conditions.
        """
        self.database = database
        self.load_prices = load_prices

    def screen(self, condition: Condition, symbols: list[str] | None = None) -> list[str]:
        """Return the symbols that satisfy ``condition``.

        Args:
            condition: The (possibly composite) condition to apply.
            symbols: Symbols to test; defaults to every stored security.

        Returns:
            Passing symbols, in the order tested.
        """
        with self.database.session() as session:
            targets = symbols if symbols is not None else list_symbols(session)
            fundamentals_repo = FundamentalsRepository(session)
            price_repo = PriceRepository(session)

            passing: list[str] = []
            for symbol in targets:
                prices = price_repo.get_prices(symbol) if self.load_prices else None
                context = ScreeningContext(
                    symbol=symbol,
                    fundamentals=fundamentals_repo.get_latest(symbol),
                    prices=prices,
                )
                if condition.evaluate(context):
                    passing.append(symbol)

            logger.info(
                "Screened %d symbols with [%s]: %d passed",
                len(targets),
                condition.describe(),
                len(passing),
            )
            return passing
