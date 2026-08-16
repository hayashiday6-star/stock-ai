"""Cross-market ranking: score JP and US names on one scale.

The composite score is already comparable across markets - every default
factor (ROE, margin, P/E, yield, momentum) is a unitless ratio, so nothing
about it is denominated in yen or dollars. What is *not* comparable is size:
market cap arrives in the listing market's own currency. This module converts
it through :class:`~stock_ai.data.fx.FxConverter` so a size filter means the
same thing on both sides of the Pacific, and reports the market alongside each
row so a ranking can be read per-market or as one list.
"""

from __future__ import annotations

import pandas as pd

from stock_ai.core.logging import get_logger
from stock_ai.data.fx import DEFAULT_BASE, FxConverter
from stock_ai.database.engine import Database
from stock_ai.database.repository import (
    FinancialStatementRepository,
    FundamentalsRepository,
    PriceRepository,
    list_securities,
)
from stock_ai.portfolio.scoring import ScoreResult, WeightedScorer, default_weighted_factors
from stock_ai.screening.base import ScreeningContext

logger = get_logger(__name__)

_BASE_COLUMNS = ["symbol", "market", "score", "coverage", "market_cap"]

#: Least share of the factor weight a name must be measured on to be ranked.
#: Below this the score says more about what is missing than about the company;
#: see the module docstring of :mod:`stock_ai.portfolio.scoring`.
DEFAULT_MIN_COVERAGE = 0.5


def rank_securities(
    database: Database,
    symbols: list[str] | None = None,
    scorer: WeightedScorer | None = None,
    fx: FxConverter | None = None,
    min_market_cap: float | None = None,
    max_market_cap: float | None = None,
    load_statements: bool = False,
    min_coverage: float = DEFAULT_MIN_COVERAGE,
) -> pd.DataFrame:
    """Rank securities across markets, highest composite score first.

    Args:
        database: Source of stored fundamentals and prices.
        symbols: Symbols to rank; defaults to every stored security.
        scorer: Composite scorer; defaults to :func:`default_weighted_factors`.
        fx: Converter used to express market cap on one scale. Defaults to a
            live-rate converter; pass a pinned one for reproducible reports.
        min_market_cap: Lower size bound, **in the converter's base currency**.
        max_market_cap: Upper size bound, in the same base currency.
        load_statements: Attach each name's annual statement series, which
            the growth factors need. Off by default because it costs a
            query per symbol and the default factor set does not use it.
        min_coverage: Least share of the factor weight a name must be measured
            on to appear. Defaults to :data:`DEFAULT_MIN_COVERAGE`; pass ``0``
            to rank everything and see the thinly-measured names too.

    Returns:
        A frame of ``symbol, market, score, coverage, market_cap`` plus one
        column per factor, sorted by score descending. ``market_cap`` is stated
        in the base currency; it is ``None`` where the figure is unknown.
    """
    scorer = scorer or WeightedScorer(default_weighted_factors())
    fx = fx or FxConverter()

    rows: list[dict[str, object]] = []
    with database.session() as session:
        securities = list_securities(session)
        wanted = set(symbols) if symbols is not None else None
        fundamentals_repo = FundamentalsRepository(session)
        price_repo = PriceRepository(session)
        statement_repo = FinancialStatementRepository(session)

        for symbol, market in securities:
            if wanted is not None and symbol not in wanted:
                continue
            fundamentals = fundamentals_repo.get_latest(symbol)
            context = ScreeningContext(
                symbol=symbol,
                market=market,
                fundamentals=fundamentals,
                prices=price_repo.get_prices(symbol),
                statements=statement_repo.get_reports(symbol) if load_statements else [],
            )
            native_cap = fundamentals.market_cap if fundamentals else None
            rows.append(
                _row(scorer.score(context), market, fx.convert_from_market(native_cap, market))
            )

    frame = _to_frame(rows)
    frame = _apply_size_bounds(frame, min_market_cap, max_market_cap)
    frame = _apply_coverage_floor(frame, min_coverage)
    logger.info("Ranked %d securities (base currency %s)", len(frame), fx.base)
    return frame


def _row(result: ScoreResult, market: str, market_cap: float | None) -> dict[str, object]:
    """Flatten one score result and its market context into a table row."""
    return {
        "symbol": result.symbol,
        "market": market,
        "score": round(result.score, 1),
        "coverage": round(result.coverage, 2),
        "market_cap": market_cap,
        **{name: round(value, 3) for name, value in result.breakdown.items()},
    }


def _apply_coverage_floor(frame: pd.DataFrame, minimum: float) -> pd.DataFrame:
    """Drop names scored on too little evidence, and say how many went.

    Renormalizing over the factors that applied means a thinly-measured name is
    graded on an easier exam, and sorting descending puts it on top. Filtering
    is therefore not a nicety: without it the head of the ranking is selected
    for missing data. The count is logged because silently removing rows would
    trade one invisible distortion for another.
    """
    if minimum <= 0 or frame.empty:
        return frame

    coverage = pd.to_numeric(frame["coverage"], errors="coerce")
    keep = coverage.notna() & (coverage >= minimum)
    dropped = int((~keep).sum())
    if dropped:
        logger.info(
            "Excluded %d of %d securities scored on less than %.0f%% of the factor "
            "weight; pass min_coverage=0 to include them.",
            dropped,
            len(frame),
            minimum * 100,
        )
    return frame[keep].reset_index(drop=True)


def _to_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    """Build the ranking frame, sorted by score, with stable leading columns."""
    if not rows:
        return pd.DataFrame(columns=_BASE_COLUMNS)

    frame = pd.DataFrame(rows).sort_values("score", ascending=False).reset_index(drop=True)
    factor_columns = [c for c in frame.columns if c not in _BASE_COLUMNS]
    return frame[[*_BASE_COLUMNS, *sorted(factor_columns)]]


def _apply_size_bounds(
    frame: pd.DataFrame, minimum: float | None, maximum: float | None
) -> pd.DataFrame:
    """Filter by converted market cap, dropping rows whose size is unknown.

    A missing market cap cannot be shown to satisfy a size bound, so it is
    excluded rather than assumed to pass - the same "what cannot be verified is
    excluded" rule the screening conditions follow.
    """
    if minimum is None and maximum is None:
        return frame

    caps = pd.to_numeric(frame["market_cap"], errors="coerce")
    keep = caps.notna()
    if minimum is not None:
        keep &= caps >= minimum
    if maximum is not None:
        keep &= caps <= maximum
    return frame[keep].reset_index(drop=True)


def base_currency_label(fx: FxConverter | None = None) -> str:
    """Return the base currency a ranking's ``market_cap`` column is stated in."""
    return (fx or FxConverter(base=DEFAULT_BASE)).base
