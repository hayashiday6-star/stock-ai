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
from stock_ai.backtest.strategy import BuyAndHold, Strategy, build_strategy
from stock_ai.config.settings import Settings, get_settings
from stock_ai.core.exceptions import NotificationError
from stock_ai.core.logging import configure_logging
from stock_ai.data.base import PriceProvider
from stock_ai.data.fx import FxConverter
from stock_ai.data.jquants_fundamentals import JQuantsFundamentalsProvider
from stock_ai.data.jquants_provider import JQuantsPriceProvider
from stock_ai.data.service import FundamentalsService, IngestionService, IngestResult
from stock_ai.data.yfinance_provider import (
    YFinanceFundamentalsProvider,
    YFinancePriceProvider,
)
from stock_ai.database.engine import Database
from stock_ai.database.repository import (
    FinancialStatementRepository,
    FundamentalsRepository,
    PriceRepository,
)
from stock_ai.notification.factory import get_notifier
from stock_ai.portfolio.ranking import rank_securities
from stock_ai.portfolio.scoring import WeightedScorer, default_weighted_factors
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
        table.add_row(label, "[green]set[/]" if is_set else "[dim]—[/]")
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
        table.add_row(result.symbol, f"{result.score:.1f}", factors or "[dim]—[/]")
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
    top: int = typer.Option(20, help="Show only the top N rows."),
) -> None:
    """Rank JP and US securities together on one score.

    The composite score is built from unitless ratios, so it already compares
    across markets; only the market cap needs converting, which is what
    ``--fx``/``--base`` control.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    database = Database()
    database.create_all()
    frame = rank_securities(
        database,
        symbols=list(symbols) if symbols else None,
        fx=FxConverter(base=base, rates=_parse_fx_rates(fx_rate)),
        min_market_cap=min_market_cap,
        max_market_cap=max_market_cap,
    )

    if frame.empty:
        console.print("[yellow]No securities matched; run 'fetch' and 'fundamentals' first.[/]")
        return
    _render_ranking(frame.head(top), base.upper())


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
    "198,000,000" to the same "198,00…" — a 1000x difference shown as identical.
    """
    if value is None or pd.isna(value):
        return "—"
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
            *("—" if pd.isna(row[c]) else f"{float(row[c]):.2f}" for c in factor_columns),
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
