"""Command-line interface for stock-ai.

Exposes the ``stock-ai`` console script. Subcommands for each pipeline stage
are added as the phases progress.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import typer
from rich.console import Console
from rich.table import Table

from stock_ai import __version__
from stock_ai.ai.analysis import analyze_sentiment
from stock_ai.ai.analysis import summarize as ai_summarize
from stock_ai.ai.factory import get_ai_provider
from stock_ai.backtest.engine import BacktestEngine
from stock_ai.backtest.report import metrics_frame
from stock_ai.backtest.strategy import BuyAndHold, SMACrossover, Strategy
from stock_ai.config.settings import Settings, get_settings
from stock_ai.core.logging import configure_logging
from stock_ai.data.service import FundamentalsService, IngestionService, IngestResult
from stock_ai.data.yfinance_provider import (
    YFinanceFundamentalsProvider,
    YFinancePriceProvider,
)
from stock_ai.database.engine import Database
from stock_ai.database.repository import PriceRepository
from stock_ai.screening.base import All, Condition
from stock_ai.screening.conditions import (
    MaxPBR,
    MaxPER,
    MinDividendYield,
    MinMarketCap,
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
        table.add_row(label, "[green]set[/]" if is_set else "[dim]—[/]")
    console.print(table)


@app.command()
def fetch(
    symbols: list[str] = typer.Argument(..., help="Ticker symbols, e.g. AAPL MSFT"),
    start: str | None = typer.Option(None, help="ISO start date YYYY-MM-DD."),
    end: str | None = typer.Option(None, help="ISO end date; defaults to today."),
    lookback: int = typer.Option(365, help="Backfill days when a symbol has no data."),
) -> None:
    """Fetch daily prices for SYMBOLS and store them in the local database."""
    settings = get_settings()
    configure_logging(settings.log_level)

    database = Database()
    database.create_all()
    service = IngestionService(YFinancePriceProvider(), database, default_lookback_days=lookback)

    results = service.ingest_many(symbols, _parse_date(start), _parse_date(end))
    _render_results(results)

    if any(not r.ok for r in results):
        raise typer.Exit(code=1)


@app.command()
def fundamentals(
    symbols: list[str] = typer.Argument(..., help="Ticker symbols, e.g. AAPL MSFT"),
) -> None:
    """Fetch a fundamentals snapshot for SYMBOLS and store it in the database."""
    settings = get_settings()
    configure_logging(settings.log_level)

    database = Database()
    database.create_all()
    service = FundamentalsService(YFinanceFundamentalsProvider(), database)

    results = service.ingest_many(symbols)
    _render_results(results)

    if any(not r.ok for r in results):
        raise typer.Exit(code=1)


@app.command()
def screen(
    min_roe: float | None = typer.Option(None, help="Minimum return on equity."),
    max_per: float | None = typer.Option(None, help="Maximum price/earnings."),
    max_pbr: float | None = typer.Option(None, help="Maximum price/book."),
    min_dividend_yield: float | None = typer.Option(None, help="Minimum dividend yield."),
    min_market_cap: float | None = typer.Option(None, help="Minimum market cap."),
    out: Path | None = typer.Option(None, help="Output file; prints a table if omitted."),
    fmt: str = typer.Option("csv", "--format", help="csv | json | xlsx."),
) -> None:
    """Screen stored securities by fundamentals and report the matches."""
    settings = get_settings()
    configure_logging(settings.log_level)

    condition = _build_condition(min_roe, max_per, max_pbr, min_dividend_yield, min_market_cap)
    if out is not None and fmt.lower() not in SUPPORTED_FORMATS:
        raise typer.BadParameter(f"format must be one of {SUPPORTED_FORMATS}.")

    database = Database()
    database.create_all()
    passing = ScreeningEngine(database).screen(condition)
    report = build_report(collect_fundamentals(database, passing))

    console.print(f"Matched [bold]{len(passing)}[/] symbols for [cyan]{condition}[/]")
    if out is not None:
        write_report(report, out, fmt)
        console.print(f"Wrote {len(report)} rows to [green]{out}[/] ({fmt}).")
    else:
        _render_report(report)


@app.command()
def backtest(
    symbol: str = typer.Argument(..., help="Ticker to backtest (must be fetched)."),
    strategy: str = typer.Option("sma", help="Strategy: sma | hold."),
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
    """Construct a strategy from its short name."""
    if name == "hold":
        return BuyAndHold()
    if name == "sma":
        return SMACrossover(fast=fast, slow=slow)
    raise typer.BadParameter(f"Unknown strategy {name!r}; use 'sma' or 'hold'.")


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
        ("anthropic_api_key", settings.anthropic_api_key is not None),
        ("openai_api_key", settings.openai_api_key is not None),
        ("gemini_api_key", settings.gemini_api_key is not None),
        ("discord_webhook_url", settings.discord_webhook_url is not None),
        ("line_channel_access_token", settings.line_channel_access_token is not None),
        ("telegram_bot_token", settings.telegram_bot_token is not None),
    ]


def main() -> None:
    """Entry point for the ``stock-ai`` console script."""
    app()


if __name__ == "__main__":
    main()
