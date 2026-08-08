"""Data-access helpers for the dashboard, kept free of Streamlit.

These pure functions read from the database and reuse the scoring, screening,
and backtest layers, so the Streamlit app stays a thin rendering shell and this
logic is unit-testable without a running UI.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from stock_ai.ai.factory import get_ai_provider
from stock_ai.backtest.engine import BacktestEngine
from stock_ai.backtest.factor_test import FactorTestResult, run_factor_test
from stock_ai.backtest.report import metrics_frame
from stock_ai.backtest.strategy import BuyAndHold, build_strategy
from stock_ai.config.settings import get_settings
from stock_ai.data.base import FundamentalsProvider, PriceProvider
from stock_ai.data.fx import FxConverter
from stock_ai.data.jquants_provider import JQuantsPriceProvider
from stock_ai.data.service import (
    FundamentalsService,
    IngestionService,
    IngestResult,
)
from stock_ai.data.types import Importance
from stock_ai.data.yfinance_provider import (
    YFinanceFundamentalsProvider,
    YFinancePriceProvider,
)
from stock_ai.database.engine import Database
from stock_ai.database.repository import (
    FundamentalsRepository,
    HoldingRepository,
    PriceRepository,
    WatchlistRepository,
    list_symbols,
)
from stock_ai.ir.edinet import EdinetDisclosureSource
from stock_ai.ir.monitor import MonitorResult, WatchMonitor
from stock_ai.ir.sources import CompositeDisclosureSource, NewsDisclosureSource
from stock_ai.news.sources import YFinanceNewsSource
from stock_ai.portfolio.analysis import PortfolioAnalysis, analyze_portfolio
from stock_ai.portfolio.growth_factors import tenbagger_weighted_factors
from stock_ai.portfolio.ranking import rank_securities
from stock_ai.portfolio.scoring import WeightedScorer, default_weighted_factors
from stock_ai.screening.base import Condition, ScreeningContext
from stock_ai.screening.engine import ScreeningEngine
from stock_ai.screening.report import build_report, collect_fundamentals


def available_symbols(database: Database) -> list[str]:
    """Return every stored symbol."""
    with database.session() as session:
        return list_symbols(session)


def stored_overview(database: Database) -> pd.DataFrame:
    """Return a per-symbol overview: bar count, latest date, has fundamentals."""
    rows: list[dict[str, object]] = []
    with database.session() as session:
        price_repo = PriceRepository(session)
        fundamentals_repo = FundamentalsRepository(session)
        for symbol in list_symbols(session):
            latest = price_repo.latest_date(symbol)
            has_fund = fundamentals_repo.get_latest(symbol) is not None
            rows.append(
                {
                    "銘柄": symbol,
                    "日足本数": len(price_repo.get_prices(symbol)),
                    "最新日": latest.isoformat() if latest else "-",
                    "財務": "✓" if has_fund else "-",
                }
            )
    return pd.DataFrame(rows, columns=["銘柄", "日足本数", "最新日", "財務"])


def ingest_prices(
    database: Database,
    symbols: list[str],
    source: str = "yfinance",
    start: dt.date | None = None,
    end: dt.date | None = None,
    provider: PriceProvider | None = None,
) -> list[IngestResult]:
    """Fetch and store prices for ``symbols`` from ``source`` (US) or J-Quants (JP)."""
    market = "US"
    if provider is None:
        if source == "jquants":
            from stock_ai.config.settings import get_settings

            provider = JQuantsPriceProvider(api_key=get_settings().jquants_api_key)
            market = "JP"
        else:
            provider = YFinancePriceProvider()
    elif source == "jquants":
        market = "JP"
    service = IngestionService(provider, database)
    return service.ingest_many(symbols, start, end, market=market)


def ingest_fundamentals(
    database: Database,
    symbols: list[str],
    source: str = "yfinance",
    provider: FundamentalsProvider | None = None,
) -> list[IngestResult]:
    """Fetch and store a fundamentals snapshot for ``symbols``.

    ``source`` selects yfinance (US) or J-Quants (JP); ``provider`` overrides it.
    """
    market = "US"
    if provider is None:
        if source == "jquants":
            from stock_ai.config.settings import get_settings
            from stock_ai.data.jquants_fundamentals import JQuantsFundamentalsProvider

            provider = JQuantsFundamentalsProvider(
                api_key=get_settings().jquants_api_key,
                price_source=lambda sym: latest_close(database, sym),
            )
            market = "JP"
        else:
            provider = YFinanceFundamentalsProvider()
    elif source == "jquants":
        market = "JP"
    service = FundamentalsService(provider, database)
    return service.ingest_many(symbols, market=market)


def results_frame(results: list[IngestResult]) -> pd.DataFrame:
    """Turn ingestion results into a display table."""
    return pd.DataFrame(
        [
            {
                "銘柄": r.symbol,
                "件数": r.rows,
                "状態": "OK" if r.ok else f"エラー: {r.error or ''}",
            }
            for r in results
        ],
        columns=["銘柄", "件数", "状態"],
    )


def screen_table(database: Database, condition: Condition) -> pd.DataFrame:
    """Return the fundamentals report for symbols passing ``condition``."""
    passing = ScreeningEngine(database).screen(condition)
    return build_report(collect_fundamentals(database, passing))


def load_prices(database: Database, symbol: str) -> pd.DataFrame:
    """Return the stored OHLCV price frame for ``symbol``."""
    with database.session() as session:
        return PriceRepository(session).get_prices(symbol)


def latest_close(database: Database, symbol: str) -> float | None:
    """Return the most recent stored close for ``symbol``, or ``None``."""
    prices = load_prices(database, symbol)
    if prices.empty:
        return None
    return float(prices["close"].iloc[-1])


def score_table(database: Database, symbols: list[str]) -> pd.DataFrame:
    """Return a score ranking (symbol, score, per-factor) sorted high to low."""
    scorer = WeightedScorer(default_weighted_factors())
    rows: list[dict[str, object]] = []
    with database.session() as session:
        fundamentals_repo = FundamentalsRepository(session)
        price_repo = PriceRepository(session)
        for symbol in symbols:
            context = ScreeningContext(
                symbol=symbol,
                fundamentals=fundamentals_repo.get_latest(symbol),
                prices=price_repo.get_prices(symbol),
            )
            result = scorer.score(context)
            rows.append(
                {
                    "symbol": result.symbol,
                    "score": round(result.score, 1),
                    **{k: round(v, 3) for k, v in result.breakdown.items()},
                }
            )

    if not rows:
        return pd.DataFrame(columns=["symbol", "score"])
    return pd.DataFrame(rows).sort_values("score", ascending=False).reset_index(drop=True)


def backtest_comparison(
    database: Database,
    symbol: str,
    fast: int = 20,
    slow: int = 50,
    capital: float = 100_000.0,
    strategy: str = "sma",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return ``(equity_curves, metrics)`` for a strategy vs buy-and-hold.

    Args:
        database: Source of stored prices.
        symbol: Ticker to backtest.
        fast: Fast SMA window (``sma`` strategy).
        slow: Slow SMA / trend window.
        capital: Initial capital.
        strategy: Strategy name (``sma``/``sma200``/``macd``/``rsi``/``hold``).

    Returns:
        A tuple of the two equity curves (as one DataFrame) and the metrics table.
    """
    prices = load_prices(database, symbol)
    engine = BacktestEngine(capital)
    strat = build_strategy(strategy, fast=fast, slow=slow)

    strat_result = engine.run(prices, strat.generate_signals(prices))
    bench_result = engine.run(prices, BuyAndHold().generate_signals(prices))

    equity = pd.DataFrame(
        {strat.name: strat_result.equity, f"{symbol} buy&hold": bench_result.equity}
    )
    metrics = metrics_frame({strat.name: strat_result, f"{symbol} buy&hold": bench_result})
    return equity, metrics


# --- cross-market ranking ---------------------------------------------------


def cross_market_table(
    database: Database,
    base: str = "USD",
    rates: dict[str, float] | None = None,
    preset: str = "default",
    max_market_cap: float | None = None,
) -> pd.DataFrame:
    """Return the JP+US ranking with market cap restated in ``base``.

    ``rates`` pins the FX so a report is reproducible; anything unpinned is
    fetched live.
    """
    fx = FxConverter(base=base, rates=rates)
    factors = (
        tenbagger_weighted_factors(fx=fx) if preset == "tenbagger" else default_weighted_factors()
    )
    return rank_securities(
        database,
        scorer=WeightedScorer(factors),
        fx=fx,
        max_market_cap=max_market_cap,
        load_statements=preset == "tenbagger",
    )


# --- portfolio --------------------------------------------------------------


def portfolio_view(
    database: Database, base: str = "USD", rates: dict[str, float] | None = None
) -> PortfolioAnalysis:
    """Return the portfolio analysis in ``base`` currency."""
    return analyze_portfolio(database, fx=FxConverter(base=base, rates=rates))


def positions_frame(analysis: PortfolioAnalysis) -> pd.DataFrame:
    """Return the positions as a display table."""
    return pd.DataFrame(
        [
            {
                "銘柄": position.symbol,
                "市場": position.market,
                "セクター": str(position.sector),
                "数量": position.quantity,
                f"評価額({analysis.base_currency})": round(position.value, 2),
                "比率": position.weight,
                "損益": position.unrealized_return,
            }
            for position in analysis.positions
        ],
        columns=[
            "銘柄",
            "市場",
            "セクター",
            "数量",
            f"評価額({analysis.base_currency})",
            "比率",
            "損益",
        ],
    )


def exposure_frame(analysis: PortfolioAnalysis) -> pd.DataFrame:
    """Return sector weights as a table ready for a bar chart."""
    return pd.DataFrame(
        [
            {"セクター": str(sector), "比率": weight}
            for sector, weight in analysis.sector_weights.items()
        ]
    ).set_index("セクター")


def set_position(
    database: Database, symbol: str, quantity: float, cost: float, market: str = "US"
) -> None:
    """Record (or clear) a holding."""
    with database.session() as session:
        HoldingRepository(session).set_holding(symbol, quantity, cost, market=market)


# --- watchlist --------------------------------------------------------------


def watchlist_frame(database: Database) -> pd.DataFrame:
    """Return the watchlist as a display table."""
    with database.session() as session:
        entries = WatchlistRepository(session).list_entries()
    return pd.DataFrame(
        [
            {
                "銘柄": entry.symbol,
                "市場": entry.market,
                "通知しきい値": entry.min_importance.value,
                "メモ": entry.note or "-",
            }
            for entry in entries
        ],
        columns=["銘柄", "市場", "通知しきい値", "メモ"],
    )


def add_watch(
    database: Database,
    symbol: str,
    note: str | None,
    importance: Importance,
    market: str = "US",
) -> None:
    """Add or update a watchlist entry."""
    with database.session() as session:
        WatchlistRepository(session).add(
            symbol, note=note, min_importance=importance, market=market
        )


def remove_watch(database: Database, symbol: str) -> bool:
    """Remove a watchlist entry, reporting whether it existed."""
    with database.session() as session:
        return WatchlistRepository(session).remove(symbol)


def run_monitor(
    database: Database,
    provider_name: str,
    feed: str = "all",
    lookback_days: int = 7,
) -> MonitorResult:
    """Run one monitoring pass and return its alerts (no notification sent)."""
    settings = get_settings()
    edinet = EdinetDisclosureSource(api_key=settings.edinet_api_key, lookback_days=lookback_days)
    news = NewsDisclosureSource(YFinanceNewsSource())
    source: object
    if feed == "edinet":
        source = edinet
    elif feed == "news":
        source = news
    else:
        source = CompositeDisclosureSource(edinet, news)

    monitor = WatchMonitor(
        database, source=source, provider=get_ai_provider(provider_name, settings)
    )
    return monitor.run()


# --- factor test ------------------------------------------------------------


def factor_test(
    database: Database,
    formation: dt.date,
    preset: str = "tenbagger",
    horizon_days: int = 252,
    buckets: int = 3,
    base: str = "USD",
    rates: dict[str, float] | None = None,
) -> FactorTestResult:
    """Rank as of ``formation`` and measure the forward returns."""
    fx = FxConverter(base=base, rates=rates)
    factors = (
        tenbagger_weighted_factors(fx=fx) if preset == "tenbagger" else default_weighted_factors()
    )
    return run_factor_test(
        database,
        WeightedScorer(factors),
        formation=formation,
        horizon_days=horizon_days,
        buckets=buckets,
    )


def stored_counts(database: Database) -> dict[str, int]:
    """Count what is actually in the database, per data type.

    The dashboard shows this because "nothing changed" and "nothing was ever
    loaded" look identical on screen. A screen that finds no cheap stocks and a
    screen that has no valuation figures to read both render as an empty table.
    """
    from sqlalchemy import func, select

    from stock_ai.database.models import (
        FinancialStatement,
        FundamentalSnapshot,
        PriceBar,
        Security,
    )

    def distinct_securities(model: type) -> int:
        return int(
            session.execute(select(func.count(func.distinct(model.security_id)))).scalar_one() or 0
        )

    with database.session() as session:
        return {
            "securities": int(session.execute(select(func.count(Security.id))).scalar_one() or 0),
            "with_prices": distinct_securities(PriceBar),
            "with_statements": distinct_securities(FinancialStatement),
            "with_fundamentals": distinct_securities(FundamentalSnapshot),
        }
