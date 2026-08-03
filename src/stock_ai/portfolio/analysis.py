"""Portfolio analytics: exposure, concentration, and realized risk.

Positions are valued in a single base currency (see
:mod:`stock_ai.data.fx`), because a JP/US portfolio cannot be weighted at all
until yen and dollars are on the same scale — a ¥1m position is not ten times a
$100k one.

On what is deliberately *not* here: there is no expected-return estimate. The
obvious implementation, annualizing a trailing mean return, is a famously poor
forecast — its estimation error is large enough to swamp the signal, which is
why mean-variance optimizers built on it produce unstable weights. Rather than
dress that up as a projection, this module reports what actually happened
(realized volatility, drawdown, correlation) and leaves the forward view to the
caller.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from stock_ai.backtest.metrics import TRADING_DAYS_PER_YEAR, max_drawdown
from stock_ai.core.logging import get_logger
from stock_ai.data.fx import FxConverter
from stock_ai.data.schema import ADJ_CLOSE
from stock_ai.data.sectors import Sector
from stock_ai.data.types import HoldingRecord
from stock_ai.database.engine import Database
from stock_ai.database.repository import HoldingRepository, PriceRepository, get_profile

logger = get_logger(__name__)


@dataclass(frozen=True)
class PositionView:
    """One holding, valued and weighted in the portfolio's base currency."""

    symbol: str
    market: str
    sector: Sector
    quantity: float
    value: float
    cost: float
    weight: float

    @property
    def unrealized_return(self) -> float | None:
        """Return versus cost basis, or ``None`` when the cost is unusable."""
        if self.cost <= 0:
            return None
        return self.value / self.cost - 1.0


@dataclass(frozen=True)
class PortfolioAnalysis:
    """A valued portfolio with its exposures and realized risk."""

    positions: list[PositionView]
    total_value: float
    total_cost: float
    sector_weights: dict[Sector, float]
    market_weights: dict[str, float]
    base_currency: str
    annual_volatility: float | None = None
    max_drawdown: float | None = None
    concentration: float | None = None
    """Herfindahl index of the weights: 1/N for N equal positions, 1.0 if all in one."""
    correlations: pd.DataFrame | None = None
    unpriced: list[str] = field(default_factory=list)
    """Held symbols with no stored price — excluded from every weight below."""

    @property
    def unrealized_return(self) -> float | None:
        """Portfolio return versus total cost basis."""
        if self.total_cost <= 0:
            return None
        return self.total_value / self.total_cost - 1.0

    @property
    def effective_positions(self) -> float | None:
        """Concentration expressed as an equivalent count of equal positions."""
        if not self.concentration:
            return None
        return 1.0 / self.concentration


def analyze_portfolio(
    database: Database,
    fx: FxConverter | None = None,
    lookback: int = TRADING_DAYS_PER_YEAR,
) -> PortfolioAnalysis:
    """Value the stored holdings and describe their exposure and risk.

    Args:
        database: Source of holdings, prices, and profiles.
        fx: Converter into the reporting currency; defaults to live USD rates.
        lookback: Trailing bars used for the risk figures.

    Returns:
        A :class:`PortfolioAnalysis`. Holdings with no stored price are listed
        in ``unpriced`` and excluded from weights rather than counted at zero,
        which would silently understate every other position's share.
    """
    fx = fx or FxConverter()

    with database.session() as session:
        holdings = HoldingRepository(session).list_holdings()
        price_repo = PriceRepository(session)
        priced: list[tuple[HoldingRecord, Sector, float, float]] = []
        unpriced: list[str] = []
        returns: dict[str, pd.Series] = {}

        for holding in holdings:
            prices = price_repo.get_prices(holding.symbol)
            last = _latest_close(prices)
            if last is None:
                unpriced.append(holding.symbol)
                continue

            profile = get_profile(session, holding.symbol)
            sector = Sector(profile.sector) if profile and profile.sector else Sector.OTHER
            value = fx.convert_from_market(holding.quantity * last, holding.market) or 0.0
            cost = fx.convert_from_market(holding.cost_basis(), holding.market) or 0.0
            priced.append((holding, sector, value, cost))
            returns[holding.symbol] = _returns(prices, lookback)

    total_value = sum(value for _h, _s, value, _c in priced)
    positions = [
        PositionView(
            symbol=holding.symbol,
            market=holding.market,
            sector=sector,
            quantity=holding.quantity,
            value=value,
            cost=cost,
            weight=(value / total_value) if total_value > 0 else 0.0,
        )
        for holding, sector, value, cost in priced
    ]
    positions.sort(key=lambda p: p.weight, reverse=True)

    weights = {p.symbol: p.weight for p in positions}
    frame = _returns_frame(returns, weights)
    logger.info("Analyzed %d position(s), %d unpriced", len(positions), len(unpriced))

    return PortfolioAnalysis(
        positions=positions,
        total_value=total_value,
        total_cost=sum(cost for _h, _s, _v, cost in priced),
        sector_weights=_group_weights(positions, lambda p: p.sector),
        market_weights=_group_weights(positions, lambda p: p.market),
        base_currency=fx.base,
        annual_volatility=_portfolio_volatility(frame, weights),
        max_drawdown=_portfolio_drawdown(frame, weights),
        concentration=_herfindahl(positions),
        correlations=frame.corr() if frame is not None and frame.shape[1] > 1 else None,
        unpriced=unpriced,
    )


def _latest_close(prices: pd.DataFrame) -> float | None:
    """Return the most recent adjusted close, or ``None`` if unavailable."""
    if prices.empty or ADJ_CLOSE not in prices.columns:
        return None
    value = prices[ADJ_CLOSE].iloc[-1]
    return None if pd.isna(value) else float(value)


def _returns(prices: pd.DataFrame, lookback: int) -> pd.Series:
    """Return the trailing daily return series for one security."""
    if prices.empty or ADJ_CLOSE not in prices.columns:
        return pd.Series(dtype=float)
    return prices[ADJ_CLOSE].iloc[-(lookback + 1) :].pct_change().dropna()


def _returns_frame(returns: dict[str, pd.Series], weights: dict[str, float]) -> pd.DataFrame | None:
    """Align the per-symbol return series onto shared dates.

    Only dates every held name traded are kept: a portfolio return needs all of
    its legs, and forward-filling a missing name would invent a flat day for it.
    """
    usable = {sym: s for sym, s in returns.items() if not s.empty and weights.get(sym, 0.0) > 0}
    if not usable:
        return None
    frame = pd.concat(usable, axis=1).dropna()
    return frame if not frame.empty else None


def _weighted_returns(frame: pd.DataFrame | None, weights: dict[str, float]) -> pd.Series | None:
    """Combine per-symbol returns into the portfolio's return series."""
    if frame is None or frame.empty:
        return None
    aligned = pd.Series({sym: weights.get(sym, 0.0) for sym in frame.columns})
    total = aligned.sum()
    if total <= 0:
        return None
    return frame.mul(aligned / total, axis=1).sum(axis=1)


def _portfolio_volatility(frame: pd.DataFrame | None, weights: dict[str, float]) -> float | None:
    """Annualized standard deviation of the weighted portfolio return.

    Computed from the combined series rather than from each position's own
    volatility, so diversification is reflected: two anticorrelated names give a
    lower figure than either alone, which averaging would miss.
    """
    series = _weighted_returns(frame, weights)
    if series is None or len(series) < 2:
        return None
    return float(series.std(ddof=0) * math.sqrt(TRADING_DAYS_PER_YEAR))


def _portfolio_drawdown(frame: pd.DataFrame | None, weights: dict[str, float]) -> float | None:
    """Worst peak-to-trough decline of the weighted portfolio over the window."""
    series = _weighted_returns(frame, weights)
    if series is None or len(series) < 2:
        return None
    return max_drawdown((1.0 + series).cumprod())


def _group_weights(positions: list[PositionView], key) -> dict:  # noqa: ANN001 - generic key
    """Sum position weights by ``key``, largest group first."""
    totals: dict = {}
    for position in positions:
        totals[key(position)] = totals.get(key(position), 0.0) + position.weight
    return dict(sorted(totals.items(), key=lambda kv: kv[1], reverse=True))


def _herfindahl(positions: list[PositionView]) -> float | None:
    """Return the Herfindahl concentration index of the position weights."""
    if not positions:
        return None
    return sum(p.weight**2 for p in positions)
