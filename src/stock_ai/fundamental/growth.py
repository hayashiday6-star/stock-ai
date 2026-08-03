"""Growth, dividend, and streak metrics derived from a statement series.

Everything here reads a list of :class:`~stock_ai.data.types.FinancialReport`
sorted oldest first — the annual series, unless stated otherwise. Mixing
quarters into a year-over-year comparison would silently corrupt it, so the
caller is expected to have filtered to one period type (which
``FinancialStatementRepository.get_reports`` does by default).

Every function returns ``None`` when the series cannot support the answer,
rather than a zero that reads as a real measurement.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence

from stock_ai.data.types import FinancialReport


def _usable(value: float | None) -> bool:
    """Whether a figure can take part in arithmetic."""
    return value is not None and math.isfinite(value)


def _growth(previous: float | None, current: float | None) -> float | None:
    """Year-over-year change as a fraction, or ``None`` if not meaningful.

    A non-positive base makes the percentage meaningless — going from a ¥1bn
    loss to a ¥2bn profit is not "-300% growth" — so those return ``None``
    instead of a number that would sort wrongly against real growth rates.
    """
    if not _usable(previous) or not _usable(current) or previous <= 0:
        return None
    return current / previous - 1.0


def _field_growth(reports: Sequence[FinancialReport], field: str, periods: int = 1) -> float | None:
    """Growth of ``field`` over the last ``periods`` fiscal years."""
    if len(reports) <= periods:
        return None
    return _growth(
        getattr(reports[-1 - periods], field),
        getattr(reports[-1], field),
    )


def revenue_growth(reports: Sequence[FinancialReport], periods: int = 1) -> float | None:
    """Revenue growth over the last ``periods`` fiscal years (増収率)."""
    return _field_growth(reports, "revenue", periods)


def profit_growth(reports: Sequence[FinancialReport], periods: int = 1) -> float | None:
    """Net income growth over the last ``periods`` fiscal years (増益率)."""
    return _field_growth(reports, "net_income", periods)


def dividend_growth(reports: Sequence[FinancialReport], periods: int = 1) -> float | None:
    """Dividend-per-share growth over the last ``periods`` fiscal years (増配率)."""
    return _field_growth(reports, "dividend_per_share", periods)


def cagr(reports: Sequence[FinancialReport], field: str, years: int) -> float | None:
    """Compound annual growth rate of ``field`` over ``years`` fiscal years.

    Smoother than a single year-over-year figure, which one exceptional year
    can dominate.
    """
    if years <= 0 or len(reports) <= years:
        return None
    start = getattr(reports[-1 - years], field)
    end = getattr(reports[-1], field)
    if not _usable(start) or not _usable(end) or start <= 0 or end <= 0:
        return None
    return (end / start) ** (1.0 / years) - 1.0


def _streak(reports: Sequence[FinancialReport], holds: Callable[[float, float], bool]) -> int:
    """Count consecutive most-recent years where ``holds(previous, current)``.

    Walks backwards from the latest year and stops at the first break or at the
    first pair that cannot be compared, so a gap in the data ends the streak
    rather than being counted through.
    """
    count = 0
    for current, previous in zip(reversed(reports), reversed(reports[:-1]), strict=False):
        current_value = current.dividend_per_share
        previous_value = previous.dividend_per_share
        if not _usable(current_value) or not _usable(previous_value):
            break
        if not holds(previous_value, current_value):
            break
        count += 1
    return count


def consecutive_dividend_increases(reports: Sequence[FinancialReport]) -> int:
    """Consecutive years the dividend per share was raised (連続増配年数).

    Counted as year-over-year *increases*, so a company on its 10th raise
    returns 10. A flat year ends the streak; a missing figure ends it too,
    because an unknown year cannot be shown to have been a raise.
    """
    return _streak(reports, lambda previous, current: current > previous)


def consecutive_dividend_non_cuts(reports: Sequence[FinancialReport]) -> int:
    """Consecutive years the dividend was maintained or raised (連続非減配年数)."""
    return _streak(reports, lambda previous, current: current >= previous)


def latest_payout_ratio(reports: Sequence[FinancialReport]) -> float | None:
    """Payout ratio of the most recent report that can express one."""
    for report in reversed(reports):
        ratio = report.payout_ratio
        if ratio is not None:
            return ratio
    return None
