"""Does a score actually predict returns? Rank, hold, and compare.

A composite score is a hypothesis until it is measured. This runs the honest
version of that measurement: form a portfolio from the top-ranked names, hold
it over a forward window, and compare against an equal-weight basket of the
whole universe. If the top bucket does not beat the universe, the score is not
adding information - however sensible its factors read.

What is deliberately built in, because leaving it out is how a backtest lies:

- **Ranking uses only data available at the formation date.** Prices are
  truncated there, and statements are filtered to periods disclosed on or
  before it. Scoring on figures published later is the classic look-ahead that
  makes any factor look brilliant.
- **The benchmark is the same universe, equal-weighted.** Comparing a
  small-cap growth screen against the S&P would measure the size and style
  tilt, not the score.
- **Buckets are reported, not just the top one.** A score that works should
  show a gradient across buckets; if the bottom bucket also beats the middle,
  what is being measured is noise.

Two caveats the report cannot remove, only surface:

- **Sampling noise is large.** On a universe of a few dozen names, a top
  bucket beating the field by several percent happens routinely by chance.
  :attr:`FactorTestResult.spread_t_stat` is there to say so; read it before
  the excess return.
- **Survivorship bias is not handled.** The universe is whatever is in the
  local database today, which excludes anything delisted. Results are
  optimistic by an amount this module cannot estimate.

Treat the output as a sanity check that can falsify a score, not as evidence
that one works.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field

import pandas as pd
from sqlalchemy import func, select

from stock_ai.backtest.metrics import TRADING_DAYS_PER_YEAR
from stock_ai.core.exceptions import BacktestError
from stock_ai.core.logging import get_logger
from stock_ai.data.schema import ADJ_CLOSE
from stock_ai.database.engine import Database
from stock_ai.database.models import FinancialStatement, PriceBar, Security
from stock_ai.database.repository import (
    FinancialStatementRepository,
    FundamentalsRepository,
    PriceRepository,
    list_securities,
)
from stock_ai.portfolio.scoring import WeightedScorer
from stock_ai.screening.base import ScreeningContext

logger = get_logger(__name__)


@dataclass(frozen=True)
class BucketResult:
    """Forward performance of one slice of the ranking."""

    label: str
    symbols: list[str]
    mean_return: float
    median_return: float
    hit_rate: float
    """Fraction of names in the bucket with a positive forward return."""
    return_std: float = 0.0
    """Dispersion of returns within the bucket, needed to judge the spread."""

    @property
    def size(self) -> int:
        """How many names the bucket holds."""
        return len(self.symbols)


@dataclass(frozen=True)
class FactorTestResult:
    """The outcome of ranking a universe and measuring what happened next."""

    formation: dt.date
    horizon_days: int
    buckets: list[BucketResult]
    universe_return: float
    """Equal-weight forward return of every scored name - the benchmark."""
    scored: int
    skipped: list[str] = field(default_factory=list)
    """Names dropped for want of a score or a forward price."""
    no_forward_price: int = 0
    """Of ``skipped``: no price far enough after formation to measure."""
    no_visible_statements: int = 0
    """Of ``skipped``: statements exist, but none disclosed by the formation date."""
    no_statements_stored: int = 0
    """Of ``skipped``: no statements at all - never fetched, not merely too new."""
    no_score: int = 0
    """Of ``skipped``: statements were visible but no factor could be computed."""

    @property
    def coverage(self) -> float:
        """Fraction of the universe that was actually tested.

        Worth reading before the returns are. A spread measured on a tenth of
        the market is a statement about that tenth, and which tenth is not
        random: it is the names with the longest disclosure history.
        """
        total = self.scored + len(self.skipped)
        return self.scored / total if total else 0.0

    @property
    def top(self) -> BucketResult | None:
        """The highest-scoring bucket, if any names were scored."""
        return self.buckets[0] if self.buckets else None

    @property
    def excess_return(self) -> float | None:
        """Top bucket's return less the universe's. The number that matters."""
        return None if self.top is None else self.top.mean_return - self.universe_return

    @property
    def spread_t_stat(self) -> float | None:
        """Welch t-statistic of the top-minus-bottom return spread.

        Without this the report is dangerous: on a universe of a few dozen
        names, sampling noise alone routinely hands the top bucket a several-
        percent "edge". A spread that cannot clear roughly ``|t| >= 2`` is not
        distinguishable from chance, no matter how large the excess return
        looks.

        ``None`` when there are too few names to estimate dispersion.
        """
        if len(self.buckets) < 2:
            return None
        top, bottom = self.buckets[0], self.buckets[-1]
        if top.size < 2 or bottom.size < 2:
            return None

        standard_error = math.sqrt(
            top.return_std**2 / top.size + bottom.return_std**2 / bottom.size
        )
        if standard_error == 0:
            return None
        return (top.mean_return - bottom.mean_return) / standard_error

    @property
    def is_significant(self) -> bool:
        """Whether the top-bottom spread clears the usual two-sigma bar."""
        t_stat = self.spread_t_stat
        return t_stat is not None and abs(t_stat) >= 2.0

    @property
    def is_monotonic(self) -> bool:
        """Whether mean return falls with every step down the ranking.

        A real signal should decay across buckets. A jumbled ordering means the
        top bucket's edge, if any, is probably noise.
        """
        returns = [bucket.mean_return for bucket in self.buckets]
        return all(a >= b for a, b in zip(returns, returns[1:], strict=False))

    def to_frame(self) -> pd.DataFrame:
        """Return the buckets as a table, best-ranked first."""
        return pd.DataFrame(
            [
                {
                    "bucket": bucket.label,
                    "n": bucket.size,
                    "mean_return": bucket.mean_return,
                    "median_return": bucket.median_return,
                    "hit_rate": bucket.hit_rate,
                    "vs_universe": bucket.mean_return - self.universe_return,
                }
                for bucket in self.buckets
            ]
        )


def _forward_return(prices: pd.DataFrame, formation: dt.date, horizon_days: int) -> float | None:
    """Return the price change from ``formation`` to ``horizon_days`` bars later.

    ``None`` when either end is missing, so a name that had not listed yet or
    stopped trading is excluded rather than counted as flat.
    """
    if prices.empty or ADJ_CLOSE not in prices.columns:
        return None

    series = prices[ADJ_CLOSE].dropna()
    at_formation = series[series.index <= pd.Timestamp(formation)]
    if at_formation.empty:
        return None

    start_position = len(at_formation) - 1
    end_position = start_position + horizon_days
    if end_position >= len(series):
        return None

    start = float(series.iloc[start_position])
    end = float(series.iloc[end_position])
    return None if start <= 0 else end / start - 1.0


def _as_of_context(
    symbol: str,
    market: str,
    prices: pd.DataFrame,
    statements: list,
    fundamentals: object,
    formation: dt.date,
) -> ScreeningContext:
    """Build a scoring context restricted to what was known at ``formation``.

    Statements are filtered on their *disclosure* date, not their fiscal year:
    a year ending in March is not public until the results are filed weeks
    later, and scoring on it before then is look-ahead.
    """
    cutoff = pd.Timestamp(formation)
    visible_prices = prices[prices.index <= cutoff] if not prices.empty else prices
    visible_statements = [
        report
        for report in statements
        if report.disclosed_on is not None and report.disclosed_on <= formation
    ]
    return ScreeningContext(
        symbol=symbol,
        market=market,
        fundamentals=fundamentals,
        prices=visible_prices,
        statements=visible_statements,
    )


def run_factor_test(
    database: Database,
    scorer: WeightedScorer,
    formation: dt.date,
    horizon_days: int = TRADING_DAYS_PER_YEAR,
    buckets: int = 3,
    symbols: list[str] | None = None,
) -> FactorTestResult:
    """Rank a universe as of ``formation`` and measure the forward returns.

    Args:
        database: Source of prices, fundamentals, and statements.
        scorer: The composite scorer under test.
        formation: The date the ranking is formed on. Only data available then
            is used to score.
        horizon_days: Trading days held after formation.
        buckets: How many equal slices to split the ranking into.
        symbols: Universe to test; defaults to every stored security.

    Returns:
        Per-bucket forward performance plus the equal-weight universe return.

    Raises:
        BacktestError: If no name could be both scored and measured.
    """
    if horizon_days <= 0:
        raise BacktestError("horizon_days must be positive.")
    if buckets <= 0:
        raise BacktestError("buckets must be positive.")

    wanted = set(symbols) if symbols is not None else None
    scored: list[tuple[str, float, float]] = []  # symbol, score, forward return
    skipped: list[str] = []
    # Three very different reasons to drop a name, counted apart. Reporting one
    # total invites the wrong fix: "fetch more history" does nothing when the
    # cause is that no statement had been *disclosed* by the formation date.
    no_forward_price = 0
    no_visible_statements = 0
    no_statements_stored = 0
    no_score = 0

    with database.session() as session:
        price_repo = PriceRepository(session)
        fundamentals_repo = FundamentalsRepository(session)
        statement_repo = FinancialStatementRepository(session)

        for symbol, market in list_securities(session):
            if wanted is not None and symbol not in wanted:
                continue

            prices = price_repo.get_prices(symbol)
            forward = _forward_return(prices, formation, horizon_days)
            if forward is None:
                skipped.append(symbol)
                no_forward_price += 1
                continue

            statements = statement_repo.get_reports(symbol)
            context = _as_of_context(
                symbol,
                market,
                prices,
                statements,
                # Bounded by the formation date: a snapshot carries the day it
                # was fetched, so the unbounded "latest" is today's, and using
                # today's market cap to rank a 2024 formation is look-ahead.
                fundamentals_repo.get_latest(symbol, as_of=formation),
                formation,
            )
            result = scorer.score(context)
            if not result.breakdown:  # nothing was computable; not a real score
                skipped.append(symbol)
                if not context.statements:
                    # "Nothing visible at formation" has two causes with
                    # opposite fixes: the company had not filed yet (move the
                    # date), or this symbol has no statements stored at all
                    # (fetch them). Counting them together sent the last round
                    # of diagnosis after the wrong one.
                    if statements:
                        no_visible_statements += 1
                    else:
                        no_statements_stored += 1
                else:
                    no_score += 1
                continue
            scored.append((symbol, result.score, forward))

    if not scored:
        raise BacktestError(
            f"No security could be scored and measured at {formation} "
            f"over {horizon_days} bars; fetch more history first."
        )

    scored.sort(key=lambda row: row[1], reverse=True)
    universe_return = sum(row[2] for row in scored) / len(scored)
    logger.info(
        "Factor test at %s: %d scored, %d skipped, universe %+.2f%%",
        formation,
        len(scored),
        len(skipped),
        universe_return * 100,
    )
    if skipped:
        logger.info(
            "Skipped %d: %d no forward price, %d no statements stored at all, "
            "%d had statements but none disclosed by %s, %d unscoreable.",
            len(skipped),
            no_forward_price,
            no_statements_stored,
            no_visible_statements,
            formation,
            no_score,
        )
    return FactorTestResult(
        formation=formation,
        horizon_days=horizon_days,
        buckets=_split_buckets(scored, buckets),
        universe_return=universe_return,
        scored=len(scored),
        skipped=skipped,
        no_forward_price=no_forward_price,
        no_visible_statements=no_visible_statements,
        no_statements_stored=no_statements_stored,
        no_score=no_score,
    )


def _split_buckets(scored: list[tuple[str, float, float]], count: int) -> list[BucketResult]:
    """Split a score-sorted list into ``count`` near-equal buckets.

    Fewer names than buckets collapses to one bucket rather than producing
    single-name slices whose "mean return" is one company's luck.
    """
    if len(scored) < count:
        count = 1

    size, remainder = divmod(len(scored), count)
    result: list[BucketResult] = []
    start = 0
    for index in range(count):
        # Spread the remainder over the leading buckets so sizes differ by <= 1.
        stop = start + size + (1 if index < remainder else 0)
        slice_ = scored[start:stop]
        start = stop
        if not slice_:
            continue
        returns = pd.Series([row[2] for row in slice_])
        result.append(
            BucketResult(
                label=_bucket_label(index, count),
                symbols=[row[0] for row in slice_],
                mean_return=float(returns.mean()),
                median_return=float(returns.median()),
                hit_rate=float((returns > 0).mean()),
                # Sample std (ddof=1): these are a sample of possible outcomes,
                # not the whole population.
                return_std=float(returns.std(ddof=1)) if len(returns) > 1 else 0.0,
            )
        )
    return result


def _bucket_label(index: int, count: int) -> str:
    """Name a bucket by its position in the ranking."""
    if count == 1:
        return "all"
    if index == 0:
        return "top"
    if index == count - 1:
        return "bottom"
    return f"mid{index}"


@dataclass(frozen=True)
class FormationAdvice:
    """Which formation dates the stored data can actually support."""

    best: dt.date | None
    """Date whose coverage is highest, or ``None`` if no date works."""
    coverage: float
    """Fraction of the universe scoreable and measurable at :attr:`best`."""
    latest_feasible: dt.date | None
    """Latest formation leaving a full horizon of prices after it."""
    first_disclosure: dt.date | None
    """Earliest disclosure anywhere in the database."""
    with_statements: int = 0
    """Symbols holding at least one dated statement - the coverage ceiling."""
    universe: int = 0
    """Symbols stored in total."""


def suggest_formation(
    database: Database, horizon_days: int = TRADING_DAYS_PER_YEAR
) -> FormationAdvice:
    """Find the formation date the stored data supports best.

    Two constraints pull against each other. A formation date must be *late*
    enough that companies have filed something by then, and *early* enough that
    a full horizon of prices exists after it. A plan that only serves recent
    disclosures can leave that window narrow or empty, and the symptom is a
    factor test that silently runs on a tenth of the market.

    The date is chosen by **coverage**, never by outcome. Picking the formation
    that produces the best t-statistic is how a noise factor gets published;
    picking the one where the most names can be tested is just using the data.
    """
    calendar_horizon = dt.timedelta(days=round(horizon_days * 7 / 5))

    with database.session() as session:
        first_disclosures = dict(
            session.execute(
                select(Security.symbol, func.min(FinancialStatement.disclosed_on))
                .join(FinancialStatement, FinancialStatement.security_id == Security.id)
                .where(FinancialStatement.disclosed_on.is_not(None))
                .group_by(Security.symbol)
            ).all()
        )
        last_prices = dict(
            session.execute(
                select(Security.symbol, func.max(PriceBar.date))
                .join(PriceBar, PriceBar.security_id == Security.id)
                .group_by(Security.symbol)
            ).all()
        )
        universe = session.execute(select(func.count(Security.id))).scalar_one()

    if not first_disclosures or not last_prices or not universe:
        return FormationAdvice(None, 0.0, None, None, len(first_disclosures), universe)

    first_disclosure = min(first_disclosures.values())
    latest_feasible = max(last_prices.values()) - calendar_horizon
    if latest_feasible < first_disclosure:
        # No date satisfies both constraints: the disclosure history starts
        # after the last date a full horizon could be measured from.
        return FormationAdvice(
            None, 0.0, latest_feasible, first_disclosure, len(first_disclosures), universe
        )

    best: dt.date | None = None
    best_count = -1
    candidate = first_disclosure
    while candidate <= latest_feasible:
        count = sum(
            1
            for symbol, disclosed in first_disclosures.items()
            if disclosed <= candidate
            and (last := last_prices.get(symbol)) is not None
            and last >= candidate + calendar_horizon
        )
        if count > best_count:
            best, best_count = candidate, count
        candidate += dt.timedelta(days=14)

    return FormationAdvice(
        best=best,
        coverage=best_count / universe if universe else 0.0,
        latest_feasible=latest_feasible,
        first_disclosure=first_disclosure,
        with_statements=len(first_disclosures),
        universe=universe,
    )
