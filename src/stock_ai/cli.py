"""Command-line interface for stock-ai.

Exposes the ``stock-ai`` console script. Subcommands for each pipeline stage
are added as the phases progress.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import sys
from pathlib import Path

import pandas as pd
import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
)
from rich.table import Table

from stock_ai import __version__
from stock_ai.ai.analysis import analyze_sentiment
from stock_ai.ai.analysis import summarize as ai_summarize
from stock_ai.ai.factory import get_ai_provider
from stock_ai.ai.query import parse_query, run_query
from stock_ai.backtest.engine import BacktestEngine
from stock_ai.backtest.factor_test import (
    FactorTestResult,
    run_factor_test,
    suggest_formation,
)
from stock_ai.backtest.report import metrics_frame
from stock_ai.backtest.strategy import BuyAndHold, Strategy, build_strategy
from stock_ai.config.settings import Settings, get_settings
from stock_ai.core.exceptions import AIError, BacktestError, DataError, NotificationError
from stock_ai.core.logging import configure_logging
from stock_ai.core.scheduler import DailyScheduler
from stock_ai.data.base import PriceProvider
from stock_ai.data.bulk import BulkIngester, Dataset, store_universe
from stock_ai.data.fx import FxConverter
from stock_ai.data.jquants_fundamentals import JQuantsFundamentalsProvider
from stock_ai.data.jquants_profile import JQuantsProfileProvider
from stock_ai.data.jquants_provider import JQuantsPriceProvider
from stock_ai.data.service import FundamentalsService, IngestionService, IngestResult
from stock_ai.data.types import Importance
from stock_ai.data.universe import JQuantsUniverse, Segment
from stock_ai.data.yfinance_provider import (
    YFinanceFundamentalsProvider,
    YFinancePriceProvider,
    YFinanceProfileProvider,
)
from stock_ai.database.engine import Database
from stock_ai.database.repository import (
    FinancialStatementRepository,
    FundamentalsRepository,
    HoldingRepository,
    PriceRepository,
    WatchlistRepository,
    list_securities,
    upsert_profile,
)
from stock_ai.ir.edinet import EdinetDisclosureSource
from stock_ai.ir.monitor import WatchMonitor
from stock_ai.ir.sources import CompositeDisclosureSource, NewsDisclosureSource
from stock_ai.news.sources import YFinanceNewsSource
from stock_ai.notification.factory import get_notifier
from stock_ai.portfolio.analysis import PortfolioAnalysis, analyze_portfolio
from stock_ai.portfolio.growth_factors import tenbagger_weighted_factors
from stock_ai.portfolio.ranking import rank_securities
from stock_ai.portfolio.scoring import (
    WeightedFactor,
    WeightedScorer,
    default_weighted_factors,
)
from stock_ai.screening.base import All, Condition, ScreeningContext
from stock_ai.screening.conditions import (
    MaxMarketCap,
    MaxPayoutRatio,
    MaxPBR,
    MaxPER,
    MinConsecutiveDividendIncreases,
    MinDividendGrowth,
    MinDividendYield,
    MinMarketCap,
    MinProfitGrowth,
    MinRevenueGrowth,
    MinROE,
)
from stock_ai.screening.engine import ScreeningEngine
from stock_ai.screening.report import (
    SUPPORTED_FORMATS,
    build_report,
    collect_fundamentals,
    write_report,
)

app = typer.Typer(
    name="stock-ai",
    help="AI-driven stock screening, backtesting, and trading system.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


@app.callback()
def _root() -> None:
    """Group root: forces multi-command mode so subcommands keep their names."""


@app.command()
def version() -> None:
    """Print the installed stock-ai version."""
    console.print(f"stock-ai [bold cyan]v{__version__}[/]")


@app.command()
def info() -> None:
    """Show the active configuration (secrets are masked, never printed)."""
    settings = get_settings()
    configure_logging(settings.log_level)

    table = Table(title="stock-ai configuration")
    table.add_column("Key", style="cyan", no_wrap=True)
    table.add_column("Value", style="white")
    table.add_row("version", __version__)
    table.add_row("env", settings.env)
    table.add_row("log_level", settings.log_level)
    for label, is_set in _secret_status(settings):
        table.add_row(label, "[green]set[/]" if is_set else "[dim]-[/]")
    console.print(table)


@app.command()
def fetch(
    symbols: list[str] = typer.Argument(..., help="Ticker symbols, e.g. AAPL MSFT"),
    start: str | None = typer.Option(None, help="ISO start date YYYY-MM-DD."),
    end: str | None = typer.Option(None, help="ISO end date; defaults to today."),
    lookback: int = typer.Option(365, help="Backfill days when a symbol has no data."),
    source: str = typer.Option("yfinance", help="Data source: yfinance (US) | jquants (JP)."),
) -> None:
    """Fetch daily prices for SYMBOLS and store them in the local database."""
    settings = get_settings()
    configure_logging(settings.log_level)

    provider, market = _price_source(source, settings)
    database = Database()
    database.create_all()
    service = IngestionService(provider, database, default_lookback_days=lookback)

    results = service.ingest_many(symbols, _parse_date(start), _parse_date(end), market=market)
    _render_results(results)

    if any(not r.ok for r in results):
        raise typer.Exit(code=1)


def _price_source(source: str, settings: Settings) -> tuple[PriceProvider, str]:
    """Return the price provider and market code for a data source name."""
    key = source.lower()
    if key == "yfinance":
        return YFinancePriceProvider(), "US"
    if key == "jquants":
        return JQuantsPriceProvider(api_key=settings.jquants_api_key), "JP"
    raise typer.BadParameter(f"Unknown source {source!r}; use 'yfinance' or 'jquants'.")


@app.command()
def fundamentals(
    symbols: list[str] = typer.Argument(
        None, help="Ticker symbols, e.g. AAPL MSFT. Omit to refresh every stored US symbol."
    ),
) -> None:
    """Fetch a fundamentals snapshot for SYMBOLS and store it in the database.

    Omitting SYMBOLS refreshes everything already stored for the US market. That
    is the form to reach for after a provider-side fix: a snapshot is only as
    correct as the code that parsed it, so a corrected parser has to be run back
    over the rows the old one wrote - nothing re-reads them on its own.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    database = Database()
    database.create_all()

    targets = list(symbols or [])
    if not targets:
        with database.session() as session:
            targets = [sym for sym, market in list_securities(session) if market.upper() == "US"]
        if not targets:
            console.print(
                "[yellow]No US symbols stored.[/] Pass symbols explicitly, e.g. "
                "'stock-ai fundamentals AAPL MSFT'."
            )
            raise typer.Exit(code=1)
        console.print(f"Refreshing fundamentals for {len(targets)} stored US symbol(s).")

    service = FundamentalsService(YFinanceFundamentalsProvider(), database)

    results = service.ingest_many(targets)
    _render_results(results)

    if any(not r.ok for r in results):
        raise typer.Exit(code=1)


@app.command()
def statements(
    symbols: list[str] = typer.Argument(..., help="JP security codes, e.g. 7203 4593"),
) -> None:
    """Fetch and store the disclosed statement history for SYMBOLS (J-Quants).

    This is what the growth, dividend-streak, and payout screens read. One
    request per symbol returns every period the plan covers, so the whole
    history lands in a single run.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    database = Database()
    database.create_all()
    provider = JQuantsFundamentalsProvider(api_key=settings.jquants_api_key)

    results: list[IngestResult] = []
    for symbol in symbols:
        try:
            reports = provider.fetch_statements(symbol)
            with database.session() as session:
                rows = FinancialStatementRepository(session).upsert_reports(
                    symbol, reports, market="JP"
                )
            results.append(IngestResult(symbol, rows, ok=True))
        except Exception as exc:  # one bad symbol must not abort the batch
            results.append(IngestResult(symbol, 0, ok=False, error=str(exc)))

    _render_results(results)
    if any(not r.ok for r in results):
        raise typer.Exit(code=1)


@app.command()
def profile(
    symbols: list[str] = typer.Argument(..., help="Symbols whose sector to fetch."),
    source: str = typer.Option("yfinance", help="yfinance (US) | jquants (JP)."),
) -> None:
    """Fetch and store name and sector for SYMBOLS.

    Sector is what the portfolio breakdown groups by, and it is normalized onto
    one taxonomy so JP and US holdings can be compared.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    key = source.lower()
    if key == "jquants":
        provider = JQuantsProfileProvider(api_key=settings.jquants_api_key)
    elif key == "yfinance":
        provider = YFinanceProfileProvider()
    else:
        raise typer.BadParameter(f"Unknown source {source!r}; use 'yfinance' or 'jquants'.")

    database = Database()
    database.create_all()

    results: list[IngestResult] = []
    for symbol in symbols:
        try:
            fetched = provider.fetch_profile(symbol)
            with database.session() as session:
                upsert_profile(session, fetched)
            results.append(IngestResult(symbol, 1, ok=True))
        except Exception as exc:  # one bad symbol must not abort the batch
            results.append(IngestResult(symbol, 0, ok=False, error=str(exc)))

    _render_results(results)
    if any(not r.ok for r in results):
        raise typer.Exit(code=1)


@app.command()
def hold(
    symbol: str = typer.Argument(..., help="Symbol to record a position in."),
    quantity: float = typer.Option(..., help="Shares held; 0 removes the position."),
    cost: float = typer.Option(0.0, help="Average cost per share, in the listing currency."),
    market: str = typer.Option("US", help="Listing market: US | JP."),
) -> None:
    """Record (or clear) a holding used by the portfolio report."""
    settings = get_settings()
    configure_logging(settings.log_level)

    database = Database()
    database.create_all()
    with database.session() as session:
        HoldingRepository(session).set_holding(symbol, quantity, cost, market=market.upper())

    if quantity <= 0:
        console.print(f"Cleared holding in [cyan]{symbol}[/].")
    else:
        console.print(f"Holding {quantity:g} [cyan]{symbol}[/] at {cost:g} ({market.upper()}).")


@app.command()
def portfolio(
    base: str = typer.Option("USD", help="Reporting currency."),
    fx_rate: list[str] = typer.Option([], "--fx", help="Pin a rate as CUR=VALUE."),
    lookback: int = typer.Option(252, help="Trailing bars used for the risk figures."),
) -> None:
    """Report the stored portfolio: exposure, concentration, and realized risk."""
    settings = get_settings()
    configure_logging(settings.log_level)

    database = Database()
    database.create_all()
    try:
        analysis = analyze_portfolio(
            database,
            fx=FxConverter(base=base, rates=_parse_fx_rates(fx_rate)),
            lookback=lookback,
        )
    except DataError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc

    if not analysis.positions:
        console.print("[yellow]No priced holdings; record some with 'hold' first.[/]")
        return
    _render_portfolio(analysis)


def _render_portfolio(analysis: PortfolioAnalysis) -> None:
    """Print the portfolio report as a set of Rich tables."""
    base = analysis.base_currency
    positions = Table(title=f"portfolio ({base})")
    for column, justify in (
        ("symbol", "left"),
        ("mkt", "left"),
        ("sector", "left"),
        ("qty", "right"),
        (f"value ({base})", "right"),
        ("weight", "right"),
        ("P/L", "right"),
    ):
        positions.add_column(column, justify=justify, style="cyan" if column == "symbol" else None)
    for position in analysis.positions:
        gain = position.unrealized_return
        positions.add_row(
            position.symbol,
            position.market,
            str(position.sector),
            f"{position.quantity:g}",
            _format_cap(position.value),
            f"{position.weight:.1%}",
            "-" if gain is None else f"{gain:+.1%}",
        )
    console.print(positions)

    breakdown = Table(title="exposure")
    breakdown.add_column("group", style="cyan")
    breakdown.add_column("weight", justify="right")
    for sector, weight in analysis.sector_weights.items():
        breakdown.add_row(str(sector), f"{weight:.1%}")
    for market, weight in analysis.market_weights.items():
        breakdown.add_row(f"[dim]market:[/] {market}", f"{weight:.1%}")
    console.print(breakdown)

    risk = Table(title="risk (realized, trailing window)")
    risk.add_column("metric", style="cyan")
    risk.add_column("value", justify="right")
    total = analysis.unrealized_return
    risk.add_row(f"total value ({base})", _format_cap(analysis.total_value))
    risk.add_row("unrealized P/L", "-" if total is None else f"{total:+.2%}")
    risk.add_row("annual volatility", _optional_pct(analysis.annual_volatility))
    risk.add_row("max drawdown", _optional_pct(analysis.max_drawdown))
    risk.add_row(
        "concentration (HHI)",
        "-" if analysis.concentration is None else f"{analysis.concentration:.3f}",
    )
    effective = analysis.effective_positions
    risk.add_row("effective positions", "-" if effective is None else f"{effective:.2f}")
    console.print(risk)

    if analysis.unpriced:
        console.print(
            f"[yellow]Excluded (no stored price):[/] {', '.join(analysis.unpriced)} "
            "- run 'fetch' for these to include them in the weights."
        )
    console.print(
        "[dim]No expected-return figure: a trailing mean is too noisy to project "
        "forward, so only realized risk is reported.[/]"
    )


def _optional_pct(value: float | None) -> str:
    """Render an optional fraction as a percentage."""
    return "-" if value is None else f"{value:.2%}"


@app.command()
def universe(
    segment: str = typer.Option("prime", help="prime | standard | growth | all."),
    limit: int | None = typer.Option(None, help="Cap the list - use for a trial run."),
    store: bool = typer.Option(True, help="Store the profiles (names and sectors)."),
    as_of: str | None = typer.Option(
        None, "--as-of", help="Snapshot date (YYYY-MM-DD) for a delayed J-Quants plan."
    ),
) -> None:
    """List (and store) the JP listed universe for a market segment.

    One request. Run this before ``bulk-fetch``: it gives every later step a
    symbol list, a company name, and a sector.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    try:
        chosen = Segment(segment.lower())
    except ValueError as exc:
        raise typer.BadParameter(
            f"segment must be prime, standard, growth, or all; got {segment!r}."
        ) from exc

    snapshot = _parse_date(as_of)
    try:
        source = JQuantsUniverse(api_key=settings.jquants_api_key, as_of=snapshot)
        profiles = source.profiles(chosen, limit=limit)
    except DataError as exc:
        console.print(f"[red]{exc}[/]")
        if "403" in str(exc):
            # 403 covers two different problems and the fix differs. Saying so
            # matters because "403" otherwise reads as "wrong key" and sends
            # people to re-issue a key that was never the problem - this one
            # already answers 200 on other endpoints.
            console.print(
                "[yellow]Read the message above, not the 403.[/] J-Quants answers "
                "403 for three different problems and only the message tells them "
                "apart:\n"
                "  - 'endpoint does not exist' -> the URL is wrong, not your plan. "
                "Report it; this is a bug here.\n"
                "  - a date or period -> your plan serves delayed data. Ask for an "
                "older snapshot:\n"
                "      uv run stock-ai universe --segment growth --as-of 2025-01-31\n"
                "  - a subscription or plan -> the endpoint really is not included.\n"
                "In every case you can skip 'universe' and name symbols directly:\n"
                "  uv run stock-ai bulk-fetch --what prices --symbols 7203,6758,9984"
            )
        raise typer.Exit(code=1) from exc

    if not profiles:
        console.print(f"[yellow]No listings found on {chosen.value}.[/]")
        raise typer.Exit(code=1)

    database = Database()
    database.create_all()
    if store:
        store_universe(database, profiles)

    table = Table(title=f"{chosen.value} universe ({len(profiles)} listings)")
    table.add_column("code", style="cyan")
    table.add_column("name")
    table.add_column("sector")
    for profile in profiles[:30]:
        table.add_row(profile.symbol, profile.name or "-", profile.sector or "-")
    console.print(table)
    if len(profiles) > 30:
        console.print(f"[dim]... and {len(profiles) - 30} more.[/]")
    if store:
        console.print(f"Stored [bold]{len(profiles)}[/] profiles.")


@app.command()
def bulk_fetch(
    what: str = typer.Option("prices", help="prices | statements."),
    segment: str = typer.Option(
        "stored", help="prime | standard | growth | all | stored (symbols already in the DB)."
    ),
    symbols: str | None = typer.Option(
        None,
        "--symbols",
        help="Comma-separated codes to use instead of a segment, e.g. 7203,6758.",
    ),
    limit: int | None = typer.Option(None, help="Cap the symbol count."),
    lookback: int = typer.Option(365, help="Backfill days for a symbol with no prices."),
    throttle: float = typer.Option(0.2, help="Seconds to pause between symbols."),
    resume: bool = typer.Option(True, help="Skip symbols that are already current."),
) -> None:
    """Backfill prices or statements across a whole universe.

    Safe to interrupt and re-run: already-current symbols are skipped without a
    request, and one symbol's failure never ends the run. Expect roughly
    ``symbols x throttle`` seconds plus network time - TSE Prime is ~1,600 names.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    try:
        dataset = Dataset(what.lower())
    except ValueError as exc:
        raise typer.BadParameter(f"--what must be prices or statements; got {what!r}.") from exc

    database = Database()
    database.create_all()
    targets = _bulk_symbols(segment, symbols, settings, database, limit)
    if not targets:
        console.print(
            "[yellow]No symbols to process.[/] Run 'universe' first, or name them "
            "directly with --symbols 7203,6758."
        )
        raise typer.Exit(code=1)

    console.print(
        f"Fetching [bold]{dataset.value}[/] for {len(targets)} symbol(s). "
        "Interrupting is safe - re-run to resume."
    )
    ingester = BulkIngester(database, api_key=settings.jquants_api_key, throttle_seconds=throttle)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(dataset.value, total=len(targets))

        def advance(index: int, total: int, symbol: str) -> None:
            progress.update(task, completed=index - 1, description=f"{dataset.value} {symbol}")

        report = ingester.run(
            targets, dataset, resume=resume, lookback_days=lookback, progress=advance
        )
        progress.update(task, completed=len(targets))

    console.print(report.summary())
    if report.aborted:
        console.print(
            f"[red]Stopped early:[/] {report.aborted}\n"
            "  A rate limit applies to the whole run, not to one symbol, so "
            "continuing would only collect the same refusal.\n"
            "  Wait a while and re-run the same command - already-loaded symbols "
            "are skipped without a request, so it picks up where it stopped."
        )
    elif report.rate_limited:
        console.print(
            f"[yellow]Rate limited {report.rate_limited}x[/] - the run slowed itself "
            "down and continued. Nothing was lost."
        )
    if report.failed:
        failures = Table(title=f"failed ({len(report.failed)})")
        failures.add_column("symbol", style="cyan")
        failures.add_column("error", overflow="fold")
        for symbol, error in list(report.failed.items())[:20]:
            failures.add_row(symbol, error)
        console.print(failures)
        if len(report.failed) > 20:
            console.print(f"[dim]... and {len(report.failed) - 20} more.[/]")
        console.print("[dim]Re-run to retry only the failures.[/]")


def _bulk_symbols(
    segment: str,
    symbols: str | None,
    settings: Settings,
    database: Database,
    limit: int | None,
) -> list[str]:
    """Resolve the symbol list for a bulk run.

    An explicit ``--symbols`` list wins over the segment. That is the escape
    hatch for a J-Quants plan that refuses the listings endpoint: prices and
    statements are separate endpoints and may well be available, so being
    unable to *enumerate* the market must not stop you loading a market.

    ``stored`` reuses what is already in the database, which avoids a universe
    request when the list has not changed.
    """
    if symbols:
        named = [part.strip() for part in symbols.replace(" ", ",").split(",") if part.strip()]
        if not named:
            raise typer.BadParameter("--symbols was given but contained no codes.")
        return named[:limit] if limit else named

    key = segment.lower()
    if key == "stored":
        with database.session() as session:
            symbols = [symbol for symbol, _market in list_securities(session)]
    else:
        try:
            chosen = Segment(key)
        except ValueError as exc:
            raise typer.BadParameter(
                f"segment must be prime, standard, growth, all, or stored; got {segment!r}."
            ) from exc
        profiles = JQuantsUniverse(api_key=settings.jquants_api_key).profiles(chosen)
        store_universe(database, profiles)
        symbols = [profile.symbol for profile in profiles]
    return symbols[:limit] if limit else symbols


@app.command()
def screen(
    min_roe: float | None = typer.Option(None, help="Minimum return on equity."),
    max_per: float | None = typer.Option(None, help="Maximum price/earnings."),
    max_pbr: float | None = typer.Option(None, help="Maximum price/book."),
    min_dividend_yield: float | None = typer.Option(None, help="Minimum dividend yield."),
    min_market_cap: float | None = typer.Option(None, help="Minimum market cap."),
    max_market_cap: float | None = typer.Option(None, help="Maximum market cap (small caps)."),
    min_revenue_growth: float | None = typer.Option(None, help="増収: min revenue growth."),
    min_profit_growth: float | None = typer.Option(None, help="増益: min net income growth."),
    min_dividend_growth: float | None = typer.Option(
        None, help="増配: min DPS growth (use >0 to require a real raise)."
    ),
    growth_years: int = typer.Option(1, help="Fiscal years the growth options look back."),
    min_dividend_streak: int | None = typer.Option(
        None, help="連続増配: minimum consecutive years the dividend was raised."
    ),
    max_payout_ratio: float | None = typer.Option(None, help="Maximum payout ratio (DPS/EPS)."),
    out: Path | None = typer.Option(None, help="Output file; prints a table if omitted."),
    fmt: str = typer.Option("csv", "--format", help="csv | json | xlsx."),
) -> None:
    """Screen stored securities by fundamentals and report the matches.

    Growth and dividend-streak options read the stored statement series, which
    ``statements`` ingests. Without it those criteria match nothing, by design.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    condition = _build_condition(
        min_roe,
        max_per,
        max_pbr,
        min_dividend_yield,
        min_market_cap,
        max_market_cap,
        min_revenue_growth,
        min_profit_growth,
        min_dividend_growth,
        growth_years,
        min_dividend_streak,
        max_payout_ratio,
    )
    if out is not None and fmt.lower() not in SUPPORTED_FORMATS:
        raise typer.BadParameter(f"format must be one of {SUPPORTED_FORMATS}.")

    needs_statements = any(
        option is not None
        for option in (
            min_revenue_growth,
            min_profit_growth,
            min_dividend_growth,
            min_dividend_streak,
            max_payout_ratio,
        )
    )
    database = Database()
    database.create_all()
    passing = ScreeningEngine(database, load_statements=needs_statements).screen(condition)
    report = build_report(collect_fundamentals(database, passing))

    console.print(f"Matched [bold]{len(passing)}[/] symbols for [cyan]{condition}[/]")
    if passing and report.empty:
        # A growth screen reads the statement series, but the report is built
        # from the snapshot table. Matching symbols and then printing nothing is
        # the exact silent failure this project keeps trying to avoid, so name
        # the cause rather than leaving an empty table to be interpreted.
        console.print(
            f"[yellow]{len(passing)} symbol(s) passed but none has a fundamentals "
            "snapshot, so there is nothing to tabulate.[/]\n"
            "  JP snapshots are written by 'bulk-fetch --what statements'; if that "
            "ran before this was fixed, re-run it to fill them in.\n"
            "  US snapshots come from 'fundamentals'."
        )
    if out is not None:
        write_report(report, out, fmt)
        console.print(f"Wrote {len(report)} rows to [green]{out}[/] ({fmt}).")
    else:
        _render_report(report)


@app.command()
def backtest(
    symbol: str = typer.Argument(..., help="Ticker to backtest (must be fetched)."),
    strategy: str = typer.Option("sma", help="Strategy: hold|sma|sma200|macd|rsi."),
    fast: int = typer.Option(20, help="Fast SMA window (sma strategy)."),
    slow: int = typer.Option(50, help="Slow SMA window (sma strategy)."),
    benchmark: str | None = typer.Option(
        None, help="Benchmark symbol; default is buy-and-hold of SYMBOL."
    ),
    capital: float = typer.Option(100_000.0, help="Initial capital."),
    commission: float = typer.Option(0.0, help="Per-trade cost fraction."),
    slippage: float = typer.Option(0.0, help="Per-fill slippage fraction."),
) -> None:
    """Backtest a strategy on SYMBOL and compare it to a benchmark."""
    settings = get_settings()
    configure_logging(settings.log_level)

    database = Database()
    database.create_all()
    engine = BacktestEngine(capital, commission, slippage)

    prices = _load_prices(database, symbol)
    strat = _build_strategy(strategy, fast, slow)
    strat_result = engine.run(prices, strat.generate_signals(prices))

    if benchmark is not None:
        bench_prices = _load_prices(database, benchmark)
        bench_name = f"{benchmark} buy&hold"
    else:
        bench_prices = prices
        bench_name = f"{symbol} buy&hold"
    bench_result = engine.run(bench_prices, BuyAndHold().generate_signals(bench_prices))

    table = metrics_frame({strat.name: strat_result, bench_name: bench_result})
    _render_metrics_table(table)


@app.command()
def factor_test(
    formation: str = typer.Argument(..., help="Ranking date, YYYY-MM-DD."),
    preset: str = typer.Option("tenbagger", help="Factor set: default | tenbagger."),
    horizon: int = typer.Option(252, help="Trading days held after formation."),
    buckets: int = typer.Option(3, help="Slices to split the ranking into."),
    base: str = typer.Option("USD", help="Base currency for size comparisons."),
    fx_rate: list[str] = typer.Option([], "--fx", help="Pin a rate as CUR=VALUE."),
) -> None:
    """Test whether a score predicted returns: rank, hold, compare.

    Ranks the stored universe using only data available on FORMATION, holds the
    top bucket for --horizon bars, and compares against the equal-weight
    universe. A score that adds nothing will not beat it.

    The universe is whatever is in the local database, which excludes delisted
    names, so results are optimistic by an unmeasured amount. This can falsify
    a score; it cannot prove one works.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    fx = FxConverter(base=base, rates=_parse_fx_rates(fx_rate))
    factors, _needs_statements = _factor_preset(preset, fx)

    database = Database()
    database.create_all()
    try:
        result = run_factor_test(
            database,
            WeightedScorer(factors),
            formation=_require_date(formation),
            horizon_days=horizon,
            buckets=buckets,
        )
    except (BacktestError, DataError) as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc

    _render_factor_test(result, preset)


def _render_factor_test(result: FactorTestResult, preset: str) -> None:
    """Print the bucket table and the verdict."""
    table = Table(
        title=f"factor test: {preset} @ {result.formation}, {result.horizon_days} bars held"
    )
    table.add_column("bucket", style="cyan")
    table.add_column("n", justify="right")
    table.add_column("mean", justify="right")
    table.add_column("median", justify="right")
    table.add_column("hit rate", justify="right")
    table.add_column("vs universe", justify="right")
    for bucket in result.buckets:
        excess = bucket.mean_return - result.universe_return
        table.add_row(
            bucket.label,
            str(bucket.size),
            f"{bucket.mean_return:+.2%}",
            f"{bucket.median_return:+.2%}",
            f"{bucket.hit_rate:.0%}",
            f"[green]{excess:+.2%}[/]" if excess > 0 else f"[red]{excess:+.2%}[/]",
        )
    console.print(table)

    console.print(
        f"universe (equal weight, n={result.scored}): [bold]{result.universe_return:+.2%}[/]"
    )
    excess = result.excess_return
    if excess is None:
        return
    verdict = "beat" if excess > 0 else "did not beat"
    console.print(f"Top bucket {verdict} the universe by [bold]{excess:+.2%}[/].")

    t_stat = result.spread_t_stat
    if t_stat is None:
        console.print(
            "[yellow]Too few names to tell signal from noise[/] - "
            "an excess return here means nothing yet."
        )
    elif result.is_significant:
        console.print(f"Top-bottom spread t = [green]{t_stat:+.2f}[/] (clears 2σ).")
    else:
        console.print(
            f"[yellow]Top-bottom spread t = {t_stat:+.2f}, inside 2σ[/] - "
            "not distinguishable from chance. On a small universe an edge this "
            "size arises routinely at random."
        )
    if not result.is_monotonic:
        console.print(
            "[yellow]Returns are not monotonic across buckets[/] - the ordering "
            "carries little information, so treat any edge as noise."
        )
    if result.skipped:
        console.print(
            f"[dim]{len(result.skipped)} name(s) skipped: "
            f"{result.no_forward_price} without a forward price, "
            f"{result.no_statements_stored} with no statements stored at all, "
            f"{result.no_visible_statements} whose statements all postdate "
            f"{result.formation}, {result.no_score} unscoreable from what was visible.[/]"
        )
        if result.coverage < 0.5:
            # Coverage this low changes what the numbers above mean, so it is
            # said in full rather than left to be inferred from a row of counts.
            console.print(
                f"[yellow]Only {result.coverage:.0%} of the universe was tested.[/] "
                "That sample is not random - it is the names with the longest "
                "disclosure history, which skews old and large."
            )
            # The dominant reason decides the advice, and the two lead opposite
            # ways: missing data is fetched, late data is waited out or dated
            # around. Saying "try a later date" to someone whose statements were
            # never downloaded sends them in circles.
            if result.no_statements_stored >= max(
                result.no_visible_statements, result.no_forward_price
            ):
                console.print(
                    f"  [yellow]{result.no_statements_stored} name(s) have no statements "
                    "at all.[/] No formation date can fix that - the data was never "
                    "fetched, or the fetch failed for them:\n"
                    "      uv run stock-ai bulk-fetch --what statements --segment stored\n"
                    "  It skips symbols already stored, so this only costs the missing ones."
                )
            elif result.no_visible_statements > result.no_forward_price:
                console.print(
                    "  Most were dropped because nothing had been filed by then, not "
                    "for want of price history. Fetching more prices will not help; a "
                    "later formation date will."
                )
            _print_formation_advice(result.horizon_days)


def _print_formation_advice(horizon_days: int) -> None:
    """Say which formation dates the stored data can actually support.

    Chosen by coverage, never by outcome. Hunting for the formation date that
    produces the best t-statistic is how a noise factor gets believed; this
    reports where the most names can be tested, which is a property of the data
    and not of the answer.
    """
    advice = suggest_formation(Database(), horizon_days)
    if advice.universe and advice.with_statements < advice.universe // 2:
        # The ceiling matters more than the best date: no formation date can
        # test a symbol whose statements were never stored, so a "best coverage"
        # figure below this is a fact about the download, not about the calendar.
        console.print(
            f"  [yellow]Only {advice.with_statements} of {advice.universe} stored "
            "symbols have any dated statement at all.[/] That caps coverage no "
            "matter which date is chosen."
        )
    if advice.best is None:
        console.print(
            "[yellow]No formation date works with the data stored.[/] "
            f"The first disclosure is {advice.first_disclosure or '?'}, but a full "
            f"{horizon_days}-bar horizon has to start by {advice.latest_feasible or '?'}. "
            "The disclosure history is shorter than the holding period.\n"
            "  Shorten the horizon instead: --horizon 120 tests a six-month hold."
        )
        return
    console.print(
        f"  Best coverage is at [cyan]{advice.best}[/] ({advice.coverage:.0%} of the "
        f"universe): [dim]stock-ai factor-test {advice.best} --horizon {horizon_days}[/]\n"
        "  [dim]That date is picked by how many names can be tested, not by the "
        "result. Choosing a formation date because it produced a better spread "
        "manufactures the edge it appears to find.[/]"
    )


def _require_date(value: str) -> dt.date:
    """Parse a required ISO date argument."""
    parsed = _parse_date(value)
    if parsed is None:
        raise typer.BadParameter("A formation date is required.")
    return parsed


@app.command()
def score(
    symbols: list[str] = typer.Argument(..., help="Ticker symbols to score."),
) -> None:
    """Score SYMBOLS (0-100) from stored fundamentals and price momentum."""
    settings = get_settings()
    configure_logging(settings.log_level)

    database = Database()
    database.create_all()
    scorer = WeightedScorer(default_weighted_factors())

    results = []
    with database.session() as session:
        fundamentals_repo = FundamentalsRepository(session)
        price_repo = PriceRepository(session)
        for symbol in symbols:
            context = ScreeningContext(
                symbol=symbol,
                fundamentals=fundamentals_repo.get_latest(symbol),
                prices=price_repo.get_prices(symbol),
            )
            results.append(scorer.score(context))

    results.sort(key=lambda r: r.score, reverse=True)
    table = Table(title="scores")
    table.add_column("Symbol", style="cyan")
    table.add_column("Score", justify="right")
    table.add_column("Factors")
    for result in results:
        factors = ", ".join(f"{k}={v:.2f}" for k, v in sorted(result.breakdown.items()))
        table.add_row(result.symbol, f"{result.score:.1f}", factors or "[dim]-[/]")
    console.print(table)


@app.command()
def rank(
    symbols: list[str] | None = typer.Argument(None, help="Symbols to rank; default is all."),
    base: str = typer.Option("USD", help="Currency the market cap column is stated in."),
    fx_rate: list[str] = typer.Option(
        [],
        "--fx",
        help="Pin a rate as CUR=VALUE (e.g. --fx JPY=0.0064). Repeatable; "
        "unpinned currencies are fetched live.",
    ),
    min_market_cap: float | None = typer.Option(None, help="Minimum market cap, in --base."),
    max_market_cap: float | None = typer.Option(None, help="Maximum market cap, in --base."),
    preset: str = typer.Option(
        "default", help="Factor set: default | tenbagger (small-cap growth)."
    ),
    top: int = typer.Option(20, help="Show only the top N rows."),
) -> None:
    """Rank JP and US securities together on one score.

    The composite score is built from unitless ratios, so it already compares
    across markets; only the market cap needs converting, which is what
    ``--fx``/``--base`` control.

    ``--preset tenbagger`` swaps in a small-cap growth factor set. It reads the
    statement series, so run ``statements`` first, and treat its output as a
    shortlist to research rather than a prediction - backtest it before
    trusting it.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    fx = FxConverter(base=base, rates=_parse_fx_rates(fx_rate))
    factors, needs_statements = _factor_preset(preset, fx)

    database = Database()
    database.create_all()
    try:
        frame = rank_securities(
            database,
            symbols=list(symbols) if symbols else None,
            scorer=WeightedScorer(factors),
            fx=fx,
            min_market_cap=min_market_cap,
            max_market_cap=max_market_cap,
            load_statements=needs_statements,
        )
    except DataError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc

    if frame.empty:
        console.print("[yellow]No securities matched; run 'fetch' and 'fundamentals' first.[/]")
        return
    _render_ranking(frame.head(top), base.upper())


def _factor_preset(name: str, fx: FxConverter) -> tuple[list[WeightedFactor], bool]:
    """Return the named factor set and whether it needs the statement series."""
    key = name.lower()
    if key == "default":
        return default_weighted_factors(), False
    if key == "tenbagger":
        return tenbagger_weighted_factors(fx=fx), True
    raise typer.BadParameter(f"Unknown preset {name!r}; use 'default' or 'tenbagger'.")


def _parse_fx_rates(pairs: list[str]) -> dict[str, float]:
    """Parse repeated ``CUR=VALUE`` options into a rate map."""
    rates: dict[str, float] = {}
    for pair in pairs:
        currency, _, value = pair.partition("=")
        if not currency or not value:
            raise typer.BadParameter(f"Invalid --fx {pair!r}; use CUR=VALUE, e.g. JPY=0.0064.")
        try:
            rates[currency.strip().upper()] = float(value)
        except ValueError as exc:
            raise typer.BadParameter(f"Invalid --fx rate in {pair!r}.") from exc
    return rates


_META_COLUMNS = ("symbol", "market", "score", "market_cap")

# Factor names are self-explanatory but too wide for a table that also carries
# the meta columns; Rich would truncate them to an unreadable "divide…".
_FACTOR_ABBREVIATIONS = {
    "dividend": "div",
    "momentum": "mom",
    "profit_margin": "margin",
    "value_per": "value",
    "news_sentiment": "news",
}


def _format_cap(value: object) -> str:
    """Render a market cap compactly (``2.00T``), never truncated mid-digits.

    Printing the raw grouped number lets Rich cut "198,000,000,000" and
    "198,000,000" to the same "198,00…" - a 1000x difference shown as identical.
    """
    if value is None or pd.isna(value):
        return "-"
    amount = float(value)
    for limit, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(amount) >= limit:
            return f"{amount / limit:.2f}{suffix}"
    return f"{amount:.0f}"


def _render_ranking(frame: pd.DataFrame, base: str) -> None:
    """Print a cross-market ranking, market cap stated in ``base``."""
    factor_columns = [c for c in frame.columns if c not in _META_COLUMNS]

    table = Table(title=f"cross-market ranking (market cap in {base})")
    table.add_column("symbol", style="cyan", no_wrap=True)
    table.add_column("mkt", no_wrap=True)
    table.add_column("score", justify="right", no_wrap=True)
    table.add_column(f"cap ({base})", justify="right", no_wrap=True)
    for column in factor_columns:
        table.add_column(_FACTOR_ABBREVIATIONS.get(column, column), justify="right")

    for row in frame.to_dict("records"):
        table.add_row(
            str(row["symbol"]),
            str(row["market"]),
            f"{row['score']:.1f}",
            _format_cap(row["market_cap"]),
            *("-" if pd.isna(row[c]) else f"{float(row[c]):.2f}" for c in factor_columns),
        )
    console.print(table)


@app.command()
def notify(
    message: str = typer.Argument(..., help="Message text to send."),
    channel: str = typer.Option("console", help="console | discord | telegram | line."),
) -> None:
    """Send MESSAGE to a notification CHANNEL (defaults to console)."""
    settings = get_settings()
    configure_logging(settings.log_level)
    try:
        get_notifier(channel, settings).send(message)
    except NotificationError as exc:
        console.print(f"[red]notification failed:[/] {exc}")
        raise typer.Exit(code=1) from exc


@app.command()
def watch(
    symbol: str | None = typer.Argument(None, help="Symbol to add; omit to list."),
    note: str | None = typer.Option(None, help="Why this name is watched."),
    importance: str = typer.Option("medium", help="Alert threshold: high | medium | low."),
    market: str = typer.Option("US", help="Listing market: US | JP."),
    remove: bool = typer.Option(False, "--remove", help="Drop SYMBOL from the watchlist."),
) -> None:
    """Manage the watchlist that ``monitor`` checks."""
    settings = get_settings()
    configure_logging(settings.log_level)

    database = Database()
    database.create_all()

    if symbol is None:
        with database.session() as session:
            entries = WatchlistRepository(session).list_entries()
        if not entries:
            console.print("[yellow]Watchlist is empty.[/]")
            return
        table = Table(title="watchlist")
        table.add_column("symbol", style="cyan")
        table.add_column("mkt")
        table.add_column("alerts at")
        table.add_column("note")
        for entry in entries:
            table.add_row(entry.symbol, entry.market, entry.min_importance.value, entry.note or "-")
        console.print(table)
        return

    with database.session() as session:
        repo = WatchlistRepository(session)
        if remove:
            dropped = repo.remove(symbol)
            console.print(
                f"Removed [cyan]{symbol}[/]."
                if dropped
                else f"[yellow]{symbol} was not watched.[/]"
            )
            return
        repo.add(
            symbol,
            note=note,
            min_importance=_parse_importance(importance),
            market=market.upper(),
        )
    console.print(f"Watching [cyan]{symbol}[/] (alerts at {importance.lower()} and above).")


def _parse_importance(value: str) -> Importance:
    """Parse an importance threshold from the CLI."""
    try:
        return Importance(value.strip().lower())
    except ValueError as exc:
        raise typer.BadParameter(
            f"importance must be high, medium, or low; got {value!r}."
        ) from exc


@app.command()
def monitor(
    provider: str = typer.Option("dummy", help="AI provider: dummy|claude|openai|gemini."),
    channel: str | None = typer.Option(None, help="Send alerts to console|discord|telegram|line."),
    limit: int = typer.Option(10, help="Disclosures pulled per watched symbol."),
    source: str = typer.Option(
        "all", help="Disclosure feed: all | edinet (JP filings) | news (yfinance)."
    ),
    lookback_days: int = typer.Option(7, help="Days of EDINET filings to scan."),
) -> None:
    """Check the watchlist for disclosures worth reporting.

    Each new item is rated and summarized by the AI provider, and anything at
    or above a name's threshold becomes an alert. Reported items are recorded,
    so running this daily does not re-deliver the same news.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    database = Database()
    database.create_all()
    notifier = get_notifier(channel, settings) if channel else None
    monitor_service = WatchMonitor(
        database,
        source=_disclosure_source(source, settings, lookback_days),
        provider=get_ai_provider(provider, settings),
        notifier=notifier,
    )

    result = monitor_service.run(limit=limit, notify=notifier is not None)
    console.print(
        f"Checked [bold]{result.checked}[/] new disclosure(s), "
        f"skipped {result.skipped} already seen."
    )
    if result.unjudged:
        console.print(
            f"[yellow]{result.unjudged} could not be classified[/] "
            "(AI provider failed); they stay unseen and are retried next run."
        )
    if result.alerts:
        console.print(result.format())
    else:
        console.print("[dim]Nothing above threshold.[/]")


def _disclosure_source(name: str, settings: Settings, lookback_days: int):
    """Build the disclosure feed for a source name.

    ``all`` combines EDINET with the news wire and de-duplicates, which is the
    useful default: EDINET carries the statutory JP filings the news feed
    misses, and the news feed carries US names EDINET has nothing for.
    """
    key = name.lower()
    edinet = EdinetDisclosureSource(api_key=settings.edinet_api_key, lookback_days=lookback_days)
    news = NewsDisclosureSource(YFinanceNewsSource())
    if key == "edinet":
        return edinet
    if key == "news":
        return news
    if key == "all":
        return CompositeDisclosureSource(edinet, news)
    raise typer.BadParameter(f"Unknown source {name!r}; use all, edinet, or news.")


@app.command()
def daily(
    at: str = typer.Option("18:00", help="Local HH:MM the jobs run at."),
    symbols: list[str] | None = typer.Argument(None, help="Symbols to refresh."),
    source: str = typer.Option("yfinance", help="Price source: yfinance | jquants."),
    provider: str = typer.Option("dummy", help="AI provider used by the monitor."),
    channel: str | None = typer.Option(None, help="Notification channel for alerts."),
    feed: str = typer.Option("all", help="Disclosure feed: all | edinet | news."),
    once: bool = typer.Option(False, "--once", help="Run the jobs now and exit."),
) -> None:
    """Run the daily pipeline: refresh prices, then check the watchlist.

    ``--once`` is the form to put in cron or Task Scheduler. Without it this
    blocks and fires every day at ``--at``, which is convenient for a desktop
    but has no catch-up if the machine was asleep.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    database = Database()
    database.create_all()
    scheduler = DailyScheduler(at=at)

    targets = list(symbols) if symbols else None
    if targets:
        price_provider, market = _price_source(source, settings)
        scheduler.add(
            "prices",
            lambda: _log_ingest(
                IngestionService(price_provider, database).ingest_many(targets, market=market)
            ),
        )

    notifier = get_notifier(channel, settings) if channel else None
    scheduler.add(
        "monitor",
        lambda: WatchMonitor(
            database,
            source=_disclosure_source(feed, settings, 7),
            provider=get_ai_provider(provider, settings),
            notifier=notifier,
        ).run(notify=notifier is not None),
    )

    if once:
        results = scheduler.run_once()
        table = Table(title="daily run")
        table.add_column("job", style="cyan")
        table.add_column("status")
        for outcome in results:
            table.add_row(
                outcome.name,
                "[green]ok[/]" if outcome.ok else f"[red]error[/] {outcome.error or ''}",
            )
        console.print(table)
        if any(not r.ok for r in results):
            raise typer.Exit(code=1)
        return

    console.print(f"Running daily at [cyan]{at}[/]. Ctrl-C to stop.")
    scheduler.run_forever()


def _log_ingest(results: list[IngestResult]) -> None:
    """Raise if every symbol failed, so the scheduler records a failed job."""
    if results and all(not r.ok for r in results):
        raise RuntimeError(results[0].error or "all symbols failed")


@app.command()
def ask(
    question: str = typer.Argument(..., help='e.g. "PER15以下でROE20%以上の半導体株"'),
    provider: str = typer.Option("dummy", help="AI provider: dummy|claude|openai|gemini."),
    explain_only: bool = typer.Option(
        False, "--explain-only", help="Show the interpretation without running it."
    ),
) -> None:
    """Screen stored securities from a plain-language QUESTION.

    The model only fills in a fixed set of screening criteria - it never writes
    a query and never sees the database - so an unsupported or hallucinated
    field is refused rather than executed. The interpretation is printed before
    the results so it can be checked.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    if provider.lower() == "dummy":
        # The dummy provider echoes its prompt; it cannot emit the JSON this
        # command parses. Saying so beats "no JSON object" on a test run.
        console.print(
            "[yellow]The 'dummy' provider only echoes text and cannot answer this.[/] "
            "Use --provider claude, openai, or gemini."
        )
        raise typer.Exit(code=1)

    ai = get_ai_provider(provider, settings)
    try:
        query = parse_query(ai, question)
    except AIError as exc:
        console.print(f"[red]could not interpret the question:[/] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(f"Understood as: [cyan]{query.describe()}[/]")
    if explain_only:
        return
    if query.is_empty:
        console.print("[yellow]No criteria were recognised; nothing to screen.[/]")
        raise typer.Exit(code=1)

    database = Database()
    database.create_all()
    matches = run_query(database, query)

    console.print(f"Matched [bold]{len(matches)}[/] symbols.")
    if matches:
        _render_report(build_report(collect_fundamentals(database, matches)))
    elif query.needs_statements:
        console.print(
            "[dim]This question needs the statement series; run 'statements' "
            "for the symbols you want covered.[/]"
        )


@app.command()
def summarize(
    text: str = typer.Argument(..., help="Text (IR excerpt, news, ...) to summarize."),
    provider: str = typer.Option("dummy", help="AI provider: dummy|claude|openai|gemini."),
    max_words: int = typer.Option(120, help="Maximum words in the summary."),
) -> None:
    """Summarize TEXT with the selected AI provider."""
    settings = get_settings()
    configure_logging(settings.log_level)
    ai = get_ai_provider(provider, settings)
    console.print(ai_summarize(ai, text, max_words=max_words))


@app.command()
def sentiment(
    text: str = typer.Argument(..., help="Text to classify."),
    provider: str = typer.Option("dummy", help="AI provider: dummy|claude|openai|gemini."),
) -> None:
    """Classify the sentiment of TEXT (positive / neutral / negative)."""
    settings = get_settings()
    configure_logging(settings.log_level)
    ai = get_ai_provider(provider, settings)
    console.print(analyze_sentiment(ai, text))


def _load_prices(database: Database, symbol: str) -> pd.DataFrame:
    """Load stored prices for ``symbol`` or fail with a helpful message."""
    with database.session() as session:
        prices = PriceRepository(session).get_prices(symbol)
    if prices.empty:
        raise typer.BadParameter(f"No price data for {symbol!r}; run 'fetch' first.")
    return prices


def _build_strategy(name: str, fast: int, slow: int) -> Strategy:
    """Construct a strategy from its short name (see ``build_strategy``)."""
    try:
        return build_strategy(name, fast=fast, slow=slow)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _render_metrics_table(frame: pd.DataFrame) -> None:
    """Print a strategy-vs-benchmark metrics comparison."""
    table = Table(title="backtest comparison")
    table.add_column("strategy", style="cyan")
    for column in ("total_return", "cagr", "sharpe", "max_dd", "pf", "win", "trades"):
        table.add_column(column, justify="right")
    for row in frame.itertuples(index=False):
        table.add_row(
            row.strategy,
            f"{row.total_return:.2%}",
            f"{row.cagr:.2%}",
            f"{row.sharpe:.2f}",
            f"{row.max_drawdown:.2%}",
            f"{row.profit_factor:.2f}",
            f"{row.win_rate:.2%}",
            str(int(row.num_trades)),
        )
    console.print(table)


def _build_condition(
    min_roe: float | None,
    max_per: float | None,
    max_pbr: float | None,
    min_dividend_yield: float | None,
    min_market_cap: float | None,
    max_market_cap: float | None = None,
    min_revenue_growth: float | None = None,
    min_profit_growth: float | None = None,
    min_dividend_growth: float | None = None,
    growth_years: int = 1,
    min_dividend_streak: int | None = None,
    max_payout_ratio: float | None = None,
) -> Condition:
    """Assemble a combined condition from the provided flags."""
    conditions: list[Condition] = []
    if min_roe is not None:
        conditions.append(MinROE(min_roe))
    if max_per is not None:
        conditions.append(MaxPER(max_per))
    if max_pbr is not None:
        conditions.append(MaxPBR(max_pbr))
    if min_dividend_yield is not None:
        conditions.append(MinDividendYield(min_dividend_yield))
    if min_market_cap is not None:
        conditions.append(MinMarketCap(min_market_cap))
    if max_market_cap is not None:
        conditions.append(MaxMarketCap(max_market_cap))
    if min_revenue_growth is not None:
        conditions.append(MinRevenueGrowth(min_revenue_growth, growth_years))
    if min_profit_growth is not None:
        conditions.append(MinProfitGrowth(min_profit_growth, growth_years))
    if min_dividend_growth is not None:
        conditions.append(MinDividendGrowth(min_dividend_growth, growth_years))
    if min_dividend_streak is not None:
        conditions.append(MinConsecutiveDividendIncreases(min_dividend_streak))
    if max_payout_ratio is not None:
        conditions.append(MaxPayoutRatio(max_payout_ratio))

    if not conditions:
        raise typer.BadParameter("Provide at least one screening criterion.")
    return conditions[0] if len(conditions) == 1 else All(*conditions)


def _render_report(report: pd.DataFrame) -> None:
    """Print a screening report as a Rich table."""
    table = Table(title="screen results")
    for column in report.columns:
        table.add_column(column, overflow="fold")
    for row in report.itertuples(index=False):
        table.add_row(*(("" if v is None else str(v)) for v in row))
    console.print(table)


def _parse_date(value: str | None) -> dt.date | None:
    """Parse an ISO date string, or return ``None`` when unset."""
    if value is None:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter(f"Invalid date {value!r}; use YYYY-MM-DD.") from exc


def _render_results(results: list[IngestResult]) -> None:
    """Print an ingestion summary table."""
    table = Table(title="fetch results")
    table.add_column("Symbol", style="cyan")
    table.add_column("Rows", justify="right")
    table.add_column("Status")
    for result in results:
        status = "[green]ok[/]" if result.ok else f"[red]error[/] {result.error or ''}"
        table.add_row(result.symbol, str(result.rows), status)
    console.print(table)


def _secret_status(settings: Settings) -> list[tuple[str, bool]]:
    """Return ``(label, is_set)`` pairs for each secret without exposing values."""
    return [
        ("jquants_api_key", settings.jquants_api_key is not None),
        ("edinet_api_key", settings.edinet_api_key is not None),
        ("anthropic_api_key", settings.anthropic_api_key is not None),
        ("openai_api_key", settings.openai_api_key is not None),
        ("gemini_api_key", settings.gemini_api_key is not None),
        ("discord_webhook_url", settings.discord_webhook_url is not None),
        ("line_channel_access_token", settings.line_channel_access_token is not None),
        ("telegram_bot_token", settings.telegram_bot_token is not None),
    ]


def _make_output_encoding_safe() -> None:
    """Stop an unencodable character from killing the process.

    A Japanese Windows console runs on cp932, which has no mapping for an em
    dash, a yen sign, or any emoji. Printing one raises ``UnicodeEncodeError``
    from inside Rich, and because that happens while rendering, the command dies
    *before* doing its work - a bulk fetch that was about to load 1,600 symbols
    instead exits on a dash in its own progress message.

    The literals that caused this are gone, but the class of bug is not: any
    company name, error string, or API message could contain one. Degrading the
    character is always better than losing the run.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # pytest's capture object, a plain pipe, ...
            continue
        with contextlib.suppress(ValueError, OSError):  # detached or already closed
            reconfigure(errors="backslashreplace")


def main() -> None:
    """Entry point for the ``stock-ai`` console script."""
    _make_output_encoding_safe()
    app()


if __name__ == "__main__":
    main()
