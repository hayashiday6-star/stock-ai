"""Command-line interface for stock-ai.

Exposes the ``stock-ai`` console script. Subcommands for each pipeline stage
are added as the phases progress.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from statistics import median

import pandas as pd
import typer
from pydantic import SecretStr
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

from stock_ai.accumulation.notify import build_message as build_accumulation_message
from stock_ai.accumulation.notify import should_notify as should_notify_accumulation
from stock_ai.accumulation.pipeline import Run as AccumulationRun
from stock_ai.accumulation.pipeline import download_prices
from stock_ai.accumulation.pipeline import run as run_accumulation
from stock_ai.accumulation.report import print_report as print_accumulation_report
from stock_ai.accumulation.screen import Thresholds
from stock_ai.accumulation.universe import Listing
from stock_ai.ai.analysis import (
    analyze_sentiment,
)
from stock_ai.ai.analysis import summarize as ai_summarize
from stock_ai.ai.anthropic_provider import DEFAULT_MODEL as ANTHROPIC_DEFAULT_MODEL
from stock_ai.ai.anthropic_provider import AnthropicProvider
from stock_ai.ai.estimate import estimate_disclosure_run
from stock_ai.ai.factory import get_ai_provider
from stock_ai.ai.pricing import RunEstimate, UsageLedger
from stock_ai.ai.query import parse_query, run_query
from stock_ai.backtest.accumulation_signal import (
    DEFAULT_MIN_TURNOVER,
    count_signals,
    explain_date,
    market_volume_context,
)
from stock_ai.backtest.engine import BacktestEngine
from stock_ai.backtest.factor_test import (
    FactorTestResult,
    formation_grid,
    run_factor_test,
    suggest_formation,
    walk_forward,
)
from stock_ai.backtest.forecast_revision import (
    DEFAULT_MIN_CHANGE,
    census_revisions,
    census_sue,
)
from stock_ai.backtest.pead import (
    MIN_TURNOVER,
    OOS_FROM,
    SORT_REACTION,
    SORT_SUE,
    Period,
    build_events,
    crowding_split,
    explain_events,
    quantile_ladder,
    spread,
)
from stock_ai.backtest.pead_census import DRIFT_WINDOW, ENTRY_OFFSET, run_census
from stock_ai.backtest.power import DEFAULT_LAGS, TARGET_T, estimate_power
from stock_ai.backtest.report import metrics_frame
from stock_ai.backtest.reversal import (
    BENCHMARK,
    COST_ROUND_TRIP,
    JUDGMENT_FROM,
    build_series,
    survivorship_gap,
)
from stock_ai.backtest.reversal_census import HOLDING_DAYS as REVERSAL_HOLDING
from stock_ai.backtest.reversal_census import LOOKBACK_DAYS as REVERSAL_LOOKBACK
from stock_ai.backtest.reversal_census import run_census as run_reversal_census
from stock_ai.backtest.seasonality import (
    DEFAULT_MIN_YEARS,
    holdout_check,
    month_name,
    monthly_returns,
    scan_seasonality,
    symbol_patterns,
)
from stock_ai.backtest.strategy import BuyAndHold, Strategy, build_strategy
from stock_ai.broker.moomoo import Diagnosis as MoomooDiagnosis
from stock_ai.broker.moomoo import MoomooConfig, StageStatus, to_moomoo_code
from stock_ai.broker.moomoo import capital_flow as moomoo_capital_flow
from stock_ai.broker.moomoo import diagnose as moomoo_diagnose
from stock_ai.config.settings import Settings, get_settings
from stock_ai.core.encoding import install as install_console_encoding
from stock_ai.core.exceptions import (
    AIError,
    BacktestError,
    BrokerError,
    DataError,
    NotificationError,
    OpsError,
)
from stock_ai.core.logging import configure_logging
from stock_ai.core.scheduler import DailyScheduler, JobResult
from stock_ai.core.version import describe as describe_version
from stock_ai.data.base import PriceProvider
from stock_ai.data.bulk import BulkIngester, Dataset, store_universe
from stock_ai.data.bulk import latest_close as bulk_latest_close
from stock_ai.data.delisted import (
    DEFAULT_SNAPSHOT_DIR,
    DEFAULT_STEP_DAYS,
    ROLLING_WINDOW_START,
    delistings,
    harvest_snapshots,
    membership,
    snapshot_dates,
)
from stock_ai.data.fx import FxConverter
from stock_ai.data.jquants_exit import CANCELLATION, audit
from stock_ai.data.jquants_fundamentals import JQuantsFundamentalsProvider
from stock_ai.data.jquants_profile import JQuantsProfileProvider
from stock_ai.data.jquants_provider import JQuantsPriceProvider
from stock_ai.data.markets import split_by_market, to_yahoo_symbol
from stock_ai.data.service import FundamentalsService, IngestionService, IngestResult
from stock_ai.data.tachibana import TachibanaPriceProvider
from stock_ai.data.tachibana import build_client as build_tachibana_client
from stock_ai.data.tachibana import default_version as tachibana_default_version
from stock_ai.data.tachibana import version_warning as tachibana_version_warning
from stock_ai.data.tachibana_universe import TachibanaUniverse
from stock_ai.data.types import FinancialReport, Importance, SecurityProfile
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
    get_profile,
    list_securities,
    price_history_spans,
    upsert_profile,
)
from stock_ai.ir.edinet import (
    CURRENT_PLACEMENT,
    EdinetDisclosureSource,
    ProbeResult,
    doc_type_label,
    normalize_sec_code,
    probe_key_placements,
    sample_filing_fields,
)
from stock_ai.ir.edinet import (
    EXTRA_BODY_FIELDS as EDINET_EXTRA_BODY_FIELDS,
)
from stock_ai.ir.edinet import (
    SUBJECT_CODE_FIELDS as EDINET_SUBJECT_CODE_FIELDS,
)
from stock_ai.ir.edinet_financials import EdinetFundamentalsProvider, fetch_annual_reports
from stock_ai.ir.monitor import WatchMonitor
from stock_ai.ir.sources import CompositeDisclosureSource, NewsDisclosureSource
from stock_ai.news.sources import YFinanceNewsSource
from stock_ai.notification.base import Notifier
from stock_ai.notification.factory import get_notifier
from stock_ai.ops.bridge import get_bridge
from stock_ai.portfolio.analysis import PortfolioAnalysis, analyze_portfolio
from stock_ai.portfolio.growth_factors import tenbagger_weighted_factors
from stock_ai.portfolio.ranking import DEFAULT_MIN_COVERAGE, rank_securities
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
    company_names,
    write_report,
)

app = typer.Typer(
    name="stock-ai",
    help="AI-driven stock screening, backtesting, and trading system.",
    no_args_is_help=True,
    add_completion=False,
)
# Before the Console is built: Rich reads the stream's error handler at write
# time, and a cp932 console would otherwise escape a yen sign into "\xa5".
install_console_encoding()
console = Console()


@app.callback()
def _root() -> None:
    """Group root: forces multi-command mode so subcommands keep their names."""


@app.command()
def version() -> None:
    """Print the version, and the commit this working copy is actually on."""
    # 静的な __version__ だけでは、pull を忘れた作業コピーと最新の作業コピーが
    # 同じ文字列を返す。「古いコードで測っていないか」に答えられる必要がある。
    console.print(f"stock-ai [bold cyan]v{describe_version()}[/]")


@app.command()
def info() -> None:
    """Show the active configuration (secrets are masked, never printed)."""
    settings = get_settings()
    configure_logging(settings.log_level)

    table = Table(title="stock-ai configuration")
    table.add_column("Key", style="cyan", no_wrap=True)
    table.add_column("Value", style="white")
    # コミットまで出す。貼られた出力を見るだけで、どのコードが動いたかが
    # 確定する - 出力の形から推測しなくてよくなる。
    table.add_row("version", describe_version())
    table.add_row("env", settings.env)
    table.add_row("log_level", settings.log_level)
    # Which model the AI commands will call, and whether that was a choice.
    # It belongs next to the key because the two together decide the bill, and
    # a model picked up from .env is otherwise invisible until the invoice.
    selected = settings.anthropic_model or ANTHROPIC_DEFAULT_MODEL
    origin = "from ANTHROPIC_MODEL" if settings.anthropic_model else "built-in default"
    table.add_row("anthropic_model", f"{selected} ({origin})")
    # A key without the SDK is a configuration that looks complete and cannot
    # make a single call. Both halves are needed, so both are shown, and the
    # one that goes missing on its own is the one that is easy to overlook.
    table.add_row("anthropic sdk", _import_status("anthropic"))
    # 日本株のデータが全部どこから来るかを決める3つ。ここに出ていないと、切り替えた
    # つもりで切り替わっていないことに、数字が変わらないという形でしか気付けない。
    # 銘柄一覧は価格・財務とは別の設定である（docs/JQUANTS_EXIT.md）。
    for label, chosen, allowed in (
        ("jp_price_source", settings.jp_price_source, JP_SOURCES),
        ("jp_statement_source", settings.jp_statement_source, STATEMENT_SOURCES),
        ("jp_universe_source", settings.jp_universe_source, UNIVERSE_SOURCES),
    ):
        note = "" if chosen.strip().lower() in allowed else "  [red](未対応の値)[/]"
        table.add_row(label, f"{chosen or '(未設定)'}{note}")
    if settings.jp_price_source.strip().lower() == "tachibana":
        version = settings.tachibana_api_version or tachibana_default_version()
        warning = tachibana_version_warning(version)
        table.add_row("tachibana version", f"{version}{'  ' + warning if warning else ''}")
    for label, value in _secret_status(settings):
        table.add_row(label, _secret_summary(value))
    console.print(table)
    console.print(
        "[dim]The fingerprint is a hash prefix, not the key. It answers one "
        "question the word 'set' cannot: whether the value in .env actually "
        "changed after you re-issued a key.[/]"
    )


@app.command()
def fetch(
    symbols: list[str] | None = typer.Argument(None, help="Ticker symbols, e.g. AAPL MSFT"),
    start: str | None = typer.Option(None, help="ISO start date YYYY-MM-DD."),
    end: str | None = typer.Option(None, help="ISO end date; defaults to today."),
    lookback: int = typer.Option(365, help="Backfill days when a symbol has no data."),
    source: str = typer.Option(
        "yfinance", help="Data source: yfinance (US) | jquants, tachibana (JP)."
    ),
    symbols_file: Path | None = typer.Option(
        None, "--symbols-file", help="Text file of symbols, one per line (# comments allowed)."
    ),
) -> None:
    """Fetch daily prices for SYMBOLS and store them in the local database.

    ``--symbols-file`` is how a US universe gets loaded: ``bulk-fetch`` is
    J-Quants throughout, and yfinance has no listing endpoint to enumerate a
    market from. Re-running is cheap - a symbol that is already current fetches
    nothing - so the file can grow over time.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    targets = _resolve_symbols(symbols, symbols_file)
    database = Database()
    database.create_all()

    # Routed by the ticker, not by the flag. One --source cannot serve a mixed
    # list, and sending a Japanese code to yfinance does not fail: a bare four
    # digits is a Tadawul listing there, so 3003 comes back as City Cement and
    # is stored under ヒューリック's name. Nothing about that looks wrong later.
    results = []
    for market_code, group in split_by_market(targets).items():
        resolved = _source_for_market(market_code, source, settings)
        if resolved != source.lower():
            console.print(
                f"[yellow]{', '.join(group)} are {market_code} listings; "
                f"fetching them from {resolved} rather than {source}.[/]"
            )
        provider, market = _price_source(resolved, settings)
        service = IngestionService(provider, database, default_lookback_days=lookback)
        results.extend(
            service.ingest_many(group, _parse_date(start), _parse_date(end), market=market)
        )
    _render_results(results)

    if any(not r.ok for r in results):
        raise typer.Exit(code=1)


def _symbols_from_file(path: Path) -> list[str]:
    """Read a symbol list: one per line, ``#`` comments and blanks ignored.

    There is no listing endpoint for US equities the way J-Quants provides one
    for the TSE, so a US universe has to come from somewhere. A file is that
    somewhere, and it is deliberately not a scraped index membership list: this
    project does not ship data it cannot verify, and a stale or wrong S&P 500
    would look exactly like a correct one.

    Commas are accepted as separators too, so a list pasted from a spreadsheet
    works without reformatting.
    """
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise typer.BadParameter(f"Could not read {path}: {exc}") from exc

    raw = _decode_text_file(path, data)

    symbols: list[str] = []
    seen: set[str] = set()
    for line in raw.splitlines():
        text = line.split("#", 1)[0]
        for part in text.replace(",", " ").replace("\t", " ").split():
            # A BOM survives decoding as a zero-width character, and Python does
            # not count it as whitespace - left in, it becomes a "symbol".
            ticker = part.strip().strip("\ufeff").upper()
            # Duplicates are silent rather than an error: a hand-maintained
            # list accumulates them, and re-fetching one is only wasted time.
            if ticker and ticker not in seen:
                seen.add(ticker)
                symbols.append(ticker)
    if not symbols:
        # "contained no symbols" is a conclusion, and on its own it leaves the
        # reader with nothing to check. What the file actually holds is the
        # thing that decides what to do next.
        lines = raw.splitlines()
        first = next((line for line in lines if line.strip()), "")
        raise typer.BadParameter(
            f"{path} contained no symbols. Read {len(data)} bytes, "
            f"{len(lines)} line(s); the first non-empty line is "
            f"{first[:60]!r}. Every line was blank, or began with '#', or the "
            "file is not the one you meant - a Notepad save can land as "
            f"'{path.name}.txt'."
        )
    return symbols


def _decode_text_file(path: Path, data: bytes) -> str:
    """Decode a hand-made text file without insisting it be UTF-8.

    The expected way to produce one of these is Notepad on Japanese Windows,
    which writes UTF-16 for "Unicode", UTF-8 with a BOM, and cp932 for "ANSI" -
    and only the middle one survives a plain ``read_text``. Failing on the
    other two would reject a file whose contents are perfectly good, so the
    encodings that reach this project in practice are all tried.
    """
    for encoding in ("utf-8-sig", "utf-16", "utf-8", "cp932"):
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    raise typer.BadParameter(
        f"Could not read {path} as text. Save it as UTF-8 or ANSI from Notepad "
        "(File -> Save As -> Encoding), or check it is not a spreadsheet."
    )


def _resolve_symbols(symbols: list[str] | None, symbols_file: Path | None) -> list[str]:
    """Combine symbols given on the command line with those in a file."""
    combined = list(symbols or [])
    if symbols_file is not None:
        combined.extend(_symbols_from_file(symbols_file))
    if not combined:
        raise typer.BadParameter("Name at least one symbol, or pass --symbols-file.")
    seen: set[str] = set()
    return [sym for sym in combined if not (sym in seen or seen.add(sym))]


#: Sources that can price a Japanese listing. yfinance can too, via ``.T``, but
#: it is not offered here: it has no listing endpoint to enumerate the market
#: from, so it cannot serve ``bulk-fetch``.
JP_SOURCES = ("jquants", "tachibana")

#: 銘柄一覧（市場区分・業種）の取得元。価格・財務とは別の設定である。
UNIVERSE_SOURCES = ("jquants", "tachibana")


def _universe_source(
    settings: Settings, override: str | None = None
) -> JQuantsUniverse | TachibanaUniverse:
    """Build the listed-universe source named by ``JP_UNIVERSE_SOURCE``.

    ``JP_PRICE_SOURCE`` deliberately does not reach here. Moving prices to
    Tachibana left the universe still calling J-Quants, and nothing said so -
    that is the gap that would have surfaced on the day the plan was cancelled
    (``docs/JQUANTS_EXIT.md``). Splitting the setting only helps if it is
    visible, so ``info`` prints it too.
    """
    chosen = (override or settings.jp_universe_source or "jquants").strip().lower()
    if chosen not in UNIVERSE_SOURCES:
        raise typer.BadParameter(
            f"Unknown universe source {chosen!r}; use one of {', '.join(UNIVERSE_SOURCES)}."
        )
    if chosen == "tachibana":
        client = build_tachibana_client(
            settings.tachibana_auth_id,
            settings.tachibana_private_key,
            version=settings.tachibana_api_version,
            base=settings.tachibana_base_url,
            session_file=settings.tachibana_session_file,
        )
        return TachibanaUniverse(client.issue_masters)
    return JQuantsUniverse(api_key=settings.jquants_api_key)


def _price_source(source: str, settings: Settings) -> tuple[PriceProvider, str]:
    """Return the price provider and market code for a data source name."""
    key = source.lower()
    if key == "yfinance":
        return YFinancePriceProvider(), "US"
    if key == "jquants":
        return JQuantsPriceProvider(api_key=settings.jquants_api_key), "JP"
    if key == "tachibana":
        return (
            TachibanaPriceProvider(
                settings.tachibana_auth_id,
                settings.tachibana_private_key,
                version=settings.tachibana_api_version,
                base=settings.tachibana_base_url,
                session_file=settings.tachibana_session_file,
            ),
            "JP",
        )
    raise typer.BadParameter(
        f"Unknown source {source!r}; use 'yfinance', 'jquants' or 'tachibana'."
    )


def _source_for_market(market_code: str, requested: str, settings: Settings) -> str:
    """Which source actually serves ``market_code``.

    A ticker decides its market, and the market decides the source - but for
    Japan there is now a choice between J-Quants and Tachibana. An explicit
    ``--source`` naming one of them wins; otherwise ``JP_PRICE_SOURCE`` does.
    That way switching the whole system over is one line in ``.env``, and a
    single run can still be pointed elsewhere without changing the setting.
    """
    if market_code != "JP":
        return "yfinance"
    if requested.lower() in JP_SOURCES:
        return requested.lower()
    return settings.jp_price_source.lower()


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


#: 日本株の財務諸表を取れる先。
STATEMENT_SOURCES = ("jquants", "edinet")


def _statement_fetcher(
    source: str, settings: Settings, lookback_days: int
) -> tuple[Callable[[str], list[FinancialReport]], str]:
    """Return a per-symbol statement fetcher and the name of what it uses.

    ``edinet`` reads the 「主要な経営指標等」table out of the annual report, which
    carries five fiscal years in one filing and costs nothing. It needs a wide
    date window to find that filing - an annual report is filed once a year -
    but the day lists are shared across symbols, so the scan is paid once per
    run rather than once per name.
    """
    chosen = (source or "").strip().lower() or settings.jp_statement_source.strip().lower()
    if chosen not in STATEMENT_SOURCES:
        raise typer.BadParameter(
            f"Unknown statement source '{source}'. Use one of {STATEMENT_SOURCES}."
        )

    if chosen == "edinet":
        edinet = EdinetDisclosureSource(
            api_key=settings.edinet_api_key, lookback_days=lookback_days
        )
        return (
            lambda symbol: fetch_annual_reports(symbol, settings.edinet_api_key, source=edinet)
        ), chosen

    provider = JQuantsFundamentalsProvider(api_key=settings.jquants_api_key)
    return provider.fetch_statements, chosen


@app.command()
def statements(
    symbols: list[str] = typer.Argument(..., help="JP security codes, e.g. 7203 4593"),
    source: str = typer.Option("", "--source", help=f"One of {', '.join(STATEMENT_SOURCES)}."),
    lookback_days: int = typer.Option(
        400, "--lookback-days", help="EDINET only: how far back to look for the annual report."
    ),
) -> None:
    """Fetch and store the disclosed statement history for SYMBOLS.

    This is what the growth, dividend-streak, and payout screens read.

    ``--source jquants`` makes one request per symbol and returns every period
    the plan covers. ``--source edinet`` reads five fiscal years out of the
    annual report instead, which is free - but EPS and BPS stay empty there,
    because the filing restates EPS for splits while leaving the share count
    and the dividend at their historical scale, and mixing the two would
    double-correct one of them. Dividend per share is on the same historical
    scale as the share count, so it is filled in and restated the same way.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    database = Database()
    database.create_all()
    fetch_statements, used = _statement_fetcher(source, settings, lookback_days)
    console.print(f"[dim]財務諸表の取得元: {used}[/dim]")

    results: list[IngestResult] = []
    for symbol in symbols:
        try:
            reports = fetch_statements(symbol)
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


#: ``statements-show`` が並べる列。空の列も出す――取れていないことが見えるように。
STATEMENT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("revenue", "売上"),
    ("operating_income", "営業利益"),
    ("net_income", "純利益"),
    ("equity", "自己資本"),
    ("shares_outstanding", "株式数"),
    ("eps", "EPS"),
    ("bps", "BPS"),
    ("dividend_per_share", "1株配当"),
)


def _statement_cell(column: str, value: float | None) -> str:
    """Render one cell: 億円 for amounts, 百万株 for share counts, yen as reported.

    単位を混ぜたまま並べると、桁で異常に気付けなくなる。
    """
    if value is None:
        return "-"
    if column in ("eps", "bps", "dividend_per_share"):
        return f"{value:,.2f}"
    if column == "shares_outstanding":
        return f"{value / 1e6:,.0f}"
    return f"{value / 1e8:,.0f}"


@app.command(name="statements-show")
def statements_show(
    symbols: list[str] = typer.Argument(..., help="Symbols to show, e.g. 6501 7203"),
) -> None:
    """Show the statement history already stored for SYMBOLS.

    ``statements`` writes; this reads. Nothing else in the CLI shows what
    landed in ``financial_statements`` - the screens consume it, but a screen
    returning nothing does not say whether the data is absent or the threshold
    is wrong. Amounts are in 億円, share counts in 百万株, per-share values as
    reported.

    Columns that were never filled are still printed. An empty column is a
    finding: it says the source had nothing for it, which is what a silently
    dropped element looks like.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    database = Database()
    database.create_all()

    missing: list[str] = []
    for symbol in symbols:
        with database.session() as session:
            reports = FinancialStatementRepository(session).get_reports(symbol)
        if not reports:
            missing.append(symbol)
            continue

        table = Table(title=f"{symbol}: stored statements (億円 / 百万株)")
        table.add_column("FY", style="cyan", justify="right")
        for _column, label in STATEMENT_COLUMNS:
            table.add_column(label, justify="right")
        for report in reports:
            table.add_row(
                str(report.fiscal_year),
                *(_statement_cell(c, getattr(report, c)) for c, _label in STATEMENT_COLUMNS),
            )
        console.print(table)

    for symbol in missing:
        console.print(f"[yellow]{symbol}: 保存された財務諸表がありません。[/]")
    if missing:
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
    universe_source: str | None = typer.Option(
        None,
        "--source",
        help="Override JP_UNIVERSE_SOURCE for this run: jquants | tachibana.",
    ),
) -> None:
    """List (and store) the JP listed universe for a market segment.

    One request. Run this before ``bulk-fetch``: it gives every later step a
    symbol list, a company name, and a sector.

    The source is ``JP_UNIVERSE_SOURCE``, which is deliberately separate from
    ``JP_PRICE_SOURCE``: moving prices to Tachibana left this path still
    calling J-Quants, which is exactly the gap that would have surfaced the day
    the plan was cancelled (``docs/JQUANTS_EXIT.md``). ``--as-of`` only means
    anything to J-Quants; Tachibana's master is a snapshot of today.
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
        source = _universe_source(settings, universe_source)
        if isinstance(source, JQuantsUniverse) and snapshot is not None:
            source = JQuantsUniverse(api_key=settings.jquants_api_key, as_of=snapshot)
        console.print(f"[dim]銘柄一覧の取得元: {source.name}[/dim]")
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
    backfill: bool = typer.Option(
        False,
        "--backfill",
        help="Extend symbols that already have prices back to --lookback. "
        "Without this, --lookback only applies to symbols with no prices at all.",
    ),
    statement_source: str | None = typer.Option(
        None,
        "--statement-source",
        help="Override JP_STATEMENT_SOURCE for this run only: jquants | edinet. "
        "For a one-off backfill of a field only one source carries.",
    ),
    replace: bool = typer.Option(
        False,
        "--replace",
        help="Statements only: DELETE each symbol's stored statements before "
        "fetching, so the result matches exactly what the API returns now. "
        "Without this, rows the API no longer returns are kept forever.",
    ),
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
    if replace and dataset is not Dataset.STATEMENTS:
        raise typer.BadParameter("--replace applies to --what statements only.")
    if replace:
        # 鍵が一致する行しか置き換わらないので、鍵の意味が変わったり5年ローリング
        # 窓がずれたりすると古い行が残る。実測で 7203 は API の20件に対しDBに
        # 21行あった。数える側から見れば、存在しない開示が1件増えているのと同じ。
        removed = 0
        with database.session() as session:
            statement_repo = FinancialStatementRepository(session)
            for symbol in targets:
                removed += statement_repo.delete_reports(symbol)
        console.print(
            f"[yellow]--replace:[/] 既存の財務 {removed} 行を消した。"
            "取得が終われば、DBの中身は「いまAPIが返すもの」と一致する。"
        )
    if dataset is Dataset.PRICES and not backfill:
        _warn_if_lookback_will_not_reach(database, targets, lookback)
    # Each dataset reads its own source setting - JP_PRICE_SOURCE for prices,
    # JP_STATEMENT_SOURCE for statements. Building the wrong one for the other
    # dataset would need credentials it has no reason to require.
    price_provider = None
    if dataset is Dataset.PRICES:
        price_provider, _market = _price_source(settings.jp_price_source, settings)
        console.print(f"[dim]価格の取得元: {settings.jp_price_source.lower()}[/dim]")
    statement_provider = None
    if dataset is Dataset.STATEMENTS:
        # --statement-source は .env を書き換えずに1回だけ経路を変えるためのもの。
        # 開示時刻のように片方の情報源にしか無い列を埋め直すとき、設定値のつもりで
        # APIキーを上書きする事故（過去に実際に起きた）を避けられる。
        chosen = (statement_source or settings.jp_statement_source).strip().lower()
        if chosen not in STATEMENT_SOURCES:
            raise typer.BadParameter(
                f"--statement-source must be one of {', '.join(sorted(STATEMENT_SOURCES))}; "
                f"got {chosen!r}."
            )
        if chosen == "edinet":
            statement_provider = EdinetFundamentalsProvider(
                settings.edinet_api_key,
                price_source=lambda symbol: bulk_latest_close(database, symbol),
            )
        origin = "" if statement_source is None else "  [yellow](--statement-source で上書き)[/]"
        console.print(f"[dim]財務諸表の取得元: {chosen}[/dim]{origin}")
    ingester = BulkIngester(
        database,
        api_key=settings.jquants_api_key,
        throttle_seconds=throttle,
        price_provider=price_provider,
        statement_provider=statement_provider,
    )

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
            targets,
            dataset,
            resume=resume,
            lookback_days=lookback,
            progress=advance,
            backfill=backfill,
        )
        progress.update(task, completed=len(targets))

    console.print(report.summary())
    if resume and not report.succeeded and report.skipped:
        # 「0 ok, N skipped」で [OK] と出て終わるのが一番危ない。再開の仕組みは
        # 「行がある＝最新」と見なすので、列を後から足しても永久に埋まらない。
        # 実際、開示時刻の列を足した直後の取り直しがこの形で空振りした。
        console.print(
            f"[yellow]1件も取得していない。[/]{len(report.skipped)} 件すべてが"
            "「既に最新」として飛ばされた。\n"
            "  再開の判定は『その銘柄の行があるか』だけを見るので、**列を新しく"
            "足しても、行が既にあれば埋まらない**。\n"
            "  後から足した列を埋め直すときは [bold]--no-resume[/] を付ける。"
        )
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
        # JP only. Both providers behind BulkIngester are J-Quants, so a US
        # symbol here spends a request to be told J-Quants has never heard of
        # it, and then lands in the failure table looking like something worth
        # investigating. Observed live: AAPL, MSFT, MRVL and IONQ reported as
        # "No J-Quants statements".
        with database.session() as session:
            stored = list_securities(session)
        symbols = [symbol for symbol, market in stored if market.upper() == "JP"]
        foreign = len(stored) - len(symbols)
        if foreign:
            console.print(
                f"[dim]Skipping {foreign} non-JP symbol(s): this fetches from "
                "J-Quants. Use 'fundamentals' for US names.[/]"
            )
    else:
        try:
            chosen = Segment(key)
        except ValueError as exc:
            raise typer.BadParameter(
                f"segment must be prime, standard, growth, all, or stored; got {segment!r}."
            ) from exc
        profiles = _universe_source(settings).profiles(chosen)
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
    report = build_report(
        collect_fundamentals(database, passing), names=company_names(database, passing)
    )

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
def metrics() -> None:
    """Show the distribution of every stored fundamental metric.

    For answering "is this number plausible?" without another round trip. A
    screen returning three quarters of the market is either a market of bargains
    or a broken metric, and the median and the quartiles say which in one look.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    database = Database()
    database.create_all()
    with database.session() as session:
        symbols = [symbol for symbol, _market in list_securities(session)]
        repo = FundamentalsRepository(session)
        snapshots = [snap for sym in symbols if (snap := repo.get_latest(sym)) is not None]

    if not snapshots:
        console.print("[yellow]No fundamentals stored.[/] Run 'bulk-fetch' first.")
        raise typer.Exit(code=1)

    frame = pd.DataFrame([snap.model_dump() for snap in snapshots])
    table = Table(title=f"stored fundamentals ({len(snapshots)} symbols)")
    table.add_column("metric", style="cyan")
    table.add_column("present", justify="right")
    table.add_column("<= 0", justify="right")
    for label in ("min", "25%", "median", "75%", "max"):
        table.add_column(label, justify="right")

    for column in ("per", "pbr", "roe", "dividend_yield", "market_cap", "revenue", "net_income"):
        series = pd.to_numeric(frame.get(column), errors="coerce").dropna()
        if series.empty:
            table.add_row(column, "0", "-", *(["-"] * 5))
            continue
        quantiles = series.quantile([0.0, 0.25, 0.5, 0.75, 1.0])
        table.add_row(
            column,
            str(len(series)),
            str(int((series <= 0).sum())),
            *[_compact(value) for value in quantiles],
        )
    console.print(table)
    console.print(
        "[dim]'<= 0' matters for PER and PBR: a loss-making company has a "
        "negative P/E, and a negative number clears any ceiling.[/]"
    )


def _compact(value: float) -> str:
    """Render a number readably across the range these metrics span."""
    if value is None or not isinstance(value, int | float):
        return "-"
    magnitude = abs(value)
    if magnitude >= 1e12:
        return f"{value / 1e12:.2f}T"
    if magnitude >= 1e9:
        return f"{value / 1e9:.2f}B"
    if magnitude >= 1e6:
        return f"{value / 1e6:.2f}M"
    if magnitude >= 100:
        return f"{value:,.0f}"
    return f"{value:.4g}"


@app.command()
def inspect(
    symbol: str = typer.Argument(..., help="JP security code, e.g. 6758"),
    limit: int = typer.Option(6, help="Newest N disclosures to print."),
) -> None:
    """Print the raw J-Quants statement records for one symbol.

    Every wrong number in this project so far has come from a field that was
    named something other than expected, or that meant something other than
    expected. Guessing which costs a round trip each time; this shows the
    payload as it arrives, so the answer is one command away.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    from stock_ai.data.jquants_fundamentals import _default_fetcher

    try:
        records = _default_fetcher(settings.jquants_api_key)(symbol)
    except DataError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc

    if not records:
        console.print(f"[yellow]No statements returned for {symbol}.[/]")
        raise typer.Exit(code=1)

    newest = sorted(records, key=lambda r: str(r.get("DiscDate") or ""), reverse=True)[:limit]
    console.print(f"{len(records)} record(s); showing the newest {len(newest)}.")

    # The fields the snapshot and the growth series are built from, first, then
    # everything else - a renamed field shows up as a blank in the first block
    # and an unfamiliar name in the second.
    key_fields = [
        # Identity of the row. A forecast revision and a results announcement
        # share DiscDate and CurPerType; only DocType separates them, and only
        # one of the two reports what actually happened.
        "DiscDate",
        "DocType",
        "CurPerType",
        "CurFYSt",
        "CurFYEn",
        # Consolidated figures - what this project reads.
        "Sales",
        "OP",
        "OdP",
        "NP",
        "EPS",
        "BPS",
        "Eq",
        "TA",
        "ShOutFY",
        "ROE",
        # Non-consolidated equivalents, shown alongside so a mix-up is visible.
        "NCSales",
        "NCNP",
        "NCEPS",
        "NCBPS",
        "NCEq",
        "NCROE",
        # Dividends come in several spellings, and the forecast ones must not be
        # mistaken for declared ones.
        "DivAnn",
        "DivTotalAnn",
        "DivFY",
        "Div1Q",
        "Div2Q",
        "Div3Q",
        "DivUnit",
        "PayoutRatioAnn",
        "FDivAnn",
        "NxFDivAnn",
        # Forecasts, listed so they read as separate from the actuals above.
        "FSales",
        "FOP",
        "FNP",
        "FEPS",
    ]
    table = Table(title=f"{symbol}: key fields")
    table.add_column("field", style="cyan")
    for index in range(len(newest)):
        table.add_column(f"#{index + 1}", justify="right", overflow="fold")
    for field in key_fields:
        values = [str(record.get(field, "")) for record in newest]
        if any(values):
            table.add_row(field, *values)
    console.print(table)

    seen = {key for record in newest for key in record}
    extra = sorted(seen - set(key_fields))
    console.print(f"[dim]Other fields present: {', '.join(extra) if extra else '(none)'}[/]")


@app.command()
def backtest(
    symbol: str = typer.Argument(..., help="Ticker to backtest (must be fetched)."),
    strategy: str = typer.Option("sma", help="Strategy: hold|sma|sma200|macd|rsi."),
    fast: int = typer.Option(20, help="Fast SMA window (sma strategy)."),
    slow: int = typer.Option(50, help="Slow SMA window (sma strategy)."),
    window: int = typer.Option(200, help="Trend window (sma200 strategy)."),
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
    if strategy.lower() == "sma200" and slow != 50:
        # --slow used to drive this strategy, so someone who worked around the
        # bug by passing --slow 200 must not silently get 200 again by accident.
        console.print(
            f"[yellow]--slow does not affect sma200; use --window (currently {window}).[/]"
        )
    strat = _build_strategy(strategy, fast, slow, window)
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
    walk: bool = typer.Option(
        False,
        "--walk-forward",
        help="Test every feasible formation date and report all of them.",
    ),
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

    if walk:
        _run_walk_forward(database, WeightedScorer(factors), preset, horizon, buckets)
        return

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


@app.command(name="accum-jp-count")
def accum_jp_count(
    symbols: list[str] | None = typer.Argument(
        None, help="JP codes to scan. Omit to scan every stored JP security."
    ),
    min_market_cap: float | None = typer.Option(
        None,
        "--min-market-cap",
        help="Secondary: judgment-day market-cap floor in yen, e.g. 1e10. Needs shares "
        "outstanding disclosed as of D, which only exists ~5 years back.",
    ),
    min_turnover: float = typer.Option(
        DEFAULT_MIN_TURNOVER,
        "--min-turnover",
        help="Section 2's liquidity floor in yen: the average turnover of the 20 "
        "sessions before D, never D itself. 0 disables it.",
    ),
    material_days: bool = typer.Option(
        True,
        "--material-days/--no-material-days",
        help="Evaluate section 3-1's earnings / ex-rights flags and report the "
        "material-free subset the registration judges on.",
    ),
) -> None:
    """Count how often the JP accumulation pre-registration's 5 conditions align.

    This is reconnaissance for the pre-registration's period-split and
    sample-size blanks (sections 6-7) - it computes no return, and its counts
    are not a pass/fail result. See ``SignalCountReport`` in
    ``stock_ai.backtest.accumulation_signal`` for what this pass does and does
    not cover against the pre-registration's universe (section 2): no
    independent segment check beyond what is already stored, and delisted
    names are entirely absent. The market-cap filter is covered when
    ``--min-market-cap`` is given.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    database = Database()
    database.create_all()

    report = count_signals(
        database,
        symbols=symbols,
        min_market_cap=min_market_cap,
        min_turnover=min_turnover or None,
        flag_material_days=material_days,
    )

    console.print(
        f"銘柄: {report.symbols_scanned} 件中 {report.symbols_with_enough_history} 件が"
        f"250営業日以上の履歴あり。"
    )
    if min_turnover:
        console.print(
            f"売買代金フィルタ: {min_turnover:,.0f}円以上で除外 {report.excluded_for_turnover} 件。"
        )
    if min_market_cap is not None:
        console.print(
            f"時価総額フィルタ: {min_market_cap:,.0f}円以上で除外 "
            f"{report.excluded_for_market_cap} 件。"
        )
        if report.first_evaluable_date is not None:
            console.print(
                f"[dim]時価総額を評価できる最初の日: {report.first_evaluable_date}"
                "（これより前は発行済株式数が分からず全件除外。年の途中なら、その年の"
                "件数が少ないのはデータ開始の都合）[/]"
            )
    console.print(
        "[dim]件数のみ。リターンは計算していない。市場区分の独立検証・上場廃止銘柄は"
        "未対応 - 詳細は accumulation_signal.SignalCountReport を参照。[/]"
    )

    if report.total == 0:
        console.print("[yellow]シグナルなし。[/]")
        return

    console.print(f"合計シグナル数: {report.total} ／ 独立シグナル日数: {report.unique_dates}")

    shown = report
    if material_days:
        free = report.material_free
        console.print(
            f"[bold]材料日を除いたサブセット（主要判定の対象）: {free.total} 件 ／ "
            f"独立 {free.unique_dates} 日[/]"
        )
        excluded = report.total - free.total
        rate = excluded / report.total * 100 if report.total else 0.0
        console.print(
            f"  除外 {excluded} 件（{rate:.1f}%）＝ 決算 {report.earnings_count} 件 ／ "
            f"権利 {report.exrights_count} 件 ／ "
            f"開示日不明で判定不能 {report.unflagged_but_unevaluable} 件"
        )
        console.print(
            "[dim]  除外率がそのまま「このシグナルがどれだけ材料日に依存していたか」。"
            "以下の表は材料日を除いたサブセット。[/]"
        )
        shown = free
        if shown.total == 0:
            console.print("[yellow]材料日を除くとシグナルが残らない。[/]")
            return

    table = Table(title="年別（材料日を除く）" if material_days else "年別")
    table.add_column("年", justify="right")
    table.add_column("シグナル数", justify="right")
    table.add_column("独立シグナル日数", justify="right")
    for row in shown.by_year().itertuples():
        table.add_row(str(row.year), str(row.signals), str(row.signal_days))
    console.print(table)

    if material_days:
        distances = report.by_earnings_distance()
        if not distances.empty:
            spread = Table(title="自社の決算発表までの営業日（全シグナル、フラグ適用前）")
            spread.add_column("距離", justify="left")
            spread.add_column("シグナル数", justify="right")
            spread.add_column("割合", justify="right")
            for row in spread_rows(distances):
                spread.add_row(*row)
            console.print(spread)
            console.print(
                "[dim]決算の直前2週に山があれば、これは決算発表日ではなく発表前の"
                "静かな期間を拾っている。平坦なら決算との時間的な関係は無い。[/]"
            )

    by_date = shown.by_date()
    top = Table(title="1日あたりの上位10日（集中度の確認用）")
    top.add_column("日付", justify="right")
    top.add_column("シグナル数", justify="right")
    for row in by_date.head(10).itertuples():
        top.add_row(str(row.date), str(row.signals))
    console.print(top)
    average_per_day = shown.total / shown.unique_dates if shown.unique_dates else 0.0
    console.print(
        f"1日平均 {average_per_day:.2f} 件 ／ 最大 {shown.max_signals_per_day} 件"
        "（日次クラスタ補正の前提として、特定の1日が結果を支配していないか確認）"
    )


def spread_rows(distances: pd.DataFrame) -> list[tuple[str, str, str]]:
    """Rows for the earnings-distance table."""
    return [
        (str(row.bucket), str(row.signals), f"{row.share * 100:.1f}%")
        for row in distances.itertuples()
    ]


@app.command(name="pead-census")
def pead_census(
    symbols: list[str] | None = typer.Argument(
        None, help="JP codes to census. Omit to use every stored JP security."
    ),
) -> None:
    """Count whether PEAD is measurable on the data actually on file.

    Run this BEFORE writing the pre-registration. The accumulation study was
    registered first and measured last, and ended "検証不能" because the
    phenomenon and the data did not overlap. This answers that question up
    front. It computes no return.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    database = Database()
    database.create_all()

    report = run_census(database, symbols=symbols)
    usable = report.measurable()

    console.print(
        f"銘柄: {report.symbols_scanned} 件 ／ 開示行 {report.rows_total} 件"
        f"（うち開示日なし {report.rows_without_disclosed_on} 件、"
        f"価格データなしの銘柄 {report.symbols_without_prices} 件）"
    )
    console.print("[dim]件数のみ。リターンは計算していない。事前登録を書く前の下調べである。[/]")

    if not report.disclosures:
        console.print("[yellow]開示イベントが1件もない。まず取り込みが要る。[/]")
        return

    total = len(report.disclosures)
    console.print(
        f"開示イベント: {total} 件 ／ "
        f"[bold]D+1 で入り D+{ENTRY_OFFSET + DRIFT_WINDOW} で出られるもの: "
        f"{len(usable)} 件（{len(usable) / total * 100:.1f}%）[/]"
    )
    console.print(
        "[dim]窓が取れない開示は、件数に数えても検証には使えない - 価格データの"
        "始端・終端にかかっている分である。以下は窓が取れる分だけを数えている。[/]"
    )

    if not usable:
        console.print("[yellow]リターン窓が取れる開示が1件もない。[/]")
        return

    year_table = Table(title="年別（リターン窓が取れる開示）")
    for column in ("年", "開示件数", "銘柄数", "ユニーク開示日数"):
        year_table.add_column(column, justify="right")
    for year, count, names, days in report.by_year(usable):
        year_table.add_row(str(year), str(count), str(names), str(days))
    console.print(year_table)

    band_table = Table(title="流動性帯別（D を除く直近20営業日の平均売買代金）")
    band_table.add_column("帯", justify="right")
    band_table.add_column("開示件数", justify="right")
    band_table.add_column("割合", justify="right")
    for edge, count in report.by_band(usable):
        label = "売買代金を計算できず" if edge is None else f"{edge:,.0f}円以上"
        band_table.add_row(label, str(count), f"{count / len(usable) * 100:.1f}%")
    console.print(band_table)
    console.print(
        "[dim]帯は累積ではなく排他。「1億円以上」の行だけが、前回の事前登録が"
        "使ったユニバースに相当する。ここが薄ければ前回と同じ結末になる。[/]"
    )

    slots = report.slots_per_fiscal_year(usable)
    slot_table = Table(title="1銘柄・1会計年度あたりの開示件数")
    slot_table.add_column("件数", justify="right")
    slot_table.add_column("該当する銘柄年", justify="right")
    slot_table.add_column("割合", justify="right")
    slot_total = sum(slots.values())
    for count in sorted(slots):
        share = slots[count] / slot_total * 100 if slot_total else 0.0
        slot_table.add_row(str(count), str(slots[count]), f"{share:.1f}%")
    console.print(slot_table)
    console.print(
        "[dim]DBは（銘柄, 会計年度, 四半期）を一意キーにしているので4件が上限。"
        "四半期ごとに短信が出る以上、揃っていれば4のはず。3が並ぶなら四半期が"
        "落ちているか同じ期の再開示が上書きしている。2が多いなら四半期開示を"
        "しない銘柄が混ざっている。[/]"
    )

    kinds = report.doc_type_counts(usable)
    kind_table = Table(title="開示の種類（PEADのイベントは決算短信だけ）")
    kind_table.add_column("種類", justify="left", overflow="fold")
    kind_table.add_column("開示件数", justify="right")
    kind_table.add_column("割合", justify="right")
    for label, count in kinds.most_common(12):
        kind_table.add_row(label, str(count), f"{count / len(usable) * 100:.1f}%")
    if len(kinds) > 12:
        kind_table.add_row(f"... 他 {len(kinds) - 12} 種類", "", "")
    console.print(kind_table)
    console.print(
        "[dim]予想修正や訂正が混ざっていれば、決算への反応を測っているつもりで"
        "別のものを測ることになる。事前登録はここで種類を絞る。[/]"
    )

    timing = report.timing_counts(usable)
    timing_table = Table(title="開示のタイミング（エントリー日がこれで決まる）")
    timing_table.add_column("区分", justify="left")
    timing_table.add_column("開示件数", justify="right")
    timing_table.add_column("割合", justify="right")
    for label in ("場中", "延長後の場中（15:00-15:30）", "引け後", "時刻なし"):
        count = timing.get(label, 0)
        if count:
            timing_table.add_row(label, str(count), f"{count / len(usable) * 100:.1f}%")
    console.print(timing_table)
    if timing.get("時刻なし"):
        console.print(
            "[yellow]「時刻なし」は「場中でも引け後でもない」ではなく"
            "「取り込んでいない」である。J-Quants の DiscTime を保存するように"
            "したので、3-データ取得.bat で財務を取り直すと埋まる。[/]"
        )

    per_day = report.same_day_counts(usable)
    counts = sorted(per_day.values())
    busiest = per_day.most_common(5)
    console.print(
        f"同日発表社数: 中央値 {counts[len(counts) // 2]} 社 ／ "
        f"最大 {counts[-1]} 社 ／ 開示日 {len(per_day)} 日"
    )
    console.print("  最も混雑した日: " + "、".join(f"{day} ({n}社)" for day, n in busiest))
    console.print(
        "[dim]注意分散仮説（混雑日ほど初期反応が小さくドリフトが大きい）は、"
        "この分布に幅がなければ測れない。[/]"
    )


@app.command(name="pead-run")
def pead_run(
    period: str = typer.Argument(
        ..., help="is | oos | all. Required on purpose - see the docstring."
    ),
    benchmark: str | None = typer.Option(
        None,
        "--benchmark",
        help="Symbol to measure excess return against, e.g. a TOPIX-tracking ETF. "
        "Omitted means raw returns; the top-minus-bottom spread is unaffected "
        "either way, but the surprise ranking is.",
    ),
    min_turnover: float = typer.Option(
        MIN_TURNOVER, "--min-turnover", help="Section 2's liquidity floor in yen."
    ),
    surprise: str = typer.Option(
        SORT_REACTION,
        "--surprise",
        help="reaction (PREREG_PEAD_JP.md) | sue (PREREG_SUE_JP.md). The sorting "
        "variable is the only thing the two registrations differ on.",
    ),
    i_am_ready_for_oos: bool = typer.Option(
        False,
        "--i-am-ready-for-oos",
        help="Required to run the OOS period. The registration judges once on OOS.",
    ),
) -> None:
    """Run a sealed registration: PREREG_PEAD_JP.md or PREREG_SUE_JP.md.

    PERIOD has no default. Each registration judges on OOS exactly once, and a
    default would make that one look accidental - "just print everything" is
    how a held-out period stops being held out. Running ``oos`` additionally
    needs ``--i-am-ready-for-oos``.

    ``--surprise`` picks which registration is being run. Everything else --
    the reaction day, the trading rule, the costs, the period split, the
    metric -- is shared, so a difference in the result is a difference in the
    sorting variable and not in the implementation.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    try:
        chosen = Period(period.strip().lower())
    except ValueError as exc:
        raise typer.BadParameter(f"period must be is, oos or all; got {period!r}.") from exc

    if chosen is not Period.IS and not i_am_ready_for_oos:
        raise typer.BadParameter(
            f"'{chosen.value}' includes the held-out period. Pass --i-am-ready-for-oos "
            "once the implementation and the IS checks are done (section 6)."
        )

    database = Database()
    database.create_all()

    chosen_sort = surprise.strip().lower()
    if chosen_sort not in (SORT_REACTION, SORT_SUE):
        raise typer.BadParameter(
            f"--surprise must be {SORT_REACTION} or {SORT_SUE}; got {surprise!r}."
        )

    built = build_events(
        database,
        chosen,
        benchmark=benchmark,
        min_turnover=min_turnover or MIN_TURNOVER,
        sort=chosen_sort,
    )

    registration = "PREREG_SUE_JP.md" if chosen_sort == SORT_SUE else "PREREG_PEAD_JP.md"
    sorted_by = (
        "会社予想からの乖離（通期のみ）" if chosen_sort == SORT_SUE else "R 当日の市場対比リターン"
    )
    console.print(f"事前登録: [bold]docs/{registration}[/] ／ 並べ替え: {sorted_by}")
    console.print(
        f"期間: [bold]{chosen.value}[/] ／ 銘柄 {built.symbols_scanned} 件 ／ "
        f"イベント [bold]{built.total}[/] 件 ／ 独立 {built.unique_days} 日"
    )
    console.print(
        f"[dim]除外: 決算短信でない {built.excluded_not_earnings} ／ "
        f"開示時刻なし {built.excluded_no_time} ／ 窓が取れない {built.excluded_no_window} ／ "
        f"売買代金不足 {built.excluded_thin} ／ ベンチマークの営業日ずれ "
        f"{built.excluded_no_benchmark}[/]"
    )
    if chosen_sort == SORT_SUE:
        console.print(
            f"[dim]  さらに: 通期短信でない {built.excluded_not_annual} ／ "
            f"直前の短信に通期予想が無い {built.excluded_no_forecast}[/]"
        )
    if built.benchmark is None:
        console.print(
            "[yellow]ベンチマークなしで計算した。[/] 上位分位と下位分位の差では"
            "ベンチマークが相殺されるので主要指標は成立するが、驚きの並べ替えは"
            "地合いの影響を受ける。--benchmark で指数連動ETFを指定できる。"
        )

    if built.total == 0:
        console.print("[yellow]イベントなし。[/]")
        return

    frame = built.frame()
    table = Table(title=f"上位分位 − 下位分位（{chosen.value}、コスト控除後、日次等加重）")
    for column in ("区分", "上位", "下位", "差", "t値", "クラスタ", "イベント", "片側のみの日"):
        table.add_column(column, justify="right")

    def add(label: str, rows: pd.DataFrame, column: str = "forward") -> None:
        result = spread(rows, column=column)
        flag = "" if result.reliable else "  [yellow](クラスタ<30)[/]"
        table.add_row(
            label,
            f"{result.high * 100:+.2f}%",
            f"{result.low * 100:+.2f}%",
            f"[bold]{result.difference * 100:+.2f}%[/]",
            f"{result.t_statistic:.2f}{flag}",
            str(result.clusters),
            str(result.events),
            str(result.days_without_both_legs),
        )

    add("主要指標 R+60", frame)
    add("副次 R+20", frame, column="forward_short")
    busy, quiet = crowding_split(frame)
    add("混雑日 R+60", busy)
    add("閑散日 R+60", quiet)
    add("場中 R+60", frame[frame["intraday"]])
    add("引け後 R+60", frame[~frame["intraday"]])
    console.print(table)
    console.print(
        "[dim]合否に使うのは「主要指標 R+60」の差1つだけ（セクション5）。"
        "他はすべて副次で、判定には使わない。[/]"
    )
    console.print(
        "[dim]「片側のみの日」は上位か下位のどちらかしか出ず、差を取れなかった日。"
        "その日はロング・ショートを組めないので落とすのが正しいが、落ちるのは"
        "発表の少ない日に偏るため、残った日は混雑日寄りになる。\n"
        "3列は同じ日集合・同じ加重なので、上位 − 下位 = 差 が厳密に成り立つ。[/]"
    )

    # 分位ごとの水準が偏っているとき、それが決算の性質なのか、ユニバースと
    # ベンチマークの組成差なのかを切り分ける。差では相殺されるので判定には
    # 効かないが、切り分けないと実装の誤りと区別が付かない。
    ladder_table = Table(
        title="分位ごとの平均超過リターン（驚きの小さい順、イベント等加重、コスト控除前）"
    )
    for column in ("分位", "平均", "イベント", "日数"):
        ladder_table.add_column(column, justify="right")
    for row in quantile_ladder(frame).itertuples():
        label = (
            f"{int(row.quantile) + 1}（最下位）"
            if row.quantile == 0
            else str(int(row.quantile) + 1)
        )
        ladder_table.add_row(label, f"{row.mean * 100:+.2f}%", str(row.events), str(row.days))
    console.print(ladder_table)
    console.print(
        "[dim]差は2点しか使わないので、外れ値の多い分位が1つあるだけで動く。"
        "驚きの順に単調に並んでいれば、差そのものよりずっと強い証拠になる。"
        "全分位が同じだけ沈んでいるなら、その水準は分位に依らない何かであり、"
        "差では相殺される。\n"
        "**この表は上の表と加重が違う**（イベント等加重・全日・コスト控除前）。"
        "両端を引き算しても上の「差」にはならない。[/]"
    )

    level = frame["forward"].mean()
    market = frame["market_forward"].dropna()
    console.print(f"\n水準: 全イベントの平均超過リターン [bold]{level * 100:+.2f}%[/]")
    if not market.empty:
        # 引き算の内訳。水準が銘柄側の話かベンチマーク側の話かは、
        # 差だけを見ていても分からない。
        console.print(
            f"  内訳: 銘柄の素のリターン {(level + market.mean()) * 100:+.2f}%"
            f" − ベンチマーク {market.mean() * 100:+.2f}%"
            f" = 超過 {level * 100:+.2f}%"
        )


@app.command(name="revision-census")
def revision_census(
    symbols: list[str] | None = typer.Argument(None, help="JP codes; omit for every stored one."),
    field: str = typer.Option(
        "net_income", "--field", help="revenue | operating_income | net_income | eps."
    ),
    min_change: float = typer.Option(
        DEFAULT_MIN_CHANGE, "--min-change", help="Relative move that counts as a revision."
    ),
) -> None:
    """Count company-forecast revisions found by comparing consecutive 短信.

    Revisions are not available as their own filings: 99.2% of what
    ``fins/summary`` returns is an earnings statement, and no revision
    document appears at all. The only route is the full-year forecast that
    every 短信 carries. **Whether hypothesis 2 is viable is decided by the
    count this prints**, so it runs before any pre-registration is drafted.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    database = Database()
    database.create_all()
    report = census_revisions(database, symbols=symbols, field=field, min_change=min_change)

    console.print(
        f"銘柄 {report.symbols_scanned} 件 ／ 比較できた短信の組 {report.pairs_compared:,} ／ "
        f"[bold]修正 {report.total:,} 件[/]（{field}、{min_change:.0%}以上の変化）"
    )
    console.print(
        f"[dim]内訳: 据え置き {report.pairs_unchanged:,} ／ "
        f"予想が入っておらず比較できず {report.pairs_without_forecast:,}[/]"
    )
    console.print(
        f"  比較できなかった組の内訳: 前だけ無し {report.missing_previous_only:,} ／ "
        f"後だけ無し {report.missing_current_only:,} ／ 両方無し {report.missing_both:,}"
    )
    console.print(
        f"  [bold]SUE を計算できる組: {report.usable_for_sue:,}[/]"
        "（SUE は前回の予想と今回の実績を比べるので、前側にさえ予想があればよい）"
    )
    if report.missing_by_transition:
        worst = sorted(report.missing_by_transition.items(), key=lambda kv: -kv[1])
        console.print(
            "  期の遷移ごとの欠落: " + "、".join(f"{key} {count:,}" for key, count in worst[:6])
        )
        console.print(
            "[dim]  特定の遷移に偏っていれば構造的な欠落（通期短信に当期予想が"
            "無いなど）。散っていれば予想を出さない会社の事情である。[/]"
        )
    if report.pairs_compared and report.pairs_without_forecast == report.pairs_compared:
        console.print(
            "[yellow]全組で予想が空だった。[/] 予想フィールドは後から足した列なので、"
            "財務を取り直すまで埋まらない。checks/開示時刻の取り込み.bat を先に実行する。"
        )
        return
    if report.total == 0:
        console.print("[yellow]修正が1件も見つからない。[/]")
        return

    table = Table(title="年別の予想修正")
    for column in ("年", "修正数", "上方", "下方"):
        table.add_column(column, justify="right")
    for year, total, up, down in report.by_year():
        table.add_row(str(year), f"{total:,}", f"{up:,}", f"{down:,}")
    console.print(table)
    console.print(f"独立した開示日数: [bold]{report.unique_days}[/] 日")
    console.print(
        "[dim]件数のみ。リターンは計算していない。候補2（予想修正後のドリフト）が"
        "成立するかは、この件数が決める。年に数百件しか出ないなら設計を先に"
        "見直す必要がある。[/]"
    )


@app.command(name="sue-census")
def sue_census(
    symbols: list[str] | None = typer.Argument(None, help="JP codes; omit for every stored one."),
    field: str = typer.Option(
        "net_income", "--field", help="revenue | operating_income | net_income | eps."
    ),
    min_turnover: float = typer.Option(
        MIN_TURNOVER,
        "--min-turnover",
        help="Liquidity floor in yen, as the 20-session average before R. Same as pead-run.",
    ),
) -> None:
    """Count full-year 短信 where actual can be compared with the standing forecast.

    Quarterly SUE cannot be built from Japanese filings: the forecast is
    full-year while the actual is year-to-date, so Q1 would subtract three
    months of actual from twelve months of forecast. Only the full-year 短信
    lines up. This prints how many such events exist, how many independent
    disclosure days they fall on, and how wide the surprise distribution is
    -- **no returns are computed**, so it can be run before sealing.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    database = Database()
    database.create_all()
    report = census_sue(database, symbols=symbols, field=field)

    console.print(
        f"銘柄 {report.symbols_scanned} 件 ／ 通期短信 {report.fy_statements:,} ／ "
        f"[bold]SUE を計算できたイベント {report.total:,} 件[/]（{field}）"
    )
    console.print(
        f"[dim]落ちた分: 実績が無い {report.without_actual:,} ／ "
        f"直前の短信に通期予想が無い {report.without_prior_forecast:,}[/]"
    )
    if report.total == 0:
        console.print(
            "[yellow]1件も組めなかった。[/] 予想フィールドは後から足した列なので、"
            "財務を取り直すまで埋まらない。checks/開示時刻の取り込み.bat を先に実行する。"
        )
        return

    table = Table(title="年別の通期決算イベント")
    for column in ("年", "イベント数", "独立開示日数"):
        table.add_column(column, justify="right")
    for year, total, days in report.by_year():
        table.add_row(str(year), f"{total:,}", f"{days:,}")
    console.print(table)
    console.print(
        f"独立した開示日数（通算）: [bold]{report.unique_days}[/] 日 "
        "[dim]通期短信は5月に集中するので、件数ではなくこの日数が実質的な"
        "サンプルサイズになる。[/]"
    )

    console.print(
        "予想を出した短信: "
        + "、".join(f"{period} {count:,}" for period, count in report.forecast_sources())
    )
    quantiles = report.surprise_quantiles()
    console.print(
        "驚きの分布（相対変化率）: "
        + "、".join(f"{name} {value:+.1%}" for name, value in quantiles)
    )
    share = report.near_zero / report.total
    console.print(f"±1%未満に収まったイベント: [bold]{report.near_zero:,}[/]（{share:.1%}）")
    if share > 0.3:
        console.print(
            "[yellow]予想と実績がほぼ一致するイベントが多い。[/] 日本の会社は着地が"
            "見えた時点で予想を出し直すため、分位の中央付近が潰れうる。分位に"
            "分けても上位と下位が同じものにならないか、封印前に確認する。"
        )

    console.print()
    console.print("[bold]並べ替える変数の候補を2つ比べる[/]")
    if report.scaled_available == 0:
        console.print(
            "[yellow]時価総額を1件も出せなかった。[/] 発行済株式数か、開示日より"
            "前の株価が入っていない。相対変化率で進めるしかない。"
        )
    else:
        console.print(
            f"時価総額を出せたイベント: {report.scaled_available:,} / {report.total:,}"
            f"（出せなかった {report.without_market_cap:,}）"
        )
        console.print(
            "驚きの分布（時価総額比・bp）: "
            + "、".join(f"{name} {value:+.0f}" for name, value in report.scaled_quantiles())
        )
        rho = report.rank_correlation()
        if rho is not None:
            console.print(f"2定義の順位相関（スピアマン）: [bold]{rho:.3f}[/]")
            if rho > 0.9:
                console.print(
                    "[dim]  どちらで並べてもほぼ同じ顔ぶれになる。定義の選択は結果を"
                    "変えないので、議論する必要は無い。[/]"
                )
            else:
                console.print(
                    "[yellow]  どちらを選ぶかで顔ぶれが変わる。[/] 結果を見る前に、"
                    "理屈で決めて封印する必要がある。"
                )
        profile = report.size_profile()
        if profile:
            table = Table(title="5分位ごとの時価総額の中央値（億円）")
            table.add_column("並べ替えた変数")
            for column in ("下位20%", "中位20%", "上位20%"):
                table.add_column(column, justify="right")
            for name, low, mid, high in profile:
                table.add_row(name, f"{low:,.0f}", f"{mid:,.0f}", f"{high:,.0f}")
            console.print(table)
            console.print(
                "[dim]端の分位だけ時価総額が小さければ、その定義は驚きの大きさでは"
                "なく会社の小ささを並べている。[/]"
            )

    console.print()
    console.print("[bold]封印する手順を通った後に何件残るか[/]")
    console.print(
        "[dim]アキュムレーションの事前登録は、封印してから流動性フィルタが"
        "11,014件を279件にしていたと分かって中止になった。同じ失い方を"
        "繰り返さないために、ここで先に数える。[/]"
    )
    ladder = Table(title="入場条件ごとの残存")
    ladder.add_column("段階")
    for column in ("残った件数", "独立開示日数"):
        ladder.add_column(column, justify="right")
    for name, count, days in report.admission_ladder(min_turnover):
        ladder.add_row(name, f"{count:,}", f"{days:,}")
    console.print(ladder)

    admitted = report.admitted(min_turnover)
    if not admitted:
        console.print("[yellow]全部落ちた。この条件では1件も測れない。[/]")
    else:
        per_year = Table(title="通った後の年別")
        for column in ("年", "イベント数", "独立開示日数"):
            per_year.add_column(column, justify="right")
        for year, count, days in report.admitted_by_year(min_turnover):
            per_year.add_row(str(year), f"{count:,}", f"{days:,}")
        console.print(per_year)

        profile = report.admitted_size_profile(min_turnover)
        if profile:
            table = Table(title="通った後・月次5分位ごとの時価総額の中央値（億円）")
            table.add_column("並べ替えた変数")
            for column in ("下位20%", "中央", "上位20%"):
                table.add_column(column, justify="right")
            for name, low, mid, high in profile:
                table.add_row(name, f"{low:,.0f}", f"{mid:,.0f}", f"{high:,.0f}")
            console.print(table)
            console.print(
                "[dim]分位は流動性フィルタの後に切っている。封印する手順がそうなって"
                "いるためで、実際に売買できる銘柄の中での上位・下位になる。[/]"
            )

    console.print()
    console.print("[dim]件数と分布のみ。リターンは計算していない。[/]")


@app.command(name="universe-snapshots")
def universe_snapshots(
    dates: str = typer.Option(
        "2018-06-01,2021-06-01,2023-06-01,2025-06-01",
        "--dates",
        help="Comma-separated snapshot dates to ask J-Quants for.",
    ),
) -> None:
    """Ask whether a past listing snapshot brings back the companies that left.

    Every registration so far carries the same limitation: **delisted companies
    are not in the universe.** For a reversal test that buys the biggest
    losers, that is not a footnote - the names that fell to nothing are exactly
    the ones missing.

    ``equities/master`` takes a ``date``. If a 2018 snapshot returns codes that
    are not in today's database, the bias is fixable rather than permanent.

    **This needs the J-Quants plan being cancelled on 2026-09-22.** If it works,
    the snapshots have to be pulled before that date, not after.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    database = Database()
    database.create_all()
    with database.session() as session:
        stored = {sym for sym, market in list_securities(session) if market == "JP"}
    console.print(f"いまの DB: [bold]{len(stored):,}[/] 銘柄（JP）")

    wanted = [_parse_date(text.strip()) for text in dates.split(",") if text.strip()]
    snapshots: dict[dt.date, set[str]] = {}
    table = Table(title="スナップショットが日付ごとに違うか")
    for column in ("日付", "返った件数", "DBに無い", "備考"):
        table.add_column(column, justify="right" if column != "備考" else "left")

    for when in wanted:
        if when is None:
            continue
        try:
            found = JQuantsUniverse(api_key=settings.jquants_api_key, as_of=when).profiles(
                Segment.ALL
            )
        except Exception as exc:  # noqa: BLE001 - 断られ方そのものが知りたい
            table.add_row(str(when), "[red]取れず[/]", "-", f"{type(exc).__name__}: {exc}"[:60])
            continue
        codes = {profile.symbol for profile in found}
        snapshots[when] = codes
        # **DB との差は「廃止された数」ではない。** DB は過去に取り込んだ分の
        # 集合であって、上場一覧そのものではない。取りこぼしと廃止が混ざる。
        table.add_row(str(when), f"{len(codes):,}", f"{len(codes - stored):,}", "")
    console.print(table)
    console.print(
        "[dim]「DBに無い」は廃止数ではない。DB は過去に取り込んだ分の集合で、"
        "上場一覧そのものではないので、取りこぼしと廃止が混ざる。**廃止を数える"
        "にはスナップショット同士を比べる。**[/]"
    )

    if len(snapshots) < 2:
        console.print("[yellow]比較できるスナップショットが2つ未満。[/]")
        return

    console.print()
    console.print("[bold]スナップショット同士の差 — これが廃止された銘柄[/]")
    ordered = sorted(snapshots)
    left_behind: list[str] = []
    diff = Table()
    for column in ("期間", "前", "後", "消えた", "増えた"):
        diff.add_column(column, justify="right" if column != "期間" else "left")
    for earlier, later in zip(ordered, ordered[1:], strict=False):
        gone = sorted(snapshots[earlier] - snapshots[later])
        added = snapshots[later] - snapshots[earlier]
        left_behind.extend(gone)
        diff.add_row(
            f"{earlier} → {later}",
            f"{len(snapshots[earlier]):,}",
            f"{len(snapshots[later]):,}",
            f"[bold]{len(gone):,}[/]",
            f"{len(added):,}",
        )
    console.print(diff)

    if not left_behind:
        console.print("[yellow]消えた銘柄が1つも無い。日付が効いていない可能性がある。[/]")
        return

    # ★ここが本題。銘柄コードが分かっても、価格が取れなければ何もできない。
    console.print()
    console.print("[bold]消えた銘柄の株価が取れるか — ここが取れないと一覧だけでは使えない[/]")
    provider = JQuantsPriceProvider(api_key=settings.jquants_api_key)
    sample = left_behind[:5]
    prices = Table()
    for column in ("銘柄", "取れた本数", "期間", "結果"):
        prices.add_column(column, justify="right" if column == "取れた本数" else "left")
    usable = 0
    for symbol in sample:
        try:
            frame = provider.fetch_prices(
                symbol, dt.date.today() - dt.timedelta(days=365 * 5), dt.date.today()
            )
        except Exception as exc:  # noqa: BLE001 - 断られ方そのものが知りたい
            prices.add_row(symbol, "-", "-", f"[red]{type(exc).__name__}: {exc}[/]"[:60])
            continue
        if frame.empty:
            prices.add_row(symbol, "0", "-", "[yellow]空で返った[/]")
            continue
        usable += 1
        prices.add_row(
            symbol,
            f"{len(frame):,}",
            f"{frame.index[0].date()} 〜 {frame.index[-1].date()}",
            "[green]取れた[/]",
        )
    console.print(prices)
    console.print()
    if usable:
        console.print(
            f"[bold]廃止銘柄の株価が {usable}/{len(sample)} 件で取れた。[/] "
            "生存バイアスは制約ではなく作業になる。"
        )
        console.print(
            "[yellow]ただし解約（2026-09-22）より前に取り込む必要がある。[/] "
            "立花のマスタは現存銘柄のみなので、解約後は同じ要求が通らない。"
        )
    else:
        console.print(
            "[yellow]株価が取れなかった。[/] 銘柄コードは分かるが価格が無いので、"
            "**一覧だけでは生存バイアスを直せない。** 制約として登録に残す。"
        )


@app.command(name="delisted-harvest")
def delisted_harvest(
    start: str = typer.Option(
        ROLLING_WINDOW_START.isoformat(),
        "--start",
        help="First snapshot date. J-Quants refuses anything outside its 5-year window.",
    ),
    end: str | None = typer.Option(None, "--end", help="Last snapshot date. Defaults to today."),
    step_days: int = typer.Option(
        DEFAULT_STEP_DAYS, "--step-days", help="Days between snapshots. Monthly by default."
    ),
    directory: str = typer.Option(
        str(DEFAULT_SNAPSHOT_DIR), "--dir", help="Where the snapshots are written."
    ),
    refetch: bool = typer.Option(
        False, "--refetch", help="Re-request dates whose file already exists."
    ),
    prices: bool = typer.Option(
        True, "--prices/--no-prices", help="Also backfill prices for symbols the DB lacks."
    ),
    limit: int | None = typer.Option(None, help="Cap how many missing symbols get prices."),
    throttle: float = typer.Option(0.2, help="Seconds to pause between symbols."),
) -> None:
    """Save the listing roster, dated, before the plan that serves it is cancelled.

    Every registration so far has carried the same limitation: **delisted
    companies are not in the universe.** For a reversal test that buys the
    biggest losers that is not a footnote - the names that fell and vanished
    are exactly the ones missing.

    ``universe-snapshots`` established this is fixable: snapshot-to-snapshot
    diffs found 49-106 delistings a year, and prices came back for 5 of 5 of
    them. This command does the work that finding implies, in two steps:

    1. Walk monthly ``equities/master`` snapshots and write each to its own
       CSV. **Dated rosters, not a union** - a union lets a 2023 listing into a
       2021 quintile, which is look-ahead dressed up as a bias fix.
    2. Backfill prices for every symbol in those rosters that the database does
       not hold.

    **This is deliberately pinned to J-Quants**, ignoring ``JP_PRICE_SOURCE``.
    Tachibana's master carries currently-listed names only, so routing this
    through the configured source would silently collect nothing - the exact
    failure this project keeps hitting. It follows that the run only works
    while the J-Quants plan is live.

    Safe to interrupt: a date whose file exists is not re-requested, and a
    symbol whose prices are current is skipped.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    first = _parse_date(start)
    if first is None:
        raise typer.BadParameter(f"--start must be YYYY-MM-DD; got {start!r}.")
    last = dt.date.today() if end is None else _parse_date(end)
    if last is None:
        raise typer.BadParameter(f"--end must be YYYY-MM-DD; got {end!r}.")
    try:
        wanted = snapshot_dates(first, last, step_days)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    target = Path(directory)
    console.print(
        f"名簿を [bold]{len(wanted)}[/] 日ぶん集める（{first} 〜 {last}、{step_days}日刻み）。"
    )
    console.print(f"[dim]置き場所: {target}[/dim]")
    console.print(
        "[dim]取得元は J-Quants に固定（JP_PRICE_SOURCE は見ない）。立花のマスタは"
        "現存銘柄のみで、廃止銘柄は返らないため。[/dim]"
    )

    def fetch(on: dt.date) -> list[SecurityProfile]:
        return JQuantsUniverse(api_key=settings.jquants_api_key, as_of=on).profiles(Segment.ALL)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("snapshots", total=len(wanted))

        def one(on: dt.date) -> list[SecurityProfile]:
            progress.update(task, description=f"snapshot {on}")
            found = fetch(on)
            progress.advance(task)
            return found

        report = harvest_snapshots(target, wanted, one, refetch=refetch)
        progress.update(task, completed=len(wanted))

    console.print(report.summary())
    if report.refused:
        refusals = Table(title=f"取れなかった日付 ({len(report.refused)})")
        refusals.add_column("日付")
        refusals.add_column("断られ方", overflow="fold")
        for on, why in list(report.refused.items())[:10]:
            refusals.add_row(str(on), why)
        console.print(refusals)
        console.print(
            "[dim]5年ローリング窓の外は必ず断られる。異常ではないが、**その期間の"
            "生存バイアスはこの方法では直せない**ので、登録に境界日を書く。[/dim]"
        )

    stored = membership(target)
    if len(stored) >= 2:
        gone = delistings(stored)
        diff = Table(title="名簿同士の差 = 廃止された銘柄")
        for column in ("期間", "前", "後", "消えた"):
            diff.add_column(column, justify="left" if column == "期間" else "right")
        total_gone = sum(len(codes) for _earlier, _later, codes in gone)
        shown = gone[-24:]
        if len(shown) < len(gone):
            console.print(f"[dim]{len(gone)} 期間ぶん。表は最後の {len(shown)} 期間のみ。[/dim]")
        for earlier, later, codes in shown:
            diff.add_row(
                f"{earlier} → {later}",
                f"{len(stored[earlier]):,}",
                f"{len(stored[later]):,}",
                f"{len(codes):,}",
            )
        console.print(diff)
        console.print(f"延べ [bold]{total_gone:,}[/] 銘柄が期間中に消えた。")

    if not prices:
        console.print("[dim]--no-prices なので株価は取っていない。名簿だけ。[/dim]")
        return

    database = Database()
    database.create_all()
    with database.session() as session:
        covered = {symbol for symbol, market, *_ in price_history_spans(session) if market == "JP"}
    missing = sorted(report.union - covered)
    console.print(
        f"名簿にあって DB に株価が無い銘柄: [bold]{len(missing):,}[/] 件"
        f"（名簿 {len(report.union):,} 件中）。"
    )
    if not missing:
        console.print("[green]取り込むものは無い。[/]")
        return
    if limit:
        missing = missing[:limit]
        console.print(f"[yellow]--limit により {len(missing)} 件だけ取る。[/]")

    store_universe(database, [report.profiles[symbol] for symbol in missing])
    lookback = max(1, (dt.date.today() - first).days + 365)
    ingester = BulkIngester(
        database,
        api_key=settings.jquants_api_key,
        throttle_seconds=throttle,
        price_provider=JQuantsPriceProvider(api_key=settings.jquants_api_key),
    )
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("prices", total=len(missing))

        def advance(index: int, total: int, symbol: str) -> None:
            progress.update(task, completed=index - 1, description=f"prices {symbol}")

        prices_report = ingester.run(
            missing, Dataset.PRICES, lookback_days=lookback, progress=advance, backfill=True
        )
        progress.update(task, completed=len(missing))

    console.print(prices_report.summary())
    if prices_report.failed:
        console.print(
            f"[yellow]{len(prices_report.failed)} 件は取れなかった。[/] "
            "同じコマンドを再実行すれば、取れた分は飛ばして失敗分だけ retry する。"
        )
    console.print(
        "[bold]これで、廃止銘柄を含む universe が日付ごとに手元にある。[/] "
        "分位を組むときは「その日以前で最も新しい名簿」を使う——全期間の和集合を"
        "使うと、まだ上場していない銘柄を過去に置くことになる。"
    )


@app.command(name="jquants-inventory")
def jquants_inventory(
    directory: str = typer.Option(
        str(DEFAULT_SNAPSHOT_DIR), "--dir", help="Where the dated rosters live."
    ),
) -> None:
    """List what the cancellation takes away, and how much is already local.

    The plan ends 2026-09-22. Anything that can be refetched afterwards is not
    urgent: Tachibana still serves prices, EDINET still serves annual reports.
    Three things cannot be rebuilt from anywhere, so those are what this counts.

    1. **Dated listing rosters.** The Tachibana master returns currently-listed
       names only.
    2. **Prices for delisted symbols.** Those codes no longer exist at Tachibana.
    3. **Company full-year forecasts and disclosure times.** EDINET's annual
       reports carry actuals, and neither of these.

    The five-year rolling window bites before the cancellation does: the oldest
    end is already gone and recedes daily. Nothing is fetched here - counting
    only. ``delisted-harvest`` and ``bulk-fetch`` do the fetching.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    database = Database()
    database.create_all()
    snapshots = membership(Path(directory))
    coverage = audit(database, snapshots)
    left = coverage.days_left()

    console.print(
        f"解約日 [bold]{CANCELLATION}[/] まで "
        + (f"[bold]{left}[/] 日。" if left >= 0 else "[red]過ぎている。[/]")
    )
    console.print()

    have = Table(title="いま手元にあるもの")
    for column in ("データ", "量", "期間"):
        have.add_column(column, justify="left" if column != "量" else "right")
    # **銘柄が登録されていることと、株価があることは別。** delisted-harvest は
    # 先に銘柄を作ってから株価を取るので、途中で止まると差が開く。その差が
    # 見えないと「取り込み済み」と取り違える。
    have.add_row("銘柄の登録", f"{coverage.securities:,} 銘柄", "")
    have.add_row(
        "日足の価格",
        f"{coverage.symbols_with_prices:,} 銘柄",
        f"{coverage.price_first} 〜 {coverage.price_last}"
        if coverage.price_first
        else "[yellow]無い[/]",
    )
    have.add_row(
        "財務諸表",
        f"{coverage.statements:,} 行 / {coverage.symbols_with_statements:,} 銘柄",
        f"{coverage.statement_first} 〜 {coverage.statement_last}"
        if coverage.statement_first
        else "[yellow]無い[/]",
    )
    have.add_row("　うち開示時刻あり", f"{coverage.with_disclosed_at:,} 行", "")
    have.add_row("　うち会社予想あり", f"{coverage.with_forecast:,} 行", "")
    have.add_row(
        "日付ごとの名簿",
        f"{coverage.snapshots:,} 件",
        f"{coverage.snapshot_first} 〜 {coverage.snapshot_last}"
        if coverage.snapshot_first
        else "[yellow]無い[/]",
    )
    console.print(have)

    risk = Table(title="解約後に作り直せないもの")
    for column in ("失われるもの", "状態", "取り方"):
        risk.add_column(column, overflow="fold")
    roster_state = (
        f"[green]{coverage.snapshots} 件保存済み[/]" if coverage.snapshots else "[red]1件も無い[/]"
    )
    risk.add_row("日付ごとの上場名簿", roster_state, "checks\\廃止銘柄の取り込み.bat")
    if coverage.snapshots:
        missing = coverage.roster_without_prices
        price_state = (
            "[green]名簿の全銘柄に株価がある[/]"
            if missing == 0
            else f"[red]{missing:,} 銘柄の株価が無い[/]"
        )
    else:
        price_state = "[yellow]名簿が無いので数えられない[/]"
    risk.add_row("上場廃止銘柄の株価", price_state, "同上（名簿と同時に取る）")
    risk.add_row(
        "会社の通期予想",
        f"{coverage.with_forecast:,} 行",
        "bulk-fetch --what statements --statement-source jquants",
    )
    risk.add_row(
        "開示時刻（DiscTime）",
        f"{coverage.with_disclosed_at:,} 行",
        "checks\\開示時刻の取り込み.bat",
    )
    console.print(risk)

    console.print()
    console.print(
        "[dim]5年ローリング窓は解約より先に効く。いま取れるのは 2021-09 以降で、"
        "その端は**毎日後ろへ動く**。「解約日まで待てる」ものは1つも無い。[/]"
    )
    if coverage.snapshots and coverage.roster_without_prices:
        console.print(
            f"[yellow]名簿にあって株価が無い {coverage.roster_without_prices:,} 銘柄が"
            "残っている。[/] これがそのまま生存バイアスの残りである。"
        )
    console.print(
        "[dim]会社予想と開示時刻は決算ドリフトのテーマ用で、そのテーマは"
        "2026-09-03 に閉じた（docs/HYPOTHESES.md）。**再開する予定が無いなら"
        "取り直す必要は無い。** 再開しうるなら、解約前が最後の機会になる。[/]"
    )


@app.command(name="reversal-power")
def reversal_power(
    end: str = typer.Option(
        "2020-12-31",
        "--end",
        help="Last day used to estimate the variance. Must be before the judged period.",
    ),
    start: str | None = typer.Option(None, "--start", help="First day. Defaults to all history."),
    oos_days: int = typer.Option(
        0, "--oos-days", help="Sessions the OOS test will have. 0 counts them from the calendar."
    ),
    lags: int = typer.Option(DEFAULT_LAGS, "--lags", help="Newey-West lags. Match the holding."),
    min_turnover: float = typer.Option(MIN_TURNOVER, "--min-turnover", help="Liquidity floor."),
    lookback: int = typer.Option(REVERSAL_LOOKBACK, "--lookback", help="Sessions the fall spans."),
    holding: int = typer.Option(REVERSAL_HOLDING, "--holding", help="Sessions held."),
    benchmark: str = typer.Option(BENCHMARK, "--benchmark", help="Market series and calendar."),
) -> None:
    """Work out what size of effect the OOS test could detect - before sealing.

    Both earnings-drift registrations were sealed first and found short of power
    afterwards. SUE only revealed it through a daily standard deviation of
    21.08%, which came from having one or two names per quintile. That order was
    wrong, so this runs first.

    **No mean is computed or printed.** Only the variance and autocovariances of
    the daily series, taken from a stretch that the judged period does not use.
    Estimating a variance does not spend a hypothesis test; estimating a mean
    does. ``--end`` is refused if it reaches the judged period, so the guard is
    not a matter of remembering.

    Overlapping windows are the whole difficulty: entering daily and holding 20
    sessions makes neighbouring observations share 19 days out of 20. Treating
    them as independent understates the standard error roughly fourfold, so the
    long-run variance uses Newey-West with Bartlett weights.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    last = _parse_date(end)
    if last is None:
        raise typer.BadParameter(f"--end must be YYYY-MM-DD; got {end!r}.")
    if last >= JUDGMENT_FROM:
        raise typer.BadParameter(
            f"--end ({last}) reaches the judged period, which starts {JUDGMENT_FROM}. "
            "検出力の推定に判定期間を混ぜると、平均を見ていなくても"
            "「その期間なら何%出るか」を選べてしまう。"
        )
    first = _parse_date(start) if start else None
    if start and first is None:
        raise typer.BadParameter(f"--start must be YYYY-MM-DD; got {start!r}.")

    database = Database()
    database.create_all()
    console.print(
        f"分散だけを {first or '最初'} 〜 {last} から推定する。"
        "[bold]平均は計算しないし、出さない。[/]"
    )

    try:
        series = build_series(
            database,
            Period.ALL,
            benchmark=benchmark,
            start=first,
            end=last,
            min_turnover=min_turnover,
            lookback=lookback,
            holding=holding,
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc

    per_day = int(median(series.counts)) if series.counts else 0
    console.print(
        f"{len(series.days):,} 営業日、1日あたり中央値 {per_day} 銘柄。"
        f"（暦が合わず落ちた銘柄日 {series.excluded_calendar:,}）"
    )
    _report_spread(series, per_day)

    target = oos_days or _oos_session_count(database, benchmark, holding)
    console.print(f"OOS の想定日数: [bold]{target:,}[/] 営業日（{OOS_FROM} 以降）")

    table = Table(title="重なりを織り込んだ検出力（平均は含まない）")
    table.add_column("指標")
    for column in ("日次SD", "重なりの膨張", "OOS の標準誤差", f"t≥{TARGET_T} に必要な差"):
        table.add_column(column, justify="right")

    verdicts: list[tuple[str, float]] = []
    for label, values in (
        ("分位1 − ベンチ（主要）", series.long_only()),
        ("分位1 − 分位5（副次）", series.long_short()),
    ):
        estimate = estimate_power(values, lags=lags)
        needed = estimate.detectable(target)
        verdicts.append((label, needed))
        table.add_row(
            label,
            f"{estimate.daily_sd * 100:.2f}%",
            f"{estimate.inflation:.2f}x",
            f"{estimate.standard_error(target) * 100:.2f}%",
            f"[bold]{needed * 100:.2f}%[/]",
        )
    console.print(table)

    threshold = COST_ROUND_TRIP
    console.print()
    console.print(
        f"費用のしきい値は保有{holding}営業日あたり [bold]{threshold * 100:.2f}%[/]"
        "（ロングオンリーなので両建て前提の 0.80% の半分）。"
    )
    primary_needed = verdicts[0][1]
    if primary_needed > threshold:
        console.print(
            f"[yellow]必要な差 {primary_needed * 100:.2f}% が、しきい値 "
            f"{threshold * 100:.2f}% を上回る。[/]\n"
            "  **費用を賄うだけの効果では、この日数では有意にならない。** "
            "合格が出るとしたら、費用を大きく超える効果のときだけになる。\n"
            "  期間を延ばすか、前向きに貯めるかを、封印の前に決める。"
        )
    else:
        console.print(
            f"[green]必要な差 {primary_needed * 100:.2f}% は、しきい値 "
            f"{threshold * 100:.2f}% を下回る。[/]\n"
            "  費用を賄う水準の効果なら、この日数で有意になりうる。"
        )
    console.print(
        "[dim]この数字は「どれだけ大きければ検出できるか」であって、"
        "「どれだけ出るか」ではない。後者は判定でしか分からない。[/]"
    )


def _report_spread(series: object, per_day: int) -> None:
    """Show whether the per-symbol spread can explain the portfolio spread.

    **これを見ずに「必要な差」を信じない。** 1分位に約 n 銘柄入るなら、平均の
    ばらつきは個別のばらつきのおよそ 1/sqrt(n) まで落ちるはずである。落ちて
    いなければ、平均は数件の極端値に引っ張られている——測っているのは現象では
    なくデータの傷になる。SUE 版の 21.08% は1分位1〜2銘柄で説明がついたが、
    ここは約160銘柄なので、同じ桁が出たら説明がつかない。
    """
    if not series.forward_percentiles:  # type: ignore[attr-defined]
        return
    spread = Table(title="銘柄日ごとのフォワードリターン（分位平均の材料）")
    for label, _value in series.forward_percentiles:  # type: ignore[attr-defined]
        spread.add_column(label, justify="right")
    spread.add_row(
        *[f"{value * 100:+.1f}%" for _label, value in series.forward_percentiles]  # type: ignore[attr-defined]
    )
    console.print(spread)

    bucket = max(1, per_day // 5)
    console.print(
        f"[dim]1分位あたり約 {bucket} 銘柄。個別のばらつきがこの平方根ぶん"
        f"（÷{bucket**0.5:.1f}）まで落ちていなければ、平均は少数の極端値で"
        "できている。[/dim]"
    )

    if series.extremes:  # type: ignore[attr-defined]
        outliers = Table(title="フォワードリターンが大きい銘柄日（銘柄ごとに最悪の1件）")
        for column in ("銘柄", "判定日", "5日リターン", "フォワード"):
            outliers.add_column(column, justify="right" if "リターン" in column else "left")
        for symbol, day, back, ahead in series.extremes:  # type: ignore[attr-defined]
            outliers.add_row(symbol, str(day), f"{back * 100:+.1f}%", f"{ahead * 100:+.1f}%")
        console.print(outliers)
        console.print(
            "[dim]**+900% のような値が出たら分割・併合の調整漏れを疑う。** "
            "その銘柄と日付を [cyan]stock-ai prices[/] で直接見る。[/dim]"
        )


def _oos_session_count(database: Database, benchmark: str, holding: int) -> int:
    """Count the sessions the OOS test will have. Counts days, never values."""
    with database.session() as session:
        frame = PriceRepository(session).get_raw_prices(benchmark)
    if frame.empty:
        return 0
    days = [stamp.date() for stamp in frame.index if stamp.date() >= OOS_FROM]
    return max(0, len(days) - (holding + 1))


@app.command(name="reversal-bias")
def reversal_bias(
    directory: str = typer.Option(
        str(DEFAULT_SNAPSHOT_DIR), "--dir", help="Where the dated rosters live."
    ),
    end: str | None = typer.Option(
        None, "--end", help="Last day. Defaults to the day before OOS starts."
    ),
    min_turnover: float = typer.Option(MIN_TURNOVER, "--min-turnover", help="Liquidity floor."),
    lookback: int = typer.Option(REVERSAL_LOOKBACK, "--lookback", help="Sessions the fall spans."),
    holding: int = typer.Option(REVERSAL_HOLDING, "--holding", help="Sessions held."),
    benchmark: str = typer.Option(BENCHMARK, "--benchmark", help="Market series and calendar."),
) -> None:
    """Measure how big survivorship bias actually is, and which way it points.

    Runs the same window twice over the same days, changing exactly one thing:
    the universe. Once with the dated rosters - which hold the companies that
    were later delisted - and once with only the names still listed today. The
    difference is the bias, in size and in sign.

    **In-sample only.** ``--end`` is refused if it reaches the out-of-sample
    period; looking there would spend the one judgment the design has.

    Worth having beyond this hypothesis: every registration so far asserted the
    bias existed without a number, and the direction was never obvious - a
    takeover leaves at a premium while a failure goes to zero.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    stop = _parse_date(end) if end else OOS_FROM - dt.timedelta(days=1)
    if stop is None:
        raise typer.BadParameter(f"--end must be YYYY-MM-DD; got {end!r}.")
    if stop >= OOS_FROM:
        raise typer.BadParameter(
            f"--end ({stop}) reaches the out-of-sample period, which starts {OOS_FROM}. "
            "バイアスの実測でOOSを覗くと、判定に使える一度が失われる。"
        )

    snapshots = membership(Path(directory))
    if len(snapshots) < 2:
        console.print(
            f"[yellow]名簿が {len(snapshots)} 件しかない。[/] "
            "先に [cyan]checks\\廃止銘柄の取り込み.bat[/] を実行する。"
        )
        raise typer.Exit(code=1)
    # **名簿の実際の先頭から測る。** 定数を信じて名簿の無い日を判定日にすると、
    # その期間だけ universe が空になり、静かに落ちる。落ちること自体は正しいが、
    # 「いつから測ったか」を推測することになる。
    begin = max(JUDGMENT_FROM, min(snapshots))
    console.print(
        f"名簿 {len(snapshots)} 件（{min(snapshots)} 〜 {max(snapshots)}）。"
        f"測る期間は {begin} 〜 {stop}。"
    )

    database = Database()
    database.create_all()
    runs: dict[str, object] = {}
    for label, survivors_only in (("名簿あり", False), ("生存者のみ", True)):
        try:
            runs[label] = build_series(
                database,
                Period.ALL,
                benchmark=benchmark,
                start=begin,
                end=stop,
                min_turnover=min_turnover,
                lookback=lookback,
                holding=holding,
                snapshots=snapshots,
                survivors_only=survivors_only,
            )
        except ValueError as exc:
            console.print(f"[red]{label}: {exc}[/]")
            raise typer.Exit(code=1) from exc

    clean = runs["名簿あり"]
    survivors = runs["生存者のみ"]
    counts = Table(title="universe を差し替えると何が変わるか")
    for column in ("universe", "営業日", "1日あたり中央値"):
        counts.add_column(column, justify="left" if column == "universe" else "right")
    for label, run in (("名簿あり", clean), ("生存者のみ", survivors)):
        counts.add_row(
            label,
            f"{len(run.days):,}",
            f"{int(median(run.counts)) if run.counts else 0:,}",
        )
    console.print(counts)

    # **同じ日で揃える。** 片方にしか無い日を混ぜると、バイアスではなく期間の
    # 違いを測ることになる。
    left = dict(zip(clean.days, clean.long_only(), strict=True))
    right = dict(zip(survivors.days, survivors.long_only(), strict=True))
    shared = sorted(set(left) & set(right))
    if not shared:
        console.print("[red]両方に共通する営業日が無い。[/]")
        raise typer.Exit(code=1)
    gap = survivorship_gap([left[day] for day in shared], [right[day] for day in shared])
    average = sum(gap) / len(gap)

    console.print()
    console.print(
        f"共通の {len(shared):,} 営業日で、[bold]名簿あり − 生存者のみ = "
        f"{average * 100:+.3f}%[/]（保有{holding}営業日あたり）"
    )
    if average < 0:
        console.print(
            "  負である＝**生存者だけで測ると効果を大きく見せる。** "
            "廃止銘柄を入れると下がる。従来の「上振れするかもしれない」という"
            "推測が、符号つきの実測になった。"
        )
    elif average > 0:
        console.print(
            "  正である＝**生存者だけで測ると効果を小さく見せる。** "
            "TOB・完全子会社化がプレミアム付きで消えるぶんが効いている可能性がある。"
        )
    console.print(
        "[dim]これは IS の数字である。判定には使わない。"
        "**プロジェクト全体で使い回せる数字**として登録に書く。[/]"
    )


@app.command(name="reversal-census")
def reversal_census(
    period: str = typer.Option("all", "--period", help="is | oos | all."),
    min_turnover: float = typer.Option(
        MIN_TURNOVER, "--min-turnover", help="Liquidity floor in yen. Same as pead-run."
    ),
    lookback: int = typer.Option(
        REVERSAL_LOOKBACK, "--lookback", help="Sessions over which the fall is measured."
    ),
    holding: int = typer.Option(REVERSAL_HOLDING, "--holding", help="Sessions held."),
) -> None:
    """Count what a short-term reversal test would have to work with.

    Reversal is not event-driven: every symbol carries a trailing return on
    every session, so the population is symbol-days rather than events. That
    changes what has to be counted - the binding number is **how many symbols
    clear the filter on a single day**, because a day with too few cannot be
    cut into quintiles at all.

    **No returns are computed.** This runs before the registration is written,
    which is the order the two earnings-drift registrations did not follow.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    try:
        chosen = Period(period.strip().lower())
    except ValueError as exc:
        raise typer.BadParameter(f"--period must be is, oos or all; got {period!r}.") from exc

    database = Database()
    database.create_all()
    report = run_reversal_census(
        database,
        chosen,
        min_turnover=min_turnover or MIN_TURNOVER,
        lookback=lookback,
        holding=holding,
    )

    console.print(
        f"期間 [bold]{chosen.value}[/] ／ 銘柄 {report.symbols_scanned} 件 ／ "
        f"{lookback}日下落・{holding}日保有・売買代金 {min_turnover / 1e8:.0f}億円以上"
    )
    console.print(
        f"[bold]観測 {report.observations:,}[/]（銘柄×営業日） ／ "
        f"営業日 [bold]{report.trading_days:,}[/] 日"
    )
    console.print(
        f"[dim]除外: 価格が無い銘柄 {report.symbols_without_prices} ／ "
        f"起点が無い {report.excluded_no_lookback:,} ／ "
        f"売買代金不足 {report.excluded_thin:,} ／ "
        f"前後のバーが無い {report.excluded_no_window:,}[/]"
    )

    if report.observations == 0:
        console.print("[yellow]観測なし。価格が入っているか確認する。[/]")
        return

    table = Table(title="年別")
    for column in ("年", "観測数", "営業日数", "1日あたり"):
        table.add_column(column, justify="right")
    for year, count, day_count in report.by_year():
        table.add_row(str(year), f"{count:,}", f"{day_count:,}", f"{count / day_count:,.0f}")
    console.print(table)

    console.print(
        "1日あたりの通過銘柄数: "
        + "、".join(f"{name} {value:,}" for name, value in report.breadth())
    )
    if report.thin_days:
        console.print(
            f"[yellow]5銘柄に満たない日が {report.thin_days} 日ある。[/] "
            "その日は分位に切れないので、差を取れない。"
        )
    else:
        console.print(
            "[dim]どの営業日も5分位に切れる。決算ドリフトで効いた「両分位が同じ日に"
            "揃うか」という制約は、ここでは効かない。[/]"
        )

    console.print(
        f"{lookback}日リターンの分布: "
        + "、".join(f"{name} {value:+.1%}" for name, value in report.return_quantiles())
    )

    profile = report.turnover_profile()
    if profile:
        bar = Table(title="日次5分位ごとの売買代金の中央値（億円）")
        for name, _ in profile:
            bar.add_column(name, justify="right")
        bar.add_row(*[f"{value:,.1f}" for _, value in profile])
        console.print(bar)
        console.print(
            "[dim]リバーサルは小型・低流動性で強いことが知られている。端の分位だけ"
            "売買代金が小さければ、フィルタを通った後でも売買しにくい銘柄を並べて"
            "いることになる。[/]"
        )

    console.print()
    console.print("[dim]件数と分布のみ。リターンは計算していない。[/]")


@app.command(name="pead-explain")
def pead_explain(
    symbol: str = typer.Argument(..., help="JP code to walk through, e.g. 7203."),
    benchmark: str | None = typer.Option("1306", "--benchmark", help="Market series to net off."),
    period: str = typer.Option("is", "--period", help="is | oos | all."),
) -> None:
    """Print every number behind SYMBOL's events, so they can be checked by hand.

    Section 9's last unchecked item. Aggregates cannot reveal a mistake in how
    the aggregate is built, so this prints the dates and prices used and the
    arithmetic on top of them - the same ``reaction_position`` the aggregate
    uses, not a second implementation that could agree by accident.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    try:
        chosen = Period(period.strip().lower())
    except ValueError as exc:
        raise typer.BadParameter(f"--period must be is, oos or all; got {period!r}.") from exc

    database = Database()
    database.create_all()
    rows = explain_events(database, symbol, chosen, benchmark=benchmark or None)

    if not rows:
        console.print(f"[yellow]{symbol}: この期間にイベントなし。[/]")
        return

    console.print(f"[bold]{symbol}[/] ／ 期間 {chosen.value} ／ {len(rows)} 件")
    for row in rows:
        where = "場中" if row.intraday else "引け後"
        console.print(
            f"\n[cyan]開示 {row.disclosed_on} {row.disclosed_at}[/]"
            f"（引けは {row.session_close} なので[bold]{where}[/]）"
            f" → 反応日 R = [bold]{row.reaction_on}[/]"
        )
        console.print(
            f"  驚き（株価反応）: {row.reaction_close:,.2f} ÷ {row.prior_close:,.2f} − 1 = "
            f"[bold]{row.stock_surprise * 100:+.2f}%[/]"
        )
        if row.sue_surprise is not None:
            console.print(
                f"  驚き（会社予想）: 実績 {row.actual:,.0f} − 予想 {row.forecast:,.0f} "
                f"÷ |予想| = [bold]{row.sue_surprise * 100:+.2f}%[/]"
                f"  [dim]（{row.period_label} 短信。予想は開示日より前に公表済み）[/]"
            )
        elif row.period_label == "FY":
            console.print("  驚き（会社予想）: [yellow]直前の短信に通期予想が無く、出せない[/]")
        if row.bench_surprise is not None:
            console.print(
                f"         ベンチマーク {row.bench_reaction_close:,.2f} ÷ "
                f"{row.bench_prior_close:,.2f} − 1 = {row.bench_surprise * 100:+.2f}%"
                f" → 超過 [bold]{(row.stock_surprise - row.bench_surprise) * 100:+.2f}%[/]"
            )
        console.print(
            f"  60日 : {row.exit_on} 終値 {row.exit_close:,.2f} ÷ "
            f"{row.entry_on} 寄付 {row.entry_open:,.2f} − 1 = "
            f"[bold]{row.stock_forward * 100:+.2f}%[/]"
        )
        if row.bench_forward is not None:
            console.print(
                f"         ベンチマーク {row.bench_exit_close:,.2f} ÷ "
                f"{row.bench_entry_open:,.2f} − 1 = {row.bench_forward * 100:+.2f}%"
                f" → 超過 [bold]{(row.stock_forward - row.bench_forward) * 100:+.2f}%[/]"
            )
    console.print(
        "\n[dim]価格は分割調整後（adj_close/close を全四本値に掛けたもの）。"
        "集計と同じ関数で反応日を決めているので、ここの日付が集計で使われた"
        "日付そのものである。[/]"
    )


@app.command(name="accum-jp-explain")
def accum_jp_explain(
    on: str = typer.Argument(..., help="Judgment date to explain, YYYY-MM-DD."),
    min_turnover: float = typer.Option(
        DEFAULT_MIN_TURNOVER, "--min-turnover", help="Same floor as accum-jp-count. 0 disables."
    ),
) -> None:
    """Show why each symbol signalling on ON is, or is not, material-free.

    Reads three explanations for an unflagged earnings day apart - the
    disclosure history is not on file, the +/-1 session window is too tight,
    or the flag is not reading what is stored. They need opposite fixes.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    database = Database()
    database.create_all()

    judged = _require_date(on)
    context = market_volume_context(database, judged)
    console.print(
        f"市場全体 ({context.symbols_measured} 銘柄): 出来高倍率の中央値 "
        f"{context.median_multiple:.2f} ／ 2倍以上 {context.over_2x} 銘柄 ／ "
        f"5倍以上 {context.over_5x} 銘柄"
    )
    console.print(
        "[dim]条件②は自分の20日平均としか比べない。市場全体が膨らんだ日の5倍は、"
        "静かな日の5倍と同じ意味ではない。平常日と見比べること。[/]"
    )

    frame = explain_date(database, judged, min_turnover=min_turnover or None)
    if frame.empty:
        console.print(f"[yellow]{on} にシグナルはありません。[/]")
        return

    table = Table(title=f"{on} のシグナル {len(frame)} 件")
    for column in (
        "銘柄",
        "出来高倍率",
        "開示件数",
        "最古開示",
        "最新開示",
        "最近開示",
        "営業日差",
        "決算",
        "権利",
        "材料なし",
    ):
        table.add_column(column, justify="right")
    for row in frame.itertuples():
        table.add_row(
            row.symbol,
            f"{row.volume_multiple:.1f}",
            f"{row.disclosed}/{row.statements}",
            str(row.earliest_disclosed or "-"),
            str(row.latest_disclosed or "-"),
            str(row.nearest_disclosed or "-"),
            "-" if row.nearest_disclosed_days is None else str(row.nearest_disclosed_days),
            "✓" if row.earnings else "",
            "✓" if row.exrights else "",
            "✓" if row.material_free else "",
        )
    console.print(table)
    console.print(
        "[dim]営業日差が大きい/開示件数が1なら被覆の問題（統計を取り直す）。"
        "2〜3営業日なら窓が狭い。0〜1なのに決算欄が空なら突合の不具合。[/]"
    )


def _run_walk_forward(
    database: Database, scorer: WeightedScorer, preset: str, horizon: int, buckets: int
) -> None:
    """Test every feasible formation date and print all of them.

    One significant window is one draw. Reporting every window is what stops a
    single lucky period from being read as an edge - and what stops the reader
    from choosing the period after seeing the answers.
    """
    dates = formation_grid(database, horizon_days=horizon)
    if not dates:
        console.print(
            "[yellow]No formation date is feasible with the stored data.[/] "
            f"A {horizon}-bar horizon needs that many trading days of price "
            "history after the date, and disclosures before it."
        )
        raise typer.Exit(code=1)

    console.print(
        f"Testing [bold]{len(dates)}[/] formation date(s), quarterly, {horizon} bars each."
    )
    result = walk_forward(database, scorer, dates, horizon_days=horizon, buckets=buckets)
    if not result.runs:
        console.print("[red]No window could be tested.[/]")
        raise typer.Exit(code=1)

    table = Table(title=f"walk-forward: {preset}, {horizon} bars held")
    table.add_column("formation", style="cyan")
    table.add_column("n", justify="right")
    table.add_column("top", justify="right")
    table.add_column("universe", justify="right")
    table.add_column("excess", justify="right")
    table.add_column("t", justify="right")
    table.add_column("2σ", justify="center")
    for run in result.runs:
        top = run.top
        t_stat = run.spread_t_stat
        table.add_row(
            str(run.formation),
            str(run.scored),
            "-" if top is None else f"{top.mean_return:+.2%}",
            f"{run.universe_return:+.2%}",
            "-" if run.excess_return is None else f"{run.excess_return:+.2%}",
            "-" if t_stat is None else f"{t_stat:+.2f}",
            "[green]yes[/]" if run.is_significant else "[dim]no[/]",
        )
    console.print(table)

    median = result.median_t
    console.print(
        f"median t = {median:+.2f}" if median is not None else "median t = -",
        f"| monotonic in {result.monotonic}/{len(result.runs)} windows",
    )
    console.print(result.verdict)
    console.print(
        "[dim]Windows overlap - two formations a quarter apart share nine months "
        "of the same forward returns - so these are not independent confirmations. "
        "Read them as consistency, not as multiplied evidence. Survivorship bias "
        "is still unhandled in every window.[/]"
    )


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
        # A single window clearing the bar is where a score gets believed on
        # one observation. It happened here: this preset read t = +2.78 at one
        # date and +0.21 median across thirteen.
        console.print(
            "[yellow]One window is one draw.[/] Re-run with --walk-forward "
            "before treating this as an edge; a score that works in a single "
            "quarter is a regime bet."
        )
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
    table.add_column("Cov", justify="right")
    table.add_column("Factors")
    for result in results:
        factors = ", ".join(f"{k}={v:.2f}" for k, v in sorted(result.breakdown.items()))
        table.add_row(
            result.symbol,
            f"{result.score:.1f}",
            _format_coverage(result.coverage),
            factors or "[dim]-[/]",
        )
    console.print(table)
    if any(r.coverage < 0.8 for r in results):
        # Without this the command reproduces the ranking defect one symbol at
        # a time: a name measured on two factors shows the same "100.0" as one
        # measured on five, and nothing on screen says which is which.
        console.print(
            "[dim]Cov is how much of the factor weight could be measured. A high "
            "score at low coverage is an average over few factors, not a better "
            "company.[/]"
        )


@app.command()
def history() -> None:
    """Show how far back the stored prices reach, across the whole universe.

    The question after any backfill is not "did the command succeed" - it
    reports success either way - but "how many years actually arrived, and for
    how many names". A spot check on one symbol cannot answer the second half:
    a provider plan that caps history caps it per request, so a large name can
    look complete while most of the universe stops at the same wall.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    database = Database()
    database.create_all()
    with database.session() as session:
        spans = price_history_spans(session)

    if not spans:
        console.print("[yellow]No stored prices; run 'fetch' or 'bulk-fetch' first.[/]")
        return

    today = dt.date.today()
    years = sorted((today - earliest).days / 365.25 for _s, _m, earliest, _l, _b in spans)
    earliest_dates = [earliest for _s, _m, earliest, _l, _b in spans]

    table = Table(title=f"stored price history ({len(spans)} symbols)")
    table.add_column("percentile", style="cyan")
    table.add_column("years of history", justify="right")
    for label, value in (
        ("shortest", years[0]),
        ("25%", years[len(years) // 4]),
        ("median", years[len(years) // 2]),
        ("75%", years[3 * len(years) // 4]),
        ("longest", years[-1]),
    ):
        table.add_row(label, f"{value:.1f}")
    console.print(table)

    # A shared floor has two explanations and this command cannot tell them
    # apart, so it must not pick one. An earlier version announced "the
    # provider's history limit"; the first real run made that claim against a
    # floor of 2022-06-27, which was exactly 1,500 days before the day the
    # universe was first loaded with --lookback 1500. It was our own boundary,
    # and calling it the provider's would have closed the question wrongly.
    counts = Counter(earliest_dates)
    common_date, common_count = counts.most_common(1)[0]
    if common_count >= max(3, len(spans) // 10):
        console.print(
            f"[yellow]{common_count} of {len(spans)} symbols start on exactly {common_date}.[/]"
        )
        console.print(_shared_floor_reading(common_date, today))

    thin = sum(1 for value in years if value < 8)
    if thin:
        console.print(
            f"[dim]{thin} symbol(s) hold under 8 years. A calendar month gives one "
            "observation per year, so seasonality on those rests on very few "
            "points - see 'seasonality-scan'.[/]"
        )


#: How close to a whole number of years a shared floor must sit before it reads
#: as a rolling subscription window. A few days of slack covers weekends and
#: the drift between a run and the day it is read back.
_ROLLING_WINDOW_SLACK_DAYS = 10


def _shared_floor_reading(floor: dt.date, today: dt.date) -> str:
    """Explain a floor shared by most of the universe, without overclaiming.

    A shared floor has two causes that look identical in the data: a provider
    that will not serve earlier, or a ``--lookback`` that never asked for
    earlier. An earlier version simply asserted the first, against a floor that
    turned out to be exactly 1,500 days before the day the universe was loaded
    with ``--lookback 1500`` - our own boundary, announced as the provider's.

    One signature does separate them. A subscription window rolls, so it lands
    a whole number of years before *today*; a ``--lookback`` boundary lands an
    arbitrary number of days before whenever the load happened to run. That is
    evidence, not proof, so the check that settles it is still printed.
    """
    span_days = (today - floor).days
    years = span_days / 365.25
    if abs(span_days - round(years) * 365.25) <= _ROLLING_WINDOW_SLACK_DAYS and years >= 1:
        return (
            f"[dim]That is almost exactly {round(years)} year(s) before today, which is "
            "the shape of a rolling subscription window rather than a --lookback "
            "boundary. Reaching further back needs a different plan, not another "
            "fetch.[/]"
        )
    return (
        "[dim]That is either the provider's history limit or the boundary of "
        "whatever --lookback first loaded them. To tell them apart, backfill one "
        "symbol and watch the log:\n"
        "  [cyan]stock-ai bulk-fetch --what prices --symbols 7203 "
        "--lookback 5000 --backfill[/]\n"
        "  A 'plan covers X onward' warning means the provider set the floor; "
        "no such line means --lookback did.[/]"
    )

    thin = sum(1 for value in years if value < 8)
    if thin:
        console.print(
            f"[dim]{thin} symbol(s) hold under 8 years. A calendar month gives one "
            "observation per year, so seasonality on those rests on very few "
            "points - see 'seasonality-scan'.[/]"
        )


@app.command()
def seasonality(
    symbol: str = typer.Argument(..., help="Ticker to examine (must be fetched)."),
    min_years: int = typer.Option(
        DEFAULT_MIN_YEARS, help="Years a month needs before it is reported."
    ),
    split_year: int | None = typer.Option(
        None, help="Hold back this year onward and re-check the months found before it."
    ),
) -> None:
    """Show SYMBOL's month-by-month record, with the years behind each figure.

    ``n`` is the column to read first. A calendar month yields one observation
    per year, so four years of history means four numbers - and the mean of
    four returns is not a tendency, however clean the percentage looks.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    database = Database()
    database.create_all()
    prices = _load_prices(database, symbol)
    patterns = symbol_patterns(symbol, monthly_returns(prices), min_years)

    if not patterns:
        console.print(
            f"[yellow]No month has {min_years} years of history for {symbol}.[/] "
            "Fetch a longer series: [cyan]fetch "
            f"{symbol} --lookback 5000[/] backfills about 13 years."
        )
        return

    table = Table(title=f"{symbol} monthly record")
    table.add_column("month", style="cyan", no_wrap=True)
    table.add_column("n", justify="right")
    table.add_column("mean", justify="right")
    table.add_column("t", justify="right")
    table.add_column("up years", justify="right")
    for pattern in patterns:
        t_style = "yellow" if pattern.clears_threshold else "dim"
        table.add_row(
            month_name(pattern.month),
            str(pattern.years),
            f"{pattern.mean_return:+.2%}",
            f"[{t_style}]{pattern.t_stat:+.2f}[/]",
            f"{pattern.hit_rate:.0%}",
        )
    console.print(table)
    console.print(
        "[dim]One symbol tested across 12 months is 12 chances for noise to "
        "clear two sigma. Use 'seasonality-scan' to see how many a universe "
        "produces with the calendar shuffled out.[/]"
    )

    if split_year is not None:
        _print_holdouts(symbol, prices, split_year)


def _print_holdouts(symbol: str, prices: pd.DataFrame, split_year: int) -> None:
    """Re-check every month on years held back from the years that chose it."""
    results = [
        result
        for month in range(1, 13)
        if (result := holdout_check(symbol, prices, month, split_year)) is not None
    ]
    if not results:
        console.print(f"[yellow]Not enough history on both sides of {split_year}.[/]")
        return

    table = Table(title=f"{symbol}: found before {split_year}, measured from {split_year}")
    table.add_column("month", style="cyan", no_wrap=True)
    table.add_column("before: mean", justify="right")
    table.add_column("t", justify="right")
    table.add_column("after: mean", justify="right")
    table.add_column("n", justify="right")
    table.add_column("held", justify="right")
    for result in results:
        table.add_row(
            month_name(result.pattern.month),
            f"{result.pattern.mean_return:+.2%}",
            f"{result.pattern.t_stat:+.2f}",
            f"{result.holdout_mean:+.2%}",
            str(result.holdout_years),
            "[green]yes[/]" if result.repeated else "[red]no[/]",
        )
    console.print(table)
    repeated = sum(1 for r in results if r.repeated)
    console.print(
        f"[dim]{repeated} of {len(results)} months kept their sign. Roughly half "
        "would by chance alone - that is the number to beat, not zero.[/]"
    )


@app.command(name="seasonality-scan")
def seasonality_scan(
    month: int | None = typer.Option(None, help="Restrict the report to one month (1-12)."),
    min_years: int = typer.Option(DEFAULT_MIN_YEARS, help="Years a month needs to be tested."),
    permutations: int = typer.Option(20, help="Shuffled re-runs behind the null."),
    top: int = typer.Option(20, help="Show only the strongest N rows."),
) -> None:
    """Scan every stored symbol for calendar-month patterns.

    Testing a universe against twelve months is tens of thousands of
    hypotheses, so the count of "significant" months means nothing on its own.
    The same scan is therefore re-run with each symbol's month labels shuffled,
    which says how many hits appear when no seasonality exists by construction.
    Read the verdict before the table.
    """
    settings = get_settings()
    configure_logging(settings.log_level)
    if month is not None and not 1 <= month <= 12:
        raise typer.BadParameter("--month must be between 1 and 12.")

    database = Database()
    database.create_all()
    with database.session() as session:
        repo = PriceRepository(session)
        prices_by_symbol = {
            symbol: repo.get_prices(symbol) for symbol, _market in list_securities(session)
        }
    if not prices_by_symbol:
        console.print("[yellow]No stored prices; run 'fetch' or 'bulk-fetch' first.[/]")
        return

    # Measured: ~90s for 1,556 symbols at 20 permutations. A minute and a half
    # of silence reads as a hang, so say the number rather than "a moment".
    estimate = max(1, round(len(prices_by_symbol) * (1 + permutations) * 0.0027))
    console.print(
        f"Scanning {len(prices_by_symbol)} symbols, then {permutations} shuffled re-runs "
        f"to build the null. Expect roughly {estimate}s; there is no progress bar."
    )
    scan = scan_seasonality(
        prices_by_symbol, month=month, min_years=min_years, permutations=permutations
    )

    hits = scan.hits
    if hits:
        table = Table(title="strongest calendar-month records")
        table.add_column("symbol", style="cyan", no_wrap=True)
        table.add_column("month", no_wrap=True)
        table.add_column("n", justify="right")
        table.add_column("mean", justify="right")
        table.add_column("t", justify="right")
        table.add_column("up years", justify="right")
        for pattern in hits[:top]:
            table.add_row(
                pattern.symbol,
                month_name(pattern.month),
                str(pattern.years),
                f"{pattern.mean_return:+.2%}",
                f"{pattern.t_stat:+.2f}",
                f"{pattern.hit_rate:.0%}",
            )
        console.print(table)

    console.print(f"\n[bold]{scan.verdict}[/]")
    if scan.patterns and scan.patterns[0].years < 8:
        console.print(
            f"[yellow]Every row rests on about {scan.patterns[0].years} observations.[/] "
            "A calendar month happens once a year, so the stored history is the "
            "binding constraint here, not the method. Backfill more before "
            "reading anything into an individual name: [cyan]bulk-fetch --what "
            "prices --segment stored --lookback 5000[/]"
        )


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
    min_coverage: float = typer.Option(
        DEFAULT_MIN_COVERAGE,
        help="Least share of the factor weight a name must be measured on. 0 ranks everything.",
    ),
    top: int = typer.Option(20, help="Show only the top N rows."),
) -> None:
    """Rank JP and US securities together on one score.

    The composite score is built from unitless ratios, so it already compares
    across markets; only the market cap needs converting, which is what
    ``--fx``/``--base`` control.

    ``--preset tenbagger`` swaps in a small-cap growth factor set. It reads the
    statement series, so run ``statements`` first.

    That preset **was** backtested, which is why this no longer says to go and
    backtest it: walk-forward over 12 quarterly windows on the TSE universe
    cleared two sigma in 3, with a median ``t`` of +0.52. Use it to shortlist
    companies that are growing; do not read the ordering as a ranking worth
    allocating on.
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
            min_coverage=min_coverage,
        )
    except DataError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc

    if frame.empty:
        console.print("[yellow]No securities matched; run 'fetch' and 'fundamentals' first.[/]")
        if min_coverage > 0:
            console.print(
                f"[dim]Names measured on less than {min_coverage:.0%} of the factor weight "
                "were excluded. Run 'fundamentals' for the missing symbols, or pass "
                "--min-coverage 0 to rank them anyway.[/]"
            )
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


_META_COLUMNS = ("symbol", "market", "score", "coverage", "market_cap")

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


def _format_coverage(value: object) -> str:
    """Render how much of the factor weight a score was measured on.

    Shown in colour because it changes what the score next to it means: a 100
    scored on a third of the evidence is not the same claim as a 100 scored on
    all of it, and the number alone does not say which one it is.
    """
    if value is None or pd.isna(value):
        return "[dim]-[/]"
    fraction = float(value)
    style = "green" if fraction >= 0.8 else "yellow" if fraction >= 0.5 else "red"
    return f"[{style}]{fraction:.0%}[/]"


def _render_ranking(frame: pd.DataFrame, base: str) -> None:
    """Print a cross-market ranking, market cap stated in ``base``."""
    factor_columns = [c for c in frame.columns if c not in _META_COLUMNS]

    table = Table(title=f"cross-market ranking (market cap in {base})")
    table.add_column("symbol", style="cyan", no_wrap=True)
    table.add_column("mkt", no_wrap=True)
    table.add_column("score", justify="right", no_wrap=True)
    table.add_column("cov", justify="right", no_wrap=True)
    table.add_column(f"cap ({base})", justify="right", no_wrap=True)
    for column in factor_columns:
        table.add_column(_FACTOR_ABBREVIATIONS.get(column, column), justify="right")

    for row in frame.to_dict("records"):
        table.add_row(
            str(row["symbol"]),
            str(row["market"]),
            f"{row['score']:.1f}",
            _format_coverage(row.get("coverage")),
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
    symbols: list[str] | None = typer.Argument(None, help="Symbols to add; omit to list."),
    note: str | None = typer.Option(None, help="Why these names are watched."),
    importance: str = typer.Option("medium", help="Alert threshold: high | medium | low."),
    market: str = typer.Option("US", help="Listing market: US | JP."),
    remove: bool = typer.Option(False, "--remove", help="Drop the symbols from the watchlist."),
    symbols_file: Path | None = typer.Option(
        None, "--symbols-file", help="Text file of symbols, one per line (# comments allowed)."
    ),
) -> None:
    """Manage the watchlist that ``monitor`` checks.

    Takes any number of symbols. What that costs depends on the feed, and the
    two behave differently: EDINET matches a security code against the filings
    of the day, so a name that filed nothing costs nothing, while the news feed
    returns up to ``--limit`` items per symbol regardless. Adding two names to
    a three-name list took the next run's priced work from 1 disclosure to 20 -
    a backlog, not a new daily rate, because everything judged is remembered.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    database = Database()
    database.create_all()

    targets = list(symbols or [])
    if symbols_file is not None:
        targets.extend(_symbols_from_file(symbols_file))

    if not targets:
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

    threshold = _parse_importance(importance)
    with database.session() as session:
        repo = WatchlistRepository(session)
        if remove:
            dropped = [sym for sym in targets if repo.remove(sym)]
            missing = [sym for sym in targets if sym not in dropped]
            if dropped:
                console.print(f"Removed [cyan]{', '.join(dropped)}[/].")
            if missing:
                console.print(f"[yellow]Not watched: {', '.join(missing)}.[/]")
            return
        for sym in targets:
            repo.add(sym, note=note, min_importance=threshold, market=market.upper())

    console.print(
        f"Watching [cyan]{len(targets)}[/] name(s) at {threshold.value} and above: "
        f"{', '.join(targets)}"
    )
    if len(targets) > 1:
        console.print(
            "[dim]Price the next check before paying for it: 'stock-ai "
            "ai-cost'. Adding names does raise the cost of the *next* run: "
            "the news feed returns up to --limit items per symbol whether or "
            "not anything was filed, so each new name arrives with a backlog. "
            "After that first pass only genuinely new items are judged.[/]"
        )


def _parse_importance(value: str) -> Importance:
    """Parse an importance threshold from the CLI."""
    try:
        return Importance(value.strip().lower())
    except ValueError as exc:
        raise typer.BadParameter(
            f"importance must be high, medium, or low; got {value!r}."
        ) from exc


def _print_type_breakdown(
    rows: list[tuple[str, str, str, int]],
    by_code: dict[str, Counter[str]],
) -> None:
    """Show what each proposed name's filing count is actually made of.

    A count on its own cannot distinguish three very different names: one that
    reports substantively, one whose month is mostly 訂正 of earlier filings,
    and one whose number comes from 大量保有報告書. Only the first is worth a
    watchlist slot, and the ranking cannot tell them apart - so the breakdown
    is what turns the number into something a reader can act on.
    """
    table = Table(title="what those filings are")
    table.add_column("symbol", style="cyan")
    table.add_column("name")
    table.add_column("document type")
    table.add_column("n", justify="right")
    for index, (symbol, name, _sector, _filings) in enumerate(rows):
        types = by_code.get(normalize_sec_code(symbol) or "", Counter())
        if index:
            table.add_section()
        first = True
        for doc_type, count in types.most_common():
            table.add_row(
                symbol if first else "",
                name if first else "",
                doc_type_label(doc_type),
                str(count),
            )
            first = False
    console.print(table)
    console.print(
        "[dim]大量保有報告書 and 訂正 rows are filings about a company or "
        "repairs to earlier ones - they inflate a count without adding much "
        "a reader would act on.[/]"
    )


@app.command()
def news(
    symbol: str = typer.Argument(..., help="Symbol to pull headlines for."),
    limit: int = typer.Option(5, help="How many headlines to show."),
) -> None:
    """Show what the news feed returns for one symbol. No model, no cost.

    A feed that answers for the wrong company is invisible in an alert: the
    header carries the symbol you asked for, the summary faithfully renders
    whatever arrived, and nothing raises. Watching ヒューリック as the bare code
    ``3003`` delivered Saudi small-cap articles for exactly that reason -
    Tadawul numbers its listings in four digits too.

    This is the check that costs nothing: read the headlines and see whose
    company they are about.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    queried = to_yahoo_symbol(symbol)
    if queried != symbol:
        console.print(f"Querying [cyan]{queried}[/] (Yahoo's form of [cyan]{symbol}[/]).")

    items = YFinanceNewsSource().fetch(symbol, limit=limit)
    if not items:
        console.print(
            f"[yellow]No headlines for {symbol}.[/] That is either a quiet name "
            "or a symbol this feed does not know - the feed cannot tell you which."
        )
        raise typer.Exit(code=1)

    for index, item in enumerate(items, start=1):
        console.print(f"\n[bold]{index}. {item.title}[/]")
        if item.summary:
            console.print(f"   [dim]{_compact_text(item.summary, 200)}[/]")
    console.print("\n[dim]Check the company these are about, not just that they arrived.[/]")


def _compact_text(text: str, width: int) -> str:
    """Collapse whitespace and cut to ``width`` characters."""
    flat = " ".join(text.split())
    return flat if len(flat) <= width else f"{flat[:width]}..."


@app.command()
def forget(
    symbol: str | None = typer.Argument(None, help="Only forget this symbol. Omit for all."),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation."),
) -> None:
    """Drop the record of which disclosures have already been reported.

    The seen record is what stops a daily run re-delivering yesterday's news,
    and it is also what makes a bad pass permanent: a run that recorded
    verdicts it should not have - a stub provider, a misconfigured model -
    leaves those filings invisible to every later run, because a seen item is
    never fetched again. Forgetting them is the only way back.

    The next run then re-judges everything in its window, and bills for it.
    Price it first with 'ai-cost'.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    database = Database()
    database.create_all()

    with database.session() as session:
        pending = WatchlistRepository(session).count_seen(symbol)

    scope = f"[bold]{symbol.upper()}[/]" if symbol else "the whole watchlist"
    if not pending:
        console.print(f"Nothing on record for {scope}; nothing to forget.")
        return

    console.print(
        f"This forgets [bold]{pending}[/] reported disclosure(s) for {scope}. "
        "The next run re-judges them and bills for it."
    )
    if not yes and not typer.confirm("Continue?"):
        console.print("Left as it was.")
        raise typer.Exit(code=1)

    with database.session() as session:
        removed = WatchlistRepository(session).forget_seen(symbol)
    console.print(
        f"Forgot [bold]{removed}[/] record(s). Price the next run before "
        "paying for it: [cyan]stock-ai ai-cost[/]"
    )


@app.command(name="watch-suggest")
def watch_suggest(
    lookback_days: int = typer.Option(30, help="Days of EDINET filings to count."),
    top: int = typer.Option(20, help="How many names to propose."),
    per_sector: int = typer.Option(2, help="Cap per sector, so the list spreads. 0 = no cap."),
    add: bool = typer.Option(False, "--add", help="Add the proposed names to the watchlist."),
    importance: str = typer.Option("medium", help="Alert threshold for names added."),
    by_type: bool = typer.Option(
        False, "--by-type", help="Break each name's count down by document type."
    ),
) -> None:
    """Propose watchlist names from which companies actually file.

    This is not a view on which companies are worth owning, and nothing here
    should be read as one. A watchlist decides *what you hear about*, and on
    that question the data has something to say: a name that never files
    produces no EDINET alert however long you watch it, while still costing a
    news-feed pull on every run. So the ranking is filings made, not merit.

    Only names already in your database are proposed - watching a company
    whose prices and financials you do not hold gives an alert with nothing to
    read it against.

    The per-sector cap exists because filing frequency clusters: banks and
    real-estate trusts file constantly, and an uncapped list is mostly those.
    Spreading it is the difference between hearing about the market and
    hearing about one corner of it.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    database = Database()
    database.create_all()

    source = EdinetDisclosureSource(api_key=settings.edinet_api_key, lookback_days=lookback_days)
    console.print(f"Counting EDINET filings over the last {lookback_days} day(s)...")
    by_code = source.filing_type_counts()
    counts = Counter({code: sum(types.values()) for code, types in by_code.items()})
    failed = len(source.failed_days)
    if failed:
        # Widening the window is the natural response to an empty result, and
        # it is exactly the wrong one when the requests are not arriving: it
        # buys more failures and never reaches the cause.
        console.print(
            f"[yellow]{failed} of {lookback_days} day(s) could not be "
            "fetched[/] - those days are counted as empty. Run 'edinet-check' "
            "to see why before trusting the ranking below."
        )
    if not counts:
        if failed == lookback_days:
            console.print(
                "[red]Every day failed, so this is not a quiet window - "
                "nothing arrived at all.[/] A longer --lookback-days cannot "
                "help. Run 'edinet-check' to test the key and the connection."
            )
        else:
            console.print(
                "[yellow]No filings found.[/] With a valid key that means a "
                "very quiet window - try a longer --lookback-days. Run "
                "'edinet-check' if you suspect the key."
            )
        raise typer.Exit(code=1)

    with database.session() as session:
        watched = {e.symbol for e in WatchlistRepository(session).list_entries()}
        stored = [sym for sym, market in list_securities(session) if market.upper() == "JP"]
        profiles = {sym: get_profile(session, sym) for sym in stored}

    rows: list[tuple[str, str, str, int]] = []
    for symbol in stored:
        code = normalize_sec_code(symbol)
        if code is None or symbol in watched or code in watched:
            continue
        filings = counts.get(code, 0)
        if filings == 0:
            continue  # watching it would never produce an EDINET alert
        profile = profiles.get(symbol)
        rows.append(
            (
                symbol,
                (profile.name if profile else "") or "-",
                (profile.sector if profile else "") or "-",
                filings,
            )
        )

    rows.sort(key=lambda row: (-row[3], row[0]))
    if per_sector > 0:
        seen_sector: Counter[str] = Counter()
        capped = []
        for row in rows:
            if seen_sector[row[2]] >= per_sector:
                continue
            seen_sector[row[2]] += 1
            capped.append(row)
        rows = capped
    rows = rows[:top]

    if not rows:
        console.print(
            "[yellow]Nothing to propose.[/] Every stored JP name that filed is "
            "already watched, or none of them filed in this window."
        )
        raise typer.Exit(code=1)

    table = Table(title=f"names that actually file (last {lookback_days} days)")
    table.add_column("symbol", style="cyan")
    table.add_column("name")
    table.add_column("sector")
    table.add_column("filings", justify="right")
    for symbol, name, sector, filings in rows:
        table.add_row(symbol, name, sector, str(filings))
    console.print(table)
    console.print(
        "[dim]Ranked by filings made, not by merit - this says which names will "
        "produce alerts, and nothing about whether they are worth owning.[/]"
    )

    if by_type:
        _print_type_breakdown(rows, by_code)

    symbols = [row[0] for row in rows]
    if not add:
        console.print(
            f"\nAdd them with:\n  [cyan]stock-ai watch {' '.join(symbols)} --market JP[/]\n"
            "Price the first run afterwards - the news feed hands each new name "
            "a backlog: [cyan]stock-ai ai-cost[/]"
        )
        return

    threshold = _parse_importance(importance)
    with database.session() as session:
        repo = WatchlistRepository(session)
        for symbol in symbols:
            repo.add(symbol, note=None, min_importance=threshold, market="JP")
    console.print(f"\nAdded [bold]{len(symbols)}[/] name(s) at {threshold.value} and above.")
    console.print(
        "[yellow]The next run has a backlog[/] - the news feed returns up to "
        "--limit items per new name. Price it before paying for it: "
        "[cyan]stock-ai ai-cost[/]"
    )


@app.command()
def monitor(
    provider: str | None = typer.Option(
        None, help="AI provider: dummy|claude|openai|gemini. Default: AI_PROVIDER."
    ),
    channel: str | None = typer.Option(None, help="Send alerts to console|discord|telegram|line."),
    limit: int = typer.Option(10, help="Disclosures pulled per watched symbol."),
    feed: str = typer.Option(
        "all",
        "--feed",
        "--source",
        help="Disclosure feed: all | edinet (JP filings) | news (yfinance).",
    ),
    lookback_days: int = typer.Option(7, help="Days of EDINET filings to scan."),
    max_cost: float | None = typer.Option(
        None,
        "--max-cost",
        help="Refuse to run if the priced worst case exceeds this many USD.",
    ),
) -> None:
    """Check the watchlist for disclosures worth reporting.

    Each new item is rated and summarized by the AI provider, and anything at
    or above a name's threshold becomes an alert. Reported items are recorded,
    so running this daily does not re-deliver the same news.

    ``--max-cost`` is for the unattended case. A scheduled run bills an account
    every night with nobody watching, and the number of disclosures filed on a
    given day is not something this system chooses. The check costs nothing: it
    counts tokens, which is a separate unbilled endpoint.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    database = Database()
    database.create_all()
    notifier = get_notifier(channel, settings) if channel else None
    ai = get_ai_provider(provider or settings.ai_provider, settings)
    monitor_service = WatchMonitor(
        database,
        source=_disclosure_source(feed, settings, lookback_days),
        provider=ai,
        notifier=notifier,
    )

    if max_cost is not None and not _within_budget(monitor_service, ai, max_cost, limit):
        raise typer.Exit(code=1)

    try:
        result = monitor_service.run(limit=limit, notify=notifier is not None)
    finally:
        _report_spend(ai)
    console.print(
        f"Checked [bold]{result.checked}[/] new disclosure(s), "
        f"skipped {result.skipped} already seen."
    )
    if result.unjudged:
        console.print(
            f"[yellow]{result.unjudged} could not be classified[/] "
            "(AI provider failed); they stay unseen and are retried next run.\n"
            "  Retrying is right for a network blip and costs money every "
            "night if the cause is not one. If this count does not fall to "
            "zero, read the warning above it - the failure names its own "
            "cause - rather than letting a nightly job pay for the same "
            "refusal indefinitely."
        )
    if result.alerts:
        console.print(result.format())
    else:
        console.print("[dim]Nothing above threshold.[/]")
    if result.delivery_error:
        # Printed after the alerts, not instead of them: the items are already
        # marked seen, so this screen is the only place they still exist.
        console.print(
            f"\n[red]The alerts above were not delivered:[/] {result.delivery_error}\n"
            "  They are recorded as seen, so the next run will not repeat them. "
            "'stock-ai forget' puts them back if you need them re-sent."
        )
        raise typer.Exit(code=1)


@app.command(name="edinet-check")
def edinet_check(
    date: str | None = typer.Option(None, help="Day to request, YYYY-MM-DD. Defaults to today."),
) -> None:
    """Diagnose an EDINET key by trying every way of sending it.

    A refused EDINET request says ``invalid subscription key`` whether the key
    is wrong or merely in a place the gateway does not read, so one failure
    cannot tell those apart - and a successful browser test only proves the
    query-parameter form. This sends the same key four ways and reports each,
    which turns "401" into a specific next step.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    api_key = settings.edinet_api_key
    if api_key is None:
        console.print("[red]EDINET_API_KEY is not set.[/]")
        console.print("Set it with: [cyan]powershell -File scripts/set-key.ps1 EDINET_API_KEY[/]")
        raise typer.Exit(1)

    day = _parse_date(date) or dt.date.today()
    console.print(f"Key in .env: {_secret_summary(api_key)}")
    console.print(f"Requesting [cyan]{day.isoformat()}[/] four different ways.\n")

    results = probe_key_placements(api_key, day)

    table = Table(title=f"EDINET key placements ({day.isoformat()})")
    table.add_column("Placement", style="cyan")
    table.add_column("HTTP", justify="right")
    table.add_column("API status", justify="right")
    table.add_column("Documents", justify="right")
    table.add_column("Message")
    for result in results:
        mark = "[green]OK[/]" if result.accepted else "[red]NG[/]"
        table.add_row(
            f"{mark} {result.placement}",
            str(result.http_status if result.http_status is not None else "-"),
            result.api_status,
            str(result.documents) if result.documents is not None else "-",
            result.message,
        )
    console.print(table)
    _print_edinet_field_report(api_key, day, results)
    _print_edinet_verdict(results)


def _print_edinet_field_report(
    api_key: SecretStr, day: dt.date, results: list[ProbeResult]
) -> None:
    """Show which fields a real filing record carries, and which we read.

    The fields fed to the importance rating were picked from the published API
    spec, and a spec is not a response. This says which of them a live record
    actually has - and, as importantly, which ones EDINET returns that this
    project is throwing away.

    When the requested day had no filings - a quiet early morning, a weekend, a
    holiday - the probe's own answer carries no record to sample, and earlier
    this printed nothing at all. It now looks back for a day that did.
    """
    fields = next((r.sample_fields for r in results if r.sample_fields), ())
    sampled_on = day
    if not fields:
        if not any(r.accepted for r in results):
            return  # the key is the problem; the verdict below covers it
        console.print(
            f"\n[dim]{day.isoformat()} had no filings, so it carries no record "
            "to inspect. Looking back for a day that did...[/]"
        )
        found = sample_filing_fields(api_key, day)
        if found is None:
            console.print("[yellow]No filings in the last 10 days either.[/]")
            return
        sampled_on, fields = found

    used = {name for name, _label in EDINET_EXTRA_BODY_FIELDS}
    used |= set(EDINET_SUBJECT_CODE_FIELDS)
    used |= {"edinetCode", "filerName", "secCode", "submitDateTime"}
    present = sorted(used & set(fields))
    absent = sorted(used - set(fields))
    unread = sorted(set(fields) - used - {"docID", "docDescription", "docTypeCode"})

    console.print(
        f"\n[bold]Fields on a live filing record[/] "
        f"({len(fields)} in total, sampled from {sampled_on.isoformat()})"
    )
    if present:
        console.print(f"  [green]read, and present:[/] {', '.join(present)}")
    if absent:
        console.print(f"  [yellow]read, but this record does not have them:[/] {', '.join(absent)}")
    console.print(f"  [dim]returned but not used: {', '.join(unread)}[/]")
    console.print(
        "[dim]An EDINET alert is rated from this index, not from the filing "
        "itself - the document is served separately as XBRL and is not opened. "
        "If something here would sharpen a rating, it is worth adding.[/]"
    )


def _print_edinet_verdict(results: list[ProbeResult]) -> None:
    """Say what the probe means, so the table does not need interpreting."""
    working = [r for r in results if r.accepted]
    current = next((r for r in results if r.placement == CURRENT_PLACEMENT), None)

    if all(r.http_status is None for r in results):
        # A request that never arrived says nothing about the key. Reporting
        # this as "your key is wrong" is the misdiagnosis this command exists
        # to prevent, so it has to be ruled out before anything else.
        console.print(
            "\n[yellow]No request reached EDINET at all.[/] Every attempt failed "
            "in transport, so this says nothing about your key - it is a network, "
            "proxy, or firewall problem. Check your connection and try again."
        )
        return

    if not working:
        console.print(
            "\n[red]Every placement was refused.[/] The key itself is the "
            "problem, not how it is sent. Re-enter it with "
            "[cyan]powershell -File scripts/set-key.ps1 EDINET_API_KEY[/] and "
            "check the fingerprint above changes - if it does not, .env was not "
            "updated and the old key is still in place."
        )
        return

    if current is not None and current.accepted:
        console.print(
            "\n[green]The key works and the client already sends it correctly.[/] "
            "Run the watchlist monitor - any remaining empty result is a quiet "
            "week, not an authentication failure."
        )
        return

    names = ", ".join(r.placement for r in working)
    console.print(
        f"\n[yellow]The key is valid but the client sends it the wrong way.[/] "
        f"Accepted: {names}. This is a bug in the client, not in your key - "
        "report this table and it will be fixed."
    )


@app.command(name="moomoo-check")
def moomoo_check(
    host: str | None = typer.Option(None, help="OpenD host. Defaults to MOOMOO_OPEND_HOST."),
    port: int | None = typer.Option(None, help="OpenD port. Defaults to MOOMOO_OPEND_PORT."),
    env: str | None = typer.Option(None, help="SIMULATE or REAL. Defaults to MOOMOO_TRD_ENV."),
    market: str | None = typer.Option(None, help="JP or US. Defaults to MOOMOO_TRD_MARKET."),
    firm: str | None = typer.Option(None, help="Account entity, e.g. FUTUJP (moomoo証券)."),
    unlock: bool = typer.Option(
        False, help="Also test the trading PIN in MOOMOO_TRADE_PASSWORD (REAL only)."
    ),
    show_assets: bool = typer.Option(
        False, help="Print the account balances instead of only confirming they came back."
    ),
) -> None:
    """Check that moomoo OpenD is installed, logged in, and reaching your account.

    moomoo has no API key. Authentication is a local gateway - OpenD - that you
    log into with your moomoo securities account, and every failure along that
    chain reaches Python as the same symptom: a command that never returns.

    This walks the chain in order and stops at the first break, so the answer is
    "OpenD is not running" or "the entity is wrong for this account" rather than
    a hang. It never places an order, and it re-locks the live account
    immediately after testing the PIN.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    try:
        config = MoomooConfig(
            host=host or settings.moomoo_opend_host,
            port=port or settings.moomoo_opend_port,
            security_firm=(firm or settings.moomoo_security_firm).upper(),
            trd_market=(market or settings.moomoo_trd_market).upper(),
            trd_env=(env or settings.moomoo_trd_env).upper(),
        )
    except BrokerError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(2) from exc

    password: str | None = None
    if unlock:
        if not config.is_real:
            console.print(
                "[yellow]--unlock only applies to the REAL account; "
                f"this run is against {config.trd_env}. Ignoring it.[/]"
            )
        elif settings.moomoo_trade_password is None:
            console.print(
                "[yellow]--unlock was given but MOOMOO_TRADE_PASSWORD is not set in .env.[/]\n"
                "Set it with: [cyan]powershell -File scripts/set-key.ps1 "
                "MOOMOO_TRADE_PASSWORD[/]"
            )
        else:
            password = settings.moomoo_trade_password.get_secret_value()

    console.print(
        f"OpenD at [cyan]{config.host}:{config.port}[/] - "
        f"{config.security_firm} / {config.trd_market} / "
        f"[bold]{config.trd_env}[/]"
    )
    if config.is_real:
        console.print("[yellow]This is the live-money account.[/] No order is ever placed here.")
    console.print()

    diagnosis = moomoo_diagnose(config, unlock_password=password)

    table = Table(title="moomoo OpenD check")
    table.add_column("Step", style="cyan")
    table.add_column("Result")
    table.add_column("Detail")
    marks = {
        StageStatus.OK: "[green]OK[/]",
        StageStatus.FAILED: "[red]NG[/]",
        StageStatus.SKIPPED: "[dim]--[/]",
    }
    for stage in diagnosis.stages:
        table.add_row(stage.name, marks[stage.status], stage.detail)
    console.print(table)

    _print_moomoo_accounts(diagnosis, config, show_assets=show_assets)
    _print_moomoo_verdict(diagnosis, config)

    raise typer.Exit(0 if diagnosis.ok else 1)


def _print_moomoo_accounts(
    diagnosis: MoomooDiagnosis, config: MoomooConfig, *, show_assets: bool
) -> None:
    """List every account OpenD showed us, not only the one that was asked for.

    The most confusing moomoo failure is an empty account list from a login that
    worked: nothing is wrong with the credentials, the entity or the market
    filter simply does not match the account. Printing what *was* found next to
    what was asked for turns that into a one-line diagnosis.
    """
    if not diagnosis.accounts:
        return

    table = Table(title=f"Accounts visible through OpenD ({config.security_firm})")
    table.add_column("Account", style="cyan")
    table.add_column("Env")
    table.add_column("Type")
    table.add_column("Markets")
    table.add_column("Status")
    for account in diagnosis.accounts:
        wanted = account.trd_env == config.trd_env
        table.add_row(
            f"{'>' if wanted else ' '} {account.masked_id}",
            f"[bold]{account.trd_env}[/]" if wanted else account.trd_env,
            account.acc_type,
            "/".join(account.markets) or "-",
            account.status,
        )
    console.print(table)
    console.print(
        "[dim]Account numbers are masked to the last four digits: this report is "
        "written to a file people paste when asking for help.[/]"
    )

    summary = diagnosis.account_summary
    if not summary:
        return
    if show_assets:
        currency = summary.get("currency", config.currency)
        console.print(
            f"\nTotal assets: [bold]{summary.get('total_assets')}[/] {currency}  "
            f"(cash {summary.get('cash')}, positions {summary.get('market_val')})"
        )
    else:
        console.print(
            "\n[dim]Balances came back and were not printed. "
            "Add --show-assets to see the numbers.[/]"
        )


def _print_moomoo_verdict(diagnosis: MoomooDiagnosis, config: MoomooConfig) -> None:
    """Say what the table means, so it does not need interpreting."""
    failure = diagnosis.first_failure
    if failure is None:
        # Hints on links that *held* still matter: "quotes are up but trading is
        # not" passes this check and breaks the next thing anyone does.
        notes = [s for s in diagnosis.stages if s.hint]
        console.print(
            f"\n[green]OpenD is up and your {config.trd_env} account answers through it.[/] "
            "Authentication is done - nothing else is needed to read this account."
        )
        for stage in notes:
            console.print(f"[dim]{stage.name}: {stage.detail}. {stage.hint}[/]")
        return

    console.print(f"\n[red]Stopped at: {failure.name}.[/] {failure.detail}")
    if failure.hint:
        console.print(failure.hint)
    console.print(
        "[dim]Later steps were not attempted: each one needs the previous one, "
        "so running them would only report the same break again.[/]"
    )


@app.command(name="moomoo-flow")
def moomoo_flow(
    symbol: str = typer.Argument(..., help="Ticker in any form: 9842, JP.9842, 9842.T, AAPL."),
    start: str | None = typer.Option(None, help="YYYY-MM-DD. Ignored for --period intraday."),
    end: str | None = typer.Option(None, help="YYYY-MM-DD. Ignored for --period intraday."),
    period: str = typer.Option("day", help="intraday | day | week | month."),
    host: str | None = typer.Option(None, help="OpenD host. Defaults to MOOMOO_OPEND_HOST."),
    port: int | None = typer.Option(None, help="OpenD port. Defaults to MOOMOO_OPEND_PORT."),
    firm: str | None = typer.Option(None, help="Account entity, e.g. FUTUJP (moomoo証券)."),
) -> None:
    """Show capital in/out flow for SYMBOL, read through moomoo OpenD.

    Market data only - no account is touched and no order is placed. OpenD must
    be running and logged in; ``moomoo-check`` says so in one line when it is not.

    The symbol is converted to moomoo's own market-first form (``JP.9842``), so
    the codes used everywhere else in this project work here unchanged.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    try:
        config = MoomooConfig(
            host=host or settings.moomoo_opend_host,
            port=port or settings.moomoo_opend_port,
            security_firm=(firm or settings.moomoo_security_firm).upper(),
            trd_market=settings.moomoo_trd_market.upper(),
            trd_env=settings.moomoo_trd_env.upper(),
        )
    except BrokerError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(2) from exc

    period_type = period.upper()
    # A range asked for in whole days against an intraday feed is not a smaller
    # request, it is a different one - and it would come back as today's minutes
    # under the dates the user typed. Say so rather than answering the wrong
    # question quietly.
    if period_type == "INTRADAY" and (start or end):
        console.print("[yellow]--period intraday covers today only; --start/--end are ignored.[/]")
        start = end = None
    elif period_type != "INTRADAY" and not start and not end:
        end_date = dt.date.today()
        start_date = end_date - dt.timedelta(days=30)
        start, end = start_date.isoformat(), end_date.isoformat()
        console.print(f"[dim]No range given; using the last 30 days ({start} to {end}).[/]")

    code = to_moomoo_code(symbol)
    console.print(f"Capital flow for [cyan]{code}[/] ({period_type}) via OpenD\n")

    try:
        frame = moomoo_capital_flow(config, symbol, period_type=period_type, start=start, end=end)
    except BrokerError as exc:
        console.print(f"[red]{exc}[/]")
        _print_quote_entitlement_note(code)
        raise typer.Exit(1) from exc

    if frame is None or frame.empty:
        console.print(
            "[yellow]No rows.[/] The request was accepted and returned nothing - "
            "a closed-market range, or a symbol this account has no data "
            "permission for. Both look the same here, so try a range you know "
            "had trading before assuming the permission."
        )
        _print_quote_entitlement_note(code)
        raise typer.Exit(1)

    _render_capital_flow(frame, code, period_type)


def _print_quote_entitlement_note(code: str) -> None:
    """Say what a refused or empty quote request usually means, and how to tell.

    The trap here is that ``moomoo-check`` passing makes the connection feel
    proven, so the next refusal reads as a bug in this code. It usually is not:
    moomoo grants *quote* access per market, and that grant is separate from
    the account's trading permissions - a market you are cleared to trade is
    not necessarily one the API serves quotes for, and the published table has
    said "not currently available" for whole markets.

    Neither this note nor anything else here hard-codes which markets those
    are. That list changes, and a stale copy of it would confidently contradict
    the gateway. The gateway's own message above is the current answer; this
    only says how to read it, and names the one test that separates an
    account-wide problem from a per-market one.
    """
    market = code.split(".")[0]
    console.print(
        f"\n[dim]A refusal or an empty answer here is usually quote entitlement, "
        f"not this code. moomoo grants quote access per market, separately from "
        f"what the account may trade, and it has listed whole markets as not "
        f"available through the API - so passing moomoo-check does not imply "
        f"{market} quotes.\n"
        f"To tell an account-wide problem from a {market}-only one, try a US "
        f"symbol: [cyan]uv run stock-ai moomoo-flow AAPL[/]. If that works, the "
        f"account is fine and {market} quotes are the missing piece; check the "
        f"market table in docs/MOOMOO_OPEND.md and moomoo's own quote-permission "
        f"page.[/]"
    )


def _render_capital_flow(frame: pd.DataFrame, code: str, period_type: str) -> None:
    """Print the flow table, dropping the columns this period does not fill.

    The API documents two fields as period-dependent: ``main_in_flow`` is valid
    only for the historical periods (day/week/month) and ``last_valid_time``
    only for intraday. A column that is present but meaningless is worse than an
    absent one - it reads as a real zero - so ``Main`` is only shown where the
    API says it means something.
    """
    intraday = period_type == "INTRADAY"
    table = Table(title=f"Capital flow: {code} (net, in the listing currency)")
    table.add_column("Time" if intraday else "Date", style="cyan", no_wrap=True)
    table.add_column("Net", justify="right")
    if not intraday:
        table.add_column("Main", justify="right")
    table.add_column("Super", justify="right")
    table.add_column("Big", justify="right")
    table.add_column("Mid", justify="right")
    table.add_column("Small", justify="right")

    for row in frame.to_dict("records"):
        # The timestamp is 'yyyy-MM-dd HH:mm:ss' whatever the period, so a daily
        # row would otherwise carry a 00:00:00 that means nothing.
        when = str(row.get("capital_flow_item_time", ""))
        cells = [when if intraday else when.split(" ")[0], _signed(row.get("in_flow"))]
        if not intraday:
            cells.append(_signed(row.get("main_in_flow")))
        cells += [
            _signed(row.get("super_in_flow")),
            _signed(row.get("big_in_flow")),
            _signed(row.get("mid_in_flow")),
            _signed(row.get("sml_in_flow")),
        ]
        table.add_row(*cells)
    console.print(table)

    note = "[dim]Net (in_flow) is the overall net figure and the four order-size bands sum to it."
    if not intraday:
        # Observed, not specified: across 22 live daily rows for US.AAPL, the
        # bands summed to Net and Super+Big equalled Main on every one. The API
        # documents main_in_flow only as "the large-order net inflow", so this
        # is a reading of the data rather than a guarantee - which is why it is
        # said as such, and why Main stays a column of its own.
        note += (
            " Main (main_in_flow) is reported separately; on live data it has "
            "matched Super+Big exactly, so read it as a subtotal of those two "
            "rather than a fifth band."
        )
    console.print(note + " Regular session only: no pre- or post-market.[/]")


def _signed(value: object) -> str:
    """Format a flow figure, coloured by direction so a wall of numbers reads."""
    if not isinstance(value, int | float) or isinstance(value, bool):
        return "-"
    if pd.isna(value):
        return "-"
    text = _compact(float(value))
    if value > 0:
        return f"[green]+{text}[/]"
    if value < 0:
        return f"[red]{text}[/]"
    return text


@app.command()
def accumulation(
    symbols: list[str] | None = typer.Argument(
        None, help="Screen only these symbols, e.g. AAPL MSFT NVDA. Omit for the whole market."
    ),
    symbols_file: Path | None = typer.Option(
        None,
        "--symbols-file",
        help="Read symbols from a file, one per line (# comments allowed).",
    ),
    limit: int = typer.Option(10, help="Rows in the phase-1 table."),
    deep: int = typer.Option(5, help="How many of them get the phase-2/3 deep dive."),
    period: str = typer.Option("1y", help="History to download. 1y is what the 52-week low needs."),
    min_market_cap: float = typer.Option(300_000_000.0, help="Phase-1 floor, USD."),
    min_volume: float = typer.Option(500_000.0, help="20-day average volume floor, shares."),
    min_price: float = typer.Option(5.0, help="Price floor, USD."),
    volume_multiple: float = typer.Option(5.0, help="Latest volume over the prior 20-day average."),
    max_above_low: float = typer.Option(0.15, help="Ceiling on distance above the 52-week low."),
    max_range: float = typer.Option(0.10, help="Ceiling on the 20-day high-low range."),
    host: str | None = typer.Option(None, help="OpenD host. Defaults to MOOMOO_OPEND_HOST."),
    port: int | None = typer.Option(None, help="OpenD port. Defaults to MOOMOO_OPEND_PORT."),
    firm: str | None = typer.Option(None, help="Account entity, e.g. FUTUJP."),
    channel: str | None = typer.Option(
        None, help="Also send a summary: console | discord | telegram | line."
    ),
    heartbeat: bool = typer.Option(
        False,
        "--heartbeat",
        help="Notify even when nothing passed, so a quiet day and a dead job differ.",
    ),
) -> None:
    """Screen US equities for institutional accumulation, in three phases.

    Phase 1 is a price and volume pass over the whole market; phase 2 adds
    funding flow through moomoo OpenD, the short side, and the chart; phase 3
    tests whether the base has broken.

    Metrics no reachable source provides - dark-pool share, block prints,
    borrow fees - are printed as 取得不可 with the reason. Nothing here is
    estimated to fill a gap, and nothing here is investment advice.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    try:
        config = MoomooConfig(
            host=host or settings.moomoo_opend_host,
            port=port or settings.moomoo_opend_port,
            security_firm=(firm or settings.moomoo_security_firm).upper(),
            trd_market=settings.moomoo_trd_market.upper(),
            trd_env=settings.moomoo_trd_env.upper(),
        )
    except BrokerError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(2) from exc

    listings: list[Listing] | None = None
    if symbols or symbols_file is not None:
        named = _resolve_symbols(symbols, symbols_file)
        listings = [Listing(symbol.upper(), symbol.upper(), "指定") for symbol in named]
        source = str(symbols_file) if symbols_file is not None else "コマンドラインの指定"
        console.print(f"ユニバース: {source} の [cyan]{len(listings)}[/] 銘柄")
    else:
        console.print(
            "ユニバース: NASDAQ Trader の上場ファイルを取得します"
            "（ETF・ADR・SPAC・ワラント等は除外）"
        )

    thresholds = Thresholds(
        min_market_cap=min_market_cap,
        min_avg_volume=min_volume,
        min_price=min_price,
        volume_multiple=volume_multiple,
        max_above_52w_low=max_above_low,
        max_range_20d=max_range,
    )

    console.print("[dim]価格データを取得しています。全市場の場合は数分かかります...[/]\n")
    try:
        result = run_accumulation(
            config=config,
            listings=listings,
            price_loader=lambda symbols: download_prices(symbols, period=period),
            thresholds=thresholds,
            screen_limit=limit,
            deep_limit=deep,
        )
    except DataError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc

    today = dt.date.today()
    print_accumulation_report(console, result, today)

    if channel:
        _send_accumulation_summary(result, channel, settings, today, heartbeat=heartbeat)

    raise typer.Exit(0 if result.rows else 1)


def _send_accumulation_summary(
    result: AccumulationRun,
    channel: str,
    settings: Settings,
    today: dt.date,
    *,
    heartbeat: bool,
) -> None:
    """Push the run to a channel, without letting delivery sink the run.

    The screen has already printed everything by the time this runs. A webhook
    that is down is a delivery problem, and failing the whole command for it
    would throw away work that succeeded - so it is reported and the exit code
    is left to the screen's own result.
    """
    if not should_notify_accumulation(result, heartbeat=heartbeat):
        console.print(
            f"\n[dim]{channel} への通知は見送りました（該当0件）。"
            "0件の日も送るなら --heartbeat を付けてください。[/]"
        )
        return

    message = build_accumulation_message(result, today)
    try:
        get_notifier(channel, settings).send(message)
    except NotificationError as exc:
        console.print(f"\n[red]{channel} への通知に失敗しました: {exc}[/]")
        return
    console.print(f"\n[green]{channel} に通知しました[/] ({len(message)} 文字)")


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


@app.command(name="ai-cost")
def ai_cost(
    feed: str = typer.Option("all", help="Disclosure feed: all | edinet | news."),
    lookback_days: int = typer.Option(7, help="Days of EDINET filings to scan."),
    limit: int = typer.Option(10, help="Maximum disclosures per symbol."),
    model: str | None = typer.Option(
        None, help="Model to price; defaults to ANTHROPIC_MODEL, else the built-in default."
    ),
) -> None:
    """Price the next watchlist run before paying for it.

    Counts the exact input tokens of every prompt the run would send, using the
    provider's token-counting endpoint - which does no generation and is not
    billed - and pairs them with the ``max_tokens`` ceiling on each call.

    The result is a range, not a figure. Every disclosure is rated, but only
    the ones the model calls important enough are also summarized, and that
    verdict is not knowable in advance. The low end assumes none clear the
    threshold; the high end assumes all of them do.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    # Defaulting to the configured model, not the built-in one, is what keeps
    # the estimate honest: pricing opus while ANTHROPIC_MODEL selects haiku
    # would be off by a factor of five in the direction that matters.
    priced_model = model or settings.anthropic_model or ANTHROPIC_DEFAULT_MODEL
    provider = AnthropicProvider(api_key=settings.anthropic_api_key, model=priced_model)
    database = Database()
    database.create_all()
    monitor = WatchMonitor(
        database,
        source=_disclosure_source(feed, settings, lookback_days),
        provider=provider,
    )

    console.print(
        f"Pricing a run with [cyan]--feed {feed} --limit {limit} "
        f"--lookback-days {lookback_days}[/] (no model calls yet)."
    )
    work = monitor.pending_texts(limit=limit)
    if not work:
        console.print(
            "[green]Nothing pending, so the next run costs nothing.[/] "
            "Every disclosure on the watchlist has already been seen."
        )
        return

    try:
        estimate = _estimate_run(provider, work)
    except AIError as exc:
        console.print(f"[red]{exc}[/]")
        console.print(
            "[dim]Counting tokens needs the anthropic package ('uv sync --extra "
            "ai') and a key that the API accepts. The message above says which "
            "of the two is missing - it is not always the key.[/]"
        )
        raise typer.Exit(code=1) from exc

    _render_estimate(estimate, feed=feed, limit=limit)


def _estimate_run(provider: AnthropicProvider, work: list[tuple[str, str]]) -> RunEstimate:
    """Count the tokens the run would send, drawing a bar while it goes.

    The counting itself lives in :mod:`stock_ai.ai.estimate` because the
    dashboard prices the same run, and two figures that disagree would leave
    the reader with no way to decide which is real.
    """
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("counting tokens", total=len(work))
        return estimate_disclosure_run(
            provider,
            work,
            on_progress=lambda done, _total: progress.update(task, completed=done),
        )


def _render_estimate(estimate: RunEstimate, feed: str = "", limit: int = 0) -> None:
    """Show the range, and say plainly which half of it is a guess.

    The title carries the flags the estimate assumed. ``monitor --limit 3``
    against an ``ai-cost`` left at the default 10 prices work the run will not
    do, and the two figures then disagree for a reason nothing on screen
    explains - which is how a cost preview stops being believable.
    """
    assumed = f" at --feed {feed} --limit {limit}" if feed else ""
    table = Table(title=f"cost of the next monitor run ({estimate.model}{assumed})")
    table.add_column("", style="cyan")
    table.add_column("disclosures", justify="right")
    table.add_column("input tokens", justify="right")
    table.add_column("output cap", justify="right")
    table.add_column("cost (USD)", justify="right")

    rating_out = estimate.rating_output_cap * estimate.items
    summary_out = estimate.summary_output_cap * estimate.items
    table.add_row(
        "rate only",
        str(estimate.items),
        f"{estimate.rating_input_tokens:,}",
        f"{rating_out:,}",
        _money(estimate.low),
    )
    table.add_row(
        "rate + summarize",
        str(estimate.items),
        f"{estimate.rating_input_tokens + estimate.summary_input_tokens:,}",
        f"{rating_out + summary_out:,}",
        _money(estimate.high),
    )
    console.print(table)

    if not estimate.priced:
        console.print(
            f"[yellow]No cached price for {estimate.model}.[/] The token counts "
            "above are real; the dollar figure is not shown rather than guessed."
        )
        return

    console.print(
        "[dim]Both rows are worst cases, not a range around a likely figure. "
        "Input is exact - counted, not estimated. Output is the max_tokens "
        "ceiling on every call, and a rating that answers in one word uses a "
        "small fraction of it: measured replies have run 30-50 tokens against "
        f"a {estimate.rating_output_cap}-token cap, so the real cost lands far "
        "below both rows. The two differ only in whether summaries happen.[/]"
    )
    console.print(
        "[dim]The run itself prints what it actually spent when it finishes; "
        "that line, not this table, is the figure to compare against a bill. "
        "Prices here are a cached copy of Anthropic's published rates and can "
        "drift - the invoice is the authority.[/]"
    )


def _import_status(module: str) -> str:
    """Say whether ``module`` can be imported, without importing it for real."""
    import importlib.util

    try:
        found = importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):  # a broken or partially removed install
        found = False
    if found:
        return "installed"
    return "[red]missing[/] - run 'uv sync' (see tool.uv default-groups)"


def _within_budget(
    monitor_service: WatchMonitor, provider: object, max_cost: float, limit: int
) -> bool:
    """Whether the next pass is priced under ``max_cost``. Free to ask.

    Checked against the *ceiling*, deliberately. The ceiling assumes every
    disclosure is summarized, so a cap set from it will refuse some runs that
    would in fact have been cheap - and that is the right way round for a job
    nobody is watching. The cost of refusing is a day's alerts delayed, and
    nothing is marked seen, so the next run picks them up.

    Returns True when the provider cannot be priced at all, rather than
    blocking: refusing to run because the *guard* could not be evaluated would
    turn a cost feature into an outage.
    """
    count_tokens = getattr(provider, "count_tokens", None)
    if count_tokens is None:
        console.print(
            "[yellow]--max-cost needs a provider that can count tokens[/] "
            f"({getattr(provider, 'name', 'this one')} cannot). Running anyway; "
            "the spend line at the end still reports what it cost."
        )
        return True

    work = monitor_service.pending_texts(limit=limit)
    if not work:
        return True

    try:
        estimate = _estimate_run(provider, work)
    except AIError as exc:
        console.print(f"[yellow]Could not price this run ({exc}).[/] Running anyway.")
        return True

    ceiling = estimate.high
    if ceiling is None or ceiling <= max_cost:
        return True

    console.print(
        f"[red]Stopping: {estimate.items} disclosure(s) price at up to "
        f"{_money(ceiling)}, over the --max-cost of ${max_cost:,.4f}.[/]\n"
        "  Nothing was sent to the model and nothing was marked as seen, so "
        "the next run picks these up.\n"
        "  The figure is a worst case - it assumes every one of them is "
        "summarized - so the real cost would likely be well under it. Raise "
        "--max-cost, or narrow the run with --limit or --lookback-days."
    )
    return False


def _report_spend(provider: object) -> None:
    """Print what an AI command actually spent, if the provider tracks it.

    The estimate and the invoice are only useful together. ``ai-cost`` says
    what a run should cost; without this the run itself says nothing, and a
    pre-run figure nobody ever checks is a claim rather than a measurement.

    Written against whatever the provider happens to expose: ``dummy`` and the
    OpenAI/Gemini providers keep no ledger, and a command that used one simply
    prints nothing rather than a zero that would read as "free".
    """
    ledger = getattr(provider, "usage", None)
    if not isinstance(ledger, UsageLedger) or ledger.calls == 0:
        return

    model = "/".join(ledger.models) if ledger.models else "?"
    spent = _money(ledger.cost) if ledger.priced else _money(None)
    console.print(
        f"[dim]spent: {ledger.calls} call(s) to {model}, "
        f"{ledger.input_tokens:,} in / {ledger.output_tokens:,} out - {spent}[/]"
    )
    if not ledger.priced:
        console.print(
            f"[yellow]{ledger.unpriced_calls} of those calls used a model with "
            "no cached price[/], so the token counts are complete but the "
            "dollar total is not shown rather than guessed."
        )


def _money(value: float | None) -> str:
    """Render dollars, or a dash when the model has no known price."""
    if value is None:
        return "[dim]-[/]"
    if value < 0.01:
        return f"[green]<$0.01[/] ({value:.5f})"
    return f"${value:.4f}"


@app.command()
def daily(
    at: str = typer.Option("18:00", help="Local HH:MM the jobs run at."),
    symbols: list[str] | None = typer.Argument(None, help="Symbols to refresh."),
    source: str = typer.Option(
        "yfinance",
        help="Override for JP symbols: jquants | tachibana. Non-JP symbols always use "
        "yfinance; JP symbols default to JP_PRICE_SOURCE when this is left at yfinance.",
    ),
    provider: str | None = typer.Option(
        None, help="AI provider used by the monitor. Default: AI_PROVIDER."
    ),
    channel: str | None = typer.Option(None, help="Notification channel for alerts."),
    feed: str = typer.Option("all", help="Disclosure feed: all | edinet | news."),
    limit: int = typer.Option(
        10, help="Disclosures pulled per watched symbol, as in 'monitor --limit'."
    ),
    once: bool = typer.Option(False, "--once", help="Run the jobs now and exit."),
    max_cost: float | None = typer.Option(
        None,
        "--max-cost",
        help="Skip the monitor job if its priced worst case exceeds this many USD.",
    ),
    heartbeat: bool = typer.Option(
        False,
        "--heartbeat",
        help="Also notify when the run finished cleanly with nothing to report.",
    ),
) -> None:
    """Run the daily pipeline: refresh prices, then check the watchlist.

    ``--once`` is the form to put in cron or Task Scheduler. Without it this
    blocks and fires every day at ``--at``, which is convenient for a desktop
    but has no catch-up if the machine was asleep.

    Set ``--max-cost`` whenever ``--provider`` is a paid one. This runs
    unattended: how many disclosures get filed on a given day is not something
    the schedule controls, and a cap is the only thing standing between a busy
    filing day and a bill nobody chose.

    JP symbols in ``SYMBOLS`` are priced through ``JP_PRICE_SOURCE`` unless
    ``--source`` names ``jquants`` or ``tachibana`` explicitly.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    database = Database()
    database.create_all()
    scheduler = DailyScheduler(at=at)

    targets = list(symbols) if symbols else None
    if targets:
        # One --source cannot serve a mixed list: yfinance has no 7203 and
        # J-Quants has no AAPL. Routing by the ticker itself is what stops a
        # single flag being applied to symbols the provider cannot answer for.
        # JP falls back to JP_PRICE_SOURCE, not a hardcoded jquants - same
        # resolution as `fetch`, so the two never drift apart on which source
        # a JP ticker actually gets.
        for market, group in split_by_market(targets).items():
            resolved = _source_for_market(market, source, settings)
            if resolved != source.lower():
                console.print(
                    f"[yellow]{', '.join(group)} are {market} listings; "
                    f"fetching them from {resolved} rather than {source}.[/]"
                )
            price_provider, provider_market = _price_source(resolved, settings)
            scheduler.add(
                f"prices ({market})",
                lambda p=price_provider, g=group, m=provider_market: _log_ingest(
                    IngestionService(p, database).ingest_many(g, market=m)
                ),
            )

    notifier = get_notifier(channel, settings) if channel else None
    # Built once, outside the job, so the spend of the run it performs can be
    # read back afterwards. A provider constructed inside the lambda is gone by
    # the time the job returns, and with it the record of what it cost.
    ai = get_ai_provider(provider or settings.ai_provider, settings)

    def check_watchlist() -> None:
        service = WatchMonitor(
            database,
            source=_disclosure_source(feed, settings, 7),
            provider=ai,
            notifier=notifier,
        )
        if max_cost is not None and not _within_budget(service, ai, max_cost, limit=limit):
            # Raised, not returned: the scheduler records a failed job, and a
            # skipped monitor is exactly the thing that must not pass silently
            # in a log nobody opens unless something looks wrong.
            raise RuntimeError(f"monitor skipped: priced above --max-cost ${max_cost:,.4f}")
        service.run(limit=limit, notify=notifier is not None)

    scheduler.add("monitor", check_watchlist)

    if once:
        results = scheduler.run_once()
        _report_spend(ai)  # the scheduler already swallows per-job failures
        table = Table(title="daily run")
        table.add_column("job", style="cyan")
        table.add_column("status")
        for outcome in results:
            table.add_row(
                outcome.name,
                "[green]ok[/]" if outcome.ok else f"[red]error[/] {outcome.error or ''}",
            )
        console.print(table)

        failed = [r for r in results if not r.ok]
        _report_run_outcome(notifier, failed, results, heartbeat=heartbeat)
        if failed:
            raise typer.Exit(code=1)
        return

    console.print(f"Running daily at [cyan]{at}[/]. Ctrl-C to stop.")
    scheduler.run_forever()


def _report_run_outcome(
    notifier: Notifier | None,
    failed: list[JobResult],
    results: list[JobResult],
    *,
    heartbeat: bool,
) -> None:
    """Send a notification when the unattended run did not go cleanly.

    Alerts are only sent when there are alerts, which for a scheduled job makes
    the channel silent in four different situations that mean opposite things:
    nothing was filed, nothing cleared the threshold, the run was skipped by
    ``--max-cost``, and the run failed outright. A monitoring channel that says
    the same thing when all is well and when everything is broken is not a
    monitoring channel.

    So a failure always speaks. Success stays quiet unless ``--heartbeat`` is
    asked for: a message every single morning is one people stop reading, and
    then the failure message is unread too.
    """
    if notifier is None:
        return

    if failed:
        detail = "\n".join(f"- {r.name}: {r.error or 'failed'}" for r in failed)
        message = f"stock-ai daily: {len(failed)} of {len(results)} job(s) failed\n{detail}"
    elif heartbeat:
        message = f"stock-ai daily: {len(results)} job(s) ok, nothing above threshold."
    else:
        return

    try:
        notifier.send(message)
    except NotificationError as exc:
        # Never let the messenger take the run down: the jobs already ran, and
        # their outcome is in the log whether or not it could be delivered.
        console.print(f"[yellow]Could not send the run summary:[/] {exc}")


def _log_ingest(results: list[IngestResult]) -> None:
    """Raise if every symbol failed, so the scheduler records a failed job."""
    if results and all(not r.ok for r in results):
        raise RuntimeError(results[0].error or "all symbols failed")


@app.command()
def ask(
    question: str = typer.Argument(..., help='e.g. "PER15以下でROE20%以上の半導体株"'),
    provider: str | None = typer.Option(
        None, help="AI provider: dummy|claude|openai|gemini. Default: AI_PROVIDER."
    ),
    top: int = typer.Option(20, help="Rows to print; 0 for every match."),
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

    ai = get_ai_provider(provider or settings.ai_provider, settings)
    try:
        query = parse_query(ai, question)
    except AIError as exc:
        console.print(f"[red]could not interpret the question:[/] {exc}")
        raise typer.Exit(code=1) from exc
    finally:
        _report_spend(ai)
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
        # With names, like 'screen'. A four-digit code is not a company to
        # anyone reading the output, and "is this list plausible?" is the one
        # judgement the screen cannot make for itself.
        report = build_report(
            collect_fundamentals(database, matches), names=company_names(database, matches)
        )
        _render_report(report, limit=top or None)
    elif query.needs_statements:
        console.print(
            "[dim]This question needs the statement series; run 'statements' "
            "for the symbols you want covered.[/]"
        )


@app.command()
def summarize(
    text: str = typer.Argument(..., help="Text (IR excerpt, news, ...) to summarize."),
    provider: str | None = typer.Option(
        None, help="AI provider: dummy|claude|openai|gemini. Default: AI_PROVIDER."
    ),
    max_words: int = typer.Option(120, help="Maximum words in the summary."),
) -> None:
    """Summarize TEXT with the selected AI provider."""
    settings = get_settings()
    configure_logging(settings.log_level)
    ai = get_ai_provider(provider or settings.ai_provider, settings)
    # ``finally``: a call that fails after the model answered is still billed,
    # and that is precisely the run where the reader most wants the figure. The
    # first live failure of this command spent tokens and reported nothing.
    try:
        console.print(ai_summarize(ai, text, max_words=max_words))
    finally:
        _report_spend(ai)


@app.command()
def sentiment(
    text: str = typer.Argument(..., help="Text to classify."),
    provider: str | None = typer.Option(
        None, help="AI provider: dummy|claude|openai|gemini. Default: AI_PROVIDER."
    ),
) -> None:
    """Classify the sentiment of TEXT (positive / neutral / negative)."""
    settings = get_settings()
    configure_logging(settings.log_level)
    ai = get_ai_provider(provider or settings.ai_provider, settings)
    try:
        console.print(analyze_sentiment(ai, text))
    finally:
        _report_spend(ai)


def _warn_if_lookback_will_not_reach(database: Database, symbols: list[str], lookback: int) -> None:
    """Say so when ``--lookback`` asks for history the run will not fetch.

    ``--lookback`` applies only to symbols with no prices at all, so asking a
    universe that already holds four years for 5,000 days quietly does nothing:
    every symbol is current, the run reports success, and no extra history
    arrives. There is no error to notice, so the only defence is saying it
    before the run rather than after.
    """
    wanted = dt.date.today() - dt.timedelta(days=lookback)
    with database.session() as session:
        repo = PriceRepository(session)
        short = sum(
            1
            for symbol in symbols
            if (earliest := repo.earliest_date(symbol)) is not None and earliest > wanted
        )
    if not short:
        return
    console.print(
        f"[yellow]{short} of {len(symbols)} symbol(s) already hold prices that start "
        f"after {wanted}.[/] --lookback only applies to symbols with no prices at "
        "all, so their history will [bold]not[/] be extended by this run. Add "
        "[cyan]--backfill[/] to reach further back."
    )


def _load_prices(database: Database, symbol: str) -> pd.DataFrame:
    """Load stored prices for ``symbol`` or fail with a helpful message.

    The frame arrives on the adjusted basis; see
    :meth:`~stock_ai.database.repository.PriceRepository.get_prices`.
    """
    with database.session() as session:
        prices = PriceRepository(session).get_prices(symbol)
    if prices.empty:
        raise typer.BadParameter(f"No price data for {symbol!r}; run 'fetch' first.")
    return prices


def _build_strategy(name: str, fast: int, slow: int, window: int = 200) -> Strategy:
    """Construct a strategy from its short name (see ``build_strategy``)."""
    try:
        return build_strategy(name, fast=fast, slow=slow, window=window)
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


#: Report columns that are ratios, and so want decimals rather than magnitude.
_RATIO_COLUMNS = frozenset({"roe", "per", "pbr", "dividend_yield", "payout_ratio"})


def _format_cell(column: str, value: object) -> str:
    """Render one report value at a precision a person can read.

    ``str(float)`` prints seventeen significant digits. On a 344-row screen
    that wraps every column to four lines and turns the answer into a wall -
    the numbers are all correct and none of them can be compared at a glance,
    which for a table whose whole job is comparison is the same as being wrong.
    """
    if value is None:
        return ""
    if isinstance(value, float):
        if pd.isna(value):
            return "-"
        if column in _RATIO_COLUMNS:
            return f"{value:.3f}"
        return _compact(value)
    return str(value)


def _compact(value: float) -> str:
    """Render a large amount in a width a column can hold.

    A JP market cap runs to fourteen digits, and printed in full it wraps to
    five lines and pushes every other column out of shape. The suffix carries
    no currency because this table does not know one - JP rows are yen and US
    rows are dollars, which is why cross-market comparison lives in ``rank``
    and its FX conversion rather than here. Full precision is one
    ``--out results.csv`` away.
    """
    magnitude = abs(value)
    for threshold, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if magnitude >= threshold:
            return f"{value / threshold:,.2f}{suffix}"
    return f"{value:,.0f}"


def _render_report(report: pd.DataFrame, limit: int | None = None) -> None:
    """Print a screening report as a Rich table, newest precision first.

    ``limit`` caps the rows printed. A question like "PER under 15" matches
    hundreds of names, and a terminal that has to scroll past all of them is
    not showing an answer, it is hiding one.
    """
    shown = report if limit is None else report.head(limit)
    table = Table(title="screen results")
    for column in report.columns:
        table.add_column(column, overflow="fold", justify="right" if column != "symbol" else "left")
    for row in shown.itertuples(index=False):
        table.add_row(
            *(_format_cell(col, val) for col, val in zip(report.columns, row, strict=True))
        )
    console.print(table)
    if limit is not None and len(report) > limit:
        console.print(
            f"[dim]Showing {limit} of {len(report)} matches. "
            "Use --top to see more, or 'screen --out results.csv' for all of them.[/]"
        )


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


def _secret_summary(value: SecretStr | None) -> str:
    """Describe a secret without revealing it: length plus a hash prefix.

    "set" is not enough to debug an authentication failure. It cannot tell a
    freshly pasted key from the old one still sitting in .env, and it cannot
    tell a full key from one truncated by a bad copy. Length and a one-way
    fingerprint answer both while staying safe to paste into a bug report.
    """
    secret = value.get_secret_value() if value is not None else ""
    if not secret.strip():
        # An empty assignment in .env (``OPENAI_API_KEY=``) parses to "", which
        # is not None - so a naive None check reports it as "set (0 chars)".
        # That reads as configured and sends anyone debugging an auth failure
        # looking in the wrong place.
        return "[dim]not set[/]"
    digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()[:8]
    return f"[green]set[/] [dim]({len(secret)} chars, fingerprint {digest})[/]"


def _secret_status(settings: Settings) -> list[tuple[str, SecretStr | None]]:
    """Return ``(label, value)`` pairs for each secret. Values are never printed."""
    return [
        ("jquants_api_key", settings.jquants_api_key),
        ("edinet_api_key", settings.edinet_api_key),
        ("anthropic_api_key", settings.anthropic_api_key),
        ("openai_api_key", settings.openai_api_key),
        ("gemini_api_key", settings.gemini_api_key),
        ("discord_webhook_url", settings.discord_webhook_url),
        ("line_channel_access_token", settings.line_channel_access_token),
        ("telegram_bot_token", settings.telegram_bot_token),
        ("moomoo_trade_password", settings.moomoo_trade_password),
    ]


@app.command()
def ops(
    what: str = typer.Option("status", help="check | status | history | equity | jobs."),
    limit: int = typer.Option(10, help="history のとき、新しい順に何件出すか。"),
) -> None:
    """Read the state of the canonical auto-trading repository (in WSL).

    ダッシュボードの「自動売買 運用」画面と同じ経路を、画面を開かずに叩く。
    読み取り専用で、発注も帳簿の変更もしない(キルスイッチの発動と設定の保存は
    画面側にしか置いていない)。参照先は .env の OPS_WSL_DISTRO / OPS_REPO_PATH。
    """
    bridge = get_bridge()
    console.print(f"参照先(正典): [bold]{bridge.target.label}[/bold]")
    try:
        if what == "check":
            for key, value in bridge.ping().items():
                console.print(f"  {key}: {value}")
            return
        if what == "status":
            status = bridge.status()
            kills = status.get("kill_switches") or []
            console.print(
                "  キルスイッチ: [red]発動中[/red] " + ", ".join(kills)
                if kills
                else "  キルスイッチ: [green]なし[/green]"
            )
            console.print(f"  cron: {len(status.get('cron') or [])}本")
            risk = status.get("risk") or {}
            console.print(
                f"  broker: {risk.get('broker', '?')} / 資金 {risk.get('capital', 0):,}円"
            )
            table = Table(title="日本株トラックA 保有")
            for column in ("コード", "銘柄", "株数", "状態", "約定日"):
                table.add_column(column)
            for position in status.get("jp_positions") or []:
                table.add_row(
                    str(position.get("code")),
                    str(position.get("name", "")),
                    str(position.get("shares")),
                    str(position.get("status")),
                    str(position.get("exec_date")),
                )
            console.print(table)
            return
        if what == "history":
            rows = (bridge.trade_history().get("rows") or [])[:limit]
            table = Table(title=f"売買履歴(新しい順 {len(rows)}件)")
            for column in ("日付", "トラック", "コード", "銘柄", "売買", "株数", "価格"):
                table.add_column(column)
            for row in rows:
                table.add_row(
                    row["date"],
                    row["track"],
                    str(row["code"]),
                    str(row["name"]),
                    row["side"],
                    str(row["shares"]),
                    str(row["price"]),
                )
            console.print(table)
            return
        if what == "equity":
            for curve in bridge.equity().values():
                if curve is None:
                    continue
                pnl = curve["equity"] - curve["capital"]
                console.print(
                    f"  {curve['label']}: {curve['equity']:,.0f} {curve['currency']} "
                    f"({pnl:+,.0f} / {curve['asof']}時点)"
                )
            return
        if what == "jobs":
            for name in bridge.jobs():
                console.print(f"  {name}")
            return
    except OpsError as exc:
        console.print(f"[red]正典を参照できませんでした:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"[red]不明な --what:[/red] {what}")
    raise typer.Exit(code=2)


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
