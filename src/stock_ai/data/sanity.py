"""Plausibility guards for figures that arrive from a provider.

A ratio can be arithmetically correct and still impossible. A 73% dividend
yield is not a bargain, it is a unit error, a stale price, or a special
distribution being read as an ordinary one - and it will win every dividend
screen and top every income ranking before anyone notices.

The rule these functions enforce is the one this project applies everywhere: an
implausible number is discarded rather than stored. Missing data is excluded
from screens and scores; wrong data is ranked.

Kept in one module because the same guard has to hold on both sides of a
cross-market comparison. When yfinance clamped its yields and J-Quants did not,
a Japanese name with a broken figure outranked every real US payer.
"""

from __future__ import annotations

import math

from stock_ai.core.logging import get_logger

logger = get_logger(__name__)

#: A dividend yield above this is treated as bad data rather than a bargain.
#: Real equities top out well under 30%; the exceptions are special one-off
#: distributions, which are not what a yield screen is looking for either.
MAX_PLAUSIBLE_YIELD = 0.30


def plausible_dividend_yield(value: float | None, symbol: str = "") -> float | None:
    """Return ``value`` if it could be a real dividend yield, else ``None``."""
    if value is None or not math.isfinite(value):
        return None
    if value < 0.0 or value > MAX_PLAUSIBLE_YIELD:
        logger.warning(
            "Discarding implausible dividend yield %.4f%s - above %.0f%% this is "
            "a unit error or a special distribution, not income.",
            value,
            f" for {symbol}" if symbol else "",
            MAX_PLAUSIBLE_YIELD * 100,
        )
        return None
    return value
