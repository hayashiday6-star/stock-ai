"""Command-line interface for stock-ai.

Exposes the ``stock-ai`` console script. Subcommands for each pipeline stage
are added as the phases progress.
"""

from __future__ import annotations

import bisect
import contextlib
import datetime as dt
import hashlib
import math
import shutil
import sys
import time
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from statistics import fmean, median, stdev

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
from stock_ai.backtest.cross_section import beta_to_benchmark, build_estimators, t_ratio
from stock_ai.backtest.engine import BacktestEngine
from stock_ai.backtest.event_census import (
    count_52w_highs,
    count_halt_resumptions,
    count_limit_moves,
    high_event_returns,
)
from stock_ai.backtest.factor_panel import build_panel
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
from stock_ai.backtest.lowvol import (
    COST_PER_MONTH,
    DEFAULT_WINDOW,
    MIN_SYMBOLS_PER_MONTH,
    SEALED_BETA,
    break_even_alpha,
    max_drawdown,
)
from stock_ai.backtest.lowvol import DEFAULT_LAGS as LOWVOL_LAGS
from stock_ai.backtest.lowvol import (
    DETECTABLE as LOWVOL_DETECTABLE,
)
from stock_ai.backtest.lowvol import JUDGED_T as LOWVOL_JUDGED_T
from stock_ai.backtest.lowvol import (
    PASS as LOWVOL_PASS,
)
from stock_ai.backtest.lowvol import build_series as build_lowvol_series
from stock_ai.backtest.lowvol import verdict as lowvol_verdict
from stock_ai.backtest.lowvol_census import VOLATILITY_WINDOWS
from stock_ai.backtest.lowvol_census import run_census as run_lowvol_census
from stock_ai.backtest.multiplicity import (
    FAMILY_ALPHA,
    HYPOTHESIS_BUDGET,
    adjust,
    ladder,
    line_for,
)
from stock_ai.backtest.pead import (
    MIN_TURNOVER,
    ONE_WAY_COST,
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
from stock_ai.backtest.power import (
    COMPOSITE_PROCEED,
    COMPOSITE_STOP,
    DEFAULT_LAGS,
    HIGH_HOLDING,
    HIGH_IS_END,
    HIGH_SEAL,
    HIGH_SEAL_FLOOR,
    TARGET_T,
    composite_verdict,
    estimate_power,
    gate,
    high_verdict,
    judge,
    periods_needed,
    required_improvement,
    trimmed_variance,
)
from stock_ai.backtest.rehearsal import SEED as REHEARSAL_SEED
from stock_ai.backtest.report import metrics_frame
from stock_ai.backtest.reversal import (
    BENCHMARK,
    COST_ROUND_TRIP,
    DETECTABLE,
    JUDGMENT_FROM,
    PASS,
    build_series,
    survivorship_gap,
    verdict,
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
    RateLimitError,
)
from stock_ai.core.log_gaps import survey_all as survey_logs
from stock_ai.core.logging import configure_logging
from stock_ai.core.scheduler import DailyScheduler, JobResult
from stock_ai.core.version import describe as describe_version
from stock_ai.data.base import PriceProvider
from stock_ai.data.bulk import BulkIngester, Dataset, store_universe
from stock_ai.data.bulk import latest_close as bulk_latest_close
from stock_ai.data.delisted import (
    DEFAULT_SNAPSHOT_DIR,
    DEFAULT_STEP_DAYS,
    TACHIBANA_SNAPSHOT_DIR,
    all_profiles,
    beyond_the_window,
    covered_from,
    dates_without_lending,
    delistings,
    earliest_reachable,
    harvest_snapshots,
    latest_reachable,
    lending_coverage,
    membership,
    monthly_membership,
    monthly_snapshot,
    plan_is_known,
    read_snapshot,
    snapshot_dates,
    snapshot_path,
    stored_dates,
)
from stock_ai.data.fx import FxConverter
from stock_ai.data.jquants_archive import (
    DEFAULT_ARCHIVE_DIR,
    key_period,
    path_for,
    read_manifest,
)
from stock_ai.data.jquants_archive import archive as archive_bulk
from stock_ai.data.jquants_archive import orphans as archive_orphans
from stock_ai.data.jquants_archive import verify as verify_archive
from stock_ai.data.jquants_bulk import (
    ARCHIVE_ENDPOINTS,
    BULK_ENDPOINTS,
    DEADLINE_ENDPOINTS,
    PLAN_REQUESTS_PER_MINUTE,
    PRESIGNED_URL_TTL,
    BulkFile,
    download_raw,
    infer_plan,
    recommended_throttle,
)
from stock_ai.data.jquants_bulk import coverage as bulk_coverage
from stock_ai.data.jquants_bulk import download as bulk_download
from stock_ai.data.jquants_bulk import group_by_symbol as bulk_group_by_symbol
from stock_ai.data.jquants_bulk import list_files as bulk_list_files
from stock_ai.data.jquants_bulk import records_from_csv as bulk_records_from_csv
from stock_ai.data.jquants_bulk import span_years as bulk_span_years
from stock_ai.data.jquants_consistency import NO_ROSTER_ROW, ROSTER_DISAGREES
from stock_ai.data.jquants_consistency import check as consistency_check
from stock_ai.data.jquants_crosscheck import DailyMatch, compare_daily, summarise
from stock_ai.data.jquants_details import (
    RevisionCensus,
    describe_doc_type,
    is_known_doc_type,
    parse_details,
)
from stock_ai.data.jquants_details import (
    revision_census as count_revisions,
)
from stock_ai.data.jquants_exit import CANCELLATION, audit
from stock_ai.data.jquants_filter import baseline as filter_baseline
from stock_ai.data.jquants_filter import census as filter_census
from stock_ai.data.jquants_filter import product_separates
from stock_ai.data.jquants_fundamentals import JQuantsFundamentalsProvider, normalize_statements
from stock_ai.data.jquants_indices import census as topix_census
from stock_ai.data.jquants_indices import from_archive as topix_from_archive
from stock_ai.data.jquants_indices import gap_trail as topix_gap_trail
from stock_ai.data.jquants_indices import tracking_gap as topix_tracking_gap
from stock_ai.data.jquants_markets import HOLIDAY_DIVISION, TRADING_DIVISIONS, half_days_by_year
from stock_ai.data.jquants_markets import agreement as calendar_agreement
from stock_ai.data.jquants_markets import census as calendar_census
from stock_ai.data.jquants_prices import comparable as price_comparable
from stock_ai.data.jquants_prices import frames_for as price_frames_for
from stock_ai.data.jquants_prices import ingest as price_ingest
from stock_ai.data.jquants_prices import join_returns as price_join_returns
from stock_ai.data.jquants_prices import looks_unapplied as price_looks_unapplied
from stock_ai.data.jquants_prices import probe_symbols
from stock_ai.data.jquants_prices import split_day_returns as price_split_day_returns
from stock_ai.data.jquants_prices import split_verdict as price_split_verdict
from stock_ai.data.jquants_profile import JQuantsProfileProvider
from stock_ai.data.jquants_provider import JQuantsPriceProvider
from stock_ai.data.jquants_read import census as archive_census
from stock_ai.data.jquants_read import endpoint_of, read_archived, samples_per_endpoint
from stock_ai.data.jquants_read import shape_of as archive_shape
from stock_ai.data.jquants_rosters import (
    DAILY_SNAPSHOT_DIR,
    calendar_from_archive,
    explain_missing,
    market_on,
    markets_on,
    trading_days_from_archive,
)
from stock_ai.data.jquants_rosters import compare as roster_compare
from stock_ai.data.jquants_rosters import day_detail as roster_day_detail
from stock_ai.data.jquants_rosters import extract as roster_extract
from stock_ai.data.markets import split_by_market, to_yahoo_symbol
from stock_ai.data.schema import ADJ_CLOSE, CLOSE, OPEN
from stock_ai.data.service import FundamentalsService, IngestionService, IngestResult
from stock_ai.data.tachibana import TachibanaPriceProvider
from stock_ai.data.tachibana import build_client as build_tachibana_client
from stock_ai.data.tachibana import default_version as tachibana_default_version
from stock_ai.data.tachibana import version_warning as tachibana_version_warning
from stock_ai.data.tachibana_universe import TachibanaUniverse
from stock_ai.data.types import FinancialReport, Importance, SecurityProfile
from stock_ai.data.universe import FUND, JQuantsUniverse, Segment
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
    REACH_NO_FILINGS,
    REACH_OK,
    REACH_OUT_OF_RANGE,
    EdinetDisclosureSource,
    ProbeResult,
    day_reach,
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
    # **遡れる年数はプランで決まる。** 上げた日にここを直し忘れると、例外も
    # 警告も出ないまま、窓の外だと判断して古い日付を要求しない——20年ぶん
    # 払って5年ぶんだけ落とす形になる。上の3つと同じ理由でここに出す。
    plan = (settings.jquants_plan or "").strip().capitalize()
    known = plan_is_known(plan)
    reach = earliest_reachable(plan)
    newest = latest_reachable(plan)
    # **新しい端も出す。** Free は直近12週が取れない——他のプランには無い形で、
    # そこを出さないと「昨日のデータが来ない」理由が分からなくなる。
    span = f"{reach} 〜 {newest}" if newest < dt.date.today() else f"{reach} まで"
    table.add_row(
        "jquants_plan",
        f"{settings.jquants_plan or '(未設定)'}"
        + ("" if known else "  [red](未知の値。5年として扱う)[/]")
        + f"  取れる: {span}"
        + ("  [yellow](直近12週は取れない)[/]" if plan == "Free" else ""),
    )
    if settings.jp_price_source.strip().lower() == "tachibana":
        version = settings.tachibana_api_version or tachibana_default_version()
        warning = tachibana_version_warning(version)
        table.add_row("tachibana version", f"{version}{'  ' + warning if warning else ''}")
    for label, value in _secret_status(settings):
        table.add_row(label, _secret_summary(value))

    # **走らなかった日は、出力に出ない。**
    #
    # `Get-ScheduledTaskInfo` は `LastTaskResult: 0` と出るが、それは「最後に
    # 走った回」の話で、**走らなかった回は数に入らない。** 2026-09-19 に手で
    # 数えて、daily に4日・accumulation に2日の穴が見つかった。**「異常なし」
    # の顔をしたまま抜けていた。**
    #
    # **無いことを出すには、在るべき日を先に決めて引き算するしかない。**
    gaps = survey_logs(Path("logs"))
    for row in gaps:
        # 札に名前が出ているので、本文からは落とす。
        table.add_row(f"logs/{row.name}", row.summary().removeprefix(f"{row.name}: "))
    console.print(table)
    # **穴は表の1行にしない。** 表は読む側が気付く必要がある。
    for row in gaps:
        for line in row.warnings():
            console.print(f"[yellow]{line}[/]")
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


@app.command(name="revision-power")
def revision_power(  # noqa: PLR0913 - §0 が固定した条件をすべて受け取る
    directory: str = typer.Option(
        str(DEFAULT_ARCHIVE_DIR), "--dir", help="Where the archived originals live."
    ),
    benchmark: str = typer.Option(BENCHMARK, "--benchmark", help="Sets the calendar."),
    min_turnover: float = typer.Option(MIN_TURNOVER, "--min-turnover", help="Liquidity floor."),
    holding: int = typer.Option(20, "--holding", help="Sessions held, fixed by the prereg."),
    subtract: str = typer.Option(
        "index", "--subtract", help="What to deduct: index (as the prereg says) or universe."
    ),
    limit: int | None = typer.Option(None, "--limit", help="Read only the first N originals."),
) -> None:
    """Measure the IS spread for #5, so the gate can be applied.

    **判定ではない。** 見るのは IS（原本が覆う期間の前半）だけで、OOS には
    1日も触れない。

    **測る前にコミットした線がある**（事前登録 §0）——「IS の1イベントあたり
    平均超過リターンの片側95%下限が **1.2%** を下回ったら封印しない」。
    往復費用 0.4% の3倍である。**下回ればここで終わる。線は動かさない。**

    窓は **20営業日**（§3・§4）。**#8 のように機構から出る説ではない**ので、
    #3・#6 と揃えてある。**リターンを見て決めていない。**
    """
    from stock_ai.backtest.event_window import event_sample
    from stock_ai.backtest.multiplicity import (
        HYPOTHESIS_BUDGET,
        calibrated_t,
    )
    from stock_ai.backtest.pead import TURNOVER_WINDOW
    from stock_ai.backtest.reversal import COST_ROUND_TRIP
    from stock_ai.backtest.revision_census import REVISION_TYPE, census, find_upward
    from stock_ai.data.jquants_bulk import records_from_csv
    from stock_ai.data.schema import VOLUME
    from stock_ai.data.universe import four_digit_code

    settings = get_settings()
    configure_logging(settings.log_level)

    source = Path(directory)
    keys = [key for key in sorted(read_manifest(source)) if endpoint_of(key) == "/fins/summary"]
    if limit is not None:
        keys = keys[:limit]
    if not keys:
        console.print(f"[red]{source} に `/fins/summary` の原本が無い。[/]")
        raise typer.Exit(code=1)

    items = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("原本を読む", total=len(keys))
        for index, key in enumerate(keys, start=1):
            progress.update(task, completed=index)
            try:
                items.extend(records_from_csv(read_archived(path_for(source, key))))
            except Exception as exc:  # noqa: BLE001 - どこで読めないかが記録に値する
                console.print(f"[yellow]{key}: {type(exc).__name__}[/]")

    database = Database()
    database.create_all()
    with database.session() as session:
        price_repo = PriceRepository(session)
        if price_repo.get_raw_prices(benchmark).empty:
            console.print(f"[red]ベンチマーク {benchmark!r} の価格が無い。[/]")
            raise typer.Exit(code=1)
        liquid: dict[tuple[str, dt.date], bool] = {}
        symbols = sorted(
            {
                code
                for row in items
                if (row.get("DocType") or "").strip() == REVISION_TYPE
                and (code := four_digit_code((row.get("Code") or "").strip())) is not None
            }
        )
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("売買代金を読む", total=len(symbols))
            for index, symbol in enumerate(symbols, start=1):
                progress.update(task, completed=index)
                raw = price_repo.get_raw_prices(symbol)
                if raw.empty:
                    continue
                rolling = (
                    (raw[CLOSE] * raw[VOLUME]).rolling(TURNOVER_WINDOW).mean().shift(1).dropna()
                )
                for stamp, value in rolling.items():
                    liquid[(symbol, stamp.date())] = bool(value >= min_turnover)

    def liquid_on(symbol: str, on: dt.date) -> bool:
        return liquid.get((symbol, on), False)

    counted = census(items, liquid_on=liquid_on)
    if not counted.events_is:
        console.print("[red]IS に使えるイベントが無い。[/] `revision-census` を先に見ること。")
        raise typer.Exit(code=1)

    console.print(
        f"[dim]IS は {counted.split_on} まで（{counted.events_is:,} 件）。"
        f"OOS（{counted.events_oos:,} 件）には1日も触れない。窓は {holding} 営業日。[/]"
    )

    events, _seen = find_upward(items)
    kept = [
        (event.symbol, event.disclosed_on)
        for event in events
        if not event.on_statement_day and liquid_on(event.symbol, event.disclosed_on)
    ]
    # **事前登録は `1306` を指している。** 既定を変えない——判定の出た説を、
    # あとから別の相手で測り直すことになる。`--subtract universe` は、
    # 新しい説のために形だけ通してある。
    deducted = _universe_to_subtract(database, subtract, holding)
    sample = event_sample(
        database,
        kept,
        holding=holding,
        benchmark=benchmark,
        until=counted.split_on,
        subtract=deducted,
    )
    values = sample.values
    # **捨てた件数を黙って捨てない。** 引いた件数のうち何件が、どの理由で
    # 落ちたのかを出す。上場廃止で落ちる分は**悪く終わった側に偏る。**
    _report_event_disposition(sample, title="窓を当てた結果（IS のみ・件数）")
    if len(values) < 2:
        console.print(f"[red]値動きの取れたイベント日が {len(values)} しかない。[/]")
        raise typer.Exit(code=1)

    # **ロングである。** 仮説は超過リターンが正だと言っている（§1）ので、
    # 符号は反転しない。費用は往復 0.4%（§4）。
    take = [value - COST_ROUND_TRIP for value in values]

    # **膨張は「管 × 引く相手」ごとに測ってある。** 引く相手を替えると数字が
    # 動いた（0.94 → 1.09）ので、**いま引いている相手の値を当てる。**
    target = calibrated_t(HYPOTHESIS_BUDGET, inflation=_event_inflation(subtract))
    # **独立な観測は「日」である。** 系列は日ごとの等加重バスケットなので、
    # 件数で割ると n を水増しする。IS は 1,827 件が 831 日にまとまっていた
    # ——2.2倍で、検出できる差は平方根ぶん **1.48倍甘く出ていた**（2026-09-17）。
    periods = counted.days_oos or counted.events_oos
    # **`periods` が何年ぶんかを渡す。** IS の年数ではない——混ぜると、
    # 年数の列だけが別の標本を指す（2026-09-20）。
    span = _judgement_years(counted.split_on, counted.last)
    _event_gate(
        take,
        periods=periods,
        target=target,
        committed=3 * COST_ROUND_TRIP,
        holding=holding,
        reach=f"**OOS の {counted.events_oos:,} 件が固まった日数。件数ではない**",
        period_years=span,
        footnote=(
            f"[dim]見込みを測った IS は {counted.events_is:,} 件"
            "（絞り込んだ後）。**データはここから増えない。**[/]"
        ),
    )


def _needed_table(  # noqa: PLR0913 - 表の材料をすべて受け取る
    title: str,
    unit: str,
    rows: Sequence[tuple[str, bool, int, float]],
    have_periods: int,
    have_years: float,
) -> Table:
    """Build the "how many periods would be needed" table, marking the gate row.

    **どの行が関門かを、行そのものに書く。** 書かないと、参考の行の
    「足りる」が**説が通る**という意味に読める——実際そう読まれた
    （2026-09-20、ユーザーが発見）。

    §0 が見るのは**見込みの下限**の行だけである。コミットした線の行は
    「線の大きさの効果なら見分けられたか」を言っているだけで、**通る条件では
    ない。** #16 では下限に 5,726年、線に 6.4年 と出て、**6.4年 のほうが
    読まれた。**

    **差の数は出さない。** 「+4」が「あと4件で足りる」に読めた回がある
    （#15）。**要る数といま在る数を並べれば、引き算は読む側でできる。**

    Args:
        title: 表題。
        unit: 期の呼び方（「イベント日」「期」など）。
        rows: ``(札, 関門か, 要る期数, 要る年数)``。
        have_periods: いま在る期数。
        have_years: それが何年ぶんか。

    Returns:
        描く前の表。
    """
    table = Table(title=title)
    table.add_column("この大きさが本当なら", overflow="fold")
    table.add_column(f"要る{unit}", justify="right")
    table.add_column("年数", justify="right")
    table.add_column("いま在る分で", justify="right")
    for label, is_gate, count, years in rows:
        mark = "[bold]§0 の関門[/] — " if is_gate else "参考 — "
        enough = count <= have_periods
        table.add_row(
            f"{mark}{label}",
            f"{count:,}",
            f"{years:,.1f}年",
            "足りる" if enough else "[red]足りない[/]",
        )
    table.add_section()
    table.add_row(
        "[dim]いま在る（判定に使える）[/]", f"{have_periods:,}", f"{have_years:,.1f}年", "—"
    )
    return table


def _judgement_years(start: dt.date | None, end: dt.date | None) -> float:
    """How many years the judgement window spans - not the estimation window.

    `_event_gate` の「年数」の列は、**`periods` と同じ標本**で数えなければ
    ならない。呼ぶ側が IS の年数を渡していて、**「6.9年 要る」と
    「足りている」が同じ行に並んだ**（2026-09-20、ユーザーが発見）。

    Args:
        start: 判定に使う窓の始まり。
        end: 判定に使う窓の終わり。

    Returns:
        年数。どちらかが無い、または順序が逆なら 0.0（表を出さない）。
    """
    if start is None or end is None or end <= start:
        return 0.0
    return (end - start).days / 365.25


def _event_gate(  # noqa: PLR0913 - §0 の材料をすべて受け取る
    values: list[float],
    periods: int,
    target: float,
    committed: float,
    holding: int,
    reach: str,
    period_years: float = 0.0,
    footnote: str = "",
    side: str = "ロング",
) -> None:
    """Print the event-pipe gate: table, line, verdict, events needed.

    **イベント型の §0 を、表・線・関門・要る件数の順に出す。**

    **#5 が書いたものを、#15 も呼ぶ。** 同じ管の §0 を2つ書けば、片方が緩む
    ——`power-gate` が校正前の線を使っていた件、`january-power` が上限と
    比べていた件と同じ形である（`CLAUDE.md`）。

    Args:
        values: 1イベント日あたりの超過リターン。**費用を引いた後。**
        periods: 判定に使える期数。**OOS のイベント日数。件数ではない。**
        target: 封印に使う線。
        committed: 測る前にコミットした「封印しない線」。
        holding: 保有営業日数。Newey-West のラグに使う。
        reach: 「判定に使える期数」の出どころ。
        period_years: **``periods`` が何年ぶんか。** 0 なら「要る期数」を
            出さない。**推定に使った標本（IS）の年数ではない**——`values` の
            件数と割り算すると、年数の列だけが IS の率になり、「いまとの差」
            の列は OOS を見たままになる（2026-09-20、ユーザーが発見。
            「6.9年 要る」と「足りている」が同じ行に並んだ）。
            **率は `power.requirement` が `periods ÷ period_years` から作る。**
        footnote: 最後に出す1行。
        side: ``ロング`` か ``ショート``。**表示だけ。** 符号の反転は呼ぶ側で
            済ませておく（`margin_power` の「ここ1箇所だけで行う」を守る）。
    """
    from stock_ai.backtest.power import (
        Requirement,
        estimate_power,
        gate,
        requirement,
        trimmed_variance,
    )

    estimate = estimate_power(values, lags=holding)
    mean = fmean(values)
    stderr = estimate.standard_error(len(values))
    # **片側95%。** 事前登録 §0 が片側で書いている。
    floor_estimate = mean - 1.645 * stderr
    detectable = estimate.detectable(periods, target_t=target)
    trimmed, dropped = trimmed_variance(values, fraction=0.01)

    table = Table(title="§0 に入れる材料（IS から。判定ではない）")
    for column in ("項目", "値", "どこから"):
        table.add_column(column, overflow="fold")
    table.add_row("値動きの取れたイベント日", f"{len(values):,}", "IS のみ")
    table.add_row("1イベントあたりのSD", f"{estimate.daily_sd:.2%}", f"費用引き後の{side}")
    table.add_row(
        "上位1%を除いたSD",
        f"{trimmed**0.5:.2%}",
        f"{dropped} 件を除いた。**外れ値で膨らんでいないか**",
    )
    table.add_row("重なりの膨張", f"{estimate.inflation:.2f}x", f"Newey-West({holding})。実測")
    table.add_row("判定に使える期数", f"{periods:,}", reach)
    table.add_row("検出できる差", f"{detectable:.2%}", f"t≥{target:.2f}・1イベントあたり")
    table.add_row("費用", f"{COST_ROUND_TRIP:.2%}", "往復。#6 の実測値を引く")
    console.print(table)

    console.print(
        f"[bold]IS の取り高（費用引き後・{side}）: 1イベント {mean:+.2%}[/] "
        f"[dim]（片側95%の下限 {floor_estimate:+.2%}）[/]"
    )

    console.print()
    if floor_estimate < committed:
        console.print(
            f"[red]封印しない。[/] 片側95%の下限 {floor_estimate:+.2%} が、"
            f"**測る前にコミットした線 {committed:.1%} を下回った。**"
        )
        console.print(
            "[dim]事前登録 §0 にそう書いてある（往復費用 0.4% の3倍）。"
            "**費用を超えるだけの線を置くと、#7 が入った帯にまっすぐ入る。** "
            "線は動かさない。[/]"
        )
    else:
        console.print(f"[green]線（{committed:.1%}）は上回った。[/] 次は §0 のゲートである。")
        # **当てはめは `power.gate` に聞く。** ここで書き直さない。
        decision = gate(detectable, floor_estimate, mean + 1.645 * stderr)
        colour = "green" if decision.passed else "red"
        console.print(f"[bold {colour}]{decision.verdict}[/] {decision.reading}")
        if decision.passed:
            return

    # **「検出力不足」で終わらせない。** 何期あれば足りるかを出す。
    if period_years <= 0:
        return

    # **検出できる差を「検出したい効果」に入れない。** それに要る期数は、
    # 定義上いま在る期数そのものである——**答えが必ず「ちょうど足りる」に
    # なる行**で、何も言えない（2026-09-20、ユーザーが発見）。しかも #15 では
    # 線と偶然ほぼ一致し、**同じ 1.20% が2行並んで「あと4件」に読めた。**
    #
    # **`CLAUDE.md`「落ちようのない検査を『合格』と読まない」の表版である。**
    targets = {round(committed, 4)}
    if floor_estimate > 0:
        # **見込みの下限。** #12・#13 の「下限を検出するには何年要るか」と同じ。
        targets.add(round(floor_estimate, 4))

    def needed_for(effect: float) -> Requirement:
        # **手元の枠は1組だけ渡す。** ここで2つ目の率を作らない。
        return requirement(
            effect,
            estimate.daily_sd,
            estimate.inflation,
            have_periods=periods,
            have_years=period_years,
            target_t=target,
        )

    if mean <= 0:
        # **向きが逆なら、期数の話ではない。** 増やしても通らない。
        hypothetical = needed_for(committed)
        console.print()
        console.print(
            f"[dim]**期数の問題ではない。** 取り高が {mean:+.2%} で、"
            "**向きが逆である。** 増やしても、この向きのままなら通らない。"
            f"（仮に線の大きさ {committed:.1%} が本当だったとすれば、要るのは "
            f"{hypothetical.periods:,} イベント日＝{hypothetical.years:,.1f}年。"
            f"手元は {periods:,} 日＝{period_years:,.1f}年。"
            "**これは「あと少し」という意味ではない。**）[/]"
        )
        if footnote:
            console.print(footnote)
        return

    console.print()
    # **「いま在る」を表の中に置く。** 外に置くと、読む側が別の標本の数字と
    # 突き合わせる（脚注の「手元は IS 5.0年」がそれだった）。
    lines: list[tuple[str, bool, int, float]] = []
    for effect in sorted(targets):
        row = needed_for(effect)
        gated = floor_estimate > 0 and effect == round(floor_estimate, 4)
        label = (
            f"見込みの下限 1イベント {effect:.2%}"
            if gated
            else f"コミットした線 1イベント {effect:.2%}"
        )
        lines.append((label, gated, row.periods, row.years))
    console.print(
        _needed_table(
            "§0 を通すのに要るイベント日数（判定に使う窓で数える）",
            "イベント日",
            lines,
            periods,
            period_years,
        )
    )
    if floor_estimate <= 0:
        # **関門の行が出ない。** 参考の行だけ残ると、また同じ誤読になる。
        console.print(
            "[dim]**関門の行は出していない。** 見込みの下限が "
            f"{floor_estimate:+.2%} で、**0 以下では要る期数が決まらない**"
            "——増やしても通らない。上の行は「線の大きさの効果なら見分けられたか」"
            "を言っているだけである。[/]"
        )
    if footnote:
        console.print(footnote)


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
    start: str | None = typer.Option(
        None,
        "--start",
        help="First snapshot date. Defaults to the oldest the plan can reach.",
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
    fill_lending: bool = typer.Option(
        False,
        "--fill-lending",
        help="Re-request only the saved dates that carry no lending class.",
    ),
    existing: bool = typer.Option(
        False,
        "--existing",
        help="Re-request exactly the dates already saved. Ignores the date grid.",
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

    # **既定の開始日をプランから引く。** 固定値にすると、プランを上げた日に
    # 何も起きない——例外も警告も出ないまま、窓の外だと判断して古い日付を要求
    # しない。20年ぶん払って5年ぶんだけ落とす形になる。
    first = earliest_reachable(settings.jquants_plan) if start is None else _parse_date(start)
    if first is None:
        raise typer.BadParameter(f"--start must be YYYY-MM-DD; got {start!r}.")
    if start is None:
        console.print(
            f"[dim]開始日は JQUANTS_PLAN=[bold]{settings.jquants_plan}[/] から "
            f"{first}。違うなら .env を直すか --start で渡す。[/]"
        )
    last = dt.date.today() if end is None else _parse_date(end)
    if last is None:
        raise typer.BadParameter(f"--end must be YYYY-MM-DD; got {end!r}.")
    target = Path(directory)
    if existing:
        # **「いまあるものを取り直す」は、「グリッドを回す」とは別の仕事である。**
        #
        # `--refetch` だけを渡すと、日付はプランから引き直される。Premium に
        # 上げた日にそれをやって、**66枚を揃えるつもりが 2006-09-20 からの
        # 245枚を新しく作った**（2026-09-15）。古いグリッドと新しいグリッドが
        # 同居し、規則も2通りになった——**揃えるどころか、混ざり方が増えた。**
        #
        # 開始日をプランから引くのは意図した設計である（上げた日に効くように）。
        # **間違っていたのは、それを取り直しに当てはめたことである。**
        wanted = sorted(stored_dates(target))
        refetch = True
        if not wanted:
            console.print("[yellow]名簿が1枚も無い。[/] 取り直す相手がいない。")
            return
        console.print(
            f"保存済みの [bold]{len(wanted)}[/] 日ぶんを、**いまの規則で**取り直す"
            f"（{wanted[0]} 〜 {wanted[-1]}）。[dim] 日付は増やさない。[/]"
        )
    elif fill_lending:
        # **取り直す対象を日付グリッドで決めない。** 日次で書かれる名簿は
        # 30日刻みに乗らないので、グリッドで回すと取り残される。実際、63件を
        # 取り直したあとに直近3日ぶんだけが残った。
        wanted = dates_without_lending(target)
        refetch = True
        if not wanted:
            console.print("[green]貸借区分の欠けている名簿は無い。[/] 取りに行かない。")
            return
        console.print(f"貸借区分の欠けている名簿だけを取り直す（[bold]{len(wanted)}[/] 日ぶん）。")
    else:
        try:
            wanted = snapshot_dates(first, last, step_days)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
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

    # **境界は API が知っている。** 断られた文面が「ここからなら取れる」と言って
    # いるなら、その日を1回だけ取りに行く。刻み幅から逆算すると、たまたま刻みが
    # 乗った日を境界だと思い込む（30日刻みでは 2021-10-01 が最初になったが、
    # 本当の境界は 2021-09-04 で、4週間ぶん取り逃していた）。
    # 窓は毎日後ろへ動くので、いま取らなければ二度と取れない。
    boundaries = {
        edge for message in report.refused.values() if (edge := covered_from(message)) is not None
    }
    extra = min(boundaries) if boundaries else None
    if extra is not None and extra not in membership(target):
        console.print(
            f"[yellow]断られた文面が「{extra} 以降なら取れる」と言っている。[/] "
            "その日を取りに行く（窓は毎日後ろへ動くので、いま取らないと二度と取れない）。"
        )
        boundary_report = harvest_snapshots(target, [extra], fetch)
        report.written.extend(boundary_report.written)
        report.profiles.update(boundary_report.profiles)
        for when, why in boundary_report.refused.items():
            report.refused[when] = why

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
    # **ディスクにある名簿すべてを見る。** report.union は「今回要求した日付」
    # しか集めないので、要求しなかった日付の名簿にしか出ない銘柄が漏れる。
    # 実際に漏れた（4,097 と報告して、実際は 4,124）。
    profiles = all_profiles(target)
    missing = sorted(set(profiles) - covered)
    console.print(
        f"名簿にあって DB に株価が無い銘柄: [bold]{len(missing):,}[/] 件"
        f"（名簿 {len(profiles):,} 件中）。"
    )
    if not missing:
        console.print("[green]取り込むものは無い。[/]")
        return
    if limit:
        missing = missing[:limit]
        console.print(f"[yellow]--limit により {len(missing)} 件だけ取る。[/]")

    store_universe(database, [profiles[symbol] for symbol in missing])
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


@app.command(name="universe-snapshot")
def universe_snapshot(
    directory: str = typer.Option(
        str(TACHIBANA_SNAPSHOT_DIR), "--dir", help="Where the monthly rosters are kept."
    ),
    universe_source: str | None = typer.Option(
        None, "--source", help="Override JP_UNIVERSE_SOURCE: jquants | tachibana."
    ),
    force: bool = typer.Option(False, "--force", help="Rewrite this month's file if it exists."),
) -> None:
    """Keep one listing roster per month, so delistings stay recoverable.

    Tachibana's master returns currently-listed names only, which is why
    survivorship bias could not be fixed from it. **But saving one roster a
    month makes the difference between consecutive months the delistings.**
    When the J-Quants rosters stop at 2026-09, this is what keeps the record
    going.

    Safe to call every day: the file is named by month and an existing month is
    left alone. A routine that has to be *remembered* monthly is a routine that
    eventually is not.

    Written to its own directory, apart from the J-Quants rosters. The two
    exclude different things - J-Quants drops 396 fund and index listings,
    Tachibana filters by listing section - so differencing across the join
    would report names as delisted that never left.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    target = Path(directory)
    try:
        source = _universe_source(settings, universe_source)
        profiles = source.profiles(Segment.ALL)
    except DataError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc

    written = monthly_snapshot(target, profiles, force=force)
    if written is None:
        console.print(
            f"[dim]{dt.date.today():%Y-%m} の名簿は既にある（{target}）。書かない。[/dim]"
        )
    else:
        console.print(
            f"[green]{written.name}[/] に [bold]{len(profiles):,}[/] 銘柄を保存した"
            f"（取得元: {source.name}）。"
        )
        console.print("[dim]取り直せないデータなので、git にコミットして残す。[/dim]")

    stored = monthly_membership(target)
    if len(stored) < 2:
        console.print(
            f"[dim]月次の名簿は {len(stored)} 件。2件たまると、差分が"
            "「その月に消えた銘柄」になる。[/dim]"
        )
        return

    months = sorted(stored)
    table = Table(title="月次の名簿の差 = その月に消えた銘柄")
    for column in ("期間", "前", "後", "消えた", "増えた"):
        table.add_column(column, justify="left" if column == "期間" else "right")
    for earlier, later in list(zip(months, months[1:], strict=False))[-12:]:
        gone = stored[earlier] - stored[later]
        added = stored[later] - stored[earlier]
        table.add_row(
            f"{earlier} → {later}",
            f"{len(stored[earlier]):,}",
            f"{len(stored[later]):,}",
            f"[bold]{len(gone):,}[/]",
            f"{len(added):,}",
        )
    console.print(table)


def _report_plan(found: dict[str, list[BulkFile]]) -> None:
    """Name the plan from how far back the files go, and the pace it allows.

    Counting the files that came back beats asking anyone to recall which plan
    they are on. The plan fixes the per-minute ceiling, and the ceiling fixes
    the interval a per-symbol loop may use. The delisted harvest stopping at 84
    symbols is consistent with the default 0.5s - 120 a minute - sitting exactly
    on Standard's ceiling with no headroom at all.
    """
    spans = [years for years in map(bulk_span_years, found.values()) if years is not None]
    if not spans:
        return

    widest = max(spans)
    plan = infer_plan(widest)
    console.print()
    if plan is None:
        console.print(
            f"[yellow]覆っているのは約 {widest:.1f} 年ぶん。[/] "
            "プランの区切り（Light 5年 / Standard 10年 / Premium 20年）の"
            "どれにも寄らないので、上限は決め打ちしない。"
        )
        return

    limit = PLAN_REQUESTS_PER_MINUTE[plan]
    interval = recommended_throttle(plan)
    console.print(
        f"覆っているのは約 [bold]{widest:.1f}[/] 年ぶん。"
        f"契約はおそらく [bold]{plan}[/]（1分あたり [bold]{limit}[/] 回）。"
    )
    if interval is not None:
        console.print(
            f"[dim]銘柄ごとに叩くなら1件 [bold]{interval:.1f}[/] 秒あける。"
            f"いまの既定は 0.5 秒＝120回／分で、"
            + (
                "上限ちょうどで余裕が無い。"
                if limit == 120
                else f"{plan} の上限の {120 / limit:.1f} 倍にあたる。"
                if limit < 120
                else "余裕がある。"
            )
            + "大幅超過が続くと約5分あいだ完全に遮断される。[/dim]"
        )


def _differing_symbols(first: Path, second: Path, dates: list[dt.date]) -> set[str]:
    """Collect every symbol that only one of the two rosters carries.

    ``dates`` で、片方の名簿にしか出ない銘柄をすべて集める。

    表に出す例は先頭5件に絞ってあるが、**説明が付くかを見るには全部が要る。**
    5件を見て「全部 TOKYO PRO だ」と言うのは、5件を見ただけである。
    """
    found: set[str] = set()
    for date in dates:
        left = {profile.symbol for profile in read_snapshot(snapshot_path(first, date))}
        right = {profile.symbol for profile in read_snapshot(snapshot_path(second, date))}
        found |= (left - right) | (right - left)
    return found


def _roster_span(directory: Path, symbols: tuple[str, ...]) -> dict[str, tuple[dt.date, dt.date]]:
    """Return the first and last roster date each symbol appears on.

    **いつ名簿に載っていたか**が分かれば、株価が無い理由に見当が付く。全部が
    同じ日で終わっていれば廃止の取りこぼし、1日しか出ていなければ名簿側の
    ゆらぎ、といった具合である。
    """
    seen: dict[str, list[dt.date]] = {}
    wanted = set(symbols)
    for on, codes in membership(directory).items():
        for symbol in wanted & codes:
            seen.setdefault(symbol, []).append(on)
    return {symbol: (min(days), max(days)) for symbol, days in sorted(seen.items())}


def _progress_line(index: int, total: int, key: str, width: int = 100) -> str:
    """One-line progress text, padded so a shorter line clears the last one.

    **進捗は1行に収める。** 復帰文字で上書きするので、短い行のあとに長い行の
    尻尾が残る。実際に `fins_summary_20260904.csv.gz08.csv.gz` という表示が
    出た。

    見えている文字だけを詰める。**タグごと詰めると閉じタグを切る。**
    """
    text = f"{index}/{total} {key}"
    return text[: width - 1] + "…" if len(text) > width else text.ljust(width)


def _existing_parent(path: Path) -> Path:
    """Return ``path`` or its nearest existing ancestor.

    ``path`` か、その一番近い既存の親。

    空き容量は**これから作るディレクトリでは測れない。** 測れないまま黙って
    進むと、容量の話が出力から消える。
    """
    current = path.resolve()
    while not current.exists() and current != current.parent:
        current = current.parent
    return current


def _bytes_label(total: int) -> str:
    """Format a byte count without rounding a real endpoint down to zero.

    バイト数を読める単位で。**0 に丸めない。**

    下見で `investor-types` が 61 本あるのに「0 MB」と出た。四捨五入である
    ことは合っているが、**「取れなかった」と同じ見た目になる。** 数十本の
    エンドポイントが 0 と並んでいたら、そこで手が止まる——止まる先が課金中
    だと高い。
    """
    if total >= 1_000_000_000:
        return f"{total / 1_000_000_000:,.2f} GB"
    if total >= 10_000_000:
        return f"{total / 1_000_000:,.0f} MB"
    if total >= 1_000_000:
        return f"{total / 1_000_000:,.1f} MB"
    if total >= 1_000:
        return f"{total / 1_000:,.0f} KB"
    return f"{total:,} B"


@app.command(name="jquants-archive")
def jquants_archive(
    endpoints: list[str] = typer.Option(  # noqa: B008 - typer builds the default list
        list(ARCHIVE_ENDPOINTS), "--endpoint", help="Repeat to pick. Default: everything."
    ),
    directory: str = typer.Option(
        str(DEFAULT_ARCHIVE_DIR), "--dir", help="Where the raw files are kept."
    ),
    throttle: float | None = typer.Option(
        None, "--throttle", help="Seconds between files. Default: from JQUANTS_PLAN."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="List and size only. Fetch nothing."),
) -> None:
    """Save the bulk files as they arrive, before the plan that serves them ends.

    **取得は1回きり、解析は何度でもやり直せる。** 契約は 2026-09-22 で終わるが、
    パーサの誤りは10月にも11月にも見つかる。**原本が無ければ、そのとき取り返せ
    ない。**

    展開しない。CSV に直さない。**返ってきたバイト列をそのまま書く。**
    名簿を CSV で残したのと同じ理屈である。

    途中で止めても安全に再開できる。既にあって大きさの合うファイルは落としに
    行かない。**大きさが合わないものは落とし直す**——「ファイルがある」と
    「中身が揃っている」は別で、転送が途中で切れても例外が出ないことがある。

    `--dry-run` は1バイトも落とさずに本数と合計サイズだけを出す。**先に回す
    こと。** 何本・何MBかを知らずに始めない。
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    target = Path(directory)
    wanted = [item.strip() for item in endpoints if item.strip()]
    if not wanted:
        raise typer.BadParameter("--endpoint に1つ以上要る。")

    # **間隔をプランから引く。** 0.5 秒は 120回/分で、**Light の上限の2倍**
    # である。2026-09-07 のリハーサルで、384本のうち74本が 429 で落ちた。
    # `recommended_throttle` は前からあったのに、この口が呼んでいなかった
    # ——「CLI側が新しい設定を配線し忘れる」型そのものである。
    plan = (settings.jquants_plan or "").strip().capitalize()
    if throttle is None:
        throttle = recommended_throttle(plan) or 1.2
        console.print(
            f"[dim]間隔は JQUANTS_PLAN=[bold]{settings.jquants_plan}[/] から "
            f"{throttle:.2f} 秒（{PLAN_REQUESTS_PER_MINUTE.get(plan, '?')} 回/分の上限に"
            f"余裕を見た値）。[/]"
        )

    console.print(f"原本の置き場所: [bold]{target}[/]　／　対象 {len(wanted)} エンドポイント")
    console.print(
        "[dim]**展開せずにそのまま保存する。** 解析は後から何度でもやり直せるが、"
        "取得は 2026-09-22 で終わる。[/]"
    )

    table = Table(title="一覧（1バイトも落としていない）")
    for column in ("エンドポイント", "本数", "覆う範囲", "合計"):
        table.add_column(column, justify="left" if column == "エンドポイント" else "right")

    found: list[BulkFile] = []
    refused: dict[str, str] = {}
    for name in wanted:
        try:
            files = bulk_list_files(settings.jquants_api_key, endpoint=name)
        except Exception as exc:  # noqa: BLE001 - 断られ方そのものが記録に値する
            # **例外の型名だけを出さない。** `DataError` とだけ書くと、
            # 「プランに入っていない」と「エンドポイント名が違う」が同じに
            # 見える。2026-09-06 の下見では後者が6本混じっていて、断られ方の
            # 中身を出していなかったせいで気付くのが1手遅れた。
            refused[name] = f"{type(exc).__name__}: {exc}"
            table.add_row(name, "[yellow]—[/]", f"[yellow]{type(exc).__name__}[/]", "")
            continue
        span = bulk_coverage(files)
        table.add_row(
            name,
            f"{len(files):,}",
            f"{span[0]} 〜 {span[1]}" if span else "—",
            _bytes_label(sum(item.size for item in files)),
        )
        found.extend(files)
    console.print(table)

    total_bytes = sum(item.size for item in found)
    console.print(f"合計 [bold]{len(found):,}[/] 本、[bold]{_bytes_label(total_bytes)}[/]。")

    if found:
        # **本数と MB だけでは、何日ぶんの作業かが分からない。** 契約が8日
        # しかないので、そこがそのまま判断の材料になる。
        floor_seconds = len(found) * max(throttle, 0.0)
        console.print(
            f"[dim]間隔 {throttle:.2f} 秒なら、待ち時間だけで最短 "
            f"[bold]{floor_seconds / 60:,.0f} 分[/]。**転送の時間は別に乗る。**[/]"
        )
        try:
            free = shutil.disk_usage(_existing_parent(target)).free
        except OSError:
            free = None
        if free is not None:
            # 展開しないので、必要なのは一覧の合計とほぼ同じ。倍を目安に
            # するのは、取り直しと、この先エンドポイントが増えるぶんである。
            enough = free > total_bytes * 2
            console.print(
                f"[dim]置き場所の空き [bold]{_bytes_label(free)}[/]。[/]"
                if enough
                else f"[yellow]置き場所の空きが {_bytes_label(free)} しかない。[/] "
                "**途中で書けなくなると、切れたファイルが残る。**"
            )

    if refused:
        # **断られ方は判断の材料である。** 定型ではないので削らない。
        why = Table(title=f"落ちた理由 ({len(refused)})")
        why.add_column("エンドポイント")
        why.add_column("断られ方")
        for name, reason in refused.items():
            why.add_row(name, reason[:120])
        console.print(why)
        console.print(
            "[dim]**「プランに入っていない」と「名前が違う」を読み分けること。**"
            "プランで説明が付かない1本が混じっていたら、名前を疑う。[/]"
        )

    if dry_run:
        console.print("[dim]--dry-run なので、ここで止める。1バイトも落としていない。[/]")
        return
    if not found:
        console.print("[yellow]落とすものが無い。[/]")
        return

    def show(index: int, total: int, key: str) -> None:
        # **進捗は1行に収める。** 途中経過を残す形にすると、貼ったときに
        # 何百行にもなる。
        console.print(f"[dim]{_progress_line(index, total, key)}[/]", end="\r")
        if throttle:
            time.sleep(throttle)

    report = archive_bulk(
        found,
        lambda key: download_raw(settings.jquants_api_key, key),
        target,
        progress=show,
    )
    console.print()
    console.print(report.summary())

    if report.truncated:
        console.print(
            f"[yellow]大きさが合わないまま残したもの {len(report.truncated)} 本。[/] "
            "もう一度実行すると落とし直す。"
        )
    if report.failed:
        table = Table(title=f"落とせなかった ({len(report.failed)})")
        table.add_column("key")
        table.add_column("断られ方")
        for key, why in list(report.failed.items())[:20]:
            table.add_row(key, why)
        console.print(table)
        console.print("[dim]再実行すると、落とせたものは飛ばして続きから取る。[/]")

    console.print()
    console.print(
        "[dim]原本は git で追跡していない（大きすぎる）。**追跡しない = 消えてよい、"
        "ではない。** 2026-09-22 を過ぎたら取り直せないので、外付けか同期フォルダに"
        "写しを1つ置くこと。写した先でも `原本の照合.bat` で欠けを見つけられる。[/]"
    )


@app.command(name="jquants-archive-read")
def jquants_archive_read(
    directory: str = typer.Option(
        str(DEFAULT_ARCHIVE_DIR), "--dir", help="Where the raw files are kept."
    ),
    shapes: bool = typer.Option(
        True, "--shapes/--no-shapes", help="Also show one file's columns per endpoint."
    ),
    columns_of: str | None = typer.Option(
        None, "--columns", help="Print every column of this endpoint, one per line."
    ),
) -> None:
    """Read every archived original through its parser and count the rows.

    **「落とせた」と「読めた」は別である。** このプロジェクトは2日で2回それを
    踏んでいる。原本を保存したあとで読み口が繋がっていないと分かるのが、
    いちばん高い。

    **配布サンプルで通ったことは、実物で通ったことにならない。** サンプルは
    1〜6行しかなく、月次の全銘柄ファイルとは形が違いうる。文字コードも、
    サンプルが cp932 だったからといって一括ファイルもそうとは限らない。

    落とすことはしない。**解約後にも実行できる。**
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    target = Path(directory)
    reports = archive_census(target)
    if not reports:
        console.print("[yellow]原本がまだ無い。[/] 先に `原本をまるごと保存.bat`。")
        return

    table = Table(title="原本を読み口に通す（1バイトも落としていない）")
    for column, justify in (
        ("エンドポイント", "left"),
        ("本", "right"),
        ("行", "right"),
        ("状態", "left"),
    ):
        table.add_column(column, justify=justify)

    unread = 0
    for endpoint in sorted(reports):
        report = reports[endpoint]
        if report.rows:
            state = "[green]読めた[/]"
            if report.empty:
                state += f"  [yellow]中身なし {len(report.empty)} 本[/]"
            if report.failed:
                state += f"  [red]読めず {len(report.failed)} 本[/]"
        elif report.failed and all(why == "読み口が無い" for why in report.failed.values()):
            # **消さずに出す。** 読み口が無いことと、読めないことは別である。
            state = "[dim]読み口を作っていない[/]"
            unread += report.files
        else:
            state = f"[red]読めなかった {len(report.failed)} 本[/]"
        table.add_row(endpoint, f"{report.files:,}", f"{report.rows:,}", state)
    console.print(table)

    if unread:
        console.print(
            f"[dim]{unread:,} 本は読み口を作っていない（株価・名簿・財務は既存の"
            "取り込みが読む）。**保存はできている。**[/]"
        )

    broken = {
        key: why
        for report in reports.values()
        for key, why in report.failed.items()
        if why != "読み口が無い"
    }
    if broken:
        problems = Table(title=f"読めなかった ({len(broken)})")
        problems.add_column("key")
        problems.add_column("理由")
        for key, why in list(broken.items())[:10]:
            problems.add_row(key, why[:80])
        console.print(problems)

    if columns_of:
        # **切らずに出す。** 読み口を作るには全部の列が要る。配布サンプルの
        # 無いエンドポイント（`/equities/valuation`）は、ここでしか列を知れない。
        wanted = "/" + columns_of.strip().lstrip("/")
        keys = samples_per_endpoint(target).get(wanted, ())
        if not keys:
            console.print(f"[yellow]{wanted} の原本が無い。[/]")
            console.print("[dim]  上の表に出ている名前をそのまま渡すこと。[/]")
            return
        found = archive_shape(target, keys[0])
        if found is None:
            console.print(f"[yellow]{wanted} を読めなかった。[/]")
            return
        console.print()
        console.print(f"[bold]{wanted} の列（全 {len(found.columns)}）[/] [dim]{keys[0]}[/]")
        for index, name in enumerate(found.columns, start=1):
            console.print(f"  {index:2}. {name}")

    if not shapes:
        return

    console.print()
    forms = Table(title="1本ずつ見た形（実物であって、配布サンプルではない）")
    for column in ("エンドポイント", "行", "文字", "日付の範囲", "列"):
        forms.add_column(column, justify="right" if column == "行" else "left")
    for endpoint, keys in sorted(samples_per_endpoint(target).items()):
        for key in keys:
            found = archive_shape(target, key)
            if found is None:
                continue
            span = f"{found.first_date} 〜 {found.last_date}" if found.first_date else "—"
            # **表では5列で切る。** 20列を横に並べると表が壊れる。全部見たい
            # ときは `--columns` を渡す——**読み口を作るには全部要る。**
            columns = ", ".join(found.columns[:5]) + (
                f"… (全 {len(found.columns)} 列)" if len(found.columns) > 5 else ""
            )
            # **月次か日次かを名前で出す。** 1本が何日ぶんかで、20年の本数が
            # 20倍変わる。
            kind = next((name for name in ("historical", "live") if f"/{name}/" in key), "—")
            forms.add_row(
                f"{endpoint}  [dim]{kind}[/]",
                f"{found.rows:,}",
                found.encoding,
                span,
                columns,
            )
    console.print(forms)


@app.command(name="jquants-daily-rosters")
def jquants_daily_rosters(
    archive_dir: str = typer.Option(
        str(DEFAULT_ARCHIVE_DIR), "--dir", help="Where the raw files are kept."
    ),
    out_dir: str = typer.Option(
        str(DAILY_SNAPSHOT_DIR), "--out", help="Where the daily rosters are written."
    ),
    refetch: bool = typer.Option(False, "--refetch", help="Rewrite dates that already exist."),
) -> None:
    """Turn the archived bulk master into one roster per trading day.

    **一括ファイル1本の中に、その月の全営業日ぶんが入っている。** 2026-08 の
    1本が 88,870 行で、4,441銘柄 × 20営業日である。

    いまディスクにある名簿は JSON API を30日刻みで叩いた66枚だが、**同じ5年
    ぶんが一括には約1,220枚（全営業日）入っている。** 廃止が「どの月か」から
    「どの日か」になる。

    **API を1回も叩かない。** 原本さえあれば、解約後にも実行できる。

    書き出し先は `universe_snapshots/` とは別である。**混ぜない**——絞り込み
    が食い違ったとき、境目をまたいだ差が「消えてもいない銘柄が消えた」になる。
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    source, target = Path(archive_dir), Path(out_dir)
    console.print(f"原本: [bold]{source}[/]　／　書き出し先: [bold]{target}[/]")
    console.print(
        "[dim]**API を1回も叩かない。** 原本から取り出すだけなので、解約後にも実行できる。[/]"
    )

    def show(index: int, total: int, key: str) -> None:
        # **進捗は1行に収める。**
        console.print(f"[dim]{_progress_line(index, total, key)}[/]", end="\r")

    report = roster_extract(source, target, refetch=refetch, progress=show)
    console.print()
    console.print(report.summary())

    if report.written:
        first, last = min(report.written), max(report.written)
        console.print(f"覆う範囲: [bold]{first} 〜 {last}[/]")
    if report.undated:
        # **列名が変わった疑いである。** 黙って捨てると、その月だけ薄くなる。
        console.print(
            f"[yellow]日付を読めない行が {report.undated:,} 行あった。[/] "
            "**列名が変わった疑いがある。** 原本を1本見ること。"
        )
    if report.empty:
        console.print(
            f"[yellow]絞り込みのあと1銘柄も残らなかった日が {len(report.empty)} 日。[/] "
            "書いていない——書くと、その日に全銘柄が廃止したように見える。"
        )
    if report.failed:
        table = Table(title=f"読めなかった ({len(report.failed)})")
        table.add_column("key")
        table.add_column("理由")
        for key, why in list(report.failed.items())[:10]:
            table.add_row(key, why[:80])
        console.print(table)

    existing = len(stored_dates(Path(DEFAULT_SNAPSHOT_DIR)))
    written = len(stored_dates(target))
    if written:
        console.print(
            f"[dim]30日刻みの名簿は {existing} 枚、営業日ごとは [bold]{written}[/] 枚。"
            "**別のフォルダに置いてある。** 混ぜると、絞り込みの違いが"
            "「消えてもいない銘柄が消えた」に化ける。[/]"
        )

    # **別の経路で作った同じものを突き合わせる。** 片方だけを見ているかぎり、
    # 絞り込みの食い違いは「銘柄数がちょっと違う」としか見えず、それは毎日
    # 変わる値なので区別が付かない。
    console.print()
    check = roster_compare(Path(DEFAULT_SNAPSHOT_DIR), target)
    console.print(f"[bold]突き合わせ[/]: {check.summary()}")
    if check.differing:
        table = Table(title=f"食い違った日 ({len(check.differing)})")
        for column in ("日付", "30日刻みだけ", "営業日ごとだけ", "例"):
            table.add_column(column)
        for date, (left, right) in list(check.differing.items())[:10]:
            sample = check.examples.get(date, ([], []))
            table.add_row(
                str(date),
                f"{left}",
                f"{right}",
                ("−" + "、".join(sample[0]) if sample[0] else "")
                + ("　+" + "、".join(sample[1]) if sample[1] else ""),
            )
        console.print(table)
        # **「たぶん◯◯だろう」で閉じない。** 片方にしか無い銘柄の市場区分を
        # 原本から引いて、説明が付くかどうかを数える。1件でも別のものが混じって
        # いれば、そこは説明できていない。
        gap = _differing_symbols(Path(DEFAULT_SNAPSHOT_DIR), target, list(check.differing))
        # **日付ごとの差を1度だけ作る。** 銘柄×日付で引き直すと、同じファイル
        # を何百回も読むことになる。
        per_date = {
            date: _differing_symbols(Path(DEFAULT_SNAPSHOT_DIR), target, [date])
            for date in sorted(check.differing)
        }
        # **市場は移る。** TOKYO PRO Market に上場してから、数年後にスタンダード
        # やグロースへ変わる銘柄がある。最後に見えた姿で引くと、当時 TOKYO PRO
        # だった銘柄が「スタンダード」と出て、説明の付くものが付かなくなる。
        history = markets_on(source, gap) if gap else {}
        excluded = {
            symbol
            for symbol in gap
            if all(
                "TOKYO PRO" in (market_on(history.get(symbol, {}), date) or "").upper()
                for date, gap_on in per_date.items()
                if symbol in gap_on
            )
        }
        markets = {
            symbol: market_on(history.get(symbol, {}), max(check.differing)) or "" for symbol in gap
        }
        unexplained = sorted(gap - excluded)
        console.print(
            f"[dim]片方にしか無い銘柄は {len(gap)} 種類。"
            f"うち買えない市場（TOKYO PRO Market）が {len(excluded)}。[/]"
        )
        if unexplained:
            console.print(
                f"[yellow]説明の付かない銘柄が {len(unexplained)} ある。[/] "
                "**生存バイアスの計算に使う前に、ここを説明できるようにすること。**"
            )
            # **「6件ある」で止めない。** いつ食い違い、一括の名簿にそもそも
            # 出るのかまで出せば、上場直後のずれか、出所そのものの違いかが
            # 分かれる。前者は端の1日、後者は全期間である。
            detail = Table(title="説明の付かない銘柄")
            for column in ("銘柄", "いまの市場", "食い違った日の市場", "食い違った日"):
                detail.add_column(column)
            for symbol in unexplained[:10]:
                days = [str(date) for date, gap_on in per_date.items() if symbol in gap_on]
                # **その日の市場**を出す。いまの市場だけでは、移った銘柄が
                # 説明の付かないものに見える。
                when = [
                    market_on(history.get(symbol, {}), date) or "不明"
                    for date, gap_on in per_date.items()
                    if symbol in gap_on
                ]
                detail.add_row(
                    symbol,
                    markets.get(symbol) or "市場不明",
                    "、".join(sorted(set(when))),
                    "、".join(days[:3]) + ("…" if len(days) > 3 else ""),
                )
            console.print(detail)
        else:
            console.print(
                "[green]食い違いは、買えない市場を universe から外したぶんで"
                "全部説明が付く。[/] [dim]git にある名簿は、外す前の規則で"
                "作られている。[/]"
            )
    elif check.common:
        console.print(
            "[green]重なる日付では、2つの経路が同じ名簿を出している。[/] "
            "[dim]片方だけを見ていては確かめられないことである。[/]"
        )

    # **重ならなかった日付を「たぶん休日」で済ませない。** 30日刻みの日付は
    # 休日にも当たり、一括には立会日しか無いので重ならない。それは欠けでは
    # ない。だが立会日なのに名簿が無い日が混じっていたら、それは本当の欠けで
    # ある。**件数では区別が付かないので、カレンダーに当てる。**
    daily = sorted(stored_dates(target))
    grid_only = sorted(set(stored_dates(Path(DEFAULT_SNAPSHOT_DIR))) - set(daily))

    # **営業日ごとの名簿が始まる前の日付を「欠け」と呼ばない。**
    #
    # 一括の名簿は 2008-05-07 からしか無い。30日刻みの名簿にはそれより前の
    # 日付があり、立会日なら全部「本当の欠け」に落ちていた——実際 2008-02-12
    # と 2008-03-13 がそう出た（2026-09-15）。**欠けているのではなく、
    # こちらが始まっていない。**
    #
    # 「重なる期間だけ見る」を、同じ形で**3度目**に踏んだ。項目5・項目6 で
    # 直したのと同じ話である。
    before = [day for day in grid_only if daily and day < daily[0]] if daily else list(grid_only)
    inside = [day for day in grid_only if not daily or day >= daily[0]]

    if before:
        console.print(
            f"[dim]{len(before)} 日は、営業日ごとの名簿が始まる {daily[0]} より前"
            "（一括の名簿は 2008-05-07 から）。**欠けではない。**[/]"
        )
        # **それでも、その日の名簿が存在すること自体は確かめる価値がある。**
        # 一括が覆っていない日付を JSON 経路が返したことになる。同じ中身を
        # 使い回しているなら、日付の違う同じ名簿が並ぶ。
        grid_dir = Path(DEFAULT_SNAPSHOT_DIR)
        shapes = {
            frozenset(profile.symbol for profile in read_snapshot(snapshot_path(grid_dir, day)))
            for day in before
        }
        if len(shapes) == 1 and len(before) > 1:
            # **これは「疑い」ではなく、分かったことである（2026-09-15）。**
            #
            # 開始前の日付を投げても J-Quants は断らない。毎回**同じ名簿**を
            # 返す。断ってくれるなら気付けたが、返ってくるので気付けない。
            #
            # 日付の違う同じ名簿を並べて差を取れば、**消えてもいない銘柄が
            # 「消えた」になる。** 生存バイアスを直すための材料が、逆に歪みを
            # 入れる側に回る。
            console.print(
                f"[yellow]その {len(before)} 日は、**中身が1種類しかない。**[/] "
                "日付が違うのに同じ名簿である——**開始前の日付が効いていない。**"
            )
            console.print(
                "[yellow]  この "
                + "、".join(str(day) for day in before[:8])
                + ("…" if len(before) > 8 else "")
                + " は消してよい。[/] "
                "[dim]名簿としては使えず、**廃止の判定に混ぜると害になる。** "
                "日付グリッドの開始は上場銘柄一覧の開始（2008-05-07）で床を"
                "打つようにしたので、取り直しても増えない。[/]"
            )
        else:
            console.print(
                f"[dim]  中身は {len(shapes)} 種類あり、日付ごとに違う。"
                "**一括より前を JSON 経路が返している。**[/]"
            )

    if inside:
        holidays, gaps = explain_missing(inside, trading_days_from_archive(source))
        if holidays and not gaps:
            console.print(
                f"[dim]重ならなかった {len(inside)} 日は、**全部が非立会日**だった"
                "（取引カレンダーで確認）。欠けではない。[/]"
            )
        elif gaps:
            console.print(
                f"[yellow]立会日なのに営業日ごとの名簿が無い日が {len(gaps)} 日ある。[/] "
                + "、".join(str(day) for day in gaps[:10])
                + "。**これは本当の欠けである。**"
            )
        else:
            console.print(
                f"[dim]重ならなかった {len(inside)} 日は、取引カレンダーの原本が"
                "無いので説明できない。[/]"
            )


@app.command(name="jquants-bulk-prices")
def jquants_bulk_prices(
    archive_dir: str = typer.Option(
        str(DEFAULT_ARCHIVE_DIR), "--dir", help="Where the raw files are kept."
    ),
    limit: int | None = typer.Option(None, "--limit", help="Read only this many files. Try small."),
) -> None:
    """Load prices for every symbol from the archived bulk bars.

    **銘柄ごとに叩かない。** `BulkIngester` は1銘柄1リクエストで、この経路は
    2回止まっている——84銘柄で1回、3,700銘柄で1回、どちらも 429 である。
    20年 × 約4,400銘柄は1週間に収まらない。

    一括ファイルには**全銘柄の四本値が日付ごとに**入っている。**API を1回も
    叩かない**ので、解約後にも実行できる。

    保存するのは**生値**である。`get_prices` が読み出しのときに
    `adj_close / close` を掛けるので、ここで調整すると二重に掛かる。

    何度実行しても同じ結果になる。**DB は作り直せる**——原本と違って、ここでの
    失敗は安い。
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    source = Path(archive_dir)
    database = Database()
    database.create_all()

    console.print(f"原本: [bold]{source}[/]")
    console.print(
        "[dim]**API を1回も叩かない。** 一括ファイルから読むだけなので、解約後にも実行できる。[/]"
    )
    if limit:
        console.print(f"[yellow]{limit} 本だけ読む。[/] まず少数で試す形。")

    def show(index: int, total: int, key: str) -> None:
        # **進捗は1行に収める。**
        console.print(f"[dim]{_progress_line(index, total, key)}[/]", end="\r")

    with database.session() as session:
        repository = PriceRepository(session)
        report = price_ingest(
            source,
            lambda symbol, frame: repository.upsert_prices(symbol, frame, market="JP"),
            limit=limit,
            progress=show,
        )
        session.commit()

    console.print()
    console.print(report.summary())

    if report.skipped_no_close:
        # **売買が無かった日と、値が欠けた日は別である。** 数えて出す。
        console.print(
            f"[dim]終値の無い行 {report.skipped_no_close:,} を落とした。"
            "`0` を入れると「値がゼロになった日」として並ぶ。[/]"
        )
        if report.no_close_but_traded:
            # **もっともらしい数では足りない。** 売買が無ければ終値も出来高も
            # 無い。出来高だけあるなら、読み方か列の意味のどちらかが違う。
            console.print(
                f"[red]うち {report.no_close_but_traded:,} 行は出来高がある。[/] "
                "**「取引が無かった日」では説明が付かない。** 読み方か、向こうの"
                "列の意味が変わった疑いがある。原本を1本見ること。"
            )
        else:
            console.print(
                "[dim]どれも出来高が無い。**「その日は取引が無かった」で説明が付いている。**[/]"
            )
    if report.undated:
        console.print(
            f"[yellow]日付を読めない行が {report.undated:,} 行あった。[/] "
            "**列名が変わった疑いがある。**"
        )
    console.print(
        f"[dim]調整値は `AdjFactor` から組み立てた（分割の当たった行 {report.splits:,}）。[/]"
    )
    if not report.adj_c_rows:
        # **「一致した」と「比べていない」を混ぜない。**
        console.print(
            "[dim]ファイルに `AdjC` の列は無い（配布サンプルには有る）。"
            "**突き合わせる相手がいないので、組み立てが唯一の調整である。**[/]"
        )
    elif report.adj_mismatch:
        console.print(
            f"[yellow]`AdjC` のある {report.adj_c_rows:,} 行のうち "
            f"{report.adj_mismatch:,} 行が組み立てと違う。[/] どちらが正しいか"
            "を決めるまで、分割をまたぐ期間を分析に使わないこと。"
        )
    else:
        console.print(f"[green]`AdjC` のある {report.adj_c_rows:,} 行と、組み立てが一致した。[/]")
    if report.failed:
        table = Table(title=f"読めなかった ({len(report.failed)})")
        table.add_column("key")
        table.add_column("理由")
        for key, why in list(report.failed.items())[:10]:
            table.add_row(key, why[:80])
        console.print(table)

    if not report.symbols:
        return

    # **継ぎ目の検査は1日しか見ていない。** 期間の内側で起きた分割は、そこでは
    # 確かめられない。組み立てを `j >= d` で書いていたら、継ぎ目は綺麗なまま
    # 分割日だけが1日ずつずれる——どちらも例外は出ない。
    if report.split_table:
        with database.session() as session:
            repository = PriceRepository(session)
            on_split = price_split_day_returns(repository.get_prices, report.split_table)
        if on_split:
            # **「大きく動いた」だけでは判定にならない。** 権利落ち日に本当に
            # 25% 動く銘柄はあるし、分割以外の事由（併合・無償割当・合併）でも
            # 係数は立つ。規約を間違えていれば、動きは**ちょうど係数 - 1**に
            # なり、しかも**全件がそうなる。** 見るべきはそこである。
            # **係数が1に近すぎると、判定そのものができない。** 係数 0.997 なら
            # その日の動きが ±2% の中にありさえすれば「係数そのもの」に見える
            # ——動かなかった日が全部引っかかる。比べられない件は別に数える。
            checkable = [item for item in on_split if price_comparable(item[3])]
            skipped = len(on_split) - len(checkable)
            unapplied = [item for item in checkable if price_looks_unapplied(item[2], item[3])]
            loud = [item for item in on_split if abs(item[2]) > 0.2]
            middle = sorted(abs(item[2]) for item in on_split)[len(on_split) // 2]
            verdict = price_split_verdict(len(unapplied), len(checkable))

            if skipped:
                console.print(
                    f"[dim]係数が1に近すぎて比べられない権利落ち日が {skipped:,} 件"
                    f"（全 {len(on_split):,} 件のうち）。**一致しない、ではなく"
                    "比べていない。**[/]"
                )

            if verdict == "規約":
                # **全部の権利落ち日がずれる形。** `j >= d` と `j > d` の取り違え
                # なら、一部だけということはない。
                console.print(
                    f"[red]権利落ち日の動きが係数そのものになっている例が "
                    f"{len(unapplied)}/{len(checkable)}。[/] "
                    "**調整の組み立てがずれている。** "
                    + "、".join(f"{a} {b} {c:+.0%}(係数 {d})" for a, b, c, d in unapplied[:5])
                )
            elif verdict == "個別":
                # **規約の間違いなら全件がそうなる。** 少数なら、その銘柄の事情
                # である（係数の立つ日と値の付く日がずれている、など）。
                console.print(
                    f"[yellow]権利落ち日 {len(unapplied)}/{len(checkable):,} 件で、"
                    f"動きが係数そのものになっている。[/]"
                    "[dim] **全件ではないので、組み立ての規約ではない。** "
                    "銘柄ごとの事情として1件ずつ見ること。[/]"
                )
                for symbol, day, change, factor in unapplied[:5]:
                    console.print(f"  [dim]{symbol} {day} {change:+.1%}（係数 {factor:.6f}）[/]")
            else:
                console.print(
                    f"[green]権利落ち日 {len(checkable):,} 件は、どれも係数どおりの"
                    f"ずれ方をしていない[/]（値動きの中央値 {middle:.1%}）。"
                    "[dim] 組み立ては効いている。[/]"
                )
                if loud:
                    console.print(
                        f"[dim]うち {len(loud)} 件は 20% 以上動いているが、**どの係数とも"
                        "合わない**——本当に動いた日か、分割以外の事由である。[/]"
                    )

    # **取り込んだ日付そのものから取る。** ファイル名の並び順から引いていた
    # ときは、20年ぶんを入れたのに「継ぎ目 2021-09-01」と出た（2026-09-15）。
    # 原本は 2008-05 から覆っているので、そこが継ぎ目のはずである。
    #
    # 並び順は名前で決まる。向こうがファイル名の付け方を変えれば、いちばん古い
    # ファイルがいちばん前に来なくなる。**読んだ日付を使えば、名前に依らない。**
    boundary = min(report.dates) if report.dates else _archive_first_date(source)
    if boundary is None:
        return
    console.print()
    console.print(f"[dim]継ぎ目（原本が覆い始める日）: {boundary}[/]")

    sample = sorted(report.symbols)[:: max(1, len(report.symbols) // 100)][:100]
    with database.session() as session:
        repository = PriceRepository(session)
        jumps = price_join_returns(repository.get_prices, sample, boundary)

    if not jumps:
        console.print(
            "[dim]継ぎ目をまたぐ銘柄が無い。**確かめられない**——DB に原本より"
            "前の株価が入っていないだけかもしれない。[/]"
        )
        return

    big = [(symbol, value) for symbol, value in jumps if abs(value) > 0.2]
    typical = sorted(abs(value) for _symbol, value in jumps)[len(jumps) // 2]
    if big:
        console.print(
            f"[red]継ぎ目の日に 20% 以上動いた銘柄が {len(big)}/{len(jumps)}。[/] "
            "**分割調整の基準が出所で違う疑いがある。** "
            + "、".join(f"{symbol} {value:+.0%}" for symbol, value in big[:5])
        )
        console.print(
            "[yellow]このまま分析に使わないこと。[/] 立花と J-Quants のどちらか"
            "1つに揃えるか、継ぎ目より前を使わないかを決める。"
        )
    else:
        console.print(
            f"[green]継ぎ目の日は普通の1日に見える[/]（{len(jumps)} 銘柄、"
            f"値動きの中央値 {typical:.1%}）。"
            "[dim] 分割調整の基準は、出所をまたいでも揃っている。[/]"
        )


def _archive_window(directory: Path) -> tuple[dt.date, dt.date, str, str] | None:
    """Return the first and last date the archived bars cover, with the keys used.

    原本が覆う期間。**取り込みの報告と DB を突き合わせる相手**になる。

    **どの原本から取ったかも返す。** 間違った1本を選んでいても、日付だけでは
    気付けない——2026-09-15 がそうだった。
    """
    from stock_ai.data.jquants_prices import BARS_ENDPOINT

    keys = [key for key in read_manifest(directory) if endpoint_of(key) == BARS_ENDPOINT]
    if not keys:
        return None
    ordered = sorted(keys, key=lambda key: (key_period(key), key))
    first = archive_shape(directory, ordered[0])
    last = archive_shape(directory, ordered[-1])
    if first is None or last is None or not first.first_date or not last.last_date:
        return None
    start, end = _parse_date(first.first_date), _parse_date(last.last_date)
    if not start or not end:
        return None
    return (start, end, ordered[0], ordered[-1])


def _archive_first_date(directory: Path) -> dt.date | None:
    """Return the first date the archived bars cover.

    原本が覆い始める日。**継ぎ目はここである。**

    **並び順は名前で決まる。** 向こうがファイル名の付け方を変えると、いちばん
    古いファイルがいちばん前に来なくなる。実際そうなった——20年ぶんを入れた
    のに「継ぎ目 2021-09-01」と出た（2026-09-15）。原本は 2008-05 から覆って
    いる。

    **取り込んだ日付が手元にあるなら、そちらを使うこと。** 名前に依らない。
    ここは、取り込みを回さずに聞きたいときの控えである。
    """
    from stock_ai.data.jquants_prices import BARS_ENDPOINT
    from stock_ai.data.jquants_read import endpoint_of

    manifest = read_manifest(directory)
    keys = [key for key in sorted(manifest) if endpoint_of(key) == BARS_ENDPOINT]
    if not keys:
        return None
    found = archive_shape(directory, keys[0])
    if found is None or not found.first_date:
        return None
    return _parse_date(found.first_date)


@app.command(name="jquants-revision-census")
def jquants_revision_census(
    archive_dir: str = typer.Option(
        str(DEFAULT_ARCHIVE_DIR), "--dir", help="Where the raw files are kept."
    ),
    show: int = typer.Option(12, "--show", help="How many document types to list."),
) -> None:
    """Count whether forecast revisions arrive on their own day.

    **説#5 を閉じた理由そのものを測る。** 記録にはこうある。

        予想修正は**独立した開示として取得できない**。イベント日が決算発表日
        と重なる。「決算とは独立」という前提が崩れる。

    公式の書類種別一覧には `EarnForecastRevision`（業績予想の修正）が**独立
    した種別として載っている。** 載っていることと、別の日に出ることは別で
    ある——**後者を数える。**

    落とすことはしない。保存済みの `fins/summary` を読むだけである。
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    source = Path(archive_dir)
    keys = [key for key in sorted(read_manifest(source)) if endpoint_of(key) == "/fins/summary"]
    if not keys:
        console.print("[yellow]`/fins/summary` の原本が無い。[/]")
        return

    census = RevisionCensus()
    for index, key in enumerate(keys, start=1):
        console.print(f"[dim]{_progress_line(index, len(keys), key)}[/]", end="\r")
        try:
            count_revisions(parse_details(read_archived(path_for(source, key))), census)
        except Exception as exc:  # noqa: BLE001 - どこで読めないかが記録に値する
            console.print(f"[yellow]{key}: {type(exc).__name__}[/]")

    console.print()
    table = Table(title=f"書類種別（{len(keys)} 本ぶん）")
    table.add_column("書類種別")
    table.add_column("件数", justify="right")
    table.add_column("説明")
    ordered = sorted(census.doc_types.items(), key=lambda pair: -pair[1])
    for doc_type, count in ordered[:show]:
        table.add_row(doc_type or "(空)", f"{count:,}", describe_doc_type(doc_type) or "")
    console.print(table)
    if len(ordered) > show:
        console.print(f"[dim]他 {len(ordered) - show} 種別。[/]")

    unknown = [name for name in census.doc_types if name and not is_known_doc_type(name)]
    if unknown:
        # **公式の一覧に無い種別は、読み取りが推測になっている。**
        console.print(
            f"[yellow]公式の一覧に無い書類種別が {len(unknown)} 種類。[/] " + "、".join(unknown[:5])
        )

    console.print()
    console.print(f"[bold]{census.summary()}[/]")
    if not census.revisions:
        console.print(
            "[dim]**#5 を閉じた理由は、この範囲では覆らない。**予想修正の開示が1件も出ていない。[/]"
        )
        return

    share = census.standalone / census.revisions
    if share >= 0.5:
        console.print(
            "[green]予想修正の過半が、決算と別の日に出ている。[/] "
            "**#5 を閉じた理由（「独立した開示として取得できない」）は、"
            "事実として誤りである。** 記録を直すこと。"
        )
    elif census.standalone:
        console.print(
            f"[yellow]単独で出た修正が {census.standalone:,} 件ある。[/] "
            "**「取得できない」は言い過ぎだが、大半は決算と同じ日である。** "
            "独立イベントとして使うなら、単独のぶんだけを数えることになる。"
        )
    else:
        console.print(
            "[dim]どの修正も決算と同じ日に出ている。**#5 を閉じた理由はそのまま正しい。**[/]"
        )


@app.command(name="jquants-symbol-probe")
def jquants_symbol_probe(
    symbols: list[str] | None = typer.Argument(None, help="JP codes; omit to use the gap."),
    archive_dir: str = typer.Option(
        str(DEFAULT_ARCHIVE_DIR), "--dir", help="Where the raw files are kept."
    ),
) -> None:
    """Say why a symbol in the roster has no prices, from the originals.

    **「株価が無い」には、少なくとも3つの理由がありうる。**

    | 見え方 | 意味 |
    |---|---|
    | 四本値に行が無い | 一括の株価に載っていない銘柄である |
    | 行はあるが終値が無い | 上場しているが売買が成立していない |
    | 終値もある | こちらの取り込みが落としている——**不具合** |

    **件数だけでは、この3つが区別できない。** 原本まで降りて決める。

    銘柄を渡さなければ、棚卸しが数えている「名簿にあって株価が無い」銘柄を
    そのまま調べる。**引数を打たずに済むようにしてある。**
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    database = Database()
    database.create_all()
    wanted = {code.strip() for code in (symbols or []) if code.strip()}
    if not wanted:
        coverage = audit(database, snapshots=membership(Path(DEFAULT_SNAPSHOT_DIR)))
        wanted = set(coverage.missing_priced)
        console.print(f"[dim]棚卸しの「名簿にあって株価が無い」{len(wanted)} 銘柄を調べる。[/]")
    if not wanted:
        console.print("[green]株価の無い銘柄は無い。[/]")
        return

    console.print("[dim]原本を読むだけ。**取得はしない。**[/]")
    found = probe_symbols(Path(archive_dir), wanted)

    table = Table(title="株価が無い理由（原本から）")
    for column in ("銘柄", "名前", "市場", "四本値の行", "終値のある行", "見え方"):
        table.add_column(column, justify="right" if "行" in column else "left")
    for symbol in sorted(found):
        probe = found[symbol]
        table.add_row(
            symbol,
            probe.name[:16],
            probe.market[:10],
            f"{probe.bar_rows:,}",
            f"{probe.priced_rows:,}",
            probe.verdict,
        )
    console.print(table)

    broken = [p for p in found.values() if p.priced_rows]
    if broken:
        console.print(
            f"[red]{len(broken)} 銘柄は原本に終値がある。[/] "
            "**取り込みが落としている——不具合である。**"
        )
    elif all(p.bar_rows == 0 for p in found.values()):
        console.print(
            "[dim]どれも四本値に1行も無い。**一括の株価に載っていない銘柄である。**"
            "取り直しても埋まらないので、生存バイアスの残りとして数えること。[/]"
        )
    else:
        console.print(
            "[dim]行はあるが終値が無い。**上場しているが売買が成立していない。**"
            "取り直しても埋まらない。[/]"
        )


@app.command(name="jquants-crosscheck")
def jquants_crosscheck(
    symbols: list[str] | None = typer.Argument(None, help="JP codes; omit to sample."),
    archive_dir: str = typer.Option(
        str(DEFAULT_ARCHIVE_DIR), "--dir", help="Where the raw files are kept."
    ),
    sample: int = typer.Option(12, "--sample", help="How many symbols to draw when none given."),
) -> None:
    """Compare Tachibana and the archived J-Quants bars, day by day.

    **継ぎ目の検査は1日しか見ていない。** 2021-09-01 が普通の1日に見えたこと
    は、その日に段差が無いことしか言っていない。**5年ぶんの毎日が合っているか
    は、別の話である。**

    **いましかできない。** 2026-09-22 に解約すると片方が更新されなくなる。
    原本は残るが、**2つの生きた経路が同じことを言うかを確かめる機会は無くなる。**

    生の終値から比べる。両者が同じ公式の値を見ているはずの、いちばん素の
    ところである。**ここが合わないなら、調整の話をしても意味がない。**
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    source = Path(archive_dir)
    wanted = {code.strip() for code in (symbols or []) if code.strip()}
    if not wanted:
        # **立花にあるのは現存銘柄だけ。** 廃止銘柄を混ぜても比べられない。
        latest = stored_dates(DAILY_SNAPSHOT_DIR)
        if not latest:
            raise typer.BadParameter("銘柄を渡すか、先に営業日ごとの名簿を作ること。")

        living = sorted(membership(DAILY_SNAPSHOT_DIR)[latest[-1]])
        step = max(1, len(living) // max(sample, 1))
        wanted = set(living[::step][:sample])
        console.print(f"[dim]最新の名簿から {len(wanted)} 銘柄を等間隔で抜いた。[/]")

    console.print(f"[dim]原本から {len(wanted)} 銘柄ぶんを組み立てる（1周だけ読む）…[/]")
    archived = price_frames_for(source, wanted)
    if not archived:
        console.print("[yellow]原本にその銘柄が無い。[/]")
        return

    provider, _market = _price_source("tachibana", settings)
    today = dt.date.today()
    matches: list[DailyMatch] = []
    for index, symbol in enumerate(sorted(archived), start=1):
        console.print(f"[dim]{_progress_line(index, len(archived), symbol)}[/]", end="\r")
        try:
            theirs = provider.fetch_prices(symbol, dt.date(2001, 1, 1), today)
        except Exception as exc:  # noqa: BLE001 - 断られ方そのものが記録に値する
            console.print(f"[yellow]{symbol}: {type(exc).__name__}[/]")
            continue
        matches.append(compare_daily(theirs, archived[symbol], symbol))

    console.print()
    if not matches:
        console.print("[yellow]立花から1銘柄も取れなかった。[/]")
        return

    table = Table(title="立花 と J-Quants（重なる日だけ）")
    for column in ("銘柄", "比べた日", "終値が違う", "調整後が違う", "いちばん悪い日"):
        table.add_column(column, justify="left" if column == "銘柄" else "right")
    for match in matches:
        table.add_row(
            match.symbol,
            f"{match.days:,}",
            f"{match.close_differs:,}",
            f"{match.adjusted_differs:,}",
            f"{match.worst:.2%} ({match.worst_on})" if match.worst else "—",
        )
    console.print(table)
    console.print(summarise(matches))

    off = [m for m in matches if not m.agrees]
    if off:
        console.print(
            f"[red]{len(off)}/{len(matches)} 銘柄で生の終値が食い違う。[/] "
            "**同じ公式の値を見ているはずのところで合わない。** どちらが正しいか"
            "を決めるまで、継ぎ目をまたぐ期間を分析に使わないこと。"
        )
    else:
        adjusted = [m for m in matches if m.adjusted_differs]
        console.print(
            "[green]生の終値は、比べたすべての日で一致している。[/]"
            + (
                f"[yellow] ただし調整後は {len(adjusted)} 銘柄で違う。[/]"
                "[dim] 分割調整の基準が出所で違う——継ぎ目1日では見えなかったもの。[/]"
                if adjusted
                else "[dim] 調整後も一致している。[/]"
            )
        )


@app.command(name="jquants-roster-prices")
def jquants_roster_prices(
    archive_dir: str = typer.Option(
        str(DEFAULT_ARCHIVE_DIR), "--dir", help="Where the raw files are kept."
    ),
    roster_dir: str = typer.Option(
        str(DAILY_SNAPSHOT_DIR), "--rosters", help="Where the daily rosters are kept."
    ),
    limit: int | None = typer.Option(None, "--limit", help="Read only this many bars files."),
    show: int = typer.Option(10, "--show", help="How many symbols to list per warning."),
) -> None:
    """Check the roster and the bars against each other, day by day.

    **同じ原本から出た2本が、互いに整合しているか。** 名簿は
    `normalize_listings`（ETF・REIT・TOKYO PRO を落とす）を通り、四本値は
    証券コードの変換しか通らない。**だから差が出るのが普通で、件数からは何も
    分からない。** 分かるのは理由が言えるかどうかである。

    API を1回も叩かない。解約後にも実行できる。
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    source = Path(archive_dir)
    report = consistency_check(
        source,
        Path(roster_dir),
        limit=limit,
        progress=lambda index, total, key: console.print(
            f"[dim]{_progress_line(index, total, key)}[/]", end="\r"
        ),
    )
    console.print()
    if not report.days:
        console.print(
            "[yellow]突き合わせられる日が1日も無い。[/]"
            "[dim] 先に原本を保存し、営業日ごとの名簿を作ること。[/]"
        )
        raise typer.Exit(code=1)

    table = Table(title="名簿と四本値（営業日ごと）")
    table.add_column("見たもの")
    table.add_column("銘柄日", justify="right")
    table.add_row("名簿にいて終値もある", f"{report.matched:,}")
    table.add_row("名簿にいるが売買が無かった", f"{report.quiet:,}")
    table.add_row(
        "[red]名簿にいるが四本値の行が無い[/]" if report.no_bar_row else "名簿にいるが行が無い",
        f"{sum(report.no_bar_row.values()):,}",
    )
    table.add_row(
        "[red]出来高があるのに終値が無い[/]"
        if report.traded_no_close
        else "出来高があるのに終値が無い",
        f"{sum(report.traded_no_close.values()):,}",
    )
    table.add_row("終値があるが名簿にいない", f"{report.price_only:,}")
    console.print(table)

    if report.reasons:
        reasons = Table(title="名簿にいない理由")
        reasons.add_column("理由")
        reasons.add_column("銘柄日", justify="right")
        for reason, count in report.reasons.most_common():
            unexplained = reason in (NO_ROSTER_ROW, ROSTER_DISAGREES)
            reasons.add_row(f"[red]{reason}[/]" if unexplained else reason, f"{count:,}")
        console.print(reasons)

    for label, counter in (
        ("四本値の行が無い", report.no_bar_row),
        ("出来高があるのに終値が無い", report.traded_no_close),
        ("理由を言えない", report.unexplained),
    ):
        if counter:
            worst = ", ".join(f"{symbol}({days}日)" for symbol, days in counter.most_common(show))
            console.print(f"[yellow]{label}: {len(counter)} 銘柄[/] [dim]{worst}[/]")

    if report.missing_roster:
        console.print(
            f"[yellow]名簿の無い日が {len(report.missing_roster)} 日あり、比べていない。[/]"
            f"[dim] 例: {report.missing_roster[0]}[/]"
        )
    if report.missing_master:
        console.print(f"[yellow]名簿の原本が覆わない日が {len(report.missing_master)} 日ある。[/]")
    if report.failed:
        console.print(f"[yellow]読めなかった原本 {len(report.failed)} 本[/]")

    console.print(report.summary())
    broken = sum(report.no_bar_row.values()) + sum(report.traded_no_close.values())
    if broken or report.unexplained:
        console.print(
            f"[red]説明の付かないものが残っている[/]（行が無い・終値が無い {broken:,} 銘柄日、"
            f"理由を言えない {sum(report.unexplained.values()):,} 銘柄日）。"
            "**片方の絞り込みが、もう片方と食い違っている。** どちらが正しいかを"
            "決めるまで、この名簿で分位を切らないこと。"
        )
    else:
        console.print(
            "[green]食い違いは1件も残らなかった。[/]"
            "[dim] 名簿にいる銘柄は全部その日の行を持ち、名簿にいない終値は"
            "すべて「投信・ETF」か「買えない市場」で説明が付いた。[/]"
        )


@app.command(name="jquants-calendar")
def jquants_calendar(
    archive_dir: str = typer.Option(
        str(DEFAULT_ARCHIVE_DIR), "--dir", help="Where the raw files are kept."
    ),
    roster_dir: str = typer.Option(
        str(DAILY_SNAPSHOT_DIR), "--rosters", help="Where the daily rosters are kept."
    ),
) -> None:
    """Count what the archived trading calendar actually contains.

    **半日立会（`HolDiv=2`）が実在するかを数える。** コードには「`1` だけで
    絞ると年に数日だけ静かに欠ける」と書いてあるが、**書いてあることと、手元
    のデータにそれが在ることは別である。** 1日も無ければ、備えは正しくても
    効いていない。

    名簿のある日とも突き合わせる。カレンダーは「立会がある」と言っているだけ
    で、**本当にその日のデータが在るかは別のファイルが知っている。**

    API を1回も叩かない。
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    days = calendar_from_archive(Path(archive_dir))
    if days is None:
        console.print(
            "[yellow]取引カレンダーの原本が無い。[/]"
            "[dim] 先に `checks\\原本をまるごと保存.bat` を実行すること。[/]"
        )
        raise typer.Exit(code=1)
    if not days:
        console.print("[red]カレンダーの原本はあるが、1日も読めなかった。[/]")
        raise typer.Exit(code=1)

    report = calendar_census(days)
    table = Table(title="取引カレンダーの区分")
    for column, justify in (("区分", "left"), ("意味", "left"), ("日数", "right")):
        table.add_column(column, justify=justify)
    for code in sorted(report.by_division):
        label = HOLIDAY_DIVISION.get(code, "[red]一覧に無い[/]")
        name = f"[green]{code}[/]" if code in TRADING_DIVISIONS else code
        table.add_row(name, label, f"{report.by_division[code]:,}")
    console.print(table)

    if not report.half_days:
        console.print(
            "[yellow]半日立会が1日も無い。[/] **備えは正しくても効いていない。**"
            "[dim] 区分の名前が変わったか、この原本が覆う期間に無いかのどちらか。[/]"
        )
    else:
        years = half_days_by_year(days)
        listing = Table(title=f"半日立会（{len(report.half_days)} 日）")
        listing.add_column("年")
        listing.add_column("日付")
        for year in sorted(years):
            listing.add_row(str(year), "  ".join(date.isoformat() for date in years[year]))
        console.print(listing)
        console.print(
            f"[green]`1` だけで絞ると {report.lost_if_only_one} 日が落ちる。[/]"
            "[dim] 年に数日なので、件数からは気付けない種類である。[/]"
        )

    if report.unknown:
        console.print(
            f"[red]一覧に無い区分が {sum(report.unknown.values())} 日ある[/]: "
            + ", ".join(f"{code}({count})" for code, count in sorted(report.unknown.items()))
            + "。**区分が増えている。** 立会に数えるかを決めること。"
        )

    stored = set(stored_dates(Path(roster_dir)))
    if not stored:
        console.print("[dim]営業日ごとの名簿が無いので、突き合わせは飛ばした。[/]")
    else:
        match = calendar_agreement(days, stored)
        console.print(match.summary())
        for label, dates in (
            ("立会と言っているのに名簿が無い", match.trading_without_roster),
            ("立会でないのに名簿がある", match.roster_without_trading),
        ):
            if dates:
                shown = "  ".join(date.isoformat() for date in dates[:8])
                console.print(f"[yellow]{label}: {len(dates)} 日[/] [dim]{shown}[/]")
        if match.half_days and match.half_days_with_roster == match.half_days:
            console.print(
                f"[green]半日立会 {match.half_days} 日は、すべてその日の名簿がある。[/]"
                "[dim] カレンダーとは別のファイルが、同じ日を立会だと言っている。[/]"
            )

    console.print(report.summary())


@app.command(name="jquants-filter-census")
def jquants_filter_census(
    archive_dir: str = typer.Option(
        str(DEFAULT_ARCHIVE_DIR), "--dir", help="Where the raw files are kept."
    ),
    show: int = typer.Option(12, "--show", help="How many codes to list."),
    product: str | None = typer.Option(
        None, "--product", help="Name the symbols carrying this ProdCat value."
    ),
) -> None:
    """Count whether the roster filter still holds when the history gets longer.

    絞り込みは `S33`（33業種）に寄りかかっている。**無ければ無条件に「会社」と
    みなす。** 符号の名前が変わったときに universe が空になるより、ETF が1つ
    紛れるほうが安いからで、そこは意図した設計である。

    **ただしそれは、`S33` がほぼ全部の行に在ることを前提にしている。** いま
    手元にあるのは5年ぶんで、そこで揃っていることは 2006年にも揃っていること
    を意味しない。

    起きうることは2つあり、**向きが逆である。**

    | 形 | 何が起きる |
    |---|---|
    | `S33` が空 | ETF・REIT が「会社」として universe に入る |
    | 表に無い符号 | 普通の会社が投信とみなされて**落ちる** |

    **後者のほうが重い。** 符号の体系が変われば、落ちるのは1社ではなく全部に
    なりうる。どちらも例外は出ない。

    ここで出すのは**基準線**である。20年ぶんを取った日に、同じ数字を出して
    比べる。**基準線が無ければ、20年ぶんの数字を見ても多いのか少ないのかが
    言えない。**

    API を1回も叩かない。
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    wanted = product.strip() if product else None
    report = filter_census(
        Path(archive_dir),
        progress=lambda index, total, key: console.print(
            f"[dim]{_progress_line(index, total, key)}[/]", end="\r"
        ),
    )
    console.print()
    if not report.by_year:
        console.print(
            "[yellow]名簿の原本が無い。[/]"
            "[dim] 先に `checks\\原本をまるごと保存.bat` を実行すること。[/]"
        )
        raise typer.Exit(code=1)

    table = Table(title="年ごとの通り具合（銘柄日）")
    for column in ("年", "行", "残した", "`S33` が空", "表に無い符号"):
        table.add_column(column, justify="left" if column == "年" else "right")
    for year in sorted(report.by_year):
        year_slice = report.by_year[year]
        table.add_row(
            str(year),
            f"{year_slice.rows:,}",
            f"{year_slice.kept:,}",
            f"{year_slice.no_sector:,}"
            + (f" ({year_slice.no_sector_share:.2%})" if year_slice.no_sector else ""),
            f"[red]{year_slice.unknown_sector:,}[/]"
            if year_slice.unknown_sector
            else f"{year_slice.unknown_sector:,}",
        )
    console.print(table)

    if report.unknown_sector_codes:
        console.print(
            f"[red]表に無い `S33` の符号が {len(report.unknown_sector_codes)} 種類[/]"
            "[dim] これらは「その他」と同じ扱いで落ちている。**普通の会社なら、"
            "黙って universe から消えている。**[/]"
        )
        for code, count in report.unknown_sector_codes.most_common(show):
            name = report.unknown_sector_names.get(code) or "（名前も無い）"
            console.print(f"  [yellow]{code}[/] {name} — {count:,} 銘柄日")
    else:
        console.print(
            "[green]表に無い `S33` の符号は1つも無い。[/]"
            "[dim] この期間では、符号の体系はこちらの表と揃っている。[/]"
        )

    if report.no_sector_symbols:
        listed = "  ".join(sorted(report.no_sector_symbols)[:show])
        console.print(
            f"[yellow]`S33` が空の銘柄が {len(report.no_sector_symbols)} 件[/]"
            f"[dim] これらは無条件に「会社」として universe に入っている: {listed}[/]"
        )
    else:
        console.print(
            "[green]`S33` が空の行は1つも無い。[/]"
            "[dim] 受け皿の規則は、この期間では一度も使われていない。[/]"
        )

    products = Table(title="`ProdCat` は二の矢になるか")
    for column in ("行", "`ProdCat` の値"):
        products.add_column(column)
    products.add_row("残した", "  ".join(sorted(report.product_kept)) or "（空）")
    for reason, counter in sorted(report.product_dropped.items()):
        products.add_row(f"落とした / {reason}", "  ".join(sorted(counter)) or "（空）")
    console.print(products)

    separates, overlap = product_separates(report, FUND)
    if separates:
        console.print(
            "[green]`ProdCat` は、投信・ETF と会社を分けきっている。[/]"
            "**`S33` が空のときの受け皿にできる。**"
        )
    elif overlap:
        console.print(
            f"[yellow]`ProdCat` は分けきれない。[/] 両方に出る値: {'  '.join(sorted(overlap))}。"
            "**重なった値の行は、どちらとも言えない。** 受け皿にはできない。"
        )
    else:
        console.print(
            "[dim]`ProdCat` の比較はできなかった（片方が空）。**比べていない、"
            "であって分けられない、ではない。**[/]"
        )

    if report.product_symbols:
        split = Table(title="`ProdCat` の値ごとの銘柄")
        for column, justify in (
            ("値", "left"),
            ("銘柄", "right"),
            ("残した", "right"),
            ("落とした", "right"),
        ):
            split.add_column(column, justify=justify)
        for value in sorted(report.product_symbols):
            entry = report.product_symbols[value]
            label = value or "（空）"
            split.add_row(
                f"[yellow]{label}[/]" if entry.splits else label,
                f"{entry.total:,}",
                f"{len(entry.kept):,}",
                f"{len(entry.dropped_symbols):,}",
            )
        console.print(split)
        console.print(
            "[dim]黄色は、残した側と落とした側の両方にいる値。**同じ商品区分が"
            "2通りに扱われている。**[/]"
        )

        # **名前を見るまで決まらない。** 数の少ない値はその場で出し切る。
        # 1つずつ聞き直すと、そのたびに原本を1周読み直すことになる。
        for value in sorted(report.product_symbols):
            entry = report.product_symbols[value]
            if wanted is None and entry.total > show:
                continue
            if wanted is not None and value != wanted:
                continue
            named = Table(title=f"`ProdCat` = {value or '（空）'} の中身")
            for column in ("扱い", "銘柄", "名前"):
                named.add_column(column)
            for symbol in sorted(entry.kept)[:show]:
                named.add_row(
                    "[green]残した[/]", symbol, report.symbol_names.get(symbol) or "（名前なし）"
                )
            for reason, symbols in sorted(entry.dropped.items()):
                for symbol in sorted(symbols)[:show]:
                    named.add_row(
                        f"落とした / {reason}",
                        symbol,
                        report.symbol_names.get(symbol) or "（名前なし）",
                    )
            console.print(named)
        if wanted is not None and wanted not in report.product_symbols:
            console.print(
                f"[yellow]`ProdCat` = {wanted} の行が1つも無い。[/]"
                "[dim] 上の表に出ている値を渡すこと。[/]"
            )

    if report.failed:
        console.print(f"[yellow]読めなかった原本 {len(report.failed)} 本[/]")
    console.print(report.summary())
    console.print(f"[bold]{filter_baseline(report)}[/]")
    console.print("[dim]20年ぶんを取った日に、同じコマンドを実行してこの行と比べること。[/]")


@app.command(name="jquants-topix")
def jquants_topix(
    archive_dir: str = typer.Option(
        str(DEFAULT_ARCHIVE_DIR), "--dir", help="Where the raw files are kept."
    ),
    etf: str = typer.Option(BENCHMARK, "--etf", help="ETF to compare against the index."),
    show: int = typer.Option(8, "--show", help="How many gaps to list."),
) -> None:
    """Read the TOPIX index itself and compare it with the ETF we use as benchmark.

    **ベンチマークに 1306（TOPIX 連動 ETF）を使っているのは、指数が手元に
    無かったからである。** それ以上の理由は無い。

    Premium で `/indices/bars/daily/topix` が開いた。2008-05 からの18年ぶんが
    88 KB で入っている。

    | | |
    |---|---|
    | TOPIX | 指数。信託報酬も、売買のずれも無い |
    | 1306 | それを追う ETF。**信託報酬が毎日引かれ、追跡のずれが乗る** |

    18年ぶん積み上がると小さくないはずだが、**それは見込みであって測った値では
    ない。** 引き算をする。

    **置き換えは判定のやり直しではない。** #7 のベンチマークを替えて回し直せば
    2回目の判定になる。ここで作るのは測る道具で、使えるのはまだ判定を消費して
    いない説（#5・#8）である。

    API を1回も叩かない。
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    frame = topix_from_archive(Path(archive_dir))
    if frame.empty:
        console.print(
            "[yellow]TOPIX の原本が無い。[/]"
            "[dim] 先に `checks\\原本をまるごと保存.bat` を実行すること。[/]"
        )
        raise typer.Exit(code=1)

    # **穴は取引カレンダーと突き合わせて決める。** 暦の空き日数で数えていた
    # ときは 21件出て、21件とも年末年始・GW・シルバーウィークだった。
    trading = trading_days_from_archive(Path(archive_dir))
    report = topix_census(frame, trading=trading)

    table = Table(title="TOPIX（指数そのもの）")
    table.add_column("見たもの")
    table.add_column("値", justify="right")
    table.add_row("日数", f"{report.rows:,}")
    table.add_row("期間", f"{report.first} 〜 {report.last}")
    table.add_row("始まりの水準", f"{frame[CLOSE].iloc[0]:,.2f}")
    table.add_row("終わりの水準", f"{frame[CLOSE].iloc[-1]:,.2f}")
    if report.checked:
        table.add_row(
            "[red]立会なのに指数が無い日[/]" if report.missing else "立会なのに指数が無い日",
            f"{len(report.missing):,}",
        )
        label = "立会でないのに指数がある日"
        table.add_row(
            f"[yellow]{label}[/]" if report.extra else label,
            f"{len(report.extra):,}",
        )
    else:
        table.add_row("[dim]穴[/]", "[dim]カレンダーが無いので見ていない[/]")
    console.print(table)

    for label, days in (
        ("立会なのに指数が無い", report.missing),
        ("立会でないのに指数がある", report.extra),
    ):
        if days:
            listed = "  ".join(day.isoformat() for day in days[:show])
            console.print(f"[yellow]{label}: {len(days)} 日[/] [dim]{listed}[/]")
    if report.checked and not report.missing and not report.extra:
        console.print(
            "[green]立会日と1日も食い違わない。[/]"
            "[dim] カレンダーと指数は別の原本なので、別々のファイルが同じ日を"
            "立会だと言っている。[/]"
        )

    database = Database()
    with database.session() as session:
        prices = PriceRepository(session).get_prices(etf)
    if prices.empty:
        console.print(
            f"[yellow]{etf} の株価が DB に無いので、引き算は飛ばした。[/]"
            "[dim] **比べていない、であって差が無い、ではない。**[/]"
        )
        console.print(report.summary())
        return

    gap = topix_tracking_gap(frame, prices[CLOSE])
    console.print()
    console.print(f"[bold]指数 と {etf}（重なる日だけ）[/]")
    console.print(gap.summary())

    # **端点だけでは分からない。** 18年で +5.8% は、なだらかな年 0.3% の
    # 積み重ねかもしれないし、ある1日で付いた継ぎ目かもしれない。前者なら
    # 体系的な要因、後者はデータの不具合で、**次にやることが正反対になる。**
    trail = topix_gap_trail(frame, prices[CLOSE])
    if trail.by_year:
        console.print(
            f"[dim]1日の差の中央値 {trail.daily_median:.4%}、"
            f"いちばん大きい1日 {trail.largest_day:.2%}。[/]"
        )
        if trail.concentrated:
            console.print(
                "[red]差は数日に固まっている。[/] "
                "**日々の積み重ねではない。分割の調整が片方で抜けた形である。**"
            )
            detail = Table(title="食い違いの大きい日")
            for column in ("日付", "指数", "ETF", "差"):
                detail.add_column(column, justify="right" if column != "日付" else "left")
            for day, left, right, difference in trail.worst:
                detail.add_row(str(day), f"{left:+.2%}", f"{right:+.2%}", f"{difference:+.2%}")
            console.print(detail)
        else:
            console.print(
                "[yellow]差はなだらかに付いている。[/] "
                "**1日で飛んではいない。継ぎ目ではなく、毎日効く要因である。**"
            )
            years = Table(title="年ごとの差（ETF − 指数）")
            years.add_column("年")
            years.add_column("差", justify="right")
            for year, value in trail.by_year.items():
                years.add_row(str(year), f"{value:+.2%}")
            console.print(years)
    if not gap.days:
        console.print(
            "[dim]どちらも配当を含まない前提である。片方だけ配当込みなら、"
            "この差は信託報酬ではなく配当利回りを測ることになる。[/]"
        )
    elif gap.total < 0:
        console.print(
            f"[green]ETF は指数に {-gap.total:.1%} 届いていない[/]"
            f"（年あたり {-gap.annual:.2%}）。"
            "[dim] 信託報酬と追跡のずれが、この向きに出る。[/]"
        )
    else:
        console.print(
            f"[yellow]ETF が指数を {gap.total:+.1%} 上回っている[/]"
            f"（年あたり {gap.annual:+.2%}）。"
            "[dim] 信託報酬は ETF を削る側なので、この向きは向かい風である。[/]"
        )

    # **散らばりを見ずに平均だけ出さない。** 年ごとの差は −1.38% から +0.62%
    # まで振れている。平均が小さくても、散らばりがそれより大きければ
    # **雑音を発見として読むことになる**（2026-09-16）。
    if len(trail.by_year) >= 2:
        low, high = trail.interval
        console.print(
            f"[dim]年ごとの差は平均 {trail.mean_year:+.3%}、"
            f"95% の幅 {low:+.2%} 〜 {high:+.2%}（{len(trail.by_year)} 年）。[/]"
        )
        if trail.distinguishable:
            console.print(
                "[yellow]年あたりの差は 0 と区別できる。[/] **毎年同じ向きに効く要因がある。**"
            )
        else:
            console.print(
                "[green]年あたりの差は 0 と区別できない。[/] "
                "[dim]**「差が無い」ではなく「差があるとは言えない」である。** "
                "信託報酬ぶんの負の値も、この幅の中にある。18年でも足りない。[/]"
            )

    console.print(report.summary())


@app.command(name="jquants-roster-day")
def jquants_roster_day(
    dates: list[str] = typer.Argument(None, help="YYYY-MM-DD. Repeat for several."),
    archive_dir: str = typer.Option(
        str(DEFAULT_ARCHIVE_DIR), "--dir", help="Where the raw files are kept."
    ),
) -> None:
    """Say why a given day has no roster: no rows, or every row filtered out.

    **「原本に行が無い」と「行はあったが絞り込みが全部落とした」を分ける。**
    どちらも結果は「名簿の無い日」で、**直す場所が違う。**

    2026-09-15 に、立会日なのに名簿の無い日が2日見つかった（2008-12-30、
    2009-01-05 で、**範囲内の半日立会2日とぴったり同じ**）。まとめの件数だけ
    ではどちらか決まらず、**1日ぶんを名指しで見るしかなかった。**

    API を1回も叩かない。
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    wanted = [_parse_date(text.strip()) for text in (dates or []) if text.strip()]
    if not wanted or any(day is None for day in wanted):
        raise typer.BadParameter("YYYY-MM-DD で日付を渡すこと。")

    console.print(f"[dim]名簿の原本を1周読む（{len(wanted)} 日ぶんを探す）…[/]")
    for day in wanted:
        assert day is not None
        found = roster_day_detail(Path(archive_dir), day)

        table = Table(title=f"{day} の名簿の原本")
        table.add_column("見たもの")
        table.add_column("値", justify="right")
        table.add_row("その日を持つ原本", f"{len(found.files):,}")
        table.add_row("行", f"{found.rows:,}")
        table.add_row("絞り込みを通った", f"{found.kept:,}")
        for reason, count in sorted(found.reasons.items()):
            table.add_row(f"[dim]落ちた / {reason}[/]", f"{count:,}")
        console.print(table)

        if found.examples:
            listed = "  ".join(f"{code}({name})" if name else code for code, name in found.examples)
            console.print(f"[dim]例: {listed}[/]")

        if not found.files:
            console.print(
                f"[yellow]{day}: 原本にその日の行が無い。[/]"
                "**こちらでは直せない。** 名簿の無い日として記録する。"
            )
        elif found.kept:
            console.print(
                f"[green]{day}: 名簿が作れる。[/]"
                "[dim] 既に作られているはず。無いなら取り出しを疑う。[/]"
            )
        else:
            console.print(
                f"[red]{day}: 行はあるが、絞り込みが全部落とした。[/]"
                "**こちら側の問題である。** 上の理由の内訳を見ること。"
            )
        console.print()


@app.command(name="jquants-archive-verify")
def jquants_archive_verify(
    directory: str = typer.Option(
        str(DEFAULT_ARCHIVE_DIR), "--dir", help="Where the raw files are kept."
    ),
) -> None:
    """Check the archived originals against the manifest. Fetches nothing.

    **解約後こそ実行する意味がある。** そのとき欠けていると分かっても取り返せ
    ないが、**欠けているのに揃っていると思って解析するよりはよい。**

    **全部のファイルを読み直す。** 5年ぶん（265MB）なら一瞬だが、20年ぶんや
    同期フォルダ越しでは分単位になる。速さも出す——**5年ぶんの実測があれば、
    20年ぶんに何分かかるかを掛け算で出せる。**
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    target = Path(directory)
    manifest = read_manifest(target)
    if not manifest:
        # **空を「一致した」と言わない。** 写し先を打ち間違えたとき、そこには
        # 目録も原本も無い。緑の文字が出れば、確かめたつもりになる。
        console.print(
            f"[red]目録が無い: {target}[/] **確かめていない。**"
            "[dim] 写し先を打ち間違えていないか、原本をまだ保存していないかの"
            "どちらか。[/]"
        )
        raise typer.Exit(code=1)

    started = time.monotonic()
    missing, wrong_size, changed = verify_archive(
        target,
        progress=lambda index, total, key: console.print(
            f"[dim]{_progress_line(index, total, key)}[/]", end="\r"
        ),
    )
    elapsed = max(time.monotonic() - started, 1e-9)
    console.print()

    read = sum(item.bytes_written for item in manifest.values())
    if read:
        # **「この4倍を見込め」と書いていた（2026-09-12〜15）。** 5年ぶんしか
        # 手元に無かったときの目安で、20年を取ったいまは掛ける相手がいない。
        # **伸ばす先が無くなった目安を残すと、読んだ人が4倍して身構える。**
        console.print(
            f"[dim]{_bytes_label(read)} を {elapsed:.1f} 秒で読み直した"
            f"（{_bytes_label(int(read / elapsed))}/秒）。[/]"
        )

    # **目録 → ディスクは `verify` が見る。** その逆（ディスクに置いたが目録に
    # 無い）は誰も見ていなかったので、ここで数える。
    #
    # 一覧から消えた鍵は余りにならない——**目録は積み上がる。** 2026-09-15 に
    # 「一覧に無い65本は目録にも無い」と読んで外した。`read_manifest` が既存を
    # 読んでから足すので、鍵は残る。書き直すのは中身であって鍵の集合ではない。
    extra = archive_orphans(target)
    if extra:
        console.print(
            f"[yellow]目録に無いファイルが {len(extra)} 本ある。[/]"
            "[dim] 照合の対象外で、写しには運ばれる。**消していない**——"
            "向こうのファイル名が変わったときに、古いほうが残る。[/]"
        )
        for name in extra[:5]:
            console.print(f"  [dim]{name}[/]")

    if not (missing or wrong_size or changed):
        console.print(f"[green]目録の {len(manifest)} 本すべてが一致している。[/]")
        return

    for label, keys in (
        ("消えている", missing),
        ("大きさが合わない", wrong_size),
        ("中身が変わった", changed),
    ):
        if keys:
            console.print(f"[red]{label}: {len(keys)} 本[/]")
            for key in keys[:10]:
                console.print(f"  [dim]{key}[/]")
    raise typer.Exit(code=1)


@app.command(name="jquants-bulk-list")
def jquants_bulk_list(
    endpoint: str | None = typer.Option(
        None, "--endpoint", help="e.g. /fins/summary. Omit to survey the deadline set."
    ),
    every: bool = typer.Option(False, "--all", help="Survey every bulk endpoint, not just two."),
    show: int = typer.Option(5, "--show", help="How many file names to print per endpoint."),
) -> None:
    """Survey what the bulk download offers, without fetching a single byte.

    The statements path spends one request per symbol and stopped on 429 at
    3,700 of them. ``/fins/summary`` and ``/equities/bars/daily`` are both bulk
    endpoints, where one gzipped CSV a month carries every symbol. With the
    cancellation close, whether to move is worth measuring first.

    Three numbers decide it.

    1. **How many files.** That is what says monthly or daily.
    2. **What range they cover.** If bulk covers less than the per-symbol API,
       moving loses rows - and loses them *silently*, which is why this is
       measured before anything is written.
    3. **Total size.** Whether it can be pulled at all before the deadline.

    Presigned URLs live five minutes, so the ingest has to fetch one URL and
    use it immediately. Collecting them all first would let the later ones die.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    api_key = settings.jquants_api_key
    if api_key is None:
        console.print("[red].env に JQUANTS_API_KEY がない。[/] APIキー設定.bat で設定する。")
        raise typer.Exit(code=1)

    left = (CANCELLATION - dt.date.today()).days
    console.print(
        f"解約日 [bold]{CANCELLATION}[/] まで "
        + (f"[bold]{left}[/] 日。" if left >= 0 else "[red]過ぎている。[/]")
    )
    console.print(
        "[dim]ここでは1バイトも落とさない。一覧を見るだけである。[/dim]",
    )
    console.print()

    if endpoint:
        targets: tuple[str, ...] = (endpoint,)
    elif every:
        targets = BULK_ENDPOINTS
    else:
        targets = DEADLINE_ENDPOINTS

    table = Table(title="一括ダウンロードで取れるもの")
    for column, justify in (
        ("エンドポイント", "left"),
        ("本数", "right"),
        ("覆っている範囲", "left"),
        ("合計", "right"),
    ):
        table.add_column(column, justify=justify)

    found: dict[str, list[BulkFile]] = {}
    for target in targets:
        try:
            files = bulk_list_files(api_key, endpoint=target)
        except RateLimitError:
            # **レート制限はここで飲まない。** ``RateLimitError`` は
            # ``DataError`` の一種なので、下の except より先に置く。飲むと
            # 「このエンドポイントは取れない」に化けて、残りを閉じた扉に
            # 叩きつけたうえで、表には嘘の理由が並ぶ。
            raise
        except DataError as exc:
            # プランで開いていないエンドポイントは断られる。それは答えであって、
            # ほかのエンドポイントを見に行けなくなる理由ではない。
            table.add_row(target, "[yellow]—[/]", f"[yellow]{exc}[/]", "")
            continue
        found[target] = files
        if not files:
            table.add_row(target, "0", "[yellow]1本も無い[/]", "")
            continue
        span = bulk_coverage(files)
        total = sum(item.size for item in files)
        table.add_row(
            target,
            f"{len(files):,}",
            f"{span[0]} 〜 {span[1]}" if span else "[dim]ファイル名から読めない[/dim]",
            f"{total / 1_000_000_000:.2f} GB"
            if total >= 1_000_000_000
            else f"{total / 1_000_000:.0f} MB",
        )
    console.print(table)

    for target, files in found.items():
        if not files:
            continue
        console.print()
        console.print(f"[bold]{target}[/] の最初と最後:")
        for item in files[:show]:
            console.print(f"  [dim]{item.megabytes:8.1f} MB[/dim]  {item.key}")
        if len(files) > show * 2:
            console.print(f"  [dim]… 途中 {len(files) - show * 2:,} 本 …[/dim]")
        for item in files[-show:] if len(files) > show else []:
            console.print(f"  [dim]{item.megabytes:8.1f} MB[/dim]  {item.key}")

    _report_plan(found)

    console.print()
    console.print(
        "[bold]この出力をそのまま貼ってほしい。[/] "
        "本数と範囲を見てから取り込みを作る。"
        "[dim]粒度を推測して作ると、行が少ないまま黙って入る。[/dim]"
    )
    console.print(
        f"[dim]署名付きURLの寿命は {int(PRESIGNED_URL_TTL.total_seconds() // 60)} 分。"
        "取り込みは1本ずつ「取ってすぐ落とす」形にする。[/dim]"
    )


@app.command(name="jquants-bulk-fetch")
def jquants_bulk_fetch(
    endpoint: str = typer.Option("/fins/summary", "--endpoint", help="Which bulk set to ingest."),
    since: str | None = typer.Option(None, "--since", help="Skip files older than YYYY-MM."),
    limit: int = typer.Option(0, "--limit", help="Stop after N files. 0 means all of them."),
    throttle: float = typer.Option(
        0.0, "--throttle", help="Seconds between files. 0 derives it from the plan."
    ),
) -> None:
    """Ingest the statement history from the bulk files instead of per symbol.

    The per-symbol path spends one request per code and stopped on 429 after 84
    of 3,700. This spends two per *file* - one for the presigned URL, one for
    the download - and the whole five-year history of every symbol is 83 files.

    Rows land through the same normalizer the JSON path uses, because the CSV
    column names are the API's own field names. Keeping one mapping means a
    correction cannot be applied to one path and forgotten on the other.

    Reruns are safe. ``upsert_reports`` is keyed by fiscal period and never
    replaces a stored value with a blank, so re-ingesting a month rewrites what
    changed and leaves the rest alone.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    api_key = settings.jquants_api_key
    if api_key is None:
        console.print("[red].env に JQUANTS_API_KEY がない。[/] APIキー設定.bat で設定する。")
        raise typer.Exit(code=1)

    database = Database()
    database.create_all()

    try:
        files = bulk_list_files(api_key, endpoint=endpoint)
    except DataError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc

    if not files:
        console.print(f"[yellow]{endpoint} に落とせるファイルが1本も無い。[/]")
        raise typer.Exit(code=1)

    span = bulk_coverage(files)
    years = bulk_span_years(files)
    plan = infer_plan(years) if years is not None else None
    if throttle <= 0:
        # **プランから引く。** 既定の 0.5 秒は 120回／分で、Light の上限の
        # 2倍にあたる。ここを速いままにすると、一括にしても遮断される。
        throttle = recommended_throttle(plan or "") or 1.2

    if since:
        kept = [item for item in files if _file_month(item.key) >= since]
        console.print(f"[dim]{since} 以降に絞って {len(kept)}/{len(files)} 本。[/dim]")
        files = kept
    if limit > 0:
        files = files[:limit]

    console.print(
        f"[bold]{endpoint}[/] を {len(files):,} 本。"
        + (f"覆っている範囲 {span[0]} 〜 {span[1]}。" if span else "")
        + (f"契約はおそらく {plan}。" if plan else "")
    )
    console.print(
        f"[dim]1本あたり {throttle:.1f} 秒あける。署名付きURLの寿命は "
        f"{int(PRESIGNED_URL_TTL.total_seconds() // 60)} 分なので、"
        "1本ずつ取ってすぐ落とす。[/dim]"
    )

    rows_read = 0
    rows_without_code = 0
    disclosures = 0
    statements = 0
    symbols: set[str] = set()
    failed: list[tuple[str, str]] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("一括ファイル", total=len(files))
        for index, item in enumerate(files):
            progress.update(task, description=item.key.rsplit("/", 1)[-1])
            try:
                payload = bulk_download(api_key, item.key)
            except RateLimitError:
                # 一括でも上限は上限である。**残りを閉じた扉に叩きつけない。**
                console.print(
                    f"\n[yellow]レート制限に当たった（{index} 本目まで完了）。[/] "
                    "時間を置いて同じコマンドを再実行すれば、続きから入る。"
                )
                break
            except (DataError, OSError) as exc:
                failed.append((item.key, str(exc)))
                progress.advance(task)
                continue

            records = bulk_records_from_csv(payload)
            rows_read += len(records)
            grouped = bulk_group_by_symbol(records)
            rows_without_code += len(records) - sum(len(v) for v in grouped.values())

            with database.session() as session:
                repository = FinancialStatementRepository(session)
                for symbol, rows in grouped.items():
                    reports = normalize_statements(symbol, rows)
                    if not reports:
                        continue
                    disclosures += len(reports)
                    statements += repository.upsert_reports(symbol, reports, market="JP")
                    symbols.add(symbol)

            progress.advance(task)
            if throttle and index + 1 < len(files):
                time.sleep(throttle)

    # **減る所を全部見せる。** 「読んだ行」と「書いた行」だけ出すと、差が
    # 出たときに人が理由を考えることになる。考えて当たることもあるが、
    # 当たったかどうかは分からない。段ごとに数える。
    table = Table(title="一括で入れたもの")
    table.add_column("段", justify="left")
    table.add_column("数", justify="right")
    table.add_column("前の段から", justify="right")
    kept = rows_read - rows_without_code
    table.add_row("読んだ行", f"{rows_read:,}", "")
    table.add_row(
        "銘柄コードが読めた行",
        f"{kept:,}",
        f"[yellow]−{rows_without_code:,}[/]" if rows_without_code else "0",
    )
    table.add_row(
        "会計期にまとめた後",
        f"{disclosures:,}",
        f"−{kept - disclosures:,}" if kept >= disclosures else f"+{disclosures - kept:,}",
    )
    table.add_row(
        "書いた財務諸表",
        f"{statements:,}",
        f"−{disclosures - statements:,}" if disclosures >= statements else "",
    )
    table.add_row("触れた銘柄", f"{len(symbols):,}", "")
    console.print(table)
    console.print(
        "[dim]同じ銘柄が同じ会計期を2回開示していれば（訂正など）、"
        "そこで1本にまとまる。**段の差はそれで説明が付く。**[/dim]"
    )

    if rows_without_code:
        # **黙って捨てない。** 銘柄コードの無い行があるなら、列名か区切りの
        # 読み違いを疑う。取り込み済みのつもりで足りていない、が最悪である。
        console.print(
            f"[yellow]銘柄コードの無い行が {rows_without_code:,} 行あった。[/] "
            "列名の読み違いを疑う。"
        )
    if failed:
        console.print(f"[yellow]{len(failed)} 本は落とせなかった。[/]")
        for key, reason in failed[:5]:
            console.print(f"  [dim]{key}: {reason}[/dim]")

    console.print(
        "[bold]同じコマンドを再実行して安全である。[/] "
        "会計期をキーに上書きし、既にある値を空で潰さない。"
    )


def _file_month(key: str) -> str:
    """Return the file's ``YYYY-MM``, or an empty string when unreadable."""
    span = bulk_coverage([BulkFile(key, "", 0)])
    return span[0] if span else ""


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
    # **原本が覆う期間を渡す。** DB には立花の2001年以降も入っているので、
    # 全体の行数と取り込みの報告はそもそも一致しない。期間を切って初めて
    # 比べられる。
    covered = _archive_window(Path(DEFAULT_ARCHIVE_DIR))
    window = (covered[0], covered[1]) if covered else None
    coverage = audit(database, snapshots, window=window)
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
        f"{coverage.symbols_with_prices:,} 銘柄 / {coverage.price_rows:,} 行",
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
    # **名簿が壊れていないかは、この数で分かる。** ファイルが1つ欠けても、
    # 途中で切れても、延べ銘柄数が減る。改行コードの書き換えのような
    # 「中身は同じはず」の操作のあとに確かめる先がここになる。
    have.add_row("　名簿に一度でも出た銘柄", f"{coverage.roster_symbols:,} 銘柄", "")
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

    if covered:
        # **銘柄数だけでは、書けたことにならない。** 取り込みは「N 行を
        # 書いた」と言うが、それは upsert に渡した数である。渡したことと
        # 入ったことは別で、**入らなくても例外は出ない。**
        console.print(
            f"[dim]原本が覆う {covered[0]} 〜 {covered[1]} の日足は "
            f"[bold]{coverage.price_rows_in_window:,}[/] 行。"
            "**一括取り込みが「書いた」と言った行数と突き合わせること。**[/]"
        )
        # **どの原本から期間を取ったかも出す。** 間違った1本を選んでいても、
        # 日付だけでは気付けない——2026-09-15 に、20年ぶんを入れたのに
        # 「2021-09-01 〜」と出た。鍵の形が2通りあり、文字列で並べると新しい
        # ほうが前に来ていた。
        console.print(f"[dim]  期間の出どころ: {covered[2]}[/]")
        console.print(f"[dim]              〜 {covered[3]}[/]")

    console.print()
    files, with_lending, lending_rows = lending_coverage(Path(DEFAULT_SNAPSHOT_DIR))
    if files and with_lending == files:
        console.print(
            f"[dim]名簿 {files} 件すべてに貸借区分が入っている（延べ {lending_rows:,} 行）。[/]"
        )
    elif files and with_lending:
        missing = dates_without_lending(Path(DEFAULT_SNAPSHOT_DIR))
        console.print(
            f"[yellow]名簿 {files} 件のうち、貸借区分が入っているのは {with_lending} 件だけ"
            f"（延べ {lending_rows:,} 行）。[/] 欠けているのは "
            + "、".join(str(day) for day in missing)
            + "。"
        )
        # **5年ローリング窓の前端は毎日後ろへ動く。** 保存した当時は取れた日付が、
        # 今日はもう窓の外にある。そこを「取り直せる」と案内すると、成功しない
        # .bat を何度も実行させることになる。
        stale = beyond_the_window(missing, plan=settings.jquants_plan)
        if stale:
            console.print(
                "[dim]このうち "
                + "、".join(str(day) for day in stale)
                + " は**窓の外**なので、もう取り直せない。"
                "保存した当時は窓の中だった。窓の前端は毎日後ろへ動く。[/]"
            )
        if set(missing) - set(stale):
            console.print(
                "[dim]残りは `checks\\貸借区分を取り直す.bat` で取り直せる"
                "（2026-09-22 まで）。当日ぶんは、その日の名簿が出てから。[/]"
            )
    elif files:
        console.print(
            f"[red]名簿 {files} 件のどれにも貸借区分が入っていない。[/] "
            "**取得が成功していても列が空という形は例外を出さない。** "
            "応答の項目名が想定と違う可能性がある。"
        )

    console.print(
        "[dim]5年ローリング窓は解約より先に効く。いま取れるのは 2021-09 以降で、"
        "その端は**毎日後ろへ動く**。「解約日まで待てる」ものは1つも無い。[/]"
    )
    if coverage.snapshots and coverage.roster_without_prices:
        console.print(
            f"[yellow]名簿にあって株価が無い {coverage.roster_without_prices:,} 銘柄が"
            "残っている。[/] これがそのまま生存バイアスの残りである。"
        )
        # **件数だけでは追えない。** 一括で株価を入れる前も後も 16 のままだった。
        # 名前が並べば、全部が同じ性質か（同じ日に廃止した、同じ市場、同じ桁数）
        # が一目で分かる。
        if coverage.missing_priced:
            console.print(
                "[dim]"
                + "、".join(coverage.missing_priced[:40])
                + "[/]"
                + ("" if len(coverage.missing_priced) <= 40 else " …")
            )
            when = _roster_span(Path(DEFAULT_SNAPSHOT_DIR), coverage.missing_priced)
            if when:
                console.print("[dim]名簿に出ていた期間:[/]")
                for symbol, (first, last) in list(when.items())[:10]:
                    console.print(f"  [dim]{symbol}  {first} 〜 {last}[/]")

            # **この比較は JSON 経路の名簿に対して行っている。** 一括の名簿とは
            # 出所が違うので、片方にしか無い銘柄がありうる。そこを先に切り分け
            # ないと、「株価が取れなかった」と「そもそも一括に載っていない」を
            # 取り違える。
            daily = set(stored_dates(DAILY_SNAPSHOT_DIR))
            if daily:
                in_bulk = set()
                for on, codes in membership(DAILY_SNAPSHOT_DIR).items():
                    if on in daily:
                        in_bulk |= codes & set(coverage.missing_priced)
                only_json = [s_ for s_ in coverage.missing_priced if s_ not in in_bulk]
                console.print(
                    f"[dim]このうち一括の名簿にも出るのは {len(in_bulk)} 銘柄、"
                    f"**JSON 経路の名簿にしか出ないのは {len(only_json)} 銘柄。**[/]"
                )
                if only_json:
                    console.print(
                        "[dim]" + "、".join(only_json[:40]) + "[/]  "
                        "[dim]一括に載っていない銘柄は、株価も来ない。"
                        "**取りこぼしではなく、出所の違いである。**[/]"
                    )
    console.print(
        "[dim]会社予想と開示時刻は決算ドリフトのテーマ用で、そのテーマは"
        "2026-09-03 に閉じた（docs/HYPOTHESES.md）。**再開する予定が無いなら"
        "取り直す必要は無い。** 再開しうるなら、解約前が最後の機会になる。[/]"
    )


def _valuation_precision(directory: Path) -> dict:
    """Count how many decimals each valuation column actually carries.

    原本を1本だけ読んで、列ごとの小数点以下の桁数を数える。

    **書式は揃っている**ので、全部読む必要は無い。読めなければ空を返し、
    呼ぶ側は桁に依る判定をしない——**分からないことを、分かったことにしない。**
    """
    from stock_ai.data.jquants_archive import path_for, read_manifest
    from stock_ai.data.jquants_read import endpoint_of, read_archived
    from stock_ai.data.jquants_valuation import VALUATION_ENDPOINT, decimals_seen

    keys = sorted(key for key in read_manifest(directory) if endpoint_of(key) == VALUATION_ENDPOINT)
    if not keys:
        return {}
    # **いちばん新しい原本を見る。** 古いものは空の列が多く、桁を数える材料に
    # ならない。
    return decimals_seen(read_archived(path_for(directory, keys[-1])))


@app.command(name="jquants-valuation")
def jquants_valuation(
    directory: str = typer.Option(
        str(DEFAULT_ARCHIVE_DIR), "--dir", help="Where the archived originals live."
    ),
    limit: int | None = typer.Option(None, "--limit", help="Read only the first N files."),
) -> None:
    """Read the archived valuation originals and check them against themselves.

    **時価総額を自前で組み立てないための読み口。** 株価 × 発行済株式数 は分割
    を跨ぐと尺度が変わる——このプロジェクトが繰り返し踏んでいる形である。

    別の原本を持ち出す前に、このファイルだけで確かめる。``PER × EPS`` と
    ``PBR × BPS`` はどちらも終値を指すので、**合わなければ列の意味がこちらの
    想像と違う。**

    取りには行かない。読むだけ。
    """
    from stock_ai.data.jquants_valuation import (
        ACTUAL_COLUMNS,
        COLUMN_MAP,
        SHARES_PLAUSIBLE,
        digit_spread,
        half_widths,
        identity_check,
        resolve,
        share_stability,
    )
    from stock_ai.data.jquants_valuation import (
        census as valuation_census,
    )
    from stock_ai.data.jquants_valuation import (
        from_archive as valuation_from_archive,
    )

    settings = get_settings()
    configure_logging(settings.log_level)

    # **進捗は1行に収める。** 途中経過を残す形にすると、貼ったときに何十行にもなる。
    def progress(index: int, total: int, _key: str) -> None:
        console.print(f"読んでいる… {index}/{total}", end="\r")

    frame = valuation_from_archive(Path(directory), limit=limit, progress=progress)
    console.print(" " * 40, end="\r")
    if frame.empty:
        console.print("[red]原本が1本も無い。[/] `checks\\原本をまるごと保存.bat` が先。")
        return

    found = valuation_census(frame)
    console.print(f"{len(frame):,} 銘柄日、{found.symbols:,} 銘柄、{found.first} 〜 {found.last}")

    table = Table(title="年ごとに、実績の列がどれだけ埋まっているか")
    table.add_column("年")
    table.add_column("銘柄日", justify="right")
    for name in ACTUAL_COLUMNS.values():
        table.add_column(name, justify="right")
    for year, rows in found.rows_by_year.items():
        table.add_row(
            str(year),
            f"{rows:,}",
            *(
                f"{found.share(year, name):.0%}"
                if found.share(year, name) >= 0.5
                else f"[yellow]{found.share(year, name):.0%}[/]"
                for name in ACTUAL_COLUMNS.values()
            ),
        )
    console.print(table)

    # **公式の注意書きを引き写さない。** 「2008〜2010 は Null が多い」と書いて
    # あるが、どの列がどれだけ空なのかは書いていない。手元のファイルが答える。
    # **1列だけ見て済ませない。** 最初は時価総額しか見ていなかった。実データ
    # では `eps` が 2008〜2010 で 0%、`per` も 2011 年まで 0% だったのに、
    # **警告は1行も出なかった**（2026-09-15）。表には出ているが、表は読む側が
    # 気付く必要がある。**気付かなくても目に入るのが警告である。**
    for name in ACTUAL_COLUMNS.values():
        thin = found.thin_years(name)
        if not thin:
            continue
        empty = [year for year in thin if found.share(year, name) == 0]
        console.print(
            f"[yellow]{name} が半分も埋まっていない年: [/]"
            + "、".join(str(year) for year in thin)
            + (f" [red]うち {'、'.join(str(year) for year in empty)} は皆無。[/]" if empty else "")
        )
    if any(found.thin_years(name) for name in ACTUAL_COLUMNS.values()):
        console.print(
            "[dim]その年をまたいで並べ替えると、**埋まっている銘柄だけが選ばれる。**"
            "期間を切るか、列を替えるかを先に決めること。[/]"
        )

    # **許容幅を推測で決めない。** 桁は原本に書いてある。1% という決め打ちは、
    # PBR の丸め（1 前後の値を小数2桁なら ±0.5%）が作る裾を、ちょうど切って
    # いた。そしてその裾を「列の意味が違う」と読んだ（2026-09-15）。
    counts = _valuation_precision(Path(directory))
    widths = half_widths(counts)
    if widths:
        spread = Table(title="原本に載っている桁（丸めの幅はここから出る）")
        for column in ("列", "桁の内訳", "幅"):
            spread.add_column(column)
        for name in ACTUAL_COLUMNS.values():
            if name not in widths:
                continue
            source = next(key for key, value in COLUMN_MAP.items() if value == name)
            spread.add_row(name, digit_spread(counts, source), f"±{widths[name]:g}")
        console.print(spread)

    report = identity_check(frame)
    console.print(report.summary())

    # **推測の 1% ではなく、載っている桁から出した幅で見る。**
    #
    # そして**行ごとに**見る。全体の中央値では守れない——幅の中央値が 0.55%
    # でも、EPS が 0.01 の行は PER が 37,230 になり、EPS の ±0.005 が終値の
    # ±36% に化ける。その行は 31% ずれていても「収まった」に数えられていた
    # （2026-09-15、6784）。
    if widths:
        found = resolve(frame, widths)
        console.print(found.summary())
        console.print(
            f"[dim]幅の中央値 {found.bound_median:.2%} ／ ずれの中央値 {found.gap_median:.2%}。[/]"
        )
        if found.unresolvable:
            console.print(
                f"[dim]判定できない {found.unresolvable:,} 行は、EPS が小さく PER が"
                "大きい行である。**EPS の丸めが終値の何割にもなる。**[/]"
            )
        if found.outside:
            console.print(
                f"[red]説明の付かない食い違いが {found.outside:,} 行ある。[/] "
                "**丸めでは届かない。使う前にここを説明すること。**"
            )
        elif found.judged:
            console.print(
                "[green]判定できた行では、食い違いが1件も無い。[/] "
                "[dim]**列の意味は想像どおりで、残りは丸めである。**[/]"
            )

    # **時価総額は、上の突き合わせに1度も出てこない列である。**
    # `PER × EPS` と `PBR × BPS` が見ているのは4列だけで、時価総額はそこに
    # 入っていない。**確かめていないものを、確かめたつもりにしない。**
    if widths:
        held = share_stability(frame, widths)
        console.print()
        console.print(f"[bold]時価総額[/] {held.summary()}")
        if not held.steps:
            console.print("[yellow]株式数を割り出せる行が足りない。**確かめていない。**[/]")
        elif not held.units_hold:
            # **比例していることと、単位が円であることは別である。**
            #
            # 2026-09-15 に中央値 21 株と出た。日本の上場企業に 21 株の会社は
            # 無い。それでも「同じ尺度で作られている」と緑を出していた——桁を
            # 表示しておきながら、その数字を検査に使っていなかった。
            low, high = SHARES_PLAUSIBLE
            console.print(
                f"[red]割り出した株式数 {held.median_shares:,.0f} 株は、"
                f"ありうる桁（{low:,.0f}〜{high:,.0f} 株）から "
                f"{abs(held.orders_off):.1f} 桁はみ出している。[/]"
            )
            console.print(
                "**時価総額の単位は円ではない。** "
                "[dim]比例はしている（月ごとの散らばり "
                f"{held.spread_median:.2%} ≦ 丸めの {held.spread_slack:.2%}）ので、"
                "定数倍のずれである。**円として使うと、その定数倍だけ間違える。**[/]"
            )
        elif held.level_holds:
            console.print(
                "[green]割り出した株式数は、桁も散らばりも収まっている。[/] "
                "[dim]**時価総額は終値と同じ尺度・同じ単位で作られている。** "
                f"動いた {held.moved:,} 回は分割・増資とみられる。[/]"
            )
        else:
            console.print(
                f"[red]株式数の散らばり {held.spread_median:.2%} が、丸めで説明の付く "
                f"{held.spread_slack:.2%} を超えている。[/] "
                "**時価総額が終値と同じ尺度で動いていない。使う前にここを説明すること。**"
            )

        detail = Table(title="ずれの大きいもの")
        for column in ("日付", "銘柄", "PER × EPS", "PBR × BPS"):
            detail.add_column(column, justify="right" if "×" in column else "left")
        for date, symbol, left, right in report.worst:
            detail.add_row(str(date), symbol, f"{left:,.1f}", f"{right:,.1f}")
        console.print(detail)


@app.command(name="jquants-row-audit")
def jquants_row_audit(
    directory: str = typer.Option(
        str(DEFAULT_ARCHIVE_DIR), "--dir", help="Where the archived originals live."
    ),
    rosters: str = typer.Option(
        str(DEFAULT_SNAPSHOT_DIR), "--rosters", help="Where the dated rosters live."
    ),
) -> None:
    """Count archive rows against database rows, for delisted symbols only.

    **項目4 は、この形でしか閉じられない。** データベースは行ごとの出所を
    持っていないので、立花の行と J-Quants の行を区別できない。20年に伸ばした
    ら窓が立花と重なり、差がどちらから来たのか言えなくなった。

    立花のマスタは現存銘柄しか返さない。**廃止銘柄の株価が DB にあれば、それは
    J-Quants から来たものに決まっている。**

    取りには行かない。数えるだけ。
    """
    from stock_ai.data.jquants_rowaudit import audit as row_audit

    settings = get_settings()
    configure_logging(settings.log_level)

    database = Database()
    database.create_all()
    snapshots = membership(Path(rosters))
    if len(snapshots) < 2:
        console.print(
            f"[yellow]名簿が {len(snapshots)} 枚しかない。[/] "
            "**廃止銘柄を決められない。比べていない。**"
        )
        return

    covered = _archive_window(Path(directory))
    window = (covered[0], covered[1]) if covered else None
    if window:
        console.print(f"[dim]原本が覆う期間: {window[0]} 〜 {window[1]}[/]")

    def progress(index: int, total: int, _key: str) -> None:
        console.print(f"数えている… {index}/{total}", end="\r")

    found = row_audit(database, Path(directory), snapshots, window, progress)
    console.print(" " * 40, end="\r")
    console.print(found.summary())

    if not found.symbols:
        return
    if found.difference == 0:
        console.print(
            "[green]原本から読んだ行は、全部データベースに入っている。[/] "
            "[dim]**項目4 はここで閉じる。** 立花の行が混ざらない集合で数えた。[/]"
        )
    elif found.difference < 0:
        console.print(
            f"[red]データベースに {-found.difference:,} 行足りない。[/] "
            "**読めたのに入っていない。** 取り込みが途中で落ちた可能性がある。"
        )
    else:
        console.print(
            f"[red]データベースのほうが {found.difference:,} 行多い。[/] "
            "**廃止銘柄に、原本以外から入った行がある。** "
            "[dim]名簿の廃止判定か、取り込み経路のどちらかを疑うこと。[/]"
        )


@app.command(name="antivalue-estimate")
def antivalue_estimate(
    rosters: str = typer.Option(
        str(DEFAULT_SNAPSHOT_DIR), "--rosters", help="Where the dated rosters live."
    ),
    valuation: str | None = typer.Option(None, "--valuation", help="Month-end PBR file."),
    is_end: str = typer.Option("2017-12-31", "--is-end", help="Last day of the IS window."),
    oos_periods: int = typer.Option(104, "--oos-periods", help="Months the judgement will have."),
) -> None:
    """Measure the IS window for #9, so the gate table can be filled - not judge it.

    **段2（自分の IS から推定する）の材料を出す。** 文献値を持っていないので、
    見込みはここから置く。事前登録 `docs/PREREG_ANTIVALUE_JP.md` を見ること。

    出すのは3つ。**1期あたりのSD**（検出できる差を決める）、**入れ替わり率**
    （費用を決める）、**効果の推定**（封印するかどうかを決める）。

    **判定ではない。** IS は 2009-01〜2017-12 で、OOS（2018-01〜2026-08）には
    1日も触れない。
    """
    from stock_ai.backtest.antivalue import build_series as antivalue_series
    from stock_ai.backtest.multiplicity import (
        HYPOTHESIS_BUDGET,
        MEASURED_INFLATION,
        calibrated_t,
        required_t,
    )
    from stock_ai.backtest.power import estimate_power
    from stock_ai.data.valuation_monthly import DEFAULT_PATH
    from stock_ai.data.valuation_monthly import read as read_valuation

    settings = get_settings()
    configure_logging(settings.log_level)

    cut = _parse_date(is_end)
    if cut is None:
        raise typer.BadParameter(f"--is-end must be YYYY-MM-DD; got {is_end!r}.")

    frame = read_valuation(Path(valuation) if valuation else DEFAULT_PATH)
    if frame.empty:
        console.print("[red]月末の PBR が無い。[/] `checks\\月末のPBRを抜き出す.bat` が先。")
        raise typer.Exit(code=1)

    snapshots = membership(Path(rosters))
    if not snapshots:
        console.print("[red]名簿が無い。[/] **渡さないと生存バイアスが入る。**")
        raise typer.Exit(code=1)

    database = Database()
    database.create_all()
    console.print(f"[dim]IS は {cut} まで。OOS には1日も触れない。[/]")
    series = antivalue_series(database, frame, end=cut, snapshots=snapshots)
    console.print(series.summary())
    if not series.months:
        raise typer.Exit(code=1)

    # **費用を引いてから見る。** 「取引コスト込みのリターンで判定する」
    # （docs/PURPOSE.md）。実行できない大きさを見込みに置かないため。
    cost = series.cost_per_month()

    # **2通りで測る。生の差と、β を引いた α。**
    #
    # 事前登録 §5 が「β を引いた α も併記する」と書いてある。**最初はそこを
    # 落として生の差だけで出していた。** ロング・ショートでも β は 0 ではなく、
    # 割安な側は感応度が高いことが多いので、市場が動いた月はスプレッドが
    # 一方向に出る。**その上下動が分散のほとんどを作り、検出力を食う。**
    #
    # どちらが小さいかは測るまで分からない。**両方出して、どちらで設計するか
    # を数字を見てから決める——ただし決めるのは封印の前である。**
    beta = series.beta_to_benchmark()
    measured = {
        "生の差": [value - cost for value in series.spread()],
        "α（β を引いた）": [value - cost for value in series.alpha(beta)],
    }

    table = Table(title="§0 に入れる材料（IS から。判定ではない）")
    for column in ("項目", "生の差", "α（β を引いた）", "どこから"):
        table.add_column(column, overflow="fold")

    target = calibrated_t(HYPOTHESIS_BUDGET)
    stats: dict[str, tuple[float, float, float, float, float]] = {}
    for label, values in measured.items():
        estimate = estimate_power(values, lags=3)
        stats[label] = (
            estimate.daily_sd,
            estimate.inflation,
            fmean(values),
            estimate.standard_error(len(values)),
            # **判定に使える期数で当てる。** IS の 106ヶ月ではない。
            estimate.detectable(oos_periods, target_t=target),
        )

    raw, adjusted = stats["生の差"], stats["α（β を引いた）"]
    table.add_row("1期あたりのSD", f"{raw[0]:.2%}", f"{adjusted[0]:.2%}", "IS の月次、費用引き後")
    table.add_row(
        "重なりの膨張", f"{raw[1]:.2f}x", f"{adjusted[1]:.2f}x", "Newey-West(3) と素の分散の比"
    )
    table.add_row(
        "検出できる差",
        f"年 {raw[4] * 12:.1%}",
        f"年 {adjusted[4] * 12:.1%}",
        f"t≥{target:.2f}・{oos_periods}期",
    )
    table.add_row("判定に使える期数", f"{oos_periods}", "—", "OOS の月数。**全期間ではない**")
    table.add_row("入れ替わり", f"{series.turnover():.1%}／月", "—", "実測。#7 の値は写していない")
    table.add_row("費用", f"年 {cost * 12:.2%}", "—", "往復 0.40% × 入れ替わり")
    table.add_row("β", f"{beta:+.2f}", "—", "スプレッドの、指数に対する感応度。IS で推定")
    console.print(table)

    mean, stderr = raw[2], raw[3]
    low, high = mean - 1.96 * stderr, mean + 1.96 * stderr
    console.print(
        f"[bold]IS の効果（生の差・費用引き後）: 年 {mean * 12:+.2%}[/] "
        f"[dim]（95% の幅 年 {low * 12:+.2%} 〜 {high * 12:+.2%}）[/]"
    )
    console.print(
        f"[dim]α でも併記する: 年 {adjusted[2] * 12:+.2%}"
        f"（95% の幅 年 {(adjusted[2] - 1.96 * adjusted[3]) * 12:+.2%} 〜 "
        f"{(adjusted[2] + 1.96 * adjusted[3]) * 12:+.2%}）。"
        f"**線を当てるのは生の差のほうである**——§10 が「分位差」と書いている。[/]"
    )

    # **測る前にコミットした線である。** 動かさない。
    floor = 0.01
    console.print()
    if mean * 12 < floor:
        console.print(
            f"[red]封印しない。[/] IS の推定 年 {mean * 12:+.2%} が、"
            f"**測る前にコミットした線 年 {floor:.1%} を下回った。**"
        )
        console.print(
            "[dim]事前登録 §0 にそう書いてある。**下回ったら、検出できても"
            "実行できない。** 線は動かさない。[/]"
        )
        return

    console.print(f"[green]線（年 {floor:.1%}）は上回った。[/] 次は §0 のゲートである。")
    console.print(
        "[dim]uv run stock-ai power-gate "
        f"--sd {raw[0] * 100:.2f} --periods {oos_periods} "
        f"--low {low * 12 * 100:.2f} --high {high * 12 * 100:.2f} "
        f"--inflation {raw[1]:.2f} --budget {HYPOTHESIS_BUDGET}[/]"
    )
    console.print(
        f"[dim]必要な t は {calibrated_t(HYPOTHESIS_BUDGET):.2f}"
        f"（予算 {HYPOTHESIS_BUDGET} 本の {required_t(HYPOTHESIS_BUDGET):.2f} に、"
        f"対照で測った膨張 {MEASURED_INFLATION:.2f} を掛けた）。"
        "補正なしの 2.0 ではない。[/]"
    )


@app.command(name="momentum-power")
def momentum_power(
    rosters: str = typer.Option(
        str(DEFAULT_SNAPSHOT_DIR), "--rosters", help="Where the dated rosters live."
    ),
    is_end: str = typer.Option("2017-12-31", "--is-end", help="Last day of the IS window."),
    oos_periods: int = typer.Option(104, "--oos-periods", help="Months the judgement will have."),
) -> None:
    """Measure the IS window for #12, so the gate table can be filled - not judge it.

    **段2（自分の IS から推定する）の材料を出す。** 文献はあるが**一次資料に
    届かなかった**ので、見込みはここから置く（`docs/PREREG_MOMENTUM_JP.md` §0）。

    出すのは4つ。**1期あたりのSD**（検出できる差を決める）、**入れ替わり率**
    （費用を決める）、**効果の推定**（封印するかどうかを決める）、**裾**
    （急反転でどれだけ持っていかれるか）。

    **判定ではない。** IS は 2009-01〜2017-12 で、OOS（2018-01〜2026-08）には
    1日も触れない。

    **設計は事前登録 §3 が固定している。** 形成12ヶ月・直近1ヶ月スキップ・
    保有1ヶ月・5分位・等加重。**動かす引数を置いていない**のは、動かせると
    試して選ぶことになるからである。
    """
    from stock_ai.backtest.momentum import (
        FORMATION_MONTHS,
        SKIP_MONTHS,
    )
    from stock_ai.backtest.momentum import (
        build_series as momentum_series,
    )
    from stock_ai.backtest.multiplicity import (
        HYPOTHESIS_BUDGET,
        MEASURED_INFLATION,
        calibrated_t,
        required_t,
    )
    from stock_ai.backtest.power import estimate_power

    settings = get_settings()
    configure_logging(settings.log_level)

    cut = _parse_date(is_end)
    if cut is None:
        raise typer.BadParameter(f"--is-end must be YYYY-MM-DD; got {is_end!r}.")

    snapshots = membership(Path(rosters))
    if not snapshots:
        console.print("[red]名簿が無い。[/] **渡さないと生存バイアスが入る。**")
        raise typer.Exit(code=1)

    database = Database()
    database.create_all()
    console.print(
        f"[dim]IS は {cut} まで。OOS には1日も触れない。"
        f"形成 {FORMATION_MONTHS} ヶ月・直近 {SKIP_MONTHS} ヶ月スキップ・5分位。[/]"
    )
    series = momentum_series(database, end=cut, snapshots=snapshots)
    console.print(series.summary())
    for line in series.warnings():
        console.print(f"[yellow]{line}[/]")
    if not series.months:
        raise typer.Exit(code=1)

    # **費用を引いてから見る。** 実行できない大きさを見込みに置かないため。
    cost = series.cost_per_month()

    # **生の差と α の両方を出す。** #9 は §5 に「α も併記する」と書きながら
    # §0 を生の差だけで埋めた（2026-09-16）。ここは最初から両方出す。
    beta = series.beta_to_benchmark()
    measured = {
        "生の差": [value - cost for value in series.spread()],
        "α（β を引いた）": [value - cost for value in series.alpha(beta)],
    }

    table = Table(title="§0 に入れる材料（IS から。判定ではない）")
    for column in ("項目", "生の差", "α（β を引いた）", "どこから"):
        table.add_column(column, overflow="fold")

    target = calibrated_t(HYPOTHESIS_BUDGET)
    stats: dict[str, tuple[float, float, float, float, float]] = {}
    for label, values in measured.items():
        estimate = estimate_power(values, lags=3)
        stats[label] = (
            estimate.daily_sd,
            estimate.inflation,
            fmean(values),
            estimate.standard_error(len(values)),
            # **判定に使える期数で当てる。** IS の月数ではない。
            estimate.detectable(oos_periods, target_t=target),
        )

    raw, adjusted = stats["生の差"], stats["α（β を引いた）"]
    table.add_row("1期あたりのSD", f"{raw[0]:.2%}", f"{adjusted[0]:.2%}", "IS の月次、費用引き後")
    table.add_row(
        "重なりの膨張", f"{raw[1]:.2f}x", f"{adjusted[1]:.2f}x", "Newey-West(3) と素の分散の比"
    )
    table.add_row(
        "検出できる差",
        f"年 {raw[4] * 12:.1%}",
        f"年 {adjusted[4] * 12:.1%}",
        f"t≥{target:.2f}・{oos_periods}期",
    )
    table.add_row("判定に使える期数", f"{oos_periods}", "—", "OOS の月数。**全期間ではない**")
    table.add_row(
        "入れ替わり", f"{series.turnover():.1%}／月", "—", "実測。**#9 の値は写していない**"
    )
    table.add_row("費用", f"年 {cost * 12:.2%}", "—", "往復 0.40% × 入れ替わり")
    table.add_row("β", f"{beta:+.2f}", "—", "スプレッドの、指数に対する感応度。IS で推定")
    console.print(table)

    # **裾を見る**（事前登録 §5）。平均が同じでも、ここが違えば別の戦略である。
    tail = Table(title="裾（生の差・費用引き後）")
    for column in ("項目", "値", "なぜ見るか"):
        tail.add_column(column, overflow="fold")
    tail.add_row("いちばん悪かった月", f"{series.worst_month() - cost:+.2%}", "1回の事故の大きさ")
    tail.add_row("下位5%の月の平均", f"{series.left_tail() - cost:+.2%}", "**1点ではなく帯で見る**")
    tail.add_row("勝った月の割合", f"{series.hit_rate():.1%}", "平均だけで語らない")
    console.print(tail)
    console.print(
        "[dim]**モメンタムは平常時に効いても、急反転で損失が集中しうる。** "
        "平均が同じでも、ここが違えば別の戦略である（事前登録 §5）。[/]"
    )

    mean, stderr = raw[2], raw[3]
    low, high = mean - 1.96 * stderr, mean + 1.96 * stderr
    console.print(
        f"[bold]IS の効果（生の差・費用引き後）: 年 {mean * 12:+.2%}[/] "
        f"[dim]（95% の幅 年 {low * 12:+.2%} 〜 {high * 12:+.2%}）[/]"
    )
    console.print(
        f"[dim]α でも併記する: 年 {adjusted[2] * 12:+.2%}"
        f"（95% の幅 年 {(adjusted[2] - 1.96 * adjusted[3]) * 12:+.2%} 〜 "
        f"{(adjusted[2] + 1.96 * adjusted[3]) * 12:+.2%}）。"
        f"**線を当てるのは生の差のほうである**——§10 が「分位差」と書いている。[/]"
    )

    # **測る前にコミットした線である。** 動かさない（事前登録 §0）。
    floor = MOMENTUM_FLOOR
    console.print()
    if mean * 12 < floor:
        console.print(
            f"[red]封印しない。[/] IS の推定 年 {mean * 12:+.2%} が、"
            f"**測る前にコミットした線 年 {floor:.1%} を下回った。**"
        )
        console.print(
            "[dim]事前登録 §0 にそう書いてある。**下回ったら、検出できても"
            "実行できない。** 線は動かさない。[/]"
        )
        return

    console.print(f"[green]線（年 {floor:.1%}）は上回った。[/] 次は §0 のゲートである。")
    console.print(
        "[dim]uv run stock-ai power-gate "
        f"--sd {raw[0] * 100:.2f} --periods {oos_periods} "
        f"--low {low * 12 * 100:.2f} --high {high * 12 * 100:.2f} "
        f"--inflation {raw[1]:.2f} --budget {HYPOTHESIS_BUDGET}[/]"
    )
    console.print(
        f"[dim]必要な t は {calibrated_t(HYPOTHESIS_BUDGET):.2f}"
        f"（予算 {HYPOTHESIS_BUDGET} 本の {required_t(HYPOTHESIS_BUDGET):.2f} に、"
        f"対照で測った膨張 {MEASURED_INFLATION:.2f} を掛けた）。"
        "補正なしの 2.0 ではない。[/]"
    )


@app.command(name="january-power")
def january_power(
    rosters: str = typer.Option(
        str(DEFAULT_SNAPSHOT_DIR), "--rosters", help="Where the dated rosters live."
    ),
    valuation: str | None = typer.Option(None, "--valuation", help="Month-end valuation file."),
    is_end: str = typer.Option("2017-12-31", "--is-end", help="Last day of the IS window."),
    oos_periods: int = typer.Option(9, "--oos-periods", help="Januaries the judgement has."),
) -> None:
    """Measure the IS window for #14, so the gate table can be filled - not judge it.

    **段2（自分の IS から推定する）の材料を出す。** 文献はこの環境から読めない
    ので、見込みはここから置く（`docs/PREREG_JANUARY_JP.md` §0）。

    **測るのはサイズの傾きである。** 時価総額で5分位に分け、**最小 − 最大**の
    月次スプレッドを作り、**その年の1月**と**同じ年の他の月の平均**の差を取る。
    観測は**年に1回**しかない。

    **判定ではない。** IS は 2009-01〜2017-11 で、OOS（2018-01〜2026-08）には
    1日も触れない。

    **n=9 では `t` が正規から離れる。** 自由度8の正しい線は 4.33 で、`t` の SD
    から出す 3.49 では **24% 甘い**（事前登録 §0）。ここでは**素の線 3.02**
    （下限）と**自由度8の線**の両方で検出できる差を出す。
    """
    from stock_ai.backtest.january import (
        JanuarySeries,
        annual_episodes,
    )
    from stock_ai.backtest.january import (
        build_series as size_series,
    )
    from stock_ai.backtest.multiplicity import (
        HYPOTHESIS_BUDGET,
        required_t,
        student_t_line,
    )
    from stock_ai.backtest.power import estimate_power, gate, periods_needed
    from stock_ai.core.logging import quiet_on_console
    from stock_ai.data.valuation_monthly import DEFAULT_PATH
    from stock_ai.data.valuation_monthly import read as read_valuation

    settings = get_settings()
    configure_logging(settings.log_level)

    cut = _parse_date(is_end)
    if cut is None:
        raise typer.BadParameter(f"--is-end must be YYYY-MM-DD; got {is_end!r}.")
    if oos_periods < 3:  # noqa: PLR2004 - 3点無いと散らばりが測れない
        raise typer.BadParameter(f"--oos-periods must be at least 3; got {oos_periods}.")

    snapshots = membership(Path(rosters))
    if not snapshots:
        console.print("[red]名簿が無い。[/] **渡さないと生存バイアスが入る。**")
        raise typer.Exit(code=1)

    frame = read_valuation(Path(valuation) if valuation else DEFAULT_PATH)
    if frame.empty:
        console.print("[red]月末の時価総額が無い。[/] `checks\\月末のPBRを抜き出す.bat` を先に。")
        raise typer.Exit(code=1)

    database = Database()
    database.create_all()
    console.print(
        f"[dim]IS は {cut} まで（実現した月で切る）。OOS には1日も触れない。"
        "時価総額で5分位・等加重・月次組み替え、スプレッドは**小型 − 大型**。[/]"
    )

    # **ループの中の1行記録を、貼られる出力に出さない。** ファイルには残る。
    with quiet_on_console("stock_ai.backtest.quantile_series", "stock_ai.backtest.january"):
        series = size_series(database, frame, end=cut, snapshots=snapshots)
    console.print(series.summary())
    for line in series.warnings():
        console.print(f"[yellow]{line}[/]")
    if not series.months:
        raise typer.Exit(code=1)

    # **生の差と α の両方を出す**（事前登録 §5）。#9 は「併記する」と書いて
    # おきながら生の差だけで §0 を埋めた。
    beta = series.beta_to_benchmark()
    with quiet_on_console("stock_ai.backtest.january", "stock_ai.backtest.power"):
        measured: dict[str, JanuarySeries] = {
            "生の差": annual_episodes(series.months, series.spread(), series.counts),
            "α（β を引いた）": annual_episodes(series.months, series.alpha(beta), series.counts),
        }
    annual = measured["生の差"]
    console.print(annual.summary())
    for line in annual.warnings():
        console.print(f"[yellow]{line}[/]")
    if len(annual.years) < 3:  # noqa: PLR2004 - 3点無いと散らばりが測れない
        console.print(f"[red]1月の観測が {len(annual.years)} しかない。[/] 散らばりを測れない。")
        raise typer.Exit(code=1)

    # **判定の線ではない。線の下限である。** この管はまだ校正していないが、
    # 膨張には下限 1.0 があるので（`INFLATION_FLOOR`）、**線がこれより下がる
    # ことはない。** ここで通らないなら、対照を回しても通らない。
    line_floor = required_t(HYPOTHESIS_BUDGET)
    # **自由度は「判定に使う観測数 − 1」である。** IS の年数ではない。
    small_line = student_t_line(oos_periods - 1, HYPOTHESIS_BUDGET)

    table = Table(title="§0 に入れる材料（IS から。判定ではない）")
    for column in ("項目", "生の差", "α（β を引いた）", "どこから"):
        table.add_column(column, overflow="fold")

    stats: dict[str, tuple[float, float, float, float, float]] = {}
    with quiet_on_console("stock_ai.backtest.power"):
        for label, built in measured.items():
            # **膨張は 1.0 に固定する。** n=9 では Newey-West が不安定なので、
            # 代わりに1次の自己相関を出す（事前登録 §5）。
            estimate = estimate_power(built.episodes, lags=0)
            stats[label] = (
                estimate.daily_sd,
                built.autocorrelation(),
                fmean(built.episodes),
                estimate.standard_error(len(built.episodes)),
                estimate.detectable(oos_periods, target_t=line_floor),
            )
        raw, adjusted = stats["生の差"], stats["α（β を引いた）"]
        tight = estimate_power(annual.episodes, lags=0).detectable(oos_periods, target_t=small_line)

    table.add_row("1観測あたりのSD", f"{raw[0]:.2%}", f"{adjusted[0]:.2%}", "IS の1月ごと")
    table.add_row(
        "1次の自己相関",
        f"{raw[1]:+.2f}",
        f"{adjusted[1]:+.2f}",
        "実測。**膨張は 1.0 に固定**（n=9 では Newey-West が不安定）",
    )
    table.add_row(
        "検出できる差（素の線）",
        f"1月 {raw[4]:.2%}",
        f"1月 {adjusted[4]:.2%}",
        f"t≥{line_floor:.2f}・{oos_periods}回。**線の下限**",
    )
    table.add_row(
        f"検出できる差（自由度 {oos_periods - 1} の線）",
        f"1月 {tight:.2%}",
        "—",
        f"t≥{small_line:.2f}。**対照がこれより甘い線を出すことはない**",
    )
    table.add_row("判定に使える回数", f"{oos_periods}", "—", "OOS の1月の回数。**月数ではない**")
    table.add_row(
        "入れ替わり", f"{series.turnover():.1%}／月", "—", "実測。**参考**——費用は引いていない"
    )
    table.add_row("β", f"{beta:+.2f}", "—", "スプレッドの、指数に対する感応度。IS で推定")
    console.print(table)

    tail = Table(title="裾（生の差・1月あたり）")
    for column in ("項目", "値", "なぜ見るか"):
        tail.add_column(column, overflow="fold")
    tail.add_row("いちばん悪かった年", f"{annual.worst_year():+.2%}", "1回の事故の大きさ")
    tail.add_row(
        "下位5%の平均",
        f"{annual.left_tail():+.2%}",
        f"**{len(annual.years)}観測の 5% は1点に丸まる。** 最悪の年と一緒に読む",
    )
    tail.add_row("正だった年の割合", f"{annual.hit_rate():.1%}", "平均だけで語らない")
    console.print(tail)

    mean, stderr = raw[2], raw[3]
    # **95% の幅も、正規ではなく `t` で取る。** n=9 なら 1.96 ではなく 2.31。
    width = student_t_line(len(annual.episodes) - 1, budget=1)
    low, high = mean - width * stderr, mean + width * stderr
    console.print(
        f"[bold]IS の差（1月あたり）: {mean:+.2%}[/] "
        f"[dim]（95% の幅 {low:+.2%} 〜 {high:+.2%}。幅は t({len(annual.episodes) - 1}) の "
        f"{width:.2f} で取った——**1.96 ではない**）[/]"
    )
    console.print(
        f"[dim]α でも併記する: 1月 {adjusted[2]:+.2%}。"
        "**線を当てるのは生の差のほうである**——§10 が「分位差」と書いている。[/]"
    )
    console.print(
        f"[dim]内訳: 1月の平均 {fmean(annual.januaries):+.2%}、"
        f"引いた他の月の平均 {fmean(annual.others):+.2%}。"
        "**`t` だけ見ない。下にある量も出す**（`CLAUDE.md`）。[/]"
    )

    # **測る前にコミットした線である。** 動かさない（事前登録 §0）。
    console.print()
    held = mean >= JANUARY_FLOOR
    if held:
        console.print(f"[green]線（1月 {JANUARY_FLOOR:.1%}）は上回った。[/]")
    else:
        console.print(
            f"[red]線を下回った。[/] IS の推定 1月 {mean:+.2%} が、"
            f"**測る前にコミットした線 1月 {JANUARY_FLOOR:.1%} に届かない。**"
        )
        console.print(
            "[dim]事前登録 §0 にそう書いてある。**下回ったら、将来売買しても"
            "費用を賄えない。** 線は動かさない。[/]"
        )

    # **§0 の当てはめは `power.gate` に聞く。** ここで書き直さない。
    #
    # **1度書き直して、緩いほうに外した**（2026-09-19）。合格線は「**見込みの
    # 下限**が検出できる差を上回ること」なのに、**上限**と比べていた。それは
    # #7 が落ちた形——「見込みが検出できる差をまたぐ」——をそのまま通す。
    console.print()
    verdict = gate(raw[4], low, high)
    colour = "green" if verdict.passed else "red"
    console.print(
        f"[{colour}]§0（素の線 t≥{line_floor:.2f}）: {verdict.verdict}。[/] {verdict.reading}"
    )
    console.print(
        f"[dim]見込み {low:+.2%} 〜 {high:+.2%}、検出できる差 {raw[4]:.2%}。"
        "**合格線は「下限が検出できる差を上回ること」の1つだけ**（`CLAUDE.md`）。[/]"
    )

    if verdict.passed:
        # 素の線で通ったなら、**この管の線を測らないと決まらない。**
        tighter = gate(tight, low, high)
        console.print(
            f"[dim]自由度 {oos_periods - 1} の線（t≥{small_line:.2f}）なら "
            f"{tighter.verdict}。**その線は「対照が素直ならこうなる」値で、"
            "実測ではない。**[/]"
        )
    else:
        # **何年あれば足りるかを書く**（`CLAUDE.md`）。下限が 0 をまたいで
        # いるなら、持ち上げる先が無い——#13 と同じである。
        if low > 0:
            years = periods_needed(raw[0], 1.0, low, target_t=line_floor)
            console.print(
                f"[dim]下限 {low:+.2%} を検出するには **{years:,} 回＝{years:,} 年**要る。"
                f"手元の OOS は {oos_periods} 年で、**{years - oos_periods:,} 年足りない。**[/]"
            )
        else:
            centre = periods_needed(raw[0], 1.0, max(mean, 1e-9), target_t=small_line)
            console.print(
                f"[dim]**下限が 0 をまたいでいるので、持ち上げる先が無い。** "
                f"参考までに、**中心の {mean:+.2%}** を自由度 {oos_periods - 1} の線で"
                f"検出するには **{centre:,} 年**要る（手元は {oos_periods} 年、"
                f"**{max(centre - oos_periods, 0):,} 年足りない**）。"
                "**中心は合否に使わない**——#7 はそれで回して落ちた。[/]"
            )

    # **結論は1つだけ、最後に置く。** 途中の行を結論と読まれないため——
    # 線と関門は**どちらか一方でも閉じる**（事前登録 §10）。
    console.print()
    if not held:
        console.print(
            "[bold red]結論: 封印しない。[/] **線を下回っている。** "
            "上の関門をどう読んでも変わらない（事前登録 §10）。"
        )
    elif not verdict.passed:
        console.print(
            "[bold red]結論: 封印しない。[/] **§0 の関門を通らない。** "
            "**対照を回すまでもない**——線には下限があるので、"
            "測っても検出できる差は縮まない。"
        )
    else:
        console.print(
            "[bold]結論: ここでは決まらない。[/] **この管の対照を回してから決める**"
            "（事前登録 §0・§10）。"
        )


#: これを超えたら、2つの並べ方は**実質同じ設計**とみなす（順位相関の絶対値）。
#:
#: **出典は無い。決めの値である。** 壁の表に実質同じ設計が2行在ると、
#: **後で良いほうを選んだのと区別が付かない**——`CLAUDE.md`「2箇所に同じ説が
#: あると、どちらが本当か分からなくなる」の設計版である。
SAME_DESIGN = 0.8


def _market_cap_values() -> dict[tuple[str, object], tuple[object, float]]:
    """Read the month-end market caps, for the small-cap candidate.

    **時価総額は価格の走査からは出ない。** `/equities/valuation` の
    `MktCap` を月末に畳んだ生成物（`valuation_monthly`）を読む。

    **無ければ空を返す。** 呼ぶ側が「材料が無い」と出す——**黙って候補を
    1つ落とさない**（`CLAUDE.md`「無いことは、出力に出ない」）。

    **`MktCap` は百万円単位である。** 分位に並べるだけなので単位は効かない
    が、**額として使うなら百万倍間違える**（2026-09-15 に踏んだ）。
    """
    import pandas as pd

    from stock_ai.data import valuation_monthly

    frame = valuation_monthly.read()
    if frame.empty or "market_cap" not in frame.columns:
        return {}
    found: dict[tuple[str, object], tuple[object, float]] = {}
    for row in frame.itertuples(index=False):
        value = getattr(row, "market_cap", None)
        when = getattr(row, "date", None)
        if value is None or when is None or not float(value) > 0:
            continue
        stamp = pd.Timestamp(when)
        found[(str(row.symbol), stamp.to_period("M"))] = (stamp.date(), float(value))
    return found


def _wall_ir_cell(wall: object) -> str:
    """Build the required-information-ratio cell, for the table and the document.

    **`_inflation_cell` と同じ作りである。** 2つ持つと、片方だけ直した
    ときに行が自分と食い違う。
    """
    found = wall.required_ir  # type: ignore[attr-defined]
    if found is None:
        return "—"
    years = wall.period_years  # type: ignore[attr-defined]
    return f"{found:.2f}（{years:.1f}年）"


def _wall_document(walls: list[object], missing: list[object], span: str) -> str:
    """Build the body of `docs/WALL.md` - a generated file, never edited by hand.

    Args:
        walls: 測れた設計。
        missing: 材料が無くて測れなかった候補。
        span: 何を読んだか。

    Returns:
        文書の全文。
    """
    lines = [
        "# 壁の下見 — まだ登録していない説の、検出できる差",
        "",
        "**この文書は生成物である。** 手で直さない——"
        "`uv run stock-ai wall-survey --write docs/WALL.md` が作り直す。",
        "",
        "**効果（平均）は1つも出していない。** 検出できる差は `線 × SD ÷ √n` で、"
        "**散らばりと観測数だけで決まる。** だから、これを見ても答えを先に見た"
        "ことにならない（`backtest/wall.py` の冒頭に理由を書いた）。",
        "",
        f"**読んだもの:** {span}",
        "",
        "## 壁の高さ",
        "",
        "| 候補 | 設計 | 管 | n | 1観測あたりのSD | 重なりの膨張 | 線 | "
        "**検出できる差** | 要る情報比 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for wall in walls:
        annual = wall.annual  # type: ignore[attr-defined]
        size = (
            f"年 {annual:.1%}" if annual is not None else f"1{wall.unit} {wall.detectable:.2%}"  # type: ignore[attr-defined]
        )
        # **表と同じところから作る。** 2つ持つと、片方だけ直したときに
        # 行が自分と食い違う。
        blown = _inflation_cell(wall)
        lines.append(
            f"| {wall.candidate} | {wall.name} | {wall.pipe} | "  # type: ignore[attr-defined]
            f"{wall.observations:,} | {wall.sd:.2%}／{wall.unit} | "  # type: ignore[attr-defined]
            f"{blown} | "
            f"`t ≥ {wall.line:.2f}` | **{size}** | {_wall_ir_cell(wall)} |"  # type: ignore[attr-defined]
        )
    lines += [
        "",
        "**イベント型は年率に直していない。** 資金をどれだけ張るかを決めないと"
        "直せない（`docs/PASSING.md` と同じ扱い）。**要る情報比も同じ理由で"
        "出していない**——重なる窓は、取引できる系列ではない。",
        "",
        "### 検出できる差を、行どうしで比べない",
        "",
        "**単位も、市場に居る時間の割合も違う。** 候補7 と候補11 がその実例で、"
        "年 32.0% 対 24.5% と出ていたが、**要る情報比では 1.12 対 1.09 で"
        "ほとんど差が無い**——違いは効果ではなく、**絞って市場に居ない時間が"
        "あること**だった。",
        "",
        "**要る情報比は `線 × 膨張 ÷ √年数` で、設計によらない。** n も SD も"
        "効かない（`docs/PASSING.md` §2）。**そこを比べる。**",
        "",
        "## 材料が無くて測れなかった候補",
        "",
        "| 候補 | 説 | なぜ測れないか |",
        "|---|---|---|",
    ]
    for item in missing:
        lines.append(
            f"| {item.candidate} | {item.name} | {item.reason} |"  # type: ignore[attr-defined]
        )
    lines += [
        "",
        "**無いことは、出力に出ない。** だから、測れなかったほうも表にする。",
        "",
        "## これをどう使うか",
        "",
        "**壁を越えうる設計にだけ、事前登録を書く。** 越えられないものは、"
        "この表の数字を添えて候補のまま残す——**書かない理由が数字で残る。**",
        "",
        "**壁を比べても、どの説が正しいかは分からない。** ここに出ているのは"
        "「見分けられる最小の大きさ」だけで、**効果は測っていない。**",
        "",
        "**窓やしきい値を変えれば壁も動く。** イベント型はどれも 20営業日で"
        "揃えてある（`docs/PREREG_REVISION_JP.md` の #5 と同じ物差し）。"
        "事前登録が別の窓を選ぶなら、**そこで測り直す。**",
        "",
    ]
    return "\n".join(lines)


#: 次の10本を組むのに、材料が在るか確かめたいエンドポイント。
#:
#: **「無い」と書いたことも、出力に出ない**（`CLAUDE.md`、2026-09-21）。
#: 候補6と7を「測れない」と書いていたのが両方とも誤りだったので、
#: **確かめる口のほうを置く。**
NEXT_MATERIALS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "/fins/summary",
        "高配当利回り（候補14）。**`/equities/valuation` に利回りの列は無い**",
        # **111 列ある。** 全部刷ると、この1本で 111 行になる——`CLAUDE.md`
        # 「出力を小さく保つ」。**探しているものだけ出す。**
        ("div", "配当"),
    ),
    ("/markets/short-ratio", "空売り比率（候補16）。**業種別である**——指数の1本ではない", ()),
    ("/markets/margin-interest", "信用買い残（候補15）", ()),
    ("/equities/valuation", "小型・低位・節目（候補12・13・19）。**時価総額はここ**", ()),
)

#: 表に出す列の上限。**貼られる前提で作る**（`CLAUDE.md`「出力を小さく保つ」）。
#:
#: **実データで `/fins/summary` が 111 列だった**（2026-09-21）。全部刷ると
#: 4つのエンドポイントで 450 行を超え、**ユーザーが先頭だけ貼ることになった。**
#: **見たい列が、件数の多い列に押し出される**——`MAX_SKIPPED_PER_REASON` と
#: 同じ形である。
MAX_COLUMNS_SHOWN = 24


@app.command(name="yield-audit")
def yield_audit(
    directory: str = typer.Option(
        str(DEFAULT_ARCHIVE_DIR), "--dir", help="Where the archived originals live."
    ),
    limit: int = typer.Option(20, "--limit", help="Rows to print, largest yield first."),
) -> None:
    """Look at the dividend yields that came out impossibly high - nothing is fetched.

    **実データで 647 銘柄月（0.26%）が 20% を超えた**（2026-09-21）。
    **外していないので、その 647 件が SD に入ったまま壁が出ている**
    ——外れ値は SD に効くので、**件数の小ささは理由にならない。**

    **3つのどれかである。**

    | 見え方 | どれか |
    |---|---|
    | **予想 ÷ 実績 が 10 や 100** | **訂正前の誤記**（2131 の `5600 → 56` と同じ形） |
    | どちらも大きい | **株価か単位**である |
    | 実績が空 | **無配への訂正前**か、予想しか出していない |

    **「中身を見ること」と書いて見る道具が無い、を3度やった。** ここは
    **銘柄・月・開示日・予想・実績・終値・利回り・比**を出す。

    **原因を推測で決めない。** 比が 100 に揃っていても「訂正はいつも 100倍」
    にはならない——**割合で見る。**

    **取りには行かない。** 原本を読むだけである。
    """
    from stock_ai.backtest.wall import IMPLAUSIBLE_YIELD, dividend_yields, scan
    from stock_ai.core.logging import quiet_on_console

    settings = get_settings()
    configure_logging(settings.log_level)

    database = Database()
    database.create_all()
    console.print("[dim]価格を走査しています（1銘柄1行は出しません）...[/]")
    with quiet_on_console("stock_ai.backtest.wall"):
        materials = scan(database)
        _values, census = dividend_yields(Path(directory), materials.raw_price_level)

    console.print(f"[dim]{census.summary()}[/]")
    for line in census.warnings():
        console.print(f"[yellow]{line}[/]")
    if not census.worst:
        console.print("[green]**20% を超えた銘柄月は1つも無い。**[/]")
        return

    table = Table(title=f"利回りが {IMPLAUSIBLE_YIELD:.0%} を超えた銘柄月（大きい順）")
    for column in ("銘柄", "月", "開示日", "予想", "実績", "終値", "利回り", "予想÷実績"):
        table.add_column(column, overflow="fold", justify="right" if column != "月" else "left")
    for item in census.worst[:limit]:
        ratio = item.ratio
        table.add_row(
            item.symbol,
            item.month,
            f"{item.disclosed_on}",
            f"{item.forecast:,.2f}",
            "—" if item.actual is None else f"{item.actual:,.2f}",
            f"{item.close:,.1f}",
            f"{item.yielded:.1%}",
            "—" if ratio is None else f"{ratio:,.1f}",
        )
    console.print(table)
    if len(census.worst) > limit:
        console.print(
            f"[dim]あと {len(census.worst) - limit:,} 件は出していない（`--limit` で増える）。[/]"
        )

    # **割合で見る。** 「比が 100 の行が在る」では、何も決まらない。
    rounds = [
        item.ratio
        for item in census.worst
        if item.ratio is not None and item.ratio >= 5.0  # noqa: PLR2004
    ]
    without = [item for item in census.worst if item.actual is None]
    console.print(
        f"[dim]持って返った {len(census.worst):,} 件のうち、"
        f"**予想が実績の5倍以上 {len(rounds):,} 件**、"
        f"**実績が空 {len(without):,} 件**。[/]"
    )
    console.print(
        "[dim]**どれか1つに決めない。** 比が 100 に揃っていても「訂正はいつも "
        "100倍」にはならない——**割合で見る。**[/]"
    )


@app.command(name="column-census")
def column_census_command(
    endpoint: str = typer.Option(
        "", "--endpoint", help="One endpoint, or empty for the ones the next candidates need."
    ),
    directory: str = typer.Option(
        str(DEFAULT_ARCHIVE_DIR), "--dir", help="Where the archived originals live."
    ),
    show_empty: bool = typer.Option(
        False, "--show-empty", help="Also list the columns that are never filled."
    ),
    match: str = typer.Option(
        "", "--match", help="Only columns whose name contains this. Empty uses the defaults."
    ),
    show_all: bool = typer.Option(False, "--all", help="Every column, however many there are."),
) -> None:
    """Count the original's own columns - what is there, and from when.

    **読み口が捨てている列は、読み口からは見えない。** #5 で `FS` の中の鍵を
    いくら並べても答えにならなかったのと同じ形なので、**読み口を通さずに
    原本の列を数える。**

    そして**列が在ることと、値が埋まっていることは別である。** 候補6 の
    `IV` は 2008-05 の原本に列が在るのに全行で空だった。**「在る」で先に
    進むと、IS が 359 日しかない設計になる。**

    **エンドポイントを1つずつ足さない。** 経路ごとに検査を書く方式は、
    次の1本を書き忘れた瞬間に同じことが起きる——ここは名前を受け取る。

    **取りには行かない。** 原本を読むだけである。
    """
    from stock_ai.core.logging import quiet_on_console
    from stock_ai.data.jquants_columns import column_census

    settings = get_settings()
    configure_logging(settings.log_level)

    wanted: tuple[tuple[str, str, tuple[str, ...]], ...]
    wanted = ((endpoint, "", ()),) if endpoint else NEXT_MATERIALS
    for name, why, defaults in wanted:
        # **探す綴りは、引数が在れば引数。** 無ければその口の既定。
        # `--all` は既定も引数も無視して全部出す。
        patterns = () if show_all else ((match,) if match else defaults)
        console.print(f"[bold]{name}[/]" + (f" — {why}" if why else ""))
        with quiet_on_console("stock_ai.data.jquants_columns"):
            census = column_census(Path(directory), name)
        console.print(f"[dim]{census.summary()}[/]")
        for line in census.warnings():
            console.print(f"[yellow]{line}[/]")
        if not census.rows:
            console.print()
            continue

        shown = [item for item in census.columns if item.filled or show_empty]
        if patterns:
            lowered = tuple(word.lower() for word in patterns)
            shown = [item for item in shown if any(word in item.name.lower() for word in lowered)]
        # **多い側を黙って切らない。** 切ったことと、全部出す方法を言う。
        #
        # **`--all` は上限も外す。** ここを `show_all` と繋がずに書いていて、
        # **「全部出す」と言いながら 24 列で切っていた**（自分のテストが
        # 落ちて分かった、2026-09-21）——`CLAUDE.md`「札が、数えているものと
        # 違うことを言っていないか」。
        limit = len(shown) if show_all else MAX_COLUMNS_SHOWN
        hidden = max(len(shown) - limit, 0)
        title = f"{name} の列（{census.rows:,} 行、{len(census.columns)} 列）"
        if patterns:
            title += "——『" + "』『".join(patterns) + "』を含むもの"
        table = Table(title=title)
        for column in ("列", "埋まっていた", "割合", "在る年"):
            table.add_column(column, overflow="fold", justify="left" if column == "列" else "right")
        for item in shown[:limit]:
            span = "—"
            if item.first_year is not None:
                span = (
                    f"{item.first_year}"
                    if item.first_year == item.last_year
                    else f"{item.first_year}〜{item.last_year}"
                )
            table.add_row(item.name, f"{item.filled:,}", f"{item.share(census.rows):.0%}", span)
        console.print(table)
        if not shown:
            console.print(
                "[yellow]**その綴りを含む列が1つも無い。** "
                "`--all` で全部出る——**無いことと、絞り込みで消えたことは別である。**[/]"
            )
        if hidden:
            console.print(f"[dim]あと {hidden} 列は出していない（`--all` で全部出る）。[/]")
        if census.dated_by:
            console.print(f"[dim]年は `{census.dated_by}` で数えた。[/]")
        if census.empty_columns and not show_empty:
            console.print(
                f"[dim]1行も埋まっていない {len(census.empty_columns)} 列は伏せた"
                "（`--show-empty` で出る）: " + "、".join(census.empty_columns) + "[/]"
            )
        # **標本は、桁と書式を目で見るため。** 表の数だけでは、単位の取り違え
        # （`MktCap` の百万円）は見つからない。
        #
        # **標本にも同じ上限を当てる。** 表だけ絞って標本を素通しにしていて、
        # **1行に 111 個の `名前=値` が並んだ**（自分のテストが落ちて分かった）。
        # **絞ったのに見えない、を2箇所でやらない。**
        keep = {item.name for item in shown[:limit]}
        for row in census.sample:
            pairs = [f"{key}={value}" for key, value in row.items() if value and key in keep]
            if not pairs:
                continue
            console.print("[dim]  " + "、".join(pairs) + "[/]")
        console.print()

    console.print(
        "[dim]**列が在ることと、値が埋まっていることは別である。** "
        "在る年を見てから設計を決めること——候補6 は `IV` が 2016-07 からで、"
        "**IS/OOS をこの候補だけ動かすことになった。**[/]"
    )


@app.command(name="material-coverage")
def material_coverage(
    directory: str = typer.Option(
        str(DEFAULT_ARCHIVE_DIR), "--dir", help="Where the archived originals live."
    ),
) -> None:
    """Count the materials for candidates 6 and 7 - nothing is fetched.

    **候補6と7を「測れない」と書いていたのを直すために在る**（2026-09-21）。
    心理指標も需給も、**原本には在った。**

    | 候補 | 材料 | 1観測 |
    |---|---|---|
    | 6 悲観の中に生まれ | オプションの予想変動率 | 1日 |
    | 7 需給はすべての材料に優先する | 投資部門別の売買差引 | 1週 |

    **`IV` は古い原本に入っていない。** 2008-05 では `IV` / `BaseVol` /
    `UnderPx` など9列が全行で空で、2026-01 では全部埋まっている。
    **どこから埋まるのかは、数えないと分からない**——`CLAUDE.md`「無いことは、
    出力に出ない」。

    **取りには行かない。** 原本を読むだけである。
    """
    from stock_ai.data.jquants_investor import SECTION, weekly_flows
    from stock_ai.data.jquants_options import MIN_TENOR_DAYS, daily_atm_iv

    settings = get_settings()
    configure_logging(settings.log_level)

    # --- 候補6 オプションの予想変動率 ---------------------------------------
    console.print("[bold]候補6（悲観の中に生まれ）— オプションの予想変動率[/]")
    console.print(
        f"[dim]**畳み方は先に1つ決めてある。** 残存 {MIN_TENOR_DAYS} 日以上で"
        "いちばん近い限月の、原資産にいちばん近い行使価格。コールとプットの"
        "平均。**原本の `BaseVol` と突き合わせる。**[/]"
    )
    iv = daily_atm_iv(Path(directory))
    console.print(iv.summary())
    for line in iv.warnings():
        console.print(f"[yellow]{line}[/]")

    if iv.by_year():
        table = Table(title="予想変動率が在る年（原本に在った日 / 水準を作れた日）")
        for column in ("年", "原本に在った日", "水準を作れた日", "割合"):
            table.add_column(column, justify="right" if column != "年" else "left")
        for year, days, made in iv.by_year():
            share = f"{made / days:.0%}" if days else "—"
            table.add_row(f"{year}", f"{days:,}", f"{made:,}", share)
        console.print(table)
        console.print(
            "[dim]**在るべき日を先に決めて引き算する。** 走らなかった回が出力に"
            "出ないのと同じで、**入っていない列も出力に出ない。**[/]"
        )
    if iv.checked:
        console.print(
            f"[dim]原本の `BaseVol` と突き合わせた {iv.checked:,} 日のうち、"
            f"食い違ったのは {len(iv.disagreed):,} 日。**一致は当たり前ではない**"
            "——限月の選び方を1日ずらすだけで崩れる。[/]"
        )
    if iv.disagreed:
        # **「限月の選び方を疑うこと」と書くなら、疑う材料を出す。**
        # 件数しか返していなかったので、どの日なのかを追えなかった
        # （2026-09-21。`CLAUDE.md`「『見ること』と書いただけで、見る道具を
        # 置いていないか」）。**差の大きい順**に出す。
        worst = sorted(iv.disagreed, key=lambda item: -abs(item.gap))
        table = Table(title="`BaseVol` と食い違った日（差の大きい順）")
        # **札に空白を入れない。** rich は空白で折り返すので、幅 80 で
        # 「採った SQ」が2行に割れた（テストが落ちて気付いた）。
        # **列も1つ減らした**——9 列は 80 桁に入らない。
        for column in ("日", "こちら", "原本", "差", "採ったSQ", "残存", "行使", "行"):
            table.add_column(column, overflow="fold", justify="left" if column == "日" else "right")
        for item in worst[:MAX_DISAGREEMENTS]:
            table.add_row(
                f"{item.when}",
                f"{item.atm:.4f}",
                f"{item.base:.4f}",
                f"{item.gap:+.4f}",
                f"{item.expiry}",
                f"{item.tenor}日",
                f"{item.strike:,.0f}",
                f"{item.rows}",
            )
        console.print(table)
        if len(worst) > MAX_DISAGREEMENTS:
            console.print(f"[dim]ほか {len(worst) - MAX_DISAGREEMENTS:,} 日。[/]")
        console.print(
            "[dim]**行が 1 なら、コールかプットの片側しか無かった日である。** "
            "残存が短い日ばかりなら限月の選び方、散らばっているなら別の原因。[/]"
        )

    # --- 候補7 投資部門別 ---------------------------------------------------
    console.print()
    console.print("[bold]候補7（需給はすべての材料に優先する）— 投資部門別[/]")
    console.print(
        f"[dim]**畳み方は先に1つ決めてある。** 区分は `{SECTION}`、1観測は1週、"
        "指標は `FrgnBal / TotTot`。**額そのものは使わない**"
        "——20年で市場の大きさが変わる。[/]"
    )
    flows = weekly_flows(Path(directory))
    console.print(flows.summary())
    for line in flows.warnings():
        console.print(f"[yellow]{line}[/]")

    if flows.sections:
        table = Table(title="原本に在った区分（名前は途中で変わる）")
        for column in ("区分", "行"):
            table.add_column(column, overflow="fold", justify="right" if column == "行" else "left")
        for name, count in sorted(flows.sections.items(), key=lambda item: -item[1]):
            table.add_row(name or "（空）", f"{count:,}")
        console.print(table)
    if flows.by_year():
        table = Table(title="投資部門別が在る年")
        table.add_column("年")
        table.add_column("週", justify="right")
        for year, count in flows.by_year():
            table.add_row(f"{year}", f"{count:,}")
        console.print(table)

    # --- まとめ -------------------------------------------------------------
    console.print()
    if not iv.levels and not flows.weeks:
        console.print(
            "[red]どちらの材料も作れなかった。[/] "
            "**候補6と7は、本当に測れない。** `3-データ取得.bat` で原本を"
            "保存してから、もう一度。"
        )
        raise typer.Exit(code=1)
    if not iv.levels:
        console.print("[yellow]**候補6の材料が作れない。** 候補7だけ壁を測れる。[/]")
    if not flows.weeks:
        console.print("[yellow]**候補7の材料が作れない。** 候補6だけ壁を測れる。[/]")
    console.print(
        "[green]材料は在る。[/] **壁はまだ1本も測っていない。** "
        "`research\\壁の下見.bat` が測る——**そこまでは、通りうるかどうかは"
        "設計の形からの推論であって、数字ではない。**"
    )


@app.command(name="ex-date-coverage")
def ex_date_coverage_command(
    directory: str = typer.Option(
        str(DEFAULT_ARCHIVE_DIR), "--dir", help="Where the archived originals live."
    ),
) -> None:
    """Count how many ex-dividend dates the archive can supply - nothing is fetched.

    **#9（窓は埋まる）の設計がこれに掛かっている。** 前日終値から −3% の下窓
    は、**権利落ちがまさにそう見える。** 外せなければ、事象の定義が配当を拾う。

    `/fins/dividend` は **Premium のエンドポイント**なので、解約後は原本に
    在るものがすべてである。**取りには行かない。**

    **列ごとに独立に数える。** 行が読めたことと、`ExDate` が埋まっている
    ことは別である。
    """
    from stock_ai.data.jquants_dividend import ex_date_coverage

    settings = get_settings()
    configure_logging(settings.log_level)

    found = ex_date_coverage(Path(directory))
    console.print(found.summary())
    for line in found.warnings():
        console.print(f"[yellow]{line}[/]")

    table = Table(title="権利落ち日（#9 で外す材料）")
    for column in ("項目", "値", "なぜ見るか"):
        table.add_column(column, overflow="fold")
    table.add_row("原本の本数", f"{found.files:,}", "0 なら保存できていない")
    table.add_row("行", f"{found.rows:,}", "**行。** 外す対象の単位ではない")
    table.add_row(
        "`ExDate` が在る行",
        f"{found.with_ex_date:,}",
        "**行。** 読めたことと、埋まっていることは別",
    )
    table.add_row(
        "別々の権利落ち",
        f"{found.days:,}",
        "**外す対象はこれ**（銘柄 × 日）。**下の3つはこれを分けたもの**",
    )
    table.add_row("　IS（〜2017-12）", f"{found.in_is:,}", "銘柄 × 日。**行ではない**")
    table.add_row("　OOS（2018-01〜2026-08）", f"{found.in_oos:,}", "同上")
    table.add_row("　OOS より後", f"{found.after_oos:,}", "配当は前もって公表される")
    console.print(table)
    console.print(
        "[dim]**下の3つは足すと「別々の権利落ち」になる**（合わなければ生成時に"
        "落ちる）。**行と（銘柄 × 日）を同じ列に並べていた**——2026-09-19 に"
        "ユーザーが見つけた。2.6倍ずれていた。[/]"
    )

    if not found.days:
        console.print(
            "[red]外す材料が無い。[/] **#9 の設計を変えることになる**"
            "——分割日だけで代用するか、決算期末を機械的に外すか。"
        )
        raise typer.Exit(code=1)
    console.print("[green]権利落ちを外せる。[/] 事前登録の §2 にそう書く。")


@app.command(name="gap-fill-power")
def gap_fill_power(
    archive: str = typer.Option(
        str(DEFAULT_ARCHIVE_DIR), "--dir", help="Where the archived originals live."
    ),
    benchmark: str = typer.Option(BENCHMARK, "--benchmark", help="Calendar for the windows."),
) -> None:
    """Measure the IS window for #15, so the gate table can be filled - not judge it.

    **段2（自分の IS から推定する）の材料を出す。** 文献はこの環境から読めない
    ので、見込みはここから置く（`docs/PREREG_GAP_FILL_JP.md` §0）。

    **事象は下窓 3%**（前日終値から当日始値）。**権利落ちと不連続を外す**
    ——どちらも 3% の下窓にそう見えるからである。**外した件数は別々に出す。**

    **入るのは D+1 の寄付き、降りるのは D+20 の終値。** 窓は寄付きで開くので、
    D の寄付きには間に合わない。費用は往復 0.4% を引く。

    **判定ではない。** IS は 2013-01〜2017-12（`ExDate` が 2012-12 からしか
    無い）で、OOS（2018-01〜2026-08）は**件数しか数えない。**
    """
    # **`OOS_FROM` を gap_fill から import する。** モジュールの頭に `pead`
    # の `OOS_FROM`（2024-01-01）が在り、書かないとそちらを掴む——**例外は
    # 出ず、年数の列が 3倍になる**（2026-09-20、書いた直後に気付いた）。
    from stock_ai.backtest.event_window import event_sample
    from stock_ai.backtest.gap_fill import (
        GAP_DOWN,
        HOLDING,
        IS_END,
        IS_FROM,
        OOS_END,
        OOS_FROM,
        build_events,
    )
    from stock_ai.backtest.multiplicity import HYPOTHESIS_BUDGET, calibrated_t
    from stock_ai.backtest.universe_benchmark import equal_weighted_windows
    from stock_ai.core.logging import quiet_on_console
    from stock_ai.data.jquants_dividend import ex_dates_known_by

    settings = get_settings()
    configure_logging(settings.log_level)

    known = ex_dates_known_by(Path(archive))
    announced = known.by_symbol
    if not announced:
        console.print(
            "[red]権利落ちの原本が無い。[/] **外さずには測らない**"
            "——3% の下窓は、権利落ちがそう見える（事前登録 §3）。"
            " `checks\\権利落ちは在るか.bat` で先に確かめること。"
        )
        raise typer.Exit(code=1)
    console.print(
        f"[dim]{known.summary()} {len(announced):,} 銘柄。"
        "**公表がその日より前のものだけで外す。**[/]"
    )
    for line in known.warnings():
        console.print(f"[yellow]{line}[/]")
    console.print(
        f"[dim]IS は {IS_FROM} 〜 {IS_END}。OOS は件数だけ数える。"
        f"下窓 {GAP_DOWN:.0%}、窓 {HOLDING} 営業日。[/]"
    )

    database = Database()
    database.create_all()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("下窓を集めています", total=None)

        def step(done: int, total: int) -> None:
            progress.update(task, completed=done, total=total)

        with quiet_on_console("stock_ai.backtest.gap_fill"):
            found = build_events(database, announced, progress=step)
    console.print(found.summary())
    for line in found.warnings():
        console.print(f"[yellow]{line}[/]")
    if not found.events or not found.days_oos:
        raise typer.Exit(code=1)

    console.print("[dim]引く相手（等加重の宇宙）を作っています...[/]")
    with quiet_on_console("stock_ai.backtest.universe_benchmark"):
        subtract = equal_weighted_windows(database, HOLDING)
    for line in subtract.warnings():
        console.print(f"[yellow]{line}[/]")

    with quiet_on_console("stock_ai.backtest.event_window"):
        sample = event_sample(database, found.events, HOLDING, benchmark, IS_END, subtract)
    _report_event_disposition(sample, title="窓を当てた結果（IS のみ・件数）")
    if len(sample.values) < 2:  # noqa: PLR2004 - 1日では散らばりが測れない
        console.print(f"[red]値動きの取れたイベント日が {len(sample.values)} しかない。[/]")
        raise typer.Exit(code=1)

    # **ロングである。** 仮説は超過リターンが正だと言っている（§1）ので、
    # 符号は反転しない。費用は往復 0.4%（§4）。
    take = [value - COST_ROUND_TRIP for value in sample.values]
    # **膨張は「管 × 引く相手」ごとに測ってある。** ここは等加重の宇宙を引く
    # （事前登録 §2）ので、そちらで測った 1.09 を当てる。**引く相手を替えたら
    # 数字が動いた**（0.94 → 1.09）。
    target = calibrated_t(HYPOTHESIS_BUDGET, inflation=_event_inflation("universe"))
    # **判定は OOS で行う。** 渡すのは OOS の年数である——IS の年数を渡すと、
    # 年数の列だけが IS の率になる（2026-09-20、ユーザーが発見）。
    _event_gate(
        take,
        periods=found.days_oos,
        target=target,
        committed=3 * COST_ROUND_TRIP,
        holding=HOLDING,
        reach=f"**OOS の {found.events_oos:,} 件が固まった日数。件数ではない**",
        period_years=_judgement_years(OOS_FROM, OOS_END),
        footnote=(
            f"[dim]見込みを測った IS（{IS_FROM}〜{IS_END}）は "
            f"{found.days_is:,} イベント日（{len(found.events):,} 件）。"
            "**`ExDate` が 2012-12 からしか無いので、ここから増えない。**[/]"
        ),
    )


@app.command(name="knife-power")
def knife_power(
    archive: str = typer.Option(
        str(DEFAULT_ARCHIVE_DIR), "--dir", help="Where the archived originals live."
    ),
    benchmark: str = typer.Option(BENCHMARK, "--benchmark", help="Calendar for the windows."),
) -> None:
    """Measure the IS window for #16, so the gate table can be filled - not judge it.

    **段2（自分の IS から推定する）の材料を出す。** 文献はこの環境から読めない
    ので、見込みはここから置く（`docs/PREREG_KNIFE_JP.md` §0）。

    **事象は「5営業日で −20%」、窓は 5営業日。** 窓が短いのは**格言が急落直後
    の話だから**であって、検出力のためではない（§0）。

    **測るのはショートの取り高である。** 「つかむな」は買うと損をすると言って
    いる——**向きは格言から取った。#15 の結果からではない**（§1）。

    **判定ではない。** IS は 2013-01〜2017-12 で、OOS は**件数しか数えない。**

    **線 3.30 は窓20営業日で測った値である。** §0 を通った場合だけ、
    **5営業日の対照を回してから封印する**（§10）。
    """
    # **`OOS_FROM` を gap_fill から import する。** 書かないとモジュールの頭に
    # 在る `pead` の 2024-01-01 を黙って掴む（2026-09-20）。
    from stock_ai.backtest.event_window import event_sample
    from stock_ai.backtest.gap_fill import IS_END, IS_FROM, OOS_END, OOS_FROM
    from stock_ai.backtest.knife import (
        HOLDING,
        KNIFE_DAYS,
        KNIFE_DROP,
        build_events,
    )
    from stock_ai.backtest.multiplicity import HYPOTHESIS_BUDGET, calibrated_t
    from stock_ai.backtest.universe_benchmark import equal_weighted_windows
    from stock_ai.core.logging import quiet_on_console
    from stock_ai.data.jquants_dividend import ex_dates_known_by, ex_dividends_known_by
    from stock_ai.data.schema import CLOSE, DividendAdjustment, dividend_adjusted

    settings = get_settings()
    configure_logging(settings.log_level)

    known = ex_dates_known_by(Path(archive))
    announced = known.by_symbol
    # **価格から配当を落とすための額。** 落とせない日だけ外す（§3）。
    paid = ex_dividends_known_by(Path(archive))
    if not announced:
        console.print(
            "[red]権利落ちの原本が無い。[/] **外さずには測らない**"
            "——特別配当は機械的な値下がりで、戻らない（事前登録 §3）。"
            " `checks\\権利落ちは在るか.bat` で先に確かめること。"
        )
        raise typer.Exit(code=1)
    console.print(f"[dim]{known.summary()}[/]")
    for line in known.warnings():
        console.print(f"[yellow]{line}[/]")
    console.print(
        f"[dim]IS は {IS_FROM} 〜 {IS_END}。OOS は件数だけ数える。"
        f"急落は {KNIFE_DAYS} 営業日で −{KNIFE_DROP:.0%}、窓は {HOLDING} 営業日。"
        "**測るのはショートの取り高である。**[/]"
    )

    database = Database()
    database.create_all()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("急落を集めています", total=None)

        def step(done: int, total: int) -> None:
            progress.update(task, completed=done, total=total)

        with quiet_on_console("stock_ai.backtest.knife"):
            found = build_events(database, announced, paid, progress=step)
    console.print(found.summary())
    for line in found.warnings():
        console.print(f"[yellow]{line}[/]")
    if not found.events or not found.days_oos:
        raise typer.Exit(code=1)

    console.print("[dim]引く相手（等加重の宇宙）を作っています...[/]")
    with quiet_on_console("stock_ai.backtest.universe_benchmark"):
        subtract = equal_weighted_windows(database, HOLDING)
    for line in subtract.warnings():
        console.print(f"[yellow]{line}[/]")

    # **保有する窓でも配当を落とす。** ショートは配当を払う側なので、
    # 落とさないと取り高が高く出る——**急落側だけ直すと非対称が残る**
    # （2026-09-20、ユーザーが指摘）。
    #
    # **割る相手は調整前の終値である。** 調整後で割ると、分割より前の
    # 権利落ちが分割比のぶん余計に落ちる——1:10 なら利回り 1.0% が 10.0%
    # になる（2026-09-20 に再現。監査が「窓の中の配当 +0.007%/件 に対し、
    # 抜けたのは +0.036%/件」と鳴って見つかった）。
    netting = DividendAdjustment()

    def _net_of_dividends(symbol: str, frame: object, unadjusted: object) -> object:
        nonlocal netting
        netted, counted = dividend_adjusted(
            frame,  # type: ignore[arg-type]
            paid.get(symbol),
            base=unadjusted[CLOSE].to_numpy(dtype=float),  # type: ignore[index]
            symbol=symbol,
        )
        netting = netting + counted
        return netted

    with quiet_on_console("stock_ai.backtest.event_window"):
        sample = event_sample(
            database,
            found.events,
            HOLDING,
            benchmark,
            IS_END,
            subtract,
            adjust=_net_of_dividends,
        )
    _report_event_disposition(sample, title="窓を当てた結果（IS のみ・件数）")
    # **落とした配当の内訳を出す。** 黙って飛ばしていたので、尺度を
    # 間違えていることが出力からは見えなかった（2026-09-20）。
    for line in netting.warnings():
        console.print(f"[yellow]{line}[/]")
    # **理由ごとに割る。** 急落側には既に表が在るのに、**保有窓側だけ合計の
    # ままだった**（2026-09-21、ユーザーが指摘）。`CLAUDE.md`「合計だけ出すと、
    # その中に紛れる」——#5 で会計年度末が全件読めていなかったのが表の1行に
    # しか出なかったのと同じ形である。
    _print_dividend_breakdown(netting, "保有窓で落とした配当")
    _print_raw_dividend_rows(Path(archive), netting)
    if len(sample.values) < 2:  # noqa: PLR2004 - 1日では散らばりが測れない
        console.print(f"[red]値動きの取れたイベント日が {len(sample.values)} しかない。[/]")
        raise typer.Exit(code=1)

    # **ショートの取り高に直す。** 仮説は超過リターンが負だと言っている（§1）。
    # 符号の反転はここ1箇所だけで行う。費用は往復 0.4%（§4）。
    take = [-value - COST_ROUND_TRIP for value in sample.values]
    # **等加重の宇宙を引いている**ので、そちらで測った膨張を当てる。
    target = calibrated_t(HYPOTHESIS_BUDGET, inflation=_event_inflation("universe"))
    # **判定は OOS で行う。** 渡すのは OOS の年数である（2026-09-20）。
    _event_gate(
        take,
        periods=found.days_oos,
        target=target,
        committed=3 * COST_ROUND_TRIP,
        holding=HOLDING,
        reach=f"**OOS の {found.events_oos:,} 件が固まった日数。件数ではない**",
        period_years=_judgement_years(OOS_FROM, OOS_END),
        footnote=(
            f"[dim]見込みを測った IS（{IS_FROM}〜{IS_END}）は "
            f"{found.days_is:,} イベント日（{len(found.events):,} 件）。"
            f"**線 {target:.2f} は窓20営業日で測った値**"
            "——§0 を通ったら、**5営業日の対照を回してから封印する。**[/]"
        ),
        side="ショート",
    )


@app.command(name="ex-date-audit")
def ex_date_audit(
    archive: str = typer.Option(
        str(DEFAULT_ARCHIVE_DIR), "--dir", help="Where the archived originals live."
    ),
) -> None:
    """Check the dividend data #16 leans on, and what adjusting for it changes.

    **3つ見る。**

    1. **`ExDate` はずれていないか** — 権利落ち日の前後を並べる。全件
    2. **配当を落とすと急落の数がどう変わるか** — 順序を直した効き目
    3. **保有窓の中の権利落ち** — 落とした後は押し上げが消えているはず

    **効果は1つも計算しない。** 判定を先食いしないため、リターンは触らない。
    """
    from stock_ai.backtest.ex_date_audit import (
        audit_holding_window,
        measure_adjustment,
        measure_alignment,
    )
    from stock_ai.backtest.gap_fill import IS_FROM, OOS_END
    from stock_ai.backtest.knife import HOLDING, KNIFE_DAYS, KNIFE_DROP, build_events
    from stock_ai.core.logging import quiet_on_console
    from stock_ai.data.jquants_dividend import (
        ex_dates_known_by,
        ex_dividend_rates,
        ex_dividends_known_by,
    )

    settings = get_settings()
    configure_logging(settings.log_level)

    reading = ex_dividend_rates(Path(archive))
    if not reading.rates:
        console.print(
            "[red]配当の額が1件も読めない。[/] `checks\\権利落ちは在るか.bat` を先に見ること。"
        )
        raise typer.Exit(code=1)
    rates = reading.rates
    console.print(reading.summary())
    for line in reading.warnings():
        console.print(f"[yellow]{line}[/]")
    console.print("[dim]**額は最後に公表された値**——ここは監査で、売買の判定には使わない。[/]")

    database = Database()
    database.create_all()

    def spinner() -> Progress:
        return Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeRemainingColumn(),
            console=console,
        )

    # --- 1. 権利落ち日はずれていないか ----------------------------------
    with spinner() as progress:
        task = progress.add_task("権利落ちの前後を並べています", total=None)
        with quiet_on_console("stock_ai.backtest.ex_date_audit"):
            lined = measure_alignment(
                database,
                rates,
                IS_FROM,
                OOS_END,
                progress=lambda done, total: progress.update(task, completed=done, total=total),
            )
    table = Table(title="権利落ち日の前後（全件・中央値）")
    for column in ("その日", "中央値リターン", "件数"):
        table.add_column(column, justify="left" if column == "その日" else "right")
    for offset, value, count in zip(lined.offsets, lined.medians, lined.counts, strict=True):
        label = "**権利落ち日**" if offset == 0 else f"{offset:+d} 日目"
        table.add_row(label, f"{value:+.3%}", f"{count:,}")
    console.print(table)
    console.print(lined.summary())
    if lined.zero_events:
        console.print(
            f"[dim]額 0 と読んだ {lined.zero_events:,} 件の権利落ち日は "
            f"**{lined.zero_on_the_day:+.3%}**。**無配なら段差は出ない。**[/]"
        )
    for line in lined.warnings():
        console.print(f"[yellow]{line}[/]")
    if not lined.aligned:
        console.print("[red]ここで止める。[/] **外す日が違うなら、下の2つを読む意味が無い。**")
        raise typer.Exit(code=1)
    console.print("[green]段差は権利落ち日そのものに在る。[/] `ExDate` の読み違いではない。")

    # --- 2. 配当を落とすと、急落の数がどう変わるか ----------------------
    paid = ex_dividends_known_by(Path(archive))
    with spinner() as progress:
        task = progress.add_task("配当を落とす前と後を数えています", total=None)
        with quiet_on_console("stock_ai.backtest.ex_date_audit"):
            moved = measure_adjustment(
                database,
                paid,
                IS_FROM,
                OOS_END,
                progress=lambda done, total: progress.update(task, completed=done, total=total),
            )
    console.print()
    console.print(moved.summary())
    console.print(
        f"[dim]**先に落としてから −{KNIFE_DROP:.0%} を当てる。** 「権利落ちが窓に在れば"
        "外す」は事前登録 §3 の代理で、**本物の急落を巻き込んでいた**"
        "（2026-09-20、ユーザーが指摘）。[/]"
    )
    for line in moved.warnings():
        console.print(f"[yellow]{line}[/]")

    # --- 3. 保有窓の中の権利落ち（落とした後に残っていないか） ----------
    known = ex_dates_known_by(Path(archive))
    with spinner() as progress:
        task = progress.add_task("急落を集め直しています", total=None)
        with quiet_on_console("stock_ai.backtest.knife"):
            found = build_events(
                database,
                known.by_symbol,
                paid,
                progress=lambda done, total: progress.update(task, completed=done, total=total),
            )
    console.print(found.summary())
    for line in found.warnings():
        console.print(f"[yellow]{line}[/]")
    _print_dividend_breakdown(found.dividends, "急落を集めるときに落とした配当")
    _print_raw_dividend_rows(Path(archive), found.dividends)
    _print_dividend_revisions(Path(archive))
    _print_unpublished_amounts(Path(archive), known)

    with spinner() as progress:
        task = progress.add_task("保有窓の中の権利落ちを数えています", total=None)
        with quiet_on_console("stock_ai.backtest.ex_date_audit"):
            inside = audit_holding_window(
                database,
                found.events,
                rates,
                # **測る側と同じ調整を渡す。** 渡さないと「抜けた分」が 0 に
                # なり、**調整を当てていないのと区別がつかない。**
                paid,
                progress=lambda done, total: progress.update(task, completed=done, total=total),
            )
    console.print()
    console.print(inside.summary())
    console.print(
        f"[dim]**「消えているはず」ではなく、抜けた分を測っている。** 急落を作った "
        f"{KNIFE_DAYS} 営業日と保有する {HOLDING} 営業日を、同じに扱っている。[/]"
    )
    for line in inside.warnings():
        console.print(f"[yellow]{line}[/]")
    console.print(inside.aside())


def _print_dividend_breakdown(counted: object, title: str) -> None:
    """Print the dividend-adjustment breakdown, and the rows it skipped.

    **落とした配当の内訳と、飛ばした行の中身を出す。**

    **合計だけにしない。** 「当てなかった 2,264 件」とだけ出していたので、
    **どれか1つが大きくてもその中に紛れた**（2026-09-20、ユーザーが指摘）。
    `CLAUDE.md`「列ごとに独立に数える」。

    そして**中身も出す。** 「額か終値のどちらかが読み違いである」と書いて
    おきながら、**銘柄も日付も額も出ていなかった**ので、どちら側かを決め
    られなかった。**警告に次の一手を書くなら、その一手が打てる形にして返す。**

    Args:
        counted: :class:`~stock_ai.data.schema.DividendAdjustment`。
        title: 表の見出し。**件数を入れない**（`COUNTS_IN_PROSE`）。
    """
    table = Table(title=title)
    table.add_column("どうなったか", overflow="fold")
    table.add_column("件数", justify="right")
    for label, count in counted.breakdown():  # type: ignore[attr-defined]
        table.add_row(label, f"{count:,}")
    console.print(table)

    rows = [row for row in counted.rows if row.reason != "公表が権利落ちより後"]  # type: ignore[attr-defined]
    if not rows:
        return
    # **1件取り出して、額と前日終値を並べる。** どちら側の読み違いかは、
    # それを見れば決まる。**上限つきの標本である**——件数は上の表が持つ。
    #
    # **少ない理由から出す。** 件数順に出すと、6,562 件の「足が無い」が
    # 53 件の「額が前日終値以上」を押し出す（2026-09-20、実際にそうなった）。
    # **標本は、見たいものを見るために在る。**
    order = dict(counted.breakdown())  # type: ignore[attr-defined]
    rows.sort(key=lambda row: (order.get(row.reason, 0), row.reason, row.ex_date))
    sample = Table(title="当てなかった権利落ちの中身（理由の少ない順）")
    for column in ("銘柄", "権利落ち日", "額（円）", "前日終値（調整前）", "利回り", "理由"):
        sample.add_column(column, overflow="fold", justify="right" if "額" in column else "left")
    for row in rows[:12]:
        ratio = row.ratio
        sample.add_row(
            row.symbol,
            f"{row.ex_date}",
            f"{row.rate:,.2f}",
            f"{row.base:,.2f}" if row.base > 0 else "—",
            f"{ratio:.1%}" if ratio is not None else "—",
            row.reason,
        )
    console.print(sample)


def _print_original_rows(title: str, found: list) -> None:
    """Print archive rows as wrapped name=value lines, not a table.

    **表では入らない。** 実データは 16 列が埋まっていて、幅 80 では1列 4 桁に
    なる。**列名が1文字ずつ縦に割れ、15 行の中身が 293 行になった**
    （2026-09-21、ユーザーが2度目の指摘）。

    **1度目は「消えた」のを「直った」と読んでいた。** `5656385` でこの表が
    出なくなったのは幅を直したからではなく、**出す対象が 0 件になったから**
    である。対象が戻ったら、同じ潰れ方が戻った。

    **列を減らす方向では足りない。** 全行で空の7列を落としても 16 列残る。
    **表をやめて、折り返せる形にする**——`name=value` は空白で切れるので、
    rich がどの幅でも語の途中で割らない。

    **描画を1つにする。** 前は `_print_raw_dividend_rows` と
    `_print_unpublished_amounts` が同じ表を別々に組んでいた。

    Args:
        title: 見出し。**件数を入れない**（`COUNTS_IN_PROSE`）。
        found: :func:`~stock_ai.data.jquants_dividend.raw_rows` が返す並び。
    """
    if not found:
        return
    names: list[str] = []
    for _symbol, _when, _key, row in found:
        for name in row:
            if name not in names:
                names.append(name)
    filled = [name for name in names if any((row.get(name) or "").strip() for *_h, row in found)]
    empty = [name for name in names if name not in filled]

    console.print(f"[bold]{title}[/]")
    for position, (symbol, when, key, row) in enumerate(found, start=1):
        console.print(f"[dim]  [{position}] {symbol} {when}  {key}[/]")
        pairs = "  ".join(f"{name}={(row.get(name) or '').strip() or '-'}" for name in filled)
        console.print(f"      {pairs}")
    if empty:
        # **空の列も名前は出す。** そこに答えが無かったことも答えである。
        console.print(f"[dim]  全行で空だった列: {' / '.join(empty)}[/]")


def _print_raw_dividend_rows(archive: Path, counted: object, limit: int = 3) -> None:
    """Print the raw archive rows behind the "amount exceeds the close" flag.

    **原本そのものの列名も出す。**

    `parse_dividends` は 23 列のうち 12 列しか採っていない。捨てているのは
    ``DistAmt`` / ``RetEarn`` / ``DeemDiv``（みなし配当）/ ``DeemCapGains`` /
    ``NetAssetDecRatio`` / ``IFCode`` / ``FRCode`` などで、**そこに答えが
    在れば、読み口からは永久に見えない**（`CLAUDE.md`、#5 で同じ形を踏んだ）。

    **1行1レコードで、列を横に並べる。** 転置して列名を行にしたら、同じ鍵に
    平均2.4行あるので**16列になり、列名が1文字ずつ縦に割れて 200行近く**に
    なった（2026-09-20、ユーザーが指摘）。**幅はこちらの手元にしか無い条件
    である。**

    **全行で空だった列は、名前を1行にまとめる。** 「空だった」ことは残す
    ——無いことは出力に出ない。

    **銘柄コードを焼き付けない。** その回に出た行を引く——次に中身が
    変われば、表も変わる。

    Args:
        archive: 原本の置き場所。
        counted: :class:`~stock_ai.data.schema.DividendAdjustment`。
        limit: 引く**鍵**の数。1鍵に複数行あれば、その行は全部出す。
    """
    from stock_ai.data.jquants_dividend import raw_rows

    flagged = [row for row in counted.rows if row.reason == "額が前日終値以上"]  # type: ignore[attr-defined]
    if not flagged:
        return
    wanted = [(row.symbol, row.ex_date) for row in flagged[:limit]]
    found = raw_rows(archive, wanted)
    if not found:
        console.print(
            "[yellow]**原本にその行が見つからない。** "
            "額が前日終値以上と数えたのに、引き当てられない。[/]"
        )
        return

    _print_original_rows("原本そのもの（額が前日終値以上の権利落ち）", found)
    console.print(
        f"[dim]{len(wanted)} 件ぶんを引いて {len(found)} 行。"
        "**読み口が採っているのは `Code` / `PubDate` / `PubTime` / `RefNo` / "
        "`IFTerm` / `DivRate` / `CommDivRate` / `SpecDivRate` / `ExDate` / "
        "`RecDate` / `PayDate` / `StatCode` だけである。** 額は `DivRate`。[/]"
    )


def _print_unpublished_amounts(archive: Path, known: object, limit: int = 3) -> None:
    """Report the ex-dates whose amount could not be read by the ex-date itself.

    **2つに割って出す。** 887 → 9,861 件に増えたとき、合計しか出ていな
    かったので**どちらが増えたのか分からなかった**（2026-09-21）。

    | 形 | 権利落ち日までに | 額 |
    |---|---|---|
    | 額が空 | 行は在る | `DivRate` が空 |
    | 公表が権利落ちより後 | 1行も無い | **後から出ている** |

    **そして読み口が捨てている列を数える。** 「予想の行が在るのに拾えて
    いないのではないか」は、`FRCode` を読まないと答えられない——
    **捨てている列は、読み口からは見えない**（`CLAUDE.md`、#5 と同じ形）。

    **全体と並べる。** 絞った先で `DivRate` が空なのは当たり前なので、
    **全体と食い違う列だけ**を出す（`CLAUDE.md`「件数ではなく割合を見る」）。

    Args:
        archive: 原本の置き場所。
        known: :class:`~stock_ai.data.jquants_dividend.AnnouncedExDates`。
        limit: 理由ごとに原本を引く鍵の数。**理由ごとに置く**——全体で1つに
            すると、件数の多い理由が少ない理由を押し出す。
    """
    from stock_ai.data.jquants_dividend import raw_rows, row_census

    keys: list[tuple[str, dt.date, str]] = list(known.unknown_keys)  # type: ignore[attr-defined]
    table = Table(title="権利落ち日までに額が引けなかった権利落ち")
    table.add_column("どうだったか", overflow="fold")
    table.add_column("件数", justify="right")
    table.add_row("権利落ち日までに行は在るが `DivRate` が空", f"{known.blank_by_ex_date:,}")  # type: ignore[attr-defined]
    table.add_row("権利落ち日までに1行も公表されていない", f"{known.announced_late:,}")  # type: ignore[attr-defined]
    console.print(table)
    console.print(
        "[dim]**「額が一度も公表されていない」ではない。** 下の段は後から"
        "公表されている——その日の時点で使えなかっただけである。[/]"
    )
    if not keys:
        return

    # **原本の列を数える。** 読み口が採らない列に答えが在るかは、ここでしか
    # 見えない。**全体と食い違う列だけ**を出す。
    census = row_census(archive, [(symbol, when) for symbol, when, _why in keys])
    standout = census.standout()
    if standout:
        spread = Table(title="原本の列の埋まり方（全体と食い違うものだけ）")
        spread.add_column("列", overflow="fold")
        spread.add_column("この集合", justify="right")
        spread.add_column("原本ぜんぶ", justify="right")
        for name, here, everywhere in standout[:10]:
            spread.add_row(name, f"{here:.1%}", f"{everywhere:.1%}")
        console.print(spread)
    else:
        console.print(
            "[dim]原本の列の埋まり方は、原本ぜんぶと 10 ポイント以上違わない。"
            "**この集合に特有の列は無い。**[/]"
        )

    # **`FRCode` の出方を、全体と並べる。** 予想の行が在るのに拾えていない
    # なら、ここが偏る。**偏らないことも答えである。**
    here = census.codes.get("FRCode", {})
    everywhere = census.overall_codes.get("FRCode", {})
    if here or everywhere:
        codes = Table(title="`FRCode` の出方（意味は分からないので値のまま）")
        codes.add_column("値", overflow="fold")
        codes.add_column("この集合", justify="right")
        codes.add_column("原本ぜんぶ", justify="right")
        for value in sorted(set(here) | set(everywhere), key=lambda v: -everywhere.get(v, 0))[:6]:
            mine = here.get(value, 0) / census.rows if census.rows else 0.0
            all_of = everywhere.get(value, 0) / census.overall_rows if census.overall_rows else 0.0
            codes.add_row(value, f"{here.get(value, 0):,}（{mine:.1%}）", f"{all_of:.1%}")
        console.print(codes)

    # **理由ごとに標本を出す。** 上限を全体で1つにすると、件数の多い理由が
    # 少ない理由を押し出す（2026-09-20、実際にそうなった）。
    #
    # **原本は1度だけ歩く。** 理由ごとに `raw_rows` を呼ぶと、原本を理由の
    # 数だけ読み直すことになる。
    wanted: dict[str, list[tuple[str, dt.date]]] = {}
    for symbol, when, reason in keys:
        picked = wanted.setdefault(reason, [])
        if len(picked) < limit:
            picked.append((symbol, when))
    everything = raw_rows(archive, [key for picked in wanted.values() for key in picked])
    for why in ("額が空", "公表が権利落ちより後"):
        picked = wanted.get(why, [])
        if not picked:
            continue
        chosen = set(picked)
        found = [row for row in everything if (row[0], row[1]) in chosen]
        if not found:
            console.print(f"[yellow]**「{why}」の原本が引き当てられない。**[/]")
            continue
        # **描画は1つだけ。** 前は同じ表を2箇所で組んでいて、**片方だけ
        # 直す**形になっていた（実際は両方潰れていた）。
        _print_original_rows(f"原本そのもの（{why}）", found)


def _print_dividend_revisions(archive: Path) -> None:
    """Report how often the amount was corrected before the ex-date, and by how much.

    **割合で見る。** 4件の訂正比がどれも 100 だったからといって、
    **「訂正はいつも 100 倍」にはならない**（`CLAUDE.md`「ゼロでないことを
    根拠に断定しない」）。**100 以外が在るかどうかは、全部数えないと出ない。**

    **直した後も残す。** 「いちばん早い正の額」から「権利落ち日時点の額」に
    変えたので、ここに出るのが**その直しが動かした鍵**である。

    Args:
        archive: 原本の置き場所。
    """
    from stock_ai.data.jquants_dividend import revisions_before_ex_date

    moved = revisions_before_ex_date(archive)
    if not moved:
        console.print("[dim]権利落ち日までに額が訂正された権利落ちは無かった。[/]")
        return

    ratios = sorted(after / before for before, after in moved.values() if before)
    exactly = sum(1 for value in ratios if abs(value - 0.01) < 1e-9)  # noqa: PLR2004 - 1/100
    table = Table(title="権利落ち日までに額が訂正された権利落ち")
    table.add_column("項目", overflow="fold")
    table.add_column("値", justify="right")
    table.add_row("訂正があった権利落ち", f"{len(moved):,}")
    table.add_row("うち ちょうど 1/100 に直ったもの", f"{exactly:,}")
    table.add_row("それ以外", f"{len(moved) - exactly:,}")
    if ratios:
        table.add_row("比（後 ÷ 前）の最小", f"{ratios[0]:.6g}")
        table.add_row("比の中央値", f"{ratios[len(ratios) // 2]:.6g}")
        table.add_row("比の最大", f"{ratios[-1]:.6g}")
    console.print(table)
    console.print(
        "[dim]**採るのは「権利落ち日までに公表された中でいちばん新しい額」である。** "
        "前は「いちばん早い正の額」を採っていて、**訂正前の額を掴んでいた**"
        "——`1/100` 以外が在れば、**倍率で直す形にしなくて正しかった**ことになる。[/]"
    )


def _inflation_cell(wall: object) -> str:
    """Render the inflation column so the row cannot contradict itself.

    **欄そのものに出す。** 注記に書くと、**表だけ見た人には落ちる**
    （2026-09-21、ユーザーの指摘）。`CLAUDE.md`「表は読む側が気付く必要が
    ある」。

    | 何が効いたか | 出し方 |
    |---|---|
    | 何も | `1.82x` |
    | 床（1.0 を割った） | `0.82x → 床 1.00x` |
    | 上限（標本が足りない） | `1.62x → 上限 4.47x` |

    **2箇所に書かない。** 表と `docs/WALL.md` が同じここを呼ぶ。
    """
    measured = f"{wall.inflation:.2f}x"  # type: ignore[attr-defined]
    if wall.capped:  # type: ignore[attr-defined]
        return f"{measured} → 上限 {wall.effective_inflation:.2f}x"  # type: ignore[attr-defined]
    if wall.floored:  # type: ignore[attr-defined]
        return f"{measured} → 床 {wall.effective_inflation:.2f}x"  # type: ignore[attr-defined]
    return measured


def _index_walls(
    archive: Path,
    returns: list[float],
    dates: list[dt.date],
    benchmark: str,
    sampled: dict[str, list[float]],
) -> list[object]:
    """Measure the walls for the two index-only candidates - no effects.

    **候補6と7は「測れない」と書いてあった。** どちらも原本に材料が在った
    （2026-09-21）。ここで壁だけ出す——**事前登録は書かない。**

    **畳み方は `backtest/wall.py` の説明に1つだけ書いてある。** ここで
    選ばない——複数試して良いほうを採ると、その時点で #10 と同じところに
    落ちる。

    **平均は1つも計算しない。** 返すのは `Wall` で、平均の欄が無い。

    Args:
        archive: 原本の置き場所。
        returns: ベンチマークの日次リターン。
        dates: その日付。
        benchmark: 出典に書く銘柄。
        sampled: 裾の感度を見るために系列を預ける先。**平均は取らない。**

    Returns:
        作れた `Wall` の並び。材料が無ければ空。
    """
    from stock_ai.backtest.multiplicity import line_for
    from stock_ai.backtest.power import estimate_power
    from stock_ai.backtest.wall import (
        FLOW_HOLDING,
        HOLDING,
        IS_END,
        IV_IS_END,
        IV_IS_FROM,
        IV_OOS_FROM,
        OOS_END,
        OOS_FROM,
        SHORT_RATIO_HOLDING,
        Wall,
        choose_spike,
        flow_entries,
        forward_windows,
        short_ratios,
        spaced_entries,
    )
    from stock_ai.core.logging import quiet_on_console
    from stock_ai.data.jquants_investor import SECTION, weekly_flows
    from stock_ai.data.jquants_options import daily_atm_iv

    walls: list[object] = []
    trading = sorted(dates)

    def after_entry(when: dt.date) -> dt.date | None:
        """Return the next trading day, or None.

        **その日の翌営業日である。** 暦の翌日ではない。
        """
        step = bisect.bisect_right(trading, when)
        return trading[step] if step < len(trading) else None

    def oos_entries(events: list[dt.date], holding: int, since: dt.date = OOS_FROM) -> int:
        """Count the distinct OOS entry days, not the events.

        **件数ではなく、入った日で数える。** `#5` は 1,827 件が 831 日で、
        **件数で割ると n を 2.2倍に水増しする**（`CLAUDE.md`「独立な観測を、
        件数で数えない」）。

        **OOS の初日は候補ごとに違いうる。** 候補6 は `IV` が
        2016-07 からしか無いので、暦の切り方をこの候補だけ動かしてある。
        """
        found: set[dt.date] = set()
        limit = len(trading) - holding
        where = {when: index for index, when in enumerate(trading)}
        for event in events:
            entry = after_entry(event)
            if entry is None or not (since <= entry <= OOS_END):
                continue
            if where[entry] > limit:
                continue
            found.add(entry)
        return len(found)

    # --- A 恐怖指数の跳ね上がり（候補6）-------------------------------------
    #
    # **この候補だけ、IS/OOS の切り方が違う**（2026-09-21 にコミットした）。
    # `IV` が 2016-07-19 からしか無いので、暦の 2017-12-31 で切ると IS が
    # 1.5年・最大 339 窓しかなく、**どの線を選んでも膨張が推定できない。**
    with quiet_on_console("stock_ai.data.jquants_options"):
        iv = daily_atm_iv(archive)
    console.print(f"[dim]候補A: {iv.summary()}[/]")
    for line in iv.warnings():
        console.print(f"[yellow]候補A: {line}[/]")

    # **梯子から線を選ぶ。** 満たす中でいちばん厳しいもの——**効果は1つも
    # 見ない。** 選ぶのは観測数だけである。
    choice = choose_spike(iv.levels, returns, dates, HOLDING, end=IV_IS_END)
    console.print(
        "[dim]候補A: 線を梯子から選んだ（**観測数だけで選ぶ。効果は見ていない**）: "
        + "、".join(f"+{rise:.1%}→{count}窓" for rise, count in choice.tried)
        + f"。**採った線 +{choice.rise:.1%}**（要る窓 {choice.needed}）[/]"
    )
    if not choice.cleared:
        console.print(
            f"[yellow]**候補A: 梯子のどの線でも、IS の窓が {choice.needed} に"
            f"届かない**（最大 {choice.is_windows}）。**膨張は推定できない**"
            "——下の壁は暫定である。[/]"
        )

    # **符号を付けない。** `docs/WALL.md` は「符号付きの数字が1つも出ないこと」
    # で効果の混入を止めている（`test_the_document_carries_no_effect`）。
    # **守りを緩めるのではなく、札のほうを直す。**
    name = f"恐怖指数の跳ね上がり（ATM の予想変動率 前日比 {choice.rise:.1%} 以上）"
    used, values = forward_windows(returns, dates, list(choice.events), HOLDING, end=IV_IS_END)
    observations = oos_entries(list(choice.events), HOLDING, since=IV_OOS_FROM)
    if len(values) < 2 or not observations:  # noqa: PLR2004 - 1件では散らばりが測れない
        console.print(
            f"[yellow]**{name}: IS の窓が {len(values)}、OOS の観測が "
            f"{observations}。** 壁を出せない。[/]"
        )
    else:
        sampled[name] = values
        with quiet_on_console("stock_ai.backtest.power"):
            estimate = estimate_power(values, lags=HOLDING)
        walls.append(
            Wall(
                candidate=6,
                name=name,
                pipe="指数を買うだけ（引く相手が無い）",
                unit="イベント日",
                observations=observations,
                sd=estimate.daily_sd,
                inflation=estimate.inflation,
                line=line_for("calendar"),
                source=(
                    f"{benchmark} の日次、IS {IV_IS_FROM}〜{IV_IS_END} の "
                    f"{len(values):,} 窓（跳ねた日 {len(choice.events):,}、"
                    f"窓 {HOLDING} 営業日）"
                ),
                sample=len(values),
                undersampled=estimate.undersampled,
                window=HOLDING,
                notes=(
                    f"**IS/OOS はこの候補だけ別である**（IS {IV_IS_FROM}〜{IV_IS_END}、"
                    f"OOS {IV_OOS_FROM}〜{OOS_END}）。`IV` が 2016-07 からしか無い",
                    "**管は `calendar`。** 校正したのは日次の月替わりで、"
                    "同じ形ではあるが同じ設計ではない",
                    f"**予想変動率を作れた日は {len(iv.levels):,}。** そのうち跳ねた日が "
                    f"{len(choice.events):,}、IS の窓が {len(values):,}"
                    f"（入れた窓 {len(used):,}）、"
                    f"**判定に使える OOS が {observations:,}**（n はこれ）",
                ),
            )
        )

    # --- C 空売り比率（候補16）----------------------------------------------
    #
    # **候補11 と同じ形である**——絞らず、向きだけで ±1 に切り替える。
    # **壁は符号に依存しない**ので、向きの決め方を決めても答えを先に見た
    # ことにならない。
    #
    # **重ならないように間引いて入る。** 毎日入ると膨張が √20 になり、
    # 壁がそのぶん上がる——膨張は要る情報比を下げられる3つのうちの1つである。
    with quiet_on_console("stock_ai.backtest.wall"):
        ratios = short_ratios(archive)
    console.print(f"[dim]候補C: {ratios.summary()}[/]")
    for line in ratios.warnings():
        console.print(f"[yellow]候補C: {line}[/]")

    if ratios.levels:
        name = f"空売り比率の高低（{SHORT_RATIO_HOLDING} 営業日ごと、全33業種の合計）"
        entries = spaced_entries(ratios.levels, SHORT_RATIO_HOLDING)
        _used, values = forward_windows(returns, dates, entries, SHORT_RATIO_HOLDING, end=IS_END)
        observations = oos_entries(entries, SHORT_RATIO_HOLDING)
        if len(values) < 2 or not observations:  # noqa: PLR2004 - 1件では散らばりが測れない
            console.print(
                f"[yellow]**{name}: IS の窓が {len(values)}、OOS の観測が "
                f"{observations}。** 壁を出せない。[/]"
            )
        else:
            sampled[name] = values
            with quiet_on_console("stock_ai.backtest.power"):
                # **窓が重ならないので、隣どうしの相関だけ見る。**
                estimate = estimate_power(values, lags=1)
            walls.append(
                Wall(
                    candidate=16,
                    name=name,
                    pipe="指数を買うだけ（引く相手が無い）",
                    unit="窓",
                    observations=observations,
                    sd=estimate.daily_sd,
                    inflation=estimate.inflation,
                    line=line_for("calendar"),
                    source=(
                        f"{benchmark} の日次、IS {len(values):,} 窓"
                        f"（比率を作れた日 {len(ratios.levels):,}、"
                        f"採った日 {len(entries):,}）"
                    ),
                    sample=len(values),
                    undersampled=estimate.undersampled,
                    period_years=_judgement_years(OOS_FROM, OOS_END),
                    notes=(
                        "**絞っていない。** 常に市場に居て、向きだけ切り替える"
                        "——**壁は符号に依存しない**（±1 は SD を変えない）",
                        f"**{SHORT_RATIO_HOLDING} 営業日ごとに入るので、窓が重ならない。** "
                        "毎日入ると膨張が √20 になり、壁がそのぶん上がる",
                        "**管は `calendar`。** 校正したのは日次の月替わりである",
                    ),
                )
            )

    # --- B 需給（候補7 と候補11）--------------------------------------------
    #
    # **早期 return を置かない。** 候補7 で `return walls` していたので、
    # **下に足した候補11 が黙って落ちる形**になっていた——`CLAUDE.md`
    # 「早期 return が、下に足した検査を黙らせる」（書いてある規則である）。
    with quiet_on_console("stock_ai.data.jquants_investor"):
        flows = weekly_flows(archive)
    console.print(f"[dim]候補B: {flows.summary()}[/]")
    for line in flows.warnings():
        console.print(f"[yellow]候補B: {line}[/]")
    published = [(week.published_on, week.foreign_share) for week in flows.weeks]

    # | 候補 | 事象 | 1観測 |
    # |---|---|---|
    # | 7 | その週が**買い越し** | その週の公表 |
    # | 11 | **無い**（常に市場に居る） | 全部の公表 |
    #
    # **候補11 の壁は符号に依存しない。** ±1 倍は SD を変えないので、
    # **指数の週次の散らばりと週数だけで決まる**——合図は n にも SD にも
    # 効かない。**だから効果を見たことにならない。**
    designs = (
        (
            7,
            f"需給はすべての材料に優先する（外国人の買い越した週、{SECTION}）",
            flow_entries(published),
            "**公表日の翌営業日に入る。** 週末で入ると先読みになる"
            "——公表は週の終わりの 10 日ほど後である",
        ),
        (
            11,
            f"需給の向きに従う（常に市場に居る、{SECTION}）",
            sorted(when for when, _share in published),
            "**絞らない。** 直近の公表の符号で向きを切り替えるだけなので、"
            "**壁は指数の週次の散らばりと週数だけで決まる**（±1 倍は SD を変えない）",
        ),
    )
    for candidate, name, entries, note in designs:
        if len(entries) < 2:  # noqa: PLR2004 - 1件では散らばりが測れない
            console.print(f"[yellow]**{name} の週が {len(entries)} しか無い。** 壁を出せない。[/]")
            continue
        _used, values = forward_windows(returns, dates, entries, FLOW_HOLDING, end=IS_END)
        observations = oos_entries(entries, FLOW_HOLDING)
        if len(values) < 2 or not observations:  # noqa: PLR2004 - 同上
            console.print(
                f"[yellow]**{name}: IS の窓が {len(values)}、OOS の観測が "
                f"{observations}。** 壁を出せない。[/]"
            )
            continue
        sampled[name] = values
        with quiet_on_console("stock_ai.backtest.power"):
            # **週次で保有1週なので、窓は重ならない。** 隣どうしの相関だけ見る。
            estimate = estimate_power(values, lags=1)
        walls.append(
            Wall(
                candidate=candidate,
                name=name,
                pipe="指数を買うだけ（引く相手が無い）",
                unit="公表",
                observations=observations,
                sd=estimate.daily_sd,
                inflation=estimate.inflation,
                line=line_for("calendar"),
                source=(
                    f"{benchmark} の日次、IS {len(values):,} 窓"
                    f"（採った週 {len(entries):,}、窓 {FLOW_HOLDING} 営業日）"
                ),
                sample=len(values),
                undersampled=estimate.undersampled,
                # **年に直せる。** 窓は重ならない（週に1回・保有1週）。
                #
                # **率を焼き付けない。** ここに `52.0` と書いてあった
                # （2026-09-21 まで）。候補11 はそれで合っていたが、
                # **候補7 は買い越し週にしか入らない**ので年 24.6 回で、
                # **年率が 2.1倍に出ていた**（年 32.0% → 15.1%）。
                # `Wall.per_year` が `observations ÷ period_years` から作る。
                period_years=_judgement_years(OOS_FROM, OOS_END),
                # **n の出どころを注記に出す。** 「974 週」と「n 450」が並ぶ
                # だけだと、**どこで減ったのかが出力から読み取れない**——
                # `CLAUDE.md`「同じ列に、2つの単位を並べない」の隣の形である。
                notes=(
                    note,
                    f"**`{SECTION}` の公表は {len(flows.weeks):,} 週。** "
                    f"そのうち採ったのが {len(entries):,}、IS の窓が {len(values):,}、"
                    f"**判定に使える OOS が {observations:,}**（n はこれ）",
                    "**管は `calendar`。** 校正したのは日次の月替わりである",
                ),
            )
        )
    return walls


@app.command(name="wall-survey")
def wall_survey(
    into: str | None = typer.Option(None, "--write", help="Regenerate docs/WALL.md."),
    benchmark: str = typer.Option(BENCHMARK, "--benchmark", help="Calendar and index."),
    rosters: str = typer.Option(
        str(DEFAULT_SNAPSHOT_DIR), "--rosters", help="Where the dated rosters live."
    ),
    archive: str = typer.Option(
        str(DEFAULT_ARCHIVE_DIR), "--dir", help="Where the archived originals live."
    ),
) -> None:
    """Measure how big an effect each unregistered candidate would need - no effects.

    **事前登録を書く前に、壁の高さだけを見る。** 検出できる差は
    `線 × SD ÷ √n` で、**散らばりと観測数だけで決まる**ので、これを見ても
    答えを先に見たことにならない。

    **効果（平均）は1つも計算しない。** `Wall` に平均の欄が無いのはそのため
    である——入れられる形にすると「効果がありそうだから通す」が書けてしまう。

    **IS（〜2017-12）のリターンだけを読む。** イベントの**件数**だけは OOS も
    数える——判定に使える観測数がそこで決まるためで、件数は効果ではない。

    価格を4回なめる（走査・等加重の宇宙・イベント2種）。**時間がかかる。**
    """
    from stock_ai.backtest.event_window import event_sample
    from stock_ai.backtest.multiplicity import line_for, student_t_line
    from stock_ai.backtest.power import estimate_power, trimmed_variance
    from stock_ai.backtest.quantile_series import build_panel
    from stock_ai.backtest.universe_benchmark import equal_weighted_windows
    from stock_ai.backtest.wall import (
        DIVIDEND_COLUMN,
        GAP_DOWN,
        HOLDING,
        IS_END,
        KNIFE_DAYS,
        KNIFE_DROP,
        MARGIN_LOOKBACK,
        OOS_END,
        OOS_FROM,
        TAIL_SESSIONS,
        Missing,
        Wall,
        complete_halloween_years,
        complete_tail_years,
        dividend_yields,
        halloween_episodes,
        margin_change,
        scan,
        signal_overlap,
        stale_reasons,
        tail_episodes,
        usable_rebalances,
    )
    from stock_ai.core.logging import quiet_on_console
    from stock_ai.data.schema import split_adjusted

    settings = get_settings()
    configure_logging(settings.log_level)

    snapshots = membership(Path(rosters))
    if not snapshots:
        console.print("[red]名簿が無い。[/] **渡さないと生存バイアスが入る。**")
        raise typer.Exit(code=1)

    console.print(
        f"[dim]IS（〜{IS_END}）のリターンだけを読みます。イベントの件数だけ "
        f"OOS（{OOS_FROM}〜{OOS_END}）も数えます。**効果は1つも出しません。**[/]"
    )

    database = Database()
    database.create_all()

    # --- 3 Sell in May ------------------------------------------------------
    returns, dates, _month_ends = _calendar_pipe(benchmark)
    years, episodes = halloween_episodes(returns, dates, end=IS_END)
    walls: list[Wall] = []
    # **裾の感度を見るために、系列そのものを取っておく。** 平均は取らない。
    sampled: dict[str, list[float]] = {}
    if len(episodes) >= 3:  # noqa: PLR2004 - 3点無いと散らばりが測れない
        oos_years = complete_halloween_years(OOS_FROM, OOS_END)
        sampled["Sell in May（冬 − 夏）"] = episodes
        with quiet_on_console("stock_ai.backtest.power"):
            # **n=7 では Newey-West が不安定。** 膨張は 1.0 に固定する（#14）。
            estimate = estimate_power(episodes, lags=0)
        sd, inflation = estimate.daily_sd, estimate.inflation
        walls.append(
            Wall(
                candidate=3,
                name="Sell in May（冬 − 夏）",
                pipe="年1観測の暦",
                unit="年",
                observations=oos_years,
                sd=sd,
                inflation=inflation,
                # **自由度で線を引く。** n が小さいと `t` が正規から離れる（#14）。
                line=student_t_line(max(oos_years - 1, 1)),
                source=f"{benchmark} の日次、IS {years[0]}〜{years[-1]} の {len(years)} 年",
                sample=len(episodes),
                # **1年1観測なので、年数は観測数そのものである。**
                period_years=float(oos_years),
            )
        )
    else:
        console.print("[yellow]**Sell in May の観測が3年に満たない。** 壁を出せない。[/]")

    # --- A・B 指数を買うだけの2本（材料は原本から）---------------------------
    #
    # **畳み方は `wall` の説明に1つだけ書いてある。** ここで選ばない。
    for wall in _index_walls(Path(archive), returns, dates, benchmark, sampled):
        walls.append(wall)

    # --- 価格を1度だけなめる -------------------------------------------------
    console.print("[dim]価格を走査しています（1銘柄1行は出しません）...[/]")
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("走査", total=None)

        def step(done: int, total: int) -> None:
            progress.update(task, completed=done, total=total)

        with quiet_on_console("stock_ai.backtest.wall"):
            materials = scan(database, progress=step)
    console.print(materials.summary())

    # --- 5・12・13・19 月次・分位ロングショート -------------------------------
    #
    # **1つのループで回す。** 同じ処理を4つ書けば、1つは間違える
    # （`CLAUDE.md`「同じ式を3つ書けば、1つは間違える」）。**畳み方は
    # `wall.py` の説明に書いてから測っている。**
    with database.session() as session:
        calendar = split_adjusted(PriceRepository(session).get_raw_prices(benchmark)).index
    oos_months = usable_rebalances(calendar, OOS_FROM, OOS_END)

    # **時価総額は価格の走査からは出ない。** `valuation_monthly` の生成物を
    # 読む。**無ければ「材料が無い」と出す**——黙って候補を1つ落とさない。
    caps = _market_cap_values()

    # **信用買い残は原本から。** 公表の遅れを外すのは `margin_change` の
    # 中に1つだけ置いてある——`Date` は金曜時点で、公表はその第2営業日である。
    with quiet_on_console("stock_ai.backtest.wall"):
        margin, margin_census = margin_change(Path(archive))
    console.print(f"[dim]{margin_census.summary()}[/]")
    for line in margin_census.warnings():
        console.print(f"[yellow]15: {line}[/]")

    # **配当利回りの分母は調整前の終値である。** 調整後で割ると、分割比の
    # ぶん利回りが跳ねる——1:10 で 1.0% が 10.0% になった（2026-09-20）。
    with quiet_on_console("stock_ai.backtest.wall"):
        yields, yield_census = dividend_yields(Path(archive), materials.raw_price_level)
    console.print(f"[dim]{yield_census.summary()}[/]")
    for line in yield_census.warnings():
        console.print(f"[yellow]14: {line}[/]")

    monthly_designs = (
        (5, "新値には黙ってつけ（52週高値への近さ）", materials.high52, "近さ"),
        (12, "小型株効果（時価総額の小さい順）", caps, "時価総額"),
        (13, "低位株（株価の安い順）", materials.price_level, "終値"),
        (14, f"高配当利回り（`{DIVIDEND_COLUMN}` ÷ 調整前の終値）", yields, "利回り"),
        (15, f"信用買い残の減少（{MARGIN_LOOKBACK} 公表ぶんの変化）", margin, "変化"),
        (19, "節目の株価（キリ番からの位置）", materials.round_position, "位置"),
    )
    for candidate, name, values, what in monthly_designs:
        if not values:
            console.print(f"[yellow]**{name}: 並べる材料が1つも無い。** 壁を出せない。[/]")
            continue
        with quiet_on_console("stock_ai.backtest.quantile_series"):
            panel = build_panel(database, values, end=IS_END, snapshots=snapshots)
        if len(panel.months) < 3:  # noqa: PLR2004 - 3点無いと散らばりが測れない
            console.print(f"[yellow]**{name}: 分位が3ヶ月に満たない。** 壁を出せない。[/]")
            continue
        spread = [row[-1] - row[0] for row in panel.quantiles]
        sampled[name] = spread
        with quiet_on_console("stock_ai.backtest.power"):
            estimate = estimate_power(spread, lags=3)
        walls.append(
            Wall(
                candidate=candidate,
                name=name,
                pipe="月次・分位ロングショート",
                unit="月",
                observations=oos_months,
                sd=estimate.daily_sd,
                inflation=estimate.inflation,
                line=line_for("monthly"),
                source=f"IS {len(panel.months)} ヶ月、5分位・等加重",
                sample=len(spread),
                undersampled=estimate.undersampled,
                period_years=oos_months / 12.0,
                notes=(f"{what}を作れず外した銘柄月 {panel.skipped_no_value:,}",),
            )
        )

    # **12 と 13 が同じものを並べていないか。** 壁の表に実質同じ設計が2行
    # 在ると、**後で良いほうを選んだのと区別が付かない。**
    pairs = {
        5: materials.high52,
        12: caps,
        13: materials.price_level,
        19: materials.round_position,
    }
    for left, right in ((12, 13), (13, 19), (12, 19)):
        shared, correlation = signal_overlap(pairs[left], pairs[right])
        if correlation is None:
            continue
        console.print(
            f"[dim]{left} と {right} の並べ方の順位相関 {correlation:+.2f}"
            f"（重なった銘柄月 {shared:,}）[/]"
        )
        if abs(correlation) >= SAME_DESIGN:
            console.print(
                f"[yellow]**{left} と {right} は実質同じ設計である**"
                f"（順位相関 {correlation:+.2f}）。**片方だけ残すこと**"
                "——2行在ると、後で良いほうを選んだのと区別が付かない。[/]"
            )

    # --- 18 掉尾の一振 -------------------------------------------------------
    tail_years, tail = tail_episodes(returns, dates, end=IS_END)
    if len(tail) >= 3:  # noqa: PLR2004 - 3点無いと散らばりが測れない
        oos_tail = complete_tail_years(OOS_FROM, OOS_END)
        sampled["掉尾の一振"] = tail
        with quiet_on_console("stock_ai.backtest.power"):
            # **n が小さいと Newey-West が不安定。** #14・#3 と同じく 1.0。
            estimate = estimate_power(tail, lags=0)
        walls.append(
            Wall(
                candidate=18,
                name=f"掉尾の一振（12月末の {TAIL_SESSIONS} 営業日）",
                pipe="年1観測の暦",
                unit="年",
                observations=oos_tail,
                sd=estimate.daily_sd,
                inflation=estimate.inflation,
                # **自由度で線を引く。** n が小さいと `t` が正規から離れる（#14）。
                line=student_t_line(max(oos_tail - 1, 1)),
                source=(
                    f"{benchmark} の日次、IS {tail_years[0]}〜{tail_years[-1]} の "
                    f"{len(tail_years)} 年"
                ),
                sample=len(tail),
                # **1年1観測なので、年数は観測数そのものである。**
                period_years=float(oos_tail),
                notes=(
                    f"**{TAIL_SESSIONS} 営業日に出典は無い。** 決めの値である",
                    "**引く相手が無い**（指数を買うだけ）",
                ),
            )
        )
    else:
        console.print("[yellow]**掉尾の一振の観測が3年に満たない。** 壁を出せない。[/]")

    # --- 9・10 イベント型 ----------------------------------------------------
    console.print("[dim]引く相手（等加重の宇宙）を作っています...[/]")
    with quiet_on_console("stock_ai.backtest.universe_benchmark"):
        universe = equal_weighted_windows(database, HOLDING)
    for line in universe.warnings():
        console.print(f"[yellow]{line}[/]")

    events = (
        (9, f"窓は埋まる（下窓 {GAP_DOWN:.0%}）", materials.gaps_is, materials.gaps_oos_days),
        (
            10,
            f"落ちるナイフ（{KNIFE_DAYS}営業日で −{KNIFE_DROP:.0%}）",
            materials.knives_is,
            materials.knives_oos_days,
        ),
    )
    for candidate, name, drawn, oos_days in events:
        if len(drawn) < 2:  # noqa: PLR2004 - 1件では散らばりが測れない
            console.print(f"[yellow]**{name} のイベントが {len(drawn)} 件しか無い。**[/]")
            continue
        with quiet_on_console("stock_ai.backtest.event_window"):
            sample = event_sample(database, drawn, HOLDING, benchmark, IS_END, universe)
        if len(sample.values) < 2:  # noqa: PLR2004 - 同上
            console.print(f"[yellow]**{name} の使えたイベント日が足りない。**[/]")
            continue
        if not oos_days:
            console.print(f"[yellow]**{name} は OOS に1日も無い。** 壁を出せない。[/]")
            continue
        sampled[name] = sample.values
        with quiet_on_console("stock_ai.backtest.power"):
            # **窓が20営業日、入口は毎日。** 重なりは大きい side である。
            estimate = estimate_power(sample.values, lags=HOLDING)
        sd, inflation = estimate.daily_sd, estimate.inflation
        walls.append(
            Wall(
                candidate=candidate,
                name=name,
                pipe="イベント型（等加重を引く）",
                unit="イベント日",
                observations=oos_days,
                sd=sd,
                inflation=inflation,
                line=line_for("event"),
                source=(f"IS {sample.drawn:,} 件が {len(sample.values):,} 日、窓 {HOLDING} 営業日"),
                sample=len(sample.values),
                undersampled=estimate.undersampled,
                window=HOLDING,
                notes=(
                    f"価格が1本も無くて捨てた {sample.no_prices:,}、"
                    f"その日に足が無くて捨てた {sample.not_trading:,}",
                ),
            )
        )

    if not walls:
        console.print("[red]壁を1つも出せなかった。[/]")
        raise typer.Exit(code=1)

    walls.sort(key=lambda wall: (wall.annual is None, wall.annual or wall.detectable))

    # **SD は IS で測り、n は OOS で数えている。** どちらが薄くても壁は
    # 当てにならないが、**理由が違うので別に言う。**
    #
    # **ここは前、両方 `observations`（OOS）を見ていた**——コメントには
    # 「IS が薄ければ」と書いてあったのに（2026-09-21 に気付いた）。
    # **コメントが主張していることと、コードが守っていることが別だった。**
    for wall in walls:
        if wall.observations < THIN_OBSERVATIONS:
            console.print(
                f"[yellow]**{wall.name}: 判定に使える観測が {wall.observations} しか無い。** "
                "**壁の高さ（n）が、その数で決まっている。**[/]"
            )
        if wall.sample and wall.sample < THIN_OBSERVATIONS:
            console.print(
                f"[yellow]**{wall.name}: SD を測った IS の観測が {wall.sample} しか無い。** "
                "**壁の高さ（SD）の推定が当てにならない**——OOS の数とは別の話である。[/]"
            )
        if wall.undersampled:
            console.print(
                f"[yellow]**{wall.name}: SD を測った標本（{wall.sample}）が、"
                "重なりのラグに対して足りない。** **推定値ではなく上限を使った**"
                f"（{wall.inflation:.2f}x → 上限 {wall.effective_inflation:.2f}x）。"
                "**壁が上がって他の説を超えても、推定値には戻さない**"
                "——結果を見てから規則を選ぶことになる。[/]"
            )

    # **同じ規則を、全部の行に当てたことを出す。** 1つの候補にだけ当てると、
    # 「1列だけ守る警告」と同じ形になる（`CLAUDE.md`、2026-09-21 にユーザーが
    # 指摘）。**当たらなかったなら、当たらなかったと出れば足りる。**
    capped = [wall for wall in walls if wall.capped]
    console.print(
        f"[dim]標本の足りなさは **{len(walls)} 行すべてに当てた**"
        f"（線は `power.SAMPLE_PER_LAG`）。上限に切り替えたのは {len(capped)} 行"
        + ("。" if not capped else "——" + "、".join(wall.name for wall in capped) + "。")
        + "[/]"
    )

    # **SD が数日でできていないか。** 1% を落として半分以下になるなら、それは
    # 「毎日どれくらい散らばるか」ではなく「まれに何が起きるか」を測っている
    # （`power.trimmed_variance`）。**壁の高さも、その数日で決まる。**
    for label, values in sampled.items():
        if len(values) < 100:  # noqa: PLR2004 - 1% を落とせる最小の数
            continue
        variance, dropped = trimmed_variance(values, fraction=0.01)
        trimmed = variance**0.5
        with quiet_on_console("stock_ai.backtest.power"):
            plain = estimate_power(values, lags=0).daily_sd
        if trimmed > 0 and (plain / trimmed) > TAIL_DRIVEN:
            console.print(
                f"[yellow]**{label}: 散らばりの大半が数日でできている。** "
                f"外れた {dropped} 件を落とすと SD が {trimmed:.2%} まで下がる"
                f"（{len(values):,} 件中）。**壁の高さも、その数日で決まっている。**[/]"
            )

    table = Table(title="壁の下見（**効果は出していない**）")
    for column in (
        "候補",
        "設計",
        "n",
        "1観測あたりのSD",
        "重なりの膨張",
        "線",
        "検出できる差",
        "要る情報比",
    ):
        table.add_column(column, overflow="fold")
    for wall in walls:
        annual = wall.annual
        size = f"年 {annual:.1%}" if annual is not None else f"1{wall.unit} {wall.detectable:.2%}"
        table.add_row(
            f"{wall.candidate}",
            wall.name,
            f"{wall.observations:,}",
            f"{wall.sd:.2%}／{wall.unit}",
            _inflation_cell(wall),
            f"{wall.line:.2f}",
            f"[bold]{size}[/]",
            _wall_ir_cell(wall),
        )
    console.print(table)
    # **検出できる差を、行どうしで比べない。** 単位も、市場に居る時間の割合も
    # 違う——候補7 と候補11 は年率で 32.0% 対 24.5% と出ていたのに、
    # **要る情報比では 1.12 対 1.09 でほとんど差が無かった**（2026-09-21）。
    console.print(
        "[dim]**検出できる差は、行どうしで比べられない**（単位も、市場に居る時間の"
        "割合も違う）。**設計によらないのは「要る情報比」のほうである**"
        "——`線 × 膨張 ÷ √年数`。n も SD も効かない（`docs/PASSING.md` §2）。[/]"
    )
    for wall in walls:
        for note in wall.notes:
            console.print(f"[dim]{wall.candidate}: {note}[/]")

    # **6 と 7 をここから外した**（2026-09-21）。どちらも「測れない」と
    # 書いてあったが、**材料は原本に在った**——オプションの予想変動率と、
    # 投資部門別である。上で壁を測っている。
    #
    # **無いことは出力に出ないが、「無い」と書いたことも出力に出ない。**
    # 書いた側が確かめるまで、そこに在る材料は見えないままになる。
    missing = [
        Missing(8, "噂で買って事実で売る", "「噂」の初出時点を客観的に取る口が無い"),
        Missing(
            17,
            "決算発表日を避ける",
            "**`/fins/earnings-date` は全プランで「直近のみ」**"
            "（公式表、`jquants_plan.NO_HISTORY`）。`fins/summary` の "
            "`DisclosedDate` は**実現した発表日**なので、それで避けるのは先読み。"
            "**ただし原本を毎日保存していれば、その積み重ねが**"
            "**「その日に何が予定されていたか」になる**——下の警告が数える",
            endpoint="/fins/earnings-date",
        ),
    ]
    # **「材料が無い」と書いてあるのに、原本が在る候補を言う。**
    # **2度やった**（14 と 16。どちらもユーザーが指摘）——列の棚卸しが
    # 答えを出しているのに、こちらの文面が古いまま残る。
    # **`read_manifest` は開かないので、費用が無い。**
    for line in stale_reasons(Path(archive), missing):
        console.print(f"[yellow]{line}[/]")

    absent = Table(title="材料が無くて測れなかった候補")
    for column in ("候補", "説", "なぜ測れないか"):
        absent.add_column(column, overflow="fold")
    for item in missing:
        absent.add_row(f"{item.candidate}", item.name, item.reason)
    console.print(absent)

    console.print(
        "[dim]**壁を比べても、どの説が正しいかは分からない。** "
        "ここに出ているのは見分けられる最小の大きさだけである。[/]"
    )

    if into:
        span = (
            f"IS は〜{IS_END} のリターン、イベントの件数は {OOS_FROM}〜{OOS_END} も。"
            f"イベント型の窓は {HOLDING} 営業日に揃えた"
        )
        target = Path(into)
        target.write_text(_wall_document(walls, missing, span), encoding="utf-8")
        console.print(f"[green]{target} を書き直した。[/] **生成物である。手で直さない。**")


@app.command(name="valuation-monthly")
def valuation_monthly(
    directory: str = typer.Option(
        str(DEFAULT_ARCHIVE_DIR), "--dir", help="Where the archived originals live."
    ),
    into: str | None = typer.Option(None, "--into", help="Where to write the file."),
) -> None:
    """Pull month-end PBR out of the originals, into one small file.

    **必要なのは月末の1点だけである。** 1,588万銘柄日を毎回読むと数分かかる。
    200ヶ月 × 3,500銘柄なら数 MB に収まり、**原本から作り直せる。**

    **暦の月末を探さない。** 月の途中で上場廃止になった銘柄は、その日が最後の
    観測である。暦で引くと、その銘柄がその月から丸ごと消える。

    取りには行かない。読んで書くだけ。
    """
    from stock_ai.data.valuation_monthly import DEFAULT_PATH, build

    settings = get_settings()
    configure_logging(settings.log_level)

    def progress(index: int, total: int, _key: str) -> None:
        console.print(f"読んでいる… {index}/{total}", end="\r")

    target = Path(into) if into else DEFAULT_PATH
    report = build(Path(directory), target, progress)
    console.print(" " * 40, end="\r")
    console.print(report.summary())
    if not report.rows:
        raise typer.Exit(code=1)

    size = target.stat().st_size / 1_000_000
    console.print(f"[green]{target} に書いた（{size:.1f} MB）。[/]")
    console.print(
        "[dim]**生成物である。手で直さない。** 原本から作り直せるので、"
        "食い違ったら捨てて作り直す。[/]"
    )


@app.command(name="jquants-plan-coverage")
def jquants_plan_coverage(
    to_plan: str = typer.Option("Free", "--to", help="Plan to downgrade to."),
    directory: str = typer.Option(
        str(DEFAULT_ARCHIVE_DIR), "--dir", help="Where the archived originals live."
    ),
) -> None:
    """Say whether downgrading is safe, by endpoint, without fetching anything.

    **「たぶん全部取った」で解約しない。** 公式のプラン表と、原本の目録に実際に
    何本あるかを並べる。落とすと取れなくなり、しかも手元に1本も無いものが
    あれば、それが止める理由になる。

    取りには行かない。数えるだけ。
    """
    from stock_ai.data.jquants_plan import (
        NO_HISTORY,
        UNARCHIVABLE,
        archivable,
    )
    from stock_ai.data.jquants_plan import (
        coverage as plan_coverage,
    )

    settings = get_settings()
    configure_logging(settings.log_level)

    plan = (settings.jquants_plan or "").strip().capitalize()
    target = to_plan.strip().capitalize()
    report = plan_coverage(plan, Path(directory))

    console.print(f"いま [bold]{plan or '不明'}[/] → 落とす先 [bold]{target}[/]")
    console.print()

    table = Table(title="原本の在庫（公式のプラン表に当てたもの）")
    for column, justify in (
        ("データ", "left"),
        ("最低プラン", "left"),
        ("本数", "right"),
        ("大きさ", "right"),
        ("期間", "left"),
        (f"{target} で増やせるか", "left"),
    ):
        # **名前を省略しない。** `/derivatives/bars/d…` が3行並ぶと、
        # どれが0本なのか読めない——解約の判断がその1点にかかっているのに。
        table.add_column(column, justify=justify, overflow="fold")
    for entry in report.entries:
        span = f"{entry.first[:6]} 〜 {entry.last[:6]}" if entry.first else ""
        table.add_row(
            entry.endpoint,
            entry.minimum_plan,
            f"{entry.files:,}" if entry.files else "[red]0[/]",
            f"{entry.bytes / 1_000_000:,.0f} MB" if entry.bytes else "",
            span,
            "はい" if archivable(target, entry.endpoint) else "[dim]いいえ[/]",
        )
    console.print(table)

    if report.unknown_keys:
        # **表に無い鍵が出たら、表のほうが古い。** 数えられなかったものを
        # 黙って捨てると、在庫が実際より少なく見える。
        console.print(
            f"[yellow]どのエンドポイントにも当てはまらない原本が {report.unknown_keys} 本ある。[/] "
            "**こちらの一覧のほうが古い可能性がある。**"
        )

    blockers = report.blockers(target)
    if blockers:
        console.print(
            f"[red]落とす前に取りに行く先が {len(blockers)} 本ある。[/] "
            + "、".join(entry.endpoint for entry in blockers)
        )
        console.print("[red]**いま落とすと、再契約するまで取れない。**[/]")
        return

    losing = report.losing(target)
    console.print(
        f"[green]手元に1本も無いものは無い。[/] "
        f"{target} で増やせなくなるのは {len(losing)} 種類だが、"
        "**どれも既に原本がある。**"
    )
    # **失うのは「貯めたもの」ではなく「これから取れること」である。**
    # 再契約すればその日から戻る。ここを混ぜると、戻せる話が戻せない話に
    # 見えてしまう。
    console.print(
        "[dim]落として失うのは *これから取れること* で、*貯めたもの* ではない。"
        "原本は手元とpCloudの両方にある。再契約すればその日から増やせる。[/]"
    )

    if target == "Free":
        console.print(
            "[dim]Free は取引カレンダーを除いて一括が使えず、API で見える範囲も"
            "「12週間前〜2年12週間前」になる。**日々の更新も止まる。**[/]"
        )
    for endpoint in sorted(NO_HISTORY):
        console.print(
            f"[dim]{endpoint} は全プランで直近のみ。原本が何本あっても、"
            "過去のある日に何が予定されていたかは戻らない。[/]"
        )
    for name, why in UNARCHIVABLE.items():
        console.print(f"[dim]{name}: {why} 原本に残せないので、在庫の表には出ない。[/]")


@app.command(name="price-coverage")
def price_coverage(
    market: str = typer.Option("JP", "--market", help="Which market to count."),
    thin: int = typer.Option(0, "--thin-bars", help="Bars below this count as unusable."),
    show: int = typer.Option(20, "--show", help="How many symbols to list."),
) -> None:
    """Count the symbols the roster has but the prices do not.

    **穴は、黙って観測を消す。** 陰性対照で、引いた 800,000 件のうち
    **16,677 件（2.1%）が「価格が1本も無い銘柄」に当たっていた**
    （2026-09-18）。

    **乱数だからどうでもいい、という話ではない。** 引いているのは
    `list_securities` が返す銘柄で、**説の側の候補もそこから出る。**
    そこに足が1本も無ければ、**そのイベントは判定に入らないまま消える。**

    **数えるだけで、取り込みはしない。** 穴の理由は1つではない（上場前・
    プランの範囲外・取り込み失敗）ので、見てから決める。

    API を1回も叩かない。
    """
    from stock_ai.data.price_coverage import THIN_BARS, survey

    settings = get_settings()
    configure_logging(settings.log_level)

    database = Database()
    database.create_all()
    found = survey(database, market=market, thin_bars=thin or THIN_BARS)
    if not found.listed:
        console.print(f"[red]{market} の銘柄が名簿に1件も無い。[/]")
        raise typer.Exit(code=1)

    table = Table(title=f"名簿と価格の噛み合い（{market}）")
    for column in ("見たもの", "件数", "割合"):
        table.add_column(column, overflow="fold")
    table.add_row("名簿に在る", f"{found.listed:,}", "100.0%")
    table.add_row(
        "足がある",
        f"{found.with_prices:,}",
        f"{found.with_prices / found.listed:.1%}",
    )
    table.add_row(
        "[bold]足が1本も無い（穴）[/]",
        f"[bold]{len(found.empty):,}[/]",
        f"[bold]{found.empty_share:.1%}[/]",
    )
    table.add_row(
        f"足が {found.thin_bars} 本未満（窓が開けられない）",
        f"{len(found.thin):,}",
        f"{found.thin_share:.1%}",
    )
    console.print(table)

    if found.empty:
        # **穴が一様かどうかを、読む側に気付かせない。** 偏っていれば、
        # 消える観測も偏る。件数の1行からはそれが出てこない。
        shape = Table(title="穴の内訳")
        for column in ("かたち", "件数", "穴に占める割合"):
            shape.add_column(column, overflow="fold")
        for label, rows in (
            ("英数字コード（2024年以降の上場）", found.recent_codes),
            ("名前も入っていない", found.nameless),
        ):
            shape.add_row(label, f"{len(rows):,}", f"{len(rows) / len(found.empty):.0%}")
        console.print(shape)

    # **別の切り口から同じ数を出して、一致するか見る。** 一様に銘柄を引けば、
    # この割合がそのまま捨てられる。陰性対照は 2.1% と出していた。
    console.print(
        f"[dim]一様に銘柄を引くと、**{found.unusable_share:.1%} はイベントを"
        "1件も作れない。** 陰性対照の「穴」の割合と噛み合うはずである"
        "——**噛み合わなければ、どちらかが違うものを数えている。**[/]"
    )

    for line in found.warnings():
        console.print(f"[yellow]{line}[/]")

    if found.empty and show:
        listing = Table(title=f"足が1本も無い銘柄（先頭 {min(show, len(found.empty))} 件）")
        for column in ("銘柄", "名前"):
            listing.add_column(column, overflow="fold")
        for symbol, name in found.empty[:show]:
            listing.add_row(symbol, name or "[dim]—[/]")
        console.print(listing)
        if len(found.empty) > show:
            console.print(f"[dim]ほかに {len(found.empty) - show:,} 件。`--show` で増やせる。[/]")


@app.command(name="price-audit")
def price_audit(
    symbol: str = typer.Argument(..., help="Symbol to inspect."),
    around: str = typer.Option(..., "--around", help="Date to centre the window on."),
    window: int = typer.Option(6, "--window", help="Bars to show either side."),
) -> None:
    """Show raw against adjusted bars, so a price jump can be attributed.

    A 20-session return of +125,028% is not a price move. It is a split or a
    consolidation that the stored series does not carry an adjustment for. This
    prints ``close``, ``adj_close`` and the factor between them, so the answer
    is visible rather than inferred:

    - **factor changes across the jump** - the series is adjusted, and the jump
      is in the raw close only. Analysis reading ``split_adjusted`` is fine.
    - **factor stays at 1.00 across the jump** - the source carries no
      adjustment for that action. Every return spanning it is wrong, and no
      amount of care downstream fixes it.

    This is the check for the failure this project keeps meeting: not a crash,
    but a plausible-looking number built from two different scales.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    centre = _parse_date(around)
    if centre is None:
        raise typer.BadParameter(f"--around must be YYYY-MM-DD; got {around!r}.")

    database = Database()
    database.create_all()
    with database.session() as session:
        raw = PriceRepository(session).get_raw_prices(symbol)
    if raw.empty:
        console.print(f"[yellow]{symbol} の価格が無い。[/]")
        raise typer.Exit(code=1)

    stamps = [stamp.date() for stamp in raw.index]
    nearest = min(range(len(stamps)), key=lambda index: abs(stamps[index] - centre))
    lo = max(0, nearest - window)
    hi = min(len(stamps), nearest + window + 1)

    table = Table(title=f"{symbol} — {centre} の前後")
    for column in ("日付", "始値", "終値", "調整後終値", "調整係数", "前日比(調整後)"):
        table.add_column(column, justify="right" if column != "日付" else "left")

    previous: float | None = None
    for index in range(lo, hi):
        row = raw.iloc[index]
        close = float(row[CLOSE])
        adjusted = float(row[ADJ_CLOSE]) if ADJ_CLOSE in raw.columns else float("nan")
        factor = adjusted / close if close else float("nan")
        move = (
            "" if previous is None or not previous else f"{(adjusted / previous - 1) * 100:+.1f}%"
        )
        # 調整後で見て±50%を超える1日の動きは、値動きではまず起きない。
        if move and abs(adjusted / previous - 1) > 0.5:
            move = f"[bold red]{move}[/]"
        table.add_row(
            str(stamps[index]),
            f"{float(row[OPEN]):,.1f}",
            f"{close:,.1f}",
            f"{adjusted:,.1f}",
            f"{factor:.4f}",
            move,
        )
        previous = adjusted
    console.print(table)
    console.print(
        "[dim]調整係数が窓の中で変わっていれば、系列は調整されている。"
        "**1.0000 のまま前日比だけが飛んでいれば、その銘柄には調整が入っていない。**[/]"
    )


@app.command(name="reversal-run")
def reversal_run(
    period: str = typer.Option("oos", "--period", help="oos | is. Judgment is oos, once."),
    directory: str = typer.Option(
        str(DEFAULT_SNAPSHOT_DIR), "--dir", help="Where the dated rosters live."
    ),
    min_turnover: float = typer.Option(MIN_TURNOVER, "--min-turnover", help="Liquidity floor."),
    lookback: int = typer.Option(REVERSAL_LOOKBACK, "--lookback", help="Sessions the fall spans."),
    holding: int = typer.Option(REVERSAL_HOLDING, "--holding", help="Sessions held."),
    benchmark: str = typer.Option(BENCHMARK, "--benchmark", help="Market series and calendar."),
    lags: int = typer.Option(DEFAULT_LAGS, "--lags", help="Newey-West lags. Match the holding."),
) -> None:
    """Run the sealed short-reversal test and apply the sealed reading.

    ``docs/PREREG_REVERSAL_JP.md`` was sealed on 2026-09-04 without one reversal
    return having been looked at. **This is the single judgment that design
    bought.**

    The verdict is not written here and not read off by a person: §8's table is
    applied in code. On the previous hypothesis the standard nearly moved after
    the number was seen - "so close" is exactly what a pre-registration is for.

    The universe is the dated rosters, so companies that were later delisted are
    in it. The measured survivorship bias is -0.040% per 20 sessions; without the
    rosters the result would read that much better than it is.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    try:
        chosen = Period(period.strip().lower())
    except ValueError as exc:
        raise typer.BadParameter(f"--period must be oos or is; got {period!r}.") from exc
    if chosen is Period.ALL:
        raise typer.BadParameter("--period all は判定にならない。oos か is を選ぶ。")

    snapshots = membership(Path(directory))
    if len(snapshots) < 2:
        console.print(
            f"[yellow]名簿が {len(snapshots)} 件しかない。[/] "
            "先に [cyan]checks\\廃止銘柄の取り込み.bat[/] を実行する。"
        )
        raise typer.Exit(code=1)

    begin = max(JUDGMENT_FROM, min(snapshots))
    console.print(
        f"[bold]{chosen.value.upper()}[/] で回す。universe は日付ごとの名簿 "
        f"{len(snapshots)} 件（{min(snapshots)} 〜 {max(snapshots)}）。"
    )
    if chosen is Period.OOS:
        console.print(
            "[bold yellow]これは封印済みの判定である。一度だけ回す。[/] "
            "結果を見てから条件を変えない。"
        )

    database = Database()
    database.create_all()
    variants: dict[str, object] = {}
    for label, skip in (("主要（D+1 で入る）", 1), ("副次（D+2 で入る）", 2)):
        try:
            variants[label] = build_series(
                database,
                chosen,
                benchmark=benchmark,
                start=begin,
                min_turnover=min_turnover,
                lookback=lookback,
                holding=holding,
                snapshots=snapshots,
                skip=skip,
            )
        except ValueError as exc:
            console.print(f"[red]{label}: {exc}[/]")
            raise typer.Exit(code=1) from exc

    series = variants["主要（D+1 で入る）"]
    console.print(
        f"{len(series.days):,} 営業日、1日あたり中央値 "
        f"{int(median(series.counts)) if series.counts else 0} 銘柄"
        f"（1分位あたり約 {int(median(series.counts)) // 5 if series.counts else 0}）。"
        f"不連続をまたいで落とした銘柄日 {series.excluded_discontinuity:,}。"
    )

    shape = Table(title="分位ごとの平均リターン（20営業日、ベンチマーク差引き前）")
    for column in ("分位1（最も下げた）", "分位2", "分位3", "分位4", "分位5", "ベンチマーク"):
        shape.add_column(column, justify="right")
    means = [
        sum(row[index] for row in series.quantiles) / len(series.quantiles) for index in range(5)
    ]
    shape.add_row(
        *[f"{value * 100:+.2f}%" for value in means],
        f"{sum(series.benchmark) / len(series.benchmark) * 100:+.2f}%",
    )
    console.print(shape)

    outcome = judge(series.long_only(), lags=lags)
    result, reading = verdict(outcome.mean, outcome.t_statistic)

    headline = Table(title="主要指標：分位1 − ベンチマーク")
    for column in ("営業日", "平均", "標準誤差(NW)", "t", "費用しきい値", "必要な差(事前)"):
        headline.add_column(column, justify="right")
    headline.add_row(
        f"{outcome.days:,}",
        f"[bold]{outcome.mean * 100:+.3f}%[/]",
        f"{outcome.standard_error * 100:.3f}%",
        f"[bold]{outcome.t_statistic:+.2f}[/]",
        f"{COST_ROUND_TRIP * 100:.2f}%",
        f"{DETECTABLE * 100:.2f}%",
    )
    console.print(headline)

    colour = "green" if result == PASS else "yellow"
    console.print()
    console.print(f"[bold {colour}]判定：{result}[/]")
    console.print(f"  {reading}")
    console.print(
        "[dim]この読み方は 2026-09-04 に封印した表（PREREG_REVERSAL_JP.md §8）を"
        "当てはめたものである。結果を見てから決めていない。[/]"
    )

    console.print()
    console.print("[bold]副次指標（判定には使わない）[/]")
    extra = Table()
    for column in ("指標", "平均", "t"):
        extra.add_column(column, justify="left" if column == "指標" else "right")
    long_short = judge(series.long_short(), lags=lags)
    extra.add_row(
        "分位1 − 分位5", f"{long_short.mean * 100:+.3f}%", f"{long_short.t_statistic:+.2f}"
    )
    skipped = judge(variants["副次（D+2 で入る）"].long_only(), lags=lags)
    extra.add_row("2営業日空けた版", f"{skipped.mean * 100:+.3f}%", f"{skipped.t_statistic:+.2f}")
    extra.add_row(
        "費用 0.60% で見たとき",
        f"{(outcome.mean - 0.002) * 100:+.3f}%",
        "[dim]—[/]",
    )
    console.print(extra)
    console.print(
        "[dim]「費用 0.60%」は主要指標から追加の 0.20% を引いただけの感度である"
        "（費用は分散に効かないので t は変わらない）。[/]"
    )


@app.command(name="lowvol-power")
def lowvol_power(
    end: str = typer.Option(
        "2013-12-31", "--end", help="Last month used to estimate the variance."
    ),
    oos_from: str = typer.Option(
        "2014-01-01", "--oos-from", help="First month the judgment would use."
    ),
    window: int = typer.Option(DEFAULT_WINDOW, "--window", help="Volatility window in sessions."),
    min_symbols: int = typer.Option(MIN_SYMBOLS_PER_MONTH, "--min-symbols", help="Per month."),
    min_turnover: float = typer.Option(MIN_TURNOVER, "--min-turnover", help="Liquidity floor."),
    lags: int = typer.Option(LOWVOL_LAGS, "--lags", help="Newey-West lags, in months."),
    benchmark: str = typer.Option(BENCHMARK, "--benchmark", help="Sets the calendar."),
) -> None:
    """Work out what a low-volatility test could detect - before sealing.

    **No mean is computed or printed.** Variance and autocovariances only, from
    months the judgment will not use. ``--end`` must fall before ``--oos-from``,
    and the command refuses otherwise.

    The point of this hypothesis is that the windows do not overlap: monthly
    rebalancing means each observation is a fresh month. Reversal entered daily
    and held 20 sessions, so neighbouring observations shared 19 days out of 20
    and the standard error inflated 2.95x. Expect that factor to be near 1 here,
    and treat it as a check on the claim rather than an assumption.

    The cost threshold is not assumed either. The census measured that 88.5% of
    quintile 1 survives from one month to the next, so 11.5% turns over and the
    effective cost is 0.40% x 0.115 = 0.046% a month.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    last = _parse_date(end)
    judged_from = _parse_date(oos_from)
    if last is None or judged_from is None:
        raise typer.BadParameter("--end and --oos-from must be YYYY-MM-DD.")
    if last >= judged_from:
        raise typer.BadParameter(
            f"--end ({last}) は --oos-from ({judged_from}) より前でなければならない。"
            "判定期間を検出力の推定に混ぜると、平均を見ていなくても期間を選べてしまう。"
        )

    database = Database()
    database.create_all()
    console.print(
        f"分散だけを 最初 〜 {last} から推定する。[bold]平均は計算しないし、出さない。[/]"
    )

    try:
        series = build_lowvol_series(
            database,
            Period.ALL,
            benchmark=benchmark,
            end=last,
            window=window,
            min_turnover=min_turnover,
            min_symbols=min_symbols,
        )
        judged = build_lowvol_series(
            database,
            Period.ALL,
            benchmark=benchmark,
            start=judged_from,
            window=window,
            min_turnover=min_turnover,
            min_symbols=min_symbols,
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc

    console.print(
        f"推定に使う月: [bold]{len(series.months):,}[/]"
        f"（{series.months[0]} 〜 {series.months[-1]}）、"
        f"1ヶ月あたり中央値 {int(median(series.counts))} 銘柄。"
    )
    console.print(
        f"判定に使える月: [bold]{len(judged.months):,}[/]"
        f"（{judged.months[0]} 〜 {judged.months[-1]}）。"
    )
    console.print(
        f"[dim]薄くて落とした月 {series.excluded_thin_month + judged.excluded_thin_month}、"
        f"不連続をまたいで落とした銘柄月 "
        f"{series.excluded_discontinuity + judged.excluded_discontinuity:,}。[/dim]"
    )

    target = len(judged.months)
    table = Table(title="重なりを織り込んだ検出力（平均は含まない）")
    for column in (
        "指標",
        "月次SD",
        "上位1%除去",
        "重なりの膨張",
        "OOS の標準誤差",
        f"t≥{TARGET_T}",
    ):
        table.add_column(column, justify="right" if column != "指標" else "left")

    # β は**判定に使わない期間**から推定して固定する。共分散の比なので平均は
    # 返らないが、判定期間から取れば期間を選んだことになる。
    try:
        beta = series.beta_to_benchmark()
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc
    console.print(
        f"分位1のβ（推定期間で固定）: [bold]{beta:.3f}[/]。"
        "[dim]1 より小さいほど、生のリターン差に「市場が動いた分」が混ざる。[/dim]"
    )

    needed: dict[str, float] = {}
    for label, values in (
        ("分位1 − ベンチ（生）", series.long_only()),
        ("分位1 − β×ベンチ", series.beta_adjusted(beta)),
        ("分位1 − 全分位平均", series.vs_average()),
        ("分位1 − 分位5", series.long_short()),
    ):
        estimate = estimate_power(values, lags=lags)
        trimmed, _dropped = trimmed_variance(values, fraction=0.01)
        needed[label] = estimate.detectable(target)
        table.add_row(
            label,
            f"{estimate.daily_sd * 100:.2f}%",
            f"{trimmed**0.5 * 100:.2f}%",
            f"{estimate.inflation:.2f}x",
            f"{estimate.standard_error(target) * 100:.2f}%",
            f"[bold]{needed[label] * 100:.2f}%[/]",
        )
    console.print(table)
    console.print(
        "[dim]月次リバランスなので窓が重ならない。**膨張が 1 に近ければ、この説を"
        "選んだ理由の一つが数字で確かめられたことになる**（#6 は 2.95倍）。[/dim]"
    )

    # **主要指標は α（β引き）である。** 生の差は副次。ここを取り違えると、
    # 下の「検出できない帯」が別の指標の帯になる。
    primary = needed["分位1 − β×ベンチ"]
    secondary = needed["分位1 − ベンチ（生）"]
    console.print(
        "[dim]**仮説は「リスク調整後で高い」と言っている。** 生の差は、効果と"
        "「市場への感応度が低いこと」を混ぜて測る。β を引いた行がどれだけ小さく"
        "なるかが、指標を変える価値そのものである。[/dim]"
    )
    if primary < secondary:
        console.print(
            f"[green]β を引くと必要な差が {secondary * 100:.2f}% → "
            f"{primary * 100:.2f}% に下がる[/]（年 {secondary * 12 * 100:.1f}% → "
            f"年 {primary * 12 * 100:.1f}%）。"
        )

    console.print()
    console.print(
        f"費用のしきい値は [bold]{COST_PER_MONTH * 100:.3f}%／月[/]"
        f"（年 {COST_PER_MONTH * 12 * 100:.2f}%）。"
        "0.40%／往復 × 実測の入れ替え 11.5%／月。**仮定ではなくセンサスの実測値。**"
    )
    if primary > COST_PER_MONTH:
        console.print(
            f"[yellow]α で必要な差 {primary * 100:.2f}% が、しきい値 "
            f"{COST_PER_MONTH * 100:.3f}% を上回る。[/]\n"
            f"  **{COST_PER_MONTH * 100:.3f}% 〜 {primary * 100:.2f}% は、儲かるが"
            "検出できない帯である。** #6 と同じ形の限界なので、読み方の表に書く。"
        )
    else:
        console.print(
            f"[green]必要な差 {primary * 100:.2f}% が、しきい値を下回る。[/] "
            "費用を賄う水準の効果なら検出できる。"
        )
    console.print(
        "[dim]この数字は「どれだけ大きければ検出できるか」であって、"
        "「どれだけ出るか」ではない。後者は判定でしか分からない。[/dim]"
    )


@app.command(name="lowvol-run")
def lowvol_run(
    period: str = typer.Option("oos", "--period", help="oos | is. Judgment is oos, once."),
    oos_from: str = typer.Option("2014-01-01", "--oos-from", help="First judged month."),
    beta: float = typer.Option(SEALED_BETA, "--beta", help="Sealed beta from 2002-2013."),
    window: int = typer.Option(DEFAULT_WINDOW, "--window", help="Volatility window in sessions."),
    min_symbols: int = typer.Option(MIN_SYMBOLS_PER_MONTH, "--min-symbols", help="Per month."),
    min_turnover: float = typer.Option(MIN_TURNOVER, "--min-turnover", help="Liquidity floor."),
    lags: int = typer.Option(LOWVOL_LAGS, "--lags", help="Newey-West lags, in months."),
    benchmark: str = typer.Option(BENCHMARK, "--benchmark", help="Sets the calendar."),
) -> None:
    """Run the sealed low-volatility test and apply the sealed reading.

    ``docs/PREREG_LOWVOL_JP.md`` was sealed on 2026-09-04 without one low-vol
    return having been looked at. **This is the single judgment that bought.**

    The primary measure is alpha - quintile 1 minus beta times the benchmark,
    with beta fixed at 0.542 from 2002-2013. That matches what the hypothesis
    claims ("higher risk-adjusted"), and the raw difference is reported beside
    it because the two answer different questions: with beta at 0.542, holding
    quintile 1 beats the index only when alpha exceeds 0.458 times the market's
    return. In a rising market a positive alpha can still lose to the index.

    The verdict is applied in code from section 16's table, not read off by a
    person.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    try:
        chosen = Period(period.strip().lower())
    except ValueError as exc:
        raise typer.BadParameter(f"--period must be oos or is; got {period!r}.") from exc
    judged_from = _parse_date(oos_from)
    if judged_from is None:
        raise typer.BadParameter(f"--oos-from must be YYYY-MM-DD; got {oos_from!r}.")

    database = Database()
    database.create_all()
    console.print(f"[bold]{chosen.value.upper()}[/] で回す。β は封印済みの {beta:.3f}。")
    if chosen is Period.OOS:
        console.print(
            "[bold yellow]これは封印済みの判定である。一度だけ回す。[/] "
            "結果を見てから条件を変えない。"
        )

    try:
        series = build_lowvol_series(
            database,
            Period.ALL,
            benchmark=benchmark,
            start=judged_from,
            window=window,
            min_turnover=min_turnover,
            min_symbols=min_symbols,
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc

    console.print(
        f"{len(series.months):,} ヶ月（{series.months[0]} 〜 {series.months[-1]}）、"
        f"1ヶ月あたり中央値 {int(median(series.counts))} 銘柄"
        f"（1分位あたり約 {int(median(series.counts)) // 5}）。"
        f"薄くて落とした月 {series.excluded_thin_month}、"
        f"不連続で落とした銘柄月 {series.excluded_discontinuity:,}。"
    )

    shape = Table(title="分位ごとの平均リターン（月次、ベンチマーク差引き前）")
    for column in ("分位1（最も穏やか）", "分位2", "分位3", "分位4", "分位5", "ベンチマーク"):
        shape.add_column(column, justify="right")
    means = [
        sum(row[index] for row in series.quantiles) / len(series.quantiles) for index in range(5)
    ]
    bench_mean = sum(series.benchmark) / len(series.benchmark)
    shape.add_row(*[f"{value * 100:+.2f}%" for value in means], f"{bench_mean * 100:+.2f}%")
    console.print(shape)

    outcome = judge(series.beta_adjusted(beta), lags=lags)
    result, reading = lowvol_verdict(outcome.mean, outcome.t_statistic)

    headline = Table(title="主要指標：α（分位1 − β×ベンチマーク）")
    for column in ("月数", "α", "標準誤差(NW)", "t", "費用しきい値", "必要な差(事前)"):
        headline.add_column(column, justify="right")
    headline.add_row(
        f"{outcome.days:,}",
        f"[bold]{outcome.mean * 100:+.3f}%[/]",
        f"{outcome.standard_error * 100:.3f}%",
        f"[bold]{outcome.t_statistic:+.2f}[/]",
        f"{COST_PER_MONTH * 100:.3f}%",
        f"{LOWVOL_DETECTABLE * 100:.2f}%",
    )
    console.print(headline)

    colour = "green" if result == LOWVOL_PASS else "yellow"
    console.print()
    console.print(f"[bold {colour}]判定：{result}[/]")
    console.print(f"  {reading}")
    console.print(
        "[dim]この読み方は 2026-09-04 に封印した表（PREREG_LOWVOL_JP.md §16）を"
        "当てはめたものである。結果を見てから決めていない。[/]"
    )

    # **α が何と比べた差なのかを、判定のたびに書く。** 無リスク金利を 0 と
    # 置いているので、α は「指数を β の比率で持ち、残りを現金にした場合」との
    # 差そのものである。100%指数との比較は別の問いで、それは生の差が答える。
    raw = judge(series.long_only(), lags=lags)
    break_even = break_even_alpha(beta, bench_mean)
    console.print()
    console.print(
        f"[bold]α は何と比べた差か（指数 {beta * 100:.1f}% ＋ 現金 {(1 - beta) * 100:.1f}%）[/]"
    )
    compare = Table()
    for column in (
        "この期間の市場",
        "α（対 β揃えの代替案）",
        "生の差（対 100%指数）",
        "100%指数超えに要る α",
    ):
        compare.add_column(column, justify="right")
    compare.add_row(
        f"{bench_mean * 100:+.2f}%／月",
        f"[bold]{outcome.mean * 100:+.3f}%[/]",
        f"{raw.mean * 100:+.3f}%（t {raw.t_statistic:+.2f}）",
        f"{break_even * 100:+.3f}%",
    )
    console.print(compare)
    console.print(
        f"[dim]無リスク金利を 0 と置いているので、α ＝ 分位1 − (指数 {beta * 100:.1f}% ＋ "
        f"現金 {(1 - beta) * 100:.1f}%)。**α>0 は「β を揃えた最も素朴な代替案に勝った」"
        "であって「100%指数に勝った」ではない。** 後者は生の差が答える別の問いで、"
        "判定には使わない（PREREG_LOWVOL_JP.md §17）。[/dim]"
    )

    first = [row[0] for row in series.quantiles]
    # **判定基準に含めない。** 含めれば2つ目の基準になり、多重検定になる。
    risk = Table(title="下落の浅さ（記録のみ。判定基準に含めない）")
    for column in ("", "月次SD", "最大下落"):
        risk.add_column(column, justify="right" if column else "left")
    risk.add_row(
        "分位1",
        f"{stdev(first) * 100:.2f}%",
        f"{max_drawdown(first) * 100:.1f}%",
    )
    risk.add_row(
        "ベンチマーク",
        f"{stdev(series.benchmark) * 100:.2f}%",
        f"{max_drawdown(series.benchmark) * 100:.1f}%",
    )
    console.print(risk)

    extra = Table(title="そのほかの副次指標")
    for column in ("指標", "平均", "t"):
        extra.add_column(column, justify="left" if column == "指標" else "right")
    average = judge(series.vs_average(), lags=lags)
    extra.add_row(
        "分位1 − 全分位平均", f"{average.mean * 100:+.3f}%", f"{average.t_statistic:+.2f}"
    )
    spread = judge(series.long_short(), lags=lags)
    extra.add_row("分位1 − 分位5", f"{spread.mean * 100:+.3f}%", f"{spread.t_statistic:+.2f}")
    extra.add_row(
        "費用 1.5倍で見たとき",
        f"{(outcome.mean - COST_PER_MONTH * 0.5) * 100:+.3f}%",
        "[dim]—[/]",
    )
    console.print(extra)


@app.command(name="lowvol-census")
def lowvol_census(
    period: str = typer.Option("all", "--period", help="is | oos | all."),
    windows: str = typer.Option(
        ",".join(str(w) for w in VOLATILITY_WINDOWS),
        "--windows",
        help="Comma-separated volatility windows in sessions.",
    ),
    min_turnover: float = typer.Option(MIN_TURNOVER, "--min-turnover", help="Liquidity floor."),
    min_symbols: int = typer.Option(
        100, "--min-symbols", help="Months below this many names are counted as thin."
    ),
    benchmark: str = typer.Option(BENCHMARK, "--benchmark", help="Sets the calendar."),
) -> None:
    """Count what a low-volatility test would have to work with.

    **No returns are computed.** This runs before the registration is written -
    the order the two earnings-drift registrations did not follow, and the one
    that worked for reversal.

    Three things this has to settle before anything is sealed.

    1. **Which measurement window.** 60, 120 or 250 sessions are all counted.
       A longer window demands more history, so fewer names qualify. Choosing
       on counts is not an after-the-fact choice - no return has been seen.
    2. **Whether the quantiles tilt by size.** On reversal the expected
       small-cap tilt turned out flat. Measure, then say.
    3. **Whether the quantiles tilt by sector.** Low volatility is reported to
       concentrate in domestic defensives. If it does, part of what gets
       measured is a sector return, and a sector-neutral variant belongs in the
       registration as a secondary.

    Monthly rebalancing, so observations are symbol-months rather than
    symbol-days. That is what removes the overlap inflation (2.95x) that
    reversal carried.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    try:
        chosen = Period(period.strip().lower())
    except ValueError as exc:
        raise typer.BadParameter(f"--period must be is, oos or all; got {period!r}.") from exc
    try:
        wanted = tuple(int(part) for part in windows.split(",") if part.strip())
    except ValueError as exc:
        raise typer.BadParameter(f"--windows must be integers; got {windows!r}.") from exc
    if not wanted:
        raise typer.BadParameter("--windows must name at least one window.")

    database = Database()
    database.create_all()
    console.print("[bold]リターンは1つも計算しない。[/] 数えるのは件数と分布だけ。")

    try:
        results = run_lowvol_census(
            database,
            chosen,
            benchmark=benchmark,
            min_turnover=min_turnover,
            windows=wanted,
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc

    counts = Table(title="測定窓ごとの母集団（銘柄×月）")
    for column in (
        "窓",
        "組み替え日",
        "観測",
        "1分位あたり",
        "履歴不足",
        "薄い",
        "窓が無い",
        "不連続",
    ):
        counts.add_column(column, justify="right")
    for census in results:
        counts.add_row(
            f"{census.window}日",
            f"{census.months:,}",
            f"[bold]{census.observations:,}[/]",
            f"[bold]{census.per_quantile:,}[/]",
            f"{census.excluded_no_history:,}",
            f"{census.excluded_thin:,}",
            f"{census.excluded_no_window:,}",
            f"{census.excluded_discontinuity:,}",
        )
    console.print(counts)
    console.print(
        "[dim]窓が長いほど履歴を要求するので観測が減る。**この減り方を見て窓を"
        "決める。** リターンを見ていないので、事後的な選択にはならない。[/dim]"
    )

    # **この説を選んだ理由そのものを測る。** 月次リバランスでも構成が変わらない
    # なら、実効費用は残存率で割られる。当て推量で登録すると判定の意味が変わる。
    cost = Table(title="費用の前提を決める材料")
    for column in ("窓", "分位1に残る割合", "毎月の入れ替え", f"薄い月(<{min_symbols})", "全月数"):
        cost.add_column(column, justify="right" if column != "窓" else "left")
    for census in results:
        kept, compared = census.quantile_persistence()
        thin, total = census.thin_months(min_symbols)
        cost.add_row(
            f"{census.window}日",
            "—" if compared == 0 else f"[bold]{kept * 100:.1f}%[/]",
            "—" if compared == 0 else f"{(1 - kept) * 100:.1f}%",
            f"{thin:,}",
            f"{total:,}",
        )
    console.print(cost)
    console.print(
        "[dim]残存率が高いほど、月次でも実際の売買は少ない。**#6 は20営業日ごとに"
        "全入れ替えで 0.40%／回だった。** ここで8割残るなら実効費用はその2割になる。"
        "薄い月は分位が数銘柄になるので、最低銘柄数を封印前に決める。[/dim]"
    )

    for census in results:
        console.print()
        console.print(f"[bold]{census.window}日窓[/]")

        breadth = Table(title="1ヶ月あたりの通過銘柄数")
        for label, _value in census.breadth():
            breadth.add_column(label, justify="right")
        breadth.add_row(*[f"{value:,}" for _label, value in census.breadth()])
        console.print(breadth)

        spread = Table(title="ボラティリティ（日次リターンの標準偏差）")
        for label, _value in census.volatility_quantiles():
            spread.add_column(label, justify="right")
        spread.add_row(*[f"{value * 100:.2f}%" for _label, value in census.volatility_quantiles()])
        console.print(spread)

        profile = census.turnover_profile()
        if profile:
            size = Table(title="分位ごとの売買代金の中央値（億円）")
            for label, _value in profile:
                size.add_column(label, justify="right")
            size.add_row(*[f"{value:.1f}" for _label, value in profile])
            console.print(size)

        sectors = census.sector_profile()
        if sectors:
            tilt = Table(title="分位ごとの業種構成（上位3つ）")
            tilt.add_column("分位")
            tilt.add_column("構成", overflow="fold")
            for bucket, share in sectors:
                tilt.add_row(
                    bucket, "  ".join(f"{name} {value * 100:.0f}%" for name, value in share)
                )
            console.print(tilt)

    console.print()
    console.print(
        "[dim]**分位1は低ボラ側（買う側）である。** 業種が偏っていれば、測って"
        "いるものの一部は業種のリターン差になる。業種中立版を副次に置くかどうかを"
        "この数字で決める。[/dim]"
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
        f"（暦が合わず落ちた銘柄日 {series.excluded_calendar:,}、"
        f"[bold]価格系列の不連続をまたいで落ちた銘柄日 "
        f"{series.excluded_discontinuity:,}[/]）"
    )
    _report_spread(series, per_day)

    if series.implausible:
        console.print(
            f"[yellow]フォワードリターンが ±100% を超えた銘柄日が "
            f"{series.implausible:,} 件ある。[/]\n"
            "  [dim]不連続の除外を通ったということは、**1日で ±50% を超える動きは"
            "含まれていない**。連続ストップ高を20営業日積み上げれば届く水準なので、"
            "これ自体は欠陥の証拠にならない。下の感度で効いているかどうかを見る。[/]"
        )

    target = oos_days or _oos_session_count(database, benchmark, holding)
    console.print(f"OOS の想定日数: [bold]{target:,}[/] 営業日（{OOS_FROM} 以降）")

    _report_daily_spread(series)

    table = Table(title="重なりを織り込んだ検出力（平均は含まない）")
    table.add_column("指標")
    for column in (
        "日次SD",
        "上位1%を除いたSD",
        "重なりの膨張",
        "OOS の標準誤差",
        f"t≥{TARGET_T} に必要な差",
    ):
        table.add_column(column, justify="right")

    verdicts: list[tuple[str, float]] = []
    for label, values in (
        ("分位1 − ベンチ（主要）", series.long_only()),
        ("分位1 − 分位5（副次）", series.long_short()),
    ):
        estimate = estimate_power(values, lags=lags)
        needed = estimate.detectable(target)
        verdicts.append((label, needed))
        trimmed, dropped = trimmed_variance(values, fraction=0.01)
        table.add_row(
            label,
            f"{estimate.daily_sd * 100:.2f}%",
            f"{trimmed**0.5 * 100:.2f}%",
            f"{estimate.inflation:.2f}x",
            f"{estimate.standard_error(target) * 100:.2f}%",
            f"[bold]{needed * 100:.2f}%[/]",
        )
    console.print(table)
    console.print(
        f"[dim]「上位1%を除いたSD」は感度であって推定量ではない（{dropped} 日を除外）。"
        "**全体のSDがこれの何倍もあるなら、分散は「毎日どれくらい散らばるか」ではなく"
        "「まれに何が起きるか」を測っている。**[/]"
    )

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


def _report_daily_spread(series: object) -> None:
    """Show the daily series itself - the object the variance belongs to.

    それまで見ていたのは分位平均の**材料**（銘柄日ごとのリターン）で、分散が
    付いているのは**分位平均そのもの**である。材料が正常でも、少数の日が桁違い
    なら分散はその日でできている。対象を取り違えていた。

    1分位に何銘柄入るかも一緒に出す。銘柄数が少ない日は、平均が個別銘柄の
    リターンそのものになるので、その日だけ機械的に散らばりが大きくなる。
    """
    values = series.long_only()  # type: ignore[attr-defined]
    counts = series.counts  # type: ignore[attr-defined]
    if not values:
        return

    ordered = sorted(values)

    def at(fraction: float) -> float:
        return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))]

    spread = Table(title="日次系列そのもの（分位1 − ベンチ）")
    cuts = (0.0, 0.01, 0.5, 0.99, 1.0)
    for label in ("最小", "p1", "p50", "p99", "最大"):
        spread.add_column(label, justify="right")
    spread.add_row(*[f"{at(min(cut, 0.999)) * 100:+.1f}%" for cut in cuts])
    console.print(spread)

    wild = sum(1 for value in values if abs(value) > 0.5)
    console.print(
        f"[dim]±50% を超えた営業日: {wild:,} / {len(values):,}。"
        f"1分位の銘柄数は 最小 {min(counts) // 5} ／ 中央値 "
        f"{int(median(counts)) // 5}。**銘柄数が少ない日は、平均が1銘柄の"
        "リターンそのものになる。**[/]"
    )


#: 窓がイベントを処分する先。**足すと引いた件数になる。**
#:
#: 数える側と出す側の両方がここを見る。**片方に足してもう片方に足し忘れると、
#: 合計が合わなくなって `EventSample` が落ちる。**
_DISPOSITIONS = (
    "used",
    "no_prices",
    "not_trading",
    "ended_early",
    "too_recent",
    "bad_leg",
    "no_benchmark",
)

#: 処分の日本語。**表の並びは `_DISPOSITIONS` と同じ順。**
_DISPOSITION_LABELS = {
    "used": "使えた",
    "no_prices": "価格が1本も無い銘柄（穴）",
    "not_trading": "その日に足が無い（上場前・廃止後・停止）",
    "ended_early": "上場廃止・停止で窓が切れた",
    "too_recent": "期間の端で窓が足りない",
    "bad_leg": "入る値か降りる値が欠測",
    "no_benchmark": "指数に対応する日が無い",
}


#: 何を引くか。**`index` は時価総額加重、`universe` は等加重。**
#:
#: **既定は管ごとに違う。** 事前登録が `1306` を指している説（#5・#8）は
#: `index` のままにする——**判定の出た説を、あとから別の相手で測り直さない。**
#: 対照は `universe` を既定にする。そこが直ったことを見る場所だからである。
_SUBTRACT_CHOICES = ("index", "universe")


def _event_inflation(mode: str) -> float:
    """Pick the measured inflation that matches what is being subtracted.

    **同じ管でも、引く相手を替えたら数字が動いた**（2026-09-18、どちらも400回・
    同じ種）。

    | 引く相手 | `t` の SD |
    |---|---|
    | `1306`（時価総額加重） | 0.94 |
    | 等加重の宇宙 | 1.09 |

    **測った条件と違う条件の数字を当てない。** 当てれば、線はもっともらしい
    まま根拠を失う。

    Args:
        mode: ``index`` か ``universe``。

    Returns:
        当てる膨張。

    Raises:
        typer.BadParameter: 知らない ``mode``。
    """
    from stock_ai.backtest.multiplicity import (
        MEASURED_INFLATION_EVENT,
        MEASURED_INFLATION_EVENT_INDEX,
    )

    known = {"index": MEASURED_INFLATION_EVENT_INDEX, "universe": MEASURED_INFLATION_EVENT}
    if mode not in known:
        raise typer.BadParameter(f"--subtract は {' か '.join(_SUBTRACT_CHOICES)}。")
    return known[mode]


def _universe_to_subtract(  # noqa: PLR0913 - 何で作ったかを全部受け取る
    database: Database,
    mode: str,
    holding: int,
    fraction: float = 1.0,
    seed: int = 0,
) -> object | None:
    """Build the equal-weighted benchmark when the mode asks for it.

    **引く相手を、持ち方と同じ加重にする。** `1306` は時価総額加重で、イベントの
    バスケットは等加重である。陰性対照では、その食い違いだけで**情報ゼロの並びが
    20営業日で +0.22% 勝っていた**（2026-09-17、400回）。

    Args:
        database: 価格の保存先。
        mode: ``index`` か ``universe``。
        holding: 保有営業日数。**イベント側と同じ値を渡すこと。**
        fraction: 引く相手を作るのに使う銘柄の割合。**診断用**——日は減らさず、
            引く相手の精度だけを落とす。
        seed: 間引きの種。

    Returns:
        ``universe`` なら :class:`UniverseBenchmark`、``index`` なら ``None``。

    Raises:
        typer.BadParameter: 知らない ``mode``。
    """
    from stock_ai.backtest.universe_benchmark import equal_weighted_windows

    if mode not in _SUBTRACT_CHOICES:
        raise typer.BadParameter(f"--subtract は {' か '.join(_SUBTRACT_CHOICES)}。")
    if mode == "index":
        return None

    console.print("[dim]等加重の引く相手を作っています（全銘柄の足を1度だけ読みます）...[/]")
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("銘柄を読む", total=None)

        def step(done: int, total: int) -> None:
            progress.update(task, completed=done, total=total)

        built = equal_weighted_windows(
            database, holding, progress=step, fraction=fraction, seed=seed
        )

    console.print(
        f"[dim]{len(built.window):,} 日ぶん（{built.symbols:,} 銘柄、窓 {holding} 営業日）。"
        "**引くのは時価総額加重の指数ではなく、等加重の宇宙である。**[/]"
    )
    for line in built.warnings():
        console.print(f"[yellow]{line}[/]")
    return built


def _report_event_disposition(sample: object, *, title: str) -> None:
    """Print what the event window kept and what it threw away.

    **捨てた件数を黙って捨てない**（2026-09-17）。#5・#8・陰性対照が同じ窓を
    使うので、**出す形も1つだけ置く。** 呼ぶ側で書き直すと、片方だけ直る。

    Args:
        sample: :class:`~stock_ai.backtest.event_window.EventSample`。
        title: 表の見出し。
    """
    table = Table(title=title)
    for column in ("処分", "件数", "割合"):
        table.add_column(column, overflow="fold")
    drawn = sample.drawn  # type: ignore[attr-defined]
    for key in _DISPOSITIONS:
        count = getattr(sample, key)
        share = count / drawn if drawn else 0.0
        table.add_row(_DISPOSITION_LABELS[key], f"{count:,}", f"{share:.1%}")
    table.add_row("[bold]引いた合計[/]", f"[bold]{drawn:,}[/]", "100.0%")
    console.print(table)

    # **銘柄側と指数側を別に出す。** 超過だけを見ていると、「銘柄が上がった」
    # のか「引く相手が上がらなかった」のかが分からない。
    console.print(
        f"[dim]銘柄側 {sample.stock_leg:+.2%}、指数側 {sample.bench_leg:+.2%}"  # type: ignore[attr-defined]
        f"、差 {sample.stock_leg - sample.bench_leg:+.2%}（1日あたり、窓ぶん）。[/]"  # type: ignore[attr-defined]
    )
    for line in sample.warnings():  # type: ignore[attr-defined]
        console.print(f"[yellow]{line}[/]")


#: #13 の「封印しない線」。**測る前にコミットした**（`docs/PREREG_TURN_OF_MONTH_JP.md` §0）。
#:
#: **売買しないので費用は引かない**が、将来売買するときのいちばん安い実装
#: （窓の中だけ持ち分を増やす、年12回、増分は資産の半分）の費用から置いた
#: ——`0.4% × 12 × 0.5 = 年 2.4%`。全部入って全部出るなら年 4.8% になる。
#:
#: **甘いほうを採った。** 実装をまだ選んでいないので、選んでいない実装のせいで
#: 閉じることのないようにする。**測ってから動かさない。**
TURN_OF_MONTH_FLOOR = 0.024


#: #12 の「封印しない線」。**測る前にコミットした**（`docs/PREREG_MOMENTUM_JP.md` §0）。
#:
#: 費用を賄えるかどうかの線である。#9 は入れ替わり 15.9%／月で費用 年0.76%
#: だったが、**モメンタムの順位はそれより速く動く**——12ヶ月の累積は毎月
#: いちばん古い月が落ちるので、株価が動かなくても順位が変わる。
#:
#: **測ってから動かさない。**
MOMENTUM_FLOOR = 0.02


#: #14 の「封印しない線」。**測る前にコミットした**（`docs/PREREG_JANUARY_JP.md` §0）。
#:
#: いちばん安い実装は**12月末に仕込んで1月末に外す**形で、**年1往復**である。
#: 両端がまるごと入れ替わるので入れ替わり率 1.0、往復 0.40% を掛けて **年 0.4%**。
#: **観測は年に1回なので、1月あたり 0.4% と年 0.4% は同じ数である。**
#:
#: **甘いほうを採った**（#13 と同じ扱い）。**測ってから動かさない。**
JANUARY_FLOOR = 0.004


#: 壁の下見で「薄い」と言う観測数。**これを下回ったら警告を出す。**
#:
#: 表に出るのは壁の高さ1つなので、**その裏に何観測あるかは見えない。**
#: 10 は暦から出した値ではなく、**「片手で数えられる」を超えるところ**に
#: 置いただけである——そう書いておく。
THIN_OBSERVATIONS = 10

#: 食い違った日を何日まで刷るか。**全部は刷らない。**
MAX_DISAGREEMENTS = 10

#: SD が裾でできていると言う比。**1% を落として、これだけ縮んだら警告。**
#:
#: `power.trimmed_variance` の言うとおり、**推定量ではなく感度である。**
#: 2 に根拠は無い——「半分以下になる」を数にしただけで、そう書いておく。
#: **壁の高さが数日で決まっているなら、その壁は当てにならない。**
TAIL_DRIVEN = 2.0


def _oos_session_count(database: Database, benchmark: str, holding: int) -> int:
    """Count the sessions the OOS test will have. Counts days, never values."""
    with database.session() as session:
        frame = PriceRepository(session).get_raw_prices(benchmark)
    if frame.empty:
        return 0
    days = [stamp.date() for stamp in frame.index if stamp.date() >= OOS_FROM]
    return max(0, len(days) - (holding + 1))


@app.command(name="edinet-reach")
def edinet_reach(
    years: int = typer.Option(16, "--years", help="How many years back to probe."),
    month: int = typer.Option(6, "--month", help="Month to probe each year (1-12)."),
    day: int = typer.Option(15, "--day", help="Day of month to probe."),
    pause: float = typer.Option(1.0, "--pause", help="Seconds between requests."),
) -> None:
    """Find out how far back EDINET actually serves documents.

    **The financial history stops at 58 months, and 58 months is five years.**
    That is the same number as the J-Quants rolling window, which makes it worth
    asking whether it is EDINET's limit or our own harvest setting. The two look
    identical from the database.

    One request per year, on a fixed day, counting what comes back. Nothing is
    stored and nothing is parsed beyond the count - this only answers "does the
    service still have documents from that date".

    A weekend or a holiday returns zero legitimately, so a zero is reported as
    "0 件" rather than "reached the limit". The boundary is where zeros start
    and never stop, not the first zero.

    Costs about a dozen requests. It settles whether the composite test has 58
    months to work with or twice that, and the standard error scales with the
    square root of that number.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    api_key = settings.edinet_api_key
    if api_key is None:
        console.print("[red].env に EDINET_API_KEY がない。[/] APIキー設定.bat で設定する。")
        raise typer.Exit(code=1)

    if not 1 <= month <= 12:
        raise typer.BadParameter(f"--month must be 1-12; got {month}.")

    today = dt.date.today()

    console.print(
        f"[bold]{years} 年ぶん、毎年 {month:02d}-{day:02d} を1日ずつ叩く。[/] "
        "件数を数えるだけで、中身は読まないし保存もしない。"
    )
    console.print(
        "[dim]土日祝は 0 件が正しい。**境目は「0 が始まってそのまま続く所」**で、"
        "最初の 0 ではない。[/dim]"
    )
    console.print()

    table = Table(title="EDINET はどこまで遡れるか")
    for column in ("日付", "曜日", "件数", "結果"):
        table.add_column(column, justify="left" if column in ("日付", "結果") else "right")

    held: list[dt.date] = []
    out_of_range: list[dt.date] = []
    failed = 0
    weekday_names = "月火水木金土日"

    for offset in range(years):
        year = today.year - offset
        try:
            probe = dt.date(year, month, day)
        except ValueError:
            continue
        if probe >= today:
            continue
        try:
            reach = day_reach(api_key, probe)
        except RateLimitError:
            # 走行そのものの問題である。残りを同じ調子で叩いても同じ断りが返る。
            console.print(
                f"\n[yellow]レート制限に当たった（{year} 年まで確認）。[/] "
                "時間を置いて再実行すれば続きから見られる。"
            )
            break
        except DataError as exc:
            failed += 1
            table.add_row(probe.isoformat(), "", "[yellow]—[/]", f"[yellow]{exc}[/]")
            continue

        marker = weekday_names[probe.weekday()]
        # **0 を2種類に分ける。** 休日の 0 と「その日付を持っていない」0 は
        # 別の事実で、同じ行に見せると遡れる境目を読み違える。
        if reach.reason == REACH_OK:
            held.append(probe)
            table.add_row(probe.isoformat(), marker, f"{reach.count:,}", "[green]取れる[/]")
        elif reach.reason == REACH_NO_FILINGS:
            held.append(probe)
            table.add_row(probe.isoformat(), marker, "0", "[dim]0件（休日）[/dim]")
        elif reach.reason == REACH_OUT_OF_RANGE:
            out_of_range.append(probe)
            table.add_row(
                probe.isoformat(), marker, "—", f"[yellow]範囲外（status {reach.status}）[/]"
            )
        else:
            table.add_row(probe.isoformat(), marker, "—", "[yellow]不明[/]")
        if pause:
            time.sleep(pause)

    console.print(table)
    console.print()

    if not held:
        console.print("[red]1日ぶんも取れなかった。[/] 鍵か接続を先に確かめる。")
        raise typer.Exit(code=1)

    # **境目は「持っている最も古い日」である。** 休日の 0 も「持っている」に
    # 数える——提出が無かっただけで、その日付は範囲内にある。
    oldest = min(held)
    months = (today.year - oldest.year) * 12 + (today.month - oldest.month)
    console.print(
        f"書類が返った最も古い日は [bold]{oldest}[/]。"
        f"いまから [bold]{months:,}[/] ヶ月（{months / 12:.1f}年）遡れる。"
    )

    # **58ヶ月と比べる。** ここが今回の問い。
    current = 58
    console.print()
    if months > current * 1.2:
        console.print(
            f"[green]手元の財務データ（{current}ヶ月）より長い。[/] "
            f"**58ヶ月は EDINET の制約ではない。** harvest の窓を広げる余地がある。"
        )
        console.print(
            f"[dim]標準誤差は √({months}/{current}) = "
            f"{(months / current) ** 0.5:.2f} 倍**小さく**なる方向。[/dim]"
        )
    elif months < current * 0.8:
        console.print(
            f"[yellow]手元の財務データ（{current}ヶ月）より短い。[/] "
            "有報の「主要な経営指標等」は1本で5期ぶん持つので、**書類が取れる"
            "範囲より古い年まで埋められる。** そちらを数える必要がある。"
        )
    else:
        console.print(
            f"[yellow]手元の財務データ（{current}ヶ月）とほぼ同じ。[/] "
            "**58ヶ月は EDINET の制約である公算が高い。** ただし有報1本に5期ぶん"
            "入るので、書類の範囲＋4年までは埋められる。"
        )

    if out_of_range:
        newest_refused = max(out_of_range)
        console.print(
            f"[dim]範囲外と返ったうち最も新しい日は {newest_refused}。"
            f"**境目は {newest_refused} と {oldest} のあいだにある。** "
            "窓は毎日後ろへ動くので、古い側は待つほど失われる。[/dim]"
        )
    if failed:
        console.print(f"[yellow]{failed} 日は断られた。[/] 上の理由を読む。")


@app.command(name="margin-power")
def margin_power(  # noqa: PLR0913 - §0 が固定した条件をすべて受け取る
    directory: str = typer.Option(
        str(DEFAULT_ARCHIVE_DIR), "--dir", help="Where the archived originals live."
    ),
    rosters: str = typer.Option(
        str(DAILY_SNAPSHOT_DIR), "--rosters", help="Dated rosters, for the lending flag."
    ),
    benchmark: str = typer.Option(BENCHMARK, "--benchmark", help="Sets the calendar."),
    min_turnover: float = typer.Option(MIN_TURNOVER, "--min-turnover", help="Liquidity floor."),
    holding: int | None = typer.Option(None, "--holding", help="Override the window from §3."),
    subtract: str = typer.Option(
        "index", "--subtract", help="What to deduct: index (as the prereg says) or universe."
    ),
    limit: int | None = typer.Option(None, "--limit", help="Read only the first N originals."),
) -> None:
    """Measure the IS spread for #8, so the gate can be applied.

    **判定ではない。** 見るのは IS（原本が覆う期間の前半）だけで、OOS には
    1日も触れない。

    出すのは §0 の空欄——**1イベントあたりのSD**、**重なりの膨張**、そして
    **見込み**である。保有窓は `margin-census` と同じ式から出る。

    **測る前にコミットした線がある**（事前登録 §0）——「IS の1イベントあたり
    平均超過リターンの片側95%下限が **1.2%** を下回ったら封印しない」。
    往復費用 0.4% の3倍である。**下回ればここで終わる。線は動かさない。**
    """
    from stock_ai.backtest.event_window import event_sample
    from stock_ai.backtest.margin_census import census, lending_index, spells
    from stock_ai.backtest.multiplicity import (
        HYPOTHESIS_BUDGET,
        calibrated_t,
    )
    from stock_ai.backtest.pead import TURNOVER_WINDOW
    from stock_ai.backtest.reversal import COST_ROUND_TRIP
    from stock_ai.data.jquants_margin import from_archive as margin_from_archive
    from stock_ai.data.schema import VOLUME

    settings = get_settings()
    configure_logging(settings.log_level)

    source = Path(directory)
    if not source.is_dir():
        console.print(f"[red]{source} が無い。[/] **原本が要る。**")
        raise typer.Exit(code=1)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("原本を読む", total=None)

        def step(index: int, total: int, key: str) -> None:
            progress.update(task, total=total, completed=index)

        alerts = margin_from_archive(source, limit=limit, progress=step)

    if not alerts:
        console.print(f"[red]{source} に `/markets/margin-alert` の原本が無い。[/]")
        raise typer.Exit(code=1)

    database = Database()
    database.create_all()
    with database.session() as session:
        price_repo = PriceRepository(session)
        bench = price_repo.get_raw_prices(benchmark)
        if bench.empty:
            console.print(f"[red]ベンチマーク {benchmark!r} の価格が無い。[/]")
            raise typer.Exit(code=1)
        calendar = [stamp.date() for stamp in bench.index]
        liquid: dict[tuple[str, dt.date], bool] = {}
        for symbol in sorted({alert.symbol for alert in alerts}):
            raw = price_repo.get_raw_prices(symbol)
            if raw.empty:
                continue
            rolling = (raw[CLOSE] * raw[VOLUME]).rolling(TURNOVER_WINDOW).mean().shift(1).dropna()
            for stamp, value in rolling.items():
                liquid[(symbol, stamp.date())] = bool(value >= min_turnover)

    lending = lending_index(Path(rosters))
    counted = census(
        alerts,
        calendar,
        lending_on=lending,
        liquid_on=lambda symbol, on: liquid.get((symbol, on), False),
    )
    if not counted.events_is:
        console.print("[red]IS に使えるイベントが無い。[/] `margin-census` を先に見ること。")
        raise typer.Exit(code=1)

    window = holding or counted.window
    if holding is not None:
        console.print(
            f"[yellow]窓を {holding} に上書きした。[/] "
            "**§3 の式から出る窓は "
            f"{counted.window} である。上書きしたまま封印しない。**"
        )

    console.print(
        f"[dim]IS は {counted.split_on} まで（{counted.events_is} 件）。"
        f"OOS（{counted.events_oos} 件）には1日も触れない。窓は {window} 営業日。[/]"
    )

    kept = [
        (spell.symbol, spell.onset)
        for spell in spells(alerts)
        if lending(spell.symbol, spell.onset) and liquid.get((spell.symbol, spell.onset), False)
    ]
    # **事前登録は `1306` を指している。** 既定を変えない（上と同じ理由）。
    deducted = _universe_to_subtract(database, subtract, window)
    sample = event_sample(
        database,
        kept,
        holding=window,
        benchmark=benchmark,
        until=counted.split_on,
        subtract=deducted,
    )
    values = sample.values
    # **捨てた件数を黙って捨てない**（2026-09-17）。
    _report_event_disposition(sample, title="窓を当てた結果（IS のみ・件数）")
    if len(values) < 2:
        console.print(f"[red]値動きの取れたイベントが {len(values)} 件しかない。[/]")
        raise typer.Exit(code=1)

    # **ショートの取り高に直す。** 仮説は超過リターンが負だと言っている
    # （§1）。符号の反転はここ1箇所だけで行う。費用は往復 0.4%（§4）。
    take = [-value - COST_ROUND_TRIP for value in values]

    # **膨張は「管 × 引く相手」ごとに測ってある。** 引く相手を替えると数字が
    # 動いた（0.94 → 1.09）ので、**いま引いている相手の値を当てる。**
    target = calibrated_t(HYPOTHESIS_BUDGET, inflation=_event_inflation(subtract))
    # **独立な観測は「日」である**（2026-09-17 に #5 で見つけた形）。
    periods = counted.days_oos or counted.events_oos
    # **`periods` が何年ぶんかを渡す**（2026-09-20）。
    span = _judgement_years(counted.split_on, counted.last)
    _event_gate(
        take,
        periods=periods,
        target=target,
        committed=3 * COST_ROUND_TRIP,
        holding=window,
        reach=f"**OOS の {counted.events_oos:,} 件が固まった日数。件数ではない**",
        period_years=span,
        footnote=(
            f"[dim]見込みを測った IS は {counted.events_is:,} 件（絞り込んだ後）。"
            "**いちばん減らしているのは貸借の絞りだが、空売りできない銘柄で"
            "ショートを検証しないための絞りなので動かさない。**[/]"
        ),
        side="ショート",
    )


@app.command(name="revision-census")
def revision_census_upward(
    directory: str = typer.Option(
        str(DEFAULT_ARCHIVE_DIR), "--dir", help="Where the archived originals live."
    ),
    min_turnover: float = typer.Option(MIN_TURNOVER, "--min-turnover", help="Liquidity floor."),
    limit: int | None = typer.Option(None, "--limit", help="Read only the first N originals."),
) -> None:
    """Count the upward forecast revisions - no return is computed here.

    **#5 の §0 の表を埋める。**

    **リターンを1つも計算しない。** だから判定を消費しない。

    **決算と同じ日に出た修正は外す**（§2）。#2・#3 と同じ日付集合を使わない
    ためで、**それが再開の前提そのものである。**

    読めなかったものを 0 に落とさない。会社予想が読めない行、前回の予想が無い
    行、会計年度末が読めない行は**それぞれ数えて出す。** まとめて「修正なし」に
    すると、**読めていないことが「修正が無かった」に化ける。**
    """
    from stock_ai.backtest.pead import TURNOVER_WINDOW
    from stock_ai.backtest.revision_census import REVISION_TYPE, UPWARD_MIN, census
    from stock_ai.data.jquants_bulk import records_from_csv
    from stock_ai.data.schema import VOLUME
    from stock_ai.data.universe import four_digit_code

    settings = get_settings()
    configure_logging(settings.log_level)

    source = Path(directory)
    keys = [key for key in sorted(read_manifest(source)) if endpoint_of(key) == "/fins/summary"]
    if limit is not None:
        keys = keys[:limit]
    if not keys:
        console.print(
            f"[red]{source} に `/fins/summary` の原本が無い。[/] "
            "**一括で保存したはずのものである。**"
        )
        raise typer.Exit(code=1)

    console.print("[dim]リターンは1つも計算しない。判定は消費しない。[/]")
    items = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("原本を読む", total=len(keys))
        for index, key in enumerate(keys, start=1):
            progress.update(task, completed=index)
            try:
                # **`FS` を経由しない。** 一括 CSV では `CurFYEn` も `FNP` も
                # **原本の列そのもの**で、`FS` には鍵が1つも入っていない
                # （2026-09-16、72,156 件で確認）。`parse_details` は `FS` の
                # 中身しか `values` に入れないので、そこを読むと全滅する。
                items.extend(records_from_csv(read_archived(path_for(source, key))))
            except Exception as exc:  # noqa: BLE001 - どこで読めないかが記録に値する
                console.print(f"[yellow]{key}: {type(exc).__name__}[/]")

    database = Database()
    database.create_all()
    with database.session() as session:
        price_repo = PriceRepository(session)
        liquid: dict[tuple[str, dt.date], bool] = {}
        symbols = sorted(
            {
                code
                for row in items
                if (row.get("DocType") or "").strip() == REVISION_TYPE
                and (code := four_digit_code((row.get("Code") or "").strip())) is not None
            }
        )
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("売買代金を読む", total=len(symbols))
            for index, symbol in enumerate(symbols, start=1):
                progress.update(task, completed=index)
                raw = price_repo.get_raw_prices(symbol)
                if raw.empty:
                    continue
                rolling = (
                    (raw[CLOSE] * raw[VOLUME]).rolling(TURNOVER_WINDOW).mean().shift(1).dropna()
                )
                for stamp, value in rolling.items():
                    liquid[(symbol, stamp.date())] = bool(value >= min_turnover)

    found = census(items, liquid_on=lambda symbol, on: liquid.get((symbol, on), False))
    console.print(found.summary())

    read = found.readability
    table = Table(title="§0 の件数センサス（**リターンは入っていない**）")
    for column in ("測るもの", "値"):
        table.add_column(column, overflow="fold")
    table.add_row(f"`{REVISION_TYPE}` の行", f"{read.rows:,}")
    table.add_row("　会社予想が読めなかった", f"{read.no_forecast:,}")
    table.add_row("　会計年度末が読めなかった", f"{read.no_fiscal_year:,}")
    table.add_row("　前回の予想が無い", f"{read.no_previous:,}")
    table.add_row(f"　下方修正（−{UPWARD_MIN:.0%} 以下）", f"{read.downward:,}")
    table.add_row(f"　幅が小さい（±{UPWARD_MIN:.0%} 未満）", f"{read.too_small:,}")
    table.add_row(f"[bold]　上方修正（+{UPWARD_MIN:.0%} 以上）[/]", f"[bold]{read.upward:,}[/]")
    table.add_row("うち決算と同じ日（**外す**）", f"{found.on_statement_day:,}")
    table.add_row("決算と別の日", f"{found.standalone:,}")
    table.add_row("流動性の下限を通した後", f"{found.after_liquidity:,}")
    table.add_row("　うち IS（推定に使う）", f"{found.events_is:,}")
    table.add_row("[bold]　うち OOS（判定。§0 の期数）[/]", f"[bold]{found.events_oos:,}[/]")
    table.add_row("イベントのあった日", f"{found.days_with_events:,}")
    table.add_row("上位1割の日の占有", f"{found.busiest_share:.0%}")
    table.add_row("同じ日に重なる修正（中央値）", f"{found.same_day_median:,}")
    table.add_row("修正幅（中央値）", f"{found.change_median:+.1%}")
    console.print(table)

    if found.by_year:
        years = Table(title="年ごとの上方修正（**平均だけ見ない**）")
        for column in ("年", "件数"):
            years.add_column(column, justify="right")
        for year, count in found.by_year.items():
            years.add_row(str(year), f"{count:,}")
        console.print(years)

    # **33% が読めないまま先に進まない。** どういう行が落ちているのかを出す。
    #
    # **`FNC…` は単体（非連結）である。** `F…` は連結。ここは数えるだけで、
    # **読み替えない**——「連結と単体を取り違える」は名指しで戒めてある形である。
    profile = read.missing_profile()
    if profile:
        missing = Table(title=f"会社予想が読めなかった {read.no_forecast:,} 件の中身")
        for column in ("代わりに埋まっていた列", "件数", "割合"):
            missing.add_column(column, justify="left" if column.startswith("代わり") else "right")
        for name, count, share in profile[:12]:
            note = "（単体）" if name.startswith("FNC") else ""
            missing.add_row(f"{name}{note}", f"{count:,}", f"{share:.0%}")
        console.print(missing)
        if read.missing_by_year:
            span = sorted(read.missing_by_year)
            console.print(
                f"[dim]読めなかった行の年: {span[0]} 〜 {span[-1]}、"
                f"最多は {max(read.missing_by_year, key=lambda y: read.missing_by_year[y])} 年"
                f"（{max(read.missing_by_year.values()):,} 件）。"
                "**時代に偏っていないかを見る。**[/]"
            )

    for line in found.warnings():
        console.print(f"[yellow]{line}[/]")

    # **`FS` の外も見せる。** 探しているものが `FS` に無いとき、`FS` の鍵を
    # いくら並べても答えにならない。原本そのものの列名を出す。
    #
    # 会計年度末が 72,156 件すべてで読めなかったとき、`FS` の鍵しか出して
    # いなかった（2026-09-16）。**読み口が捨てている列は、読み口からは見えない。**
    if read.rows and (read.no_fiscal_year == read.rows or read.no_forecast == read.rows):
        sample = next(iter(records_from_csv(read_archived(path_for(source, keys[0])))), {})
        console.print(
            f"[yellow]原本そのものの列: {'、'.join(sorted(sample))}。"
            "**`FS` の中だけを探していないか。**[/]"
        )

    console.print()
    console.print(
        f"[dim]IS と OOS の境は {found.split_on}（原本が覆う期間の真ん中）。"
        "次は散らばりの実測（§0）で、そこで初めてリターンを触る。[/]"
    )


@app.command(name="passing")
def passing(
    into: str | None = typer.Option(None, "--write", help="Regenerate docs/PASSING.md."),
) -> None:
    """Show what a passing hypothesis would have to return, and the five rules.

    **数字を書き写さない。** 線が変われば要るリターンも全部変わる——実際
    2026-09-17 に 3.02 → 3.39 に動いた。**書き写した数字は、古いまま
    もっともらしく見え続ける。** ここは測った散らばりから、そのつど計算する。

    `--write` を付けると `docs/PASSING.md` を作り直す。**あの文書は生成物で
    ある。** 手で直すと、どちらが本当か分からなくなる。
    """
    from stock_ai.backtest.multiplicity import (
        HYPOTHESIS_BUDGET,
        MEASURED_INFLATION,
        MEASURED_INFLATION_EVENT,
        calibrated_t,
        required_t,
    )
    from stock_ai.backtest.passing import CONDITIONS, SHAPES

    settings = get_settings()
    configure_logging(settings.log_level)

    target = calibrated_t(HYPOTHESIS_BUDGET)
    lines = _passing_lines(target, SHAPES, CONDITIONS, HYPOTHESIS_BUDGET, MEASURED_INFLATION)

    table = Table(title="合格に要るリターン（**いまの線から計算した値**）")
    for column in ("設計", "1期あたりのSD", "合格に要る大きさ", "要る情報比", "どこに書いてあるか"):
        table.add_column(column, overflow="fold")
    for shape in SHAPES:
        # **線は管ごとに違う。** 1つの線を全部に当てると、イベント型の行に
        # 月次で測った膨張が乗る（2026-09-18 まで、そうなっていた）。
        own = shape.line()
        annual = shape.required_annual(own)
        need = f"年 {annual:.1%}" if annual else f"1{shape.unit} {shape.required(own):.2%}"
        table.add_row(
            shape.name,
            f"{shape.sd:.2%}／{shape.unit}",
            f"[bold]{need}[/]",
            _ir_cell(shape),
            shape.source,
        )
    console.print(table)
    console.print(
        f"[dim]月次の線は `t ≥ {target:.2f}`（予算 {HYPOTHESIS_BUDGET} 本の "
        f"{required_t(HYPOTHESIS_BUDGET):.2f} に、対照で測った膨張 "
        f"{MEASURED_INFLATION:.2f} を掛けた）。**イベント型は別の管なので "
        f"`t ≥ {calibrated_t(HYPOTHESIS_BUDGET, inflation=MEASURED_INFLATION_EVENT):.2f}`。**"
        f"**指数に対して、手数料を引いた後で、{SHAPES[0].periods / 12:.1f}年つづける。**[/]"
    )
    console.print(
        "[dim]イベント型は**年率に直さない**——資金をどれだけ張るかを決める必要が"
        "あり、事前登録にその指定が無い。決めずに掛けると、**根拠の無い年率が出る。**[/]"
    )

    console.print()
    for heading, body in CONDITIONS:
        console.print(f"[bold]{heading}[/]")
        console.print(f"  {body}")
        console.print()

    if into:
        target_path = Path(into)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        console.print(f"[green]{target_path} を書き直した。[/] **この文書は生成物である。**")


def _gentlest_return(shapes: object) -> float:
    """The smallest required annual return across the annualisable shapes.

    **「年 X% 以上」と書くなら、下回る行が在ってはならない。** ここは
    `shapes[0]` を採っていて、**並びを「要る情報比の順」に変えた日に、
    先頭が最小でなくなった**（2026-09-21）。

    **並び順に頼る読み方を残さない。** 最小が要るなら、最小を取る。
    """
    found = [
        shape.required_annual(shape.line())
        for shape in shapes  # type: ignore[attr-defined]
        if shape.per_year > 0
    ]
    return min(found) if found else 0.0


def _judgement_span(shapes: object) -> float:
    """The judgement window in years, from the shapes that can say.

    **`periods / 12` を直に書かない。** 年率に直せない形は `per_year` が 0
    なので、割ると意味の無い数になる——`Shape.period_years` が ``None`` を
    返すのと同じ理由である。
    """
    found = [
        shape.period_years
        for shape in shapes  # type: ignore[attr-defined]
        if shape.period_years is not None
    ]
    return max(found) if found else 0.0


def _ir_cell(shape: object) -> str:
    """Build the required-information-ratio cell, for the table and the document.

    **表と `docs/PASSING.md` の両方がここを呼ぶ。** 2つ持つと、片方だけ
    直したときに食い違う——`_inflation_cell` と同じ理由である
    （`CLAUDE.md`「判定の当てはめが2箇所にあると、片方が緩む」）。

    **重なる窓には出さない。** イベント型は1観測が取引できる系列では
    ないので、年率に直すには資金の張り方を決める必要がある。**決めずに
    割ると、根拠の無い情報比が出る。**
    """
    found = shape.required_ir()  # type: ignore[attr-defined]
    if found is None:
        return "—（年率に直さない）"
    years = shape.period_years  # type: ignore[attr-defined]
    return f"{found:.2f}（{years:.1f}年）"


def _passing_lines(
    target: float,
    shapes: object,
    conditions: object,
    budget: int,
    inflation: float,
) -> list[str]:
    """Build docs/PASSING.md from the same values the console shows.

    **2つ書かない。** コンソールと文書で別々に組み立てると、片方だけ直したときに
    食い違う。
    """
    from stock_ai.backtest.multiplicity import required_t

    lines = [
        "# 合格の条件と、合格に要るリターン",
        "",
        "**この文書は生成物である。** 手で直さない——`uv run stock-ai passing --write "
        "docs/PASSING.md` が作り直す。",
        "",
        "**数字を書き写していない。** 線が変われば要るリターンも全部変わる。実際、"
        "2026-09-17 に 3.02 → 3.39 に動いた。**書き写した数字は、古いまま"
        "もっともらしく見え続ける。**",
        "",
        "## 1. 合格に要るリターン",
        "",
        f"線は **`t ≥ {target:.2f}`**（予算 {budget} 本の {required_t(budget):.2f} に、"
        f"陰性対照で測った膨張 {inflation:.2f} を掛けた）。**管ごとに違う**"
        "——下の表はそれぞれの管の線で計算してある。",
        "",
        "| 設計 | 1期あたりのSD | **合格に要る大きさ** | 要る情報比 | 線 | どこに書いてあるか |",
        "|---|---|---|---|---|---|",
    ]
    for shape in shapes:  # type: ignore[attr-defined]
        own = shape.line()
        annual = shape.required_annual(own)
        need = f"年 {annual:.1%}" if annual else f"1{shape.unit} {shape.required(own):.2%}"
        lines.append(
            f"| {shape.name} | {shape.sd:.2%}／{shape.unit} | **{need}** "
            f"| {_ir_cell(shape)} | `t ≥ {own:.2f}` | {shape.source} |"
        )
    lines += [
        "",
        "これは、",
        "",
        "- **指数に対して**（指数と同じだけ上がっても 0 である）",
        "- **手数料を引いた後で**（往復 0.4% を引いた残り）",
        f"- **{_judgement_span(shapes):.1f}年つづけて**",
        "",
        "という意味である。",
        "",
        "**イベント型は年率に直していない。** 資金をどれだけ張るかを決める必要が"
        "あり、事前登録にその指定が無い。**決めずに掛けると、根拠の無い年率が出る。**",
        "",
        "## 2. 設計によらない、1つの数",
        "",
        "上の表は設計ごとに単位も桁も違う。**効果を散らばりで割ると、1つの数になる。**",
        "",
        "```",
        "要る年率の情報比 = 線 × 膨張 ÷ √(判定に使える年数)",
        "```",
        "",
        "**観測の刻みを細かくしても下がらない。** n が増えても1観測あたりの効果が"
        "同じだけ小さくなるので、比は動かない。**絞って n を減らしても上がらない。**",
        "",
        "### 散らばりの小さい設計は、要る腕前を下げない",
        "",
        "**これがこの数のいちばんの使いどころである。**",
        "",
        "| | 要るリターン | 年あたりのSD | **要る情報比** |",
        "|---|---|---|---|",
    ]
    for shape in shapes:  # type: ignore[attr-defined]
        found = shape.required_ir()
        if found is None:
            continue
        own = shape.line()
        annual = shape.required_annual(own)
        annual_sd = shape.sd * math.sqrt(shape.per_year)
        lines.append(f"| {shape.name} | 年 {annual:.1%} | {annual_sd:.1%} | **{found:.2f}** |")
    lines += [
        "",
        "**低ボラ・ロングのみ・α は、断面ロングショートの3分の1の散らばりで組める。**"
        "要るリターンはそのぶん下がるが、**要る情報比は下がらない**——膨張のぶん、"
        "むしろ高い。",
        "",
        "**下げられるのは、膨張・線・年数の3つだけである。** SD も n も効かない。",
        "",
        "### 年数は、いま在るぶんは増やせない。**待てば伸びる**",
        "",
        "**判定に使える年数を増やせば、全部の設計の壁が下がる。** **いま増やす手は無い。**",
        "",
        "| 期間 | 状態 |",
        "|---|---|",
    ]
    # **書き写さない。** 線が動けばここも動く（`docs/PASSING.md` が生成物で
    # ある理由そのもの）。
    # **`OOS_FROM` を、モジュールの頭の `pead` のそれと取り違えない**
    # （`CLAUDE.md`「モジュールの頭に、同じ名前の別物が居ないか」。あちらは
    # 2024-01-01 で、6年半ずれる）。**ここで束ね直す。**
    from stock_ai.backtest.gap_fill import IS_END as LOOK_END
    from stock_ai.backtest.gap_fill import OOS_FROM as JUDGE_FROM
    from stock_ai.backtest.multiplicity import line_for
    from stock_ai.backtest.passing import LOOKED_FROM
    from stock_ai.backtest.power import required_information_ratio

    now_years = _judgement_span(shapes)
    calendar = line_for("calendar", budget=budget)
    lines += [
        f"| {LOOKED_FROM} より前 | **一度も見ていない** |",
        f"| {LOOKED_FROM} 〜 {LOOK_END:%Y-%m} | **8本の説で下見済み** |",
        f"| {JUDGE_FROM:%Y-%m} 〜 | OOS |",
        "",
        "**下見済みの期間を OOS に入れ替えることはできない。** 8本ぶん覗いた"
        "後の「本番」になる——下の条件②に真正面から反する。",
        "",
        "**ただし、待てば伸びる。** OOS の終わりは今日なので、**何もしなくても"
        "年数は増え、床は `√年数` で下がる。**",
        "",
        "| 判定に使える年数 | 要る情報比（暦の線・膨張 1.00） |",
        "|---|---|",
    ]
    for years, when in ((now_years, "いま"), (now_years + 5, "5年後"), (now_years + 10, "10年後")):
        lines.append(
            f"| {years:.1f}年（{when}） | "
            f"**{required_information_ratio(calendar, 1.0, years):.2f}** |"
        )
    lines += [
        "",
        "**いま際どい設計に判定を使わないことに、追加の理由が付く。** 5年待てば"
        "同じ設計の壁が2割下がる。**予算を急いで使う理由が無い。**",
        "",
        "### 床が動くとすれば、年数ではなく**予算**である",
        "",
        f"**床 {required_information_ratio(calendar, 1.0, now_years):.2f} は、"
        f"予算 {budget} 本の下での値である。** 線は予算から来ているので、"
        "**予算を減らせば床も下がる。**",
        "",
        "| 予算 | 暦の線 | 床（いまの年数・膨張 1.00） |",
        "|---|---|---|",
    ]
    for size in (budget, 10, 5):
        other = line_for("calendar", budget=size)
        lines.append(
            f"| {size} 本 | {other:.2f} | "
            f"**{required_information_ratio(other, 1.0, now_years):.2f}** |"
        )
    lines += [
        "",
        "**だから「床は動かせない」とは書かない。** そう書くと、後で予算に"
        "気付いた人には**動かしてよい理由**に見える。",
        "",
        "**動かないのではない。いま動かしてはいけない。** 8本を §0 で閉じた"
        "後に予算を減らすのは、**結果を見てから規則を選ぶこと**である"
        "——#7 が「五分五分と気付いたうえで回して負けた」のと同じ形で、"
        "**止める場所を作ったのに使わないことになる。**",
        "",
        "**緩めたくなったら、予算ではなく α を動かす。** そちらは「どれだけ"
        "間違えてよいか」の宣言で、**何本試したかという事実とは別である。**",
        "",
        "## 3. 合格の条件",
        "",
        "> **先に紙に書いたとおりに売買して、手数料を引いた後で、指数を"
        # **いちばん小さい要るリターンを採る。** 先頭の行を採っていたが、
        # **並びを「要る情報比の順」に変えた日に、それが最小でなくなった**
        # （2026-09-21）。「〜以上」と書くなら、下回る行が在ってはならない。
        f"年 {_gentlest_return(shapes):.1%} 以上"
        "（設計によってはもっと）上回り、それが続き、しかもまぐれでは説明できないこと。**",
        "",
    ]
    for heading, body in conditions:  # type: ignore[attr-defined]
        lines += [f"### {heading}", "", body, ""]
    return lines


@app.command(name="rehearsal-events")
def rehearsal_events(  # noqa: PLR0913 - イベント型と同じ条件をすべて受け取る
    benchmark: str = typer.Option(BENCHMARK, "--benchmark", help="Sets the calendar."),
    holding: int = typer.Option(20, "--holding", help="Sessions held, as the event designs use."),
    events: int = typer.Option(2_000, "--events", help="How many events to draw each run."),
    seed: int = typer.Option(REHEARSAL_SEED, "--seed", help="Fixed, so the run reproduces."),
    repeat: int = typer.Option(400, "--repeat", help="Draws, for calibration."),
    start: str = typer.Option("2009-01-01", "--start", help="First day events may land on."),
    end: str = typer.Option("2026-08-31", "--end", help="Last day events may land on."),
    subtract: str = typer.Option(
        "universe", "--subtract", help="What to deduct: universe (equal weight) or index."
    ),
    benchmark_fraction: float = typer.Option(
        1.0, "--benchmark-fraction", help="Build the deduction from this share of symbols."
    ),
) -> None:
    """Calibrate the event-type pipe - the one #8 and #5 actually use.

    **説ではない。陰性対照である。** 予算に数えない。

    **月次の盤面で測った 1.12 は、ここには当てはまらないかもしれない。**
    #8・#5 は系列がイベント日ごとで、Newey-West のラグも保有日数に取ってある。
    **別の管には別の数字がありうる。**

    乱数で選んだ日と銘柄を、`event_window.event_returns` に通す——**#8・#5 が
    使っている関数そのもの**である。別の管を作ったら、確かめたことにならない。

    **日の固まり方は本物に合わせていない。** 同じ日数・同じ件数で、中身だけを
    乱数にしている。**本物より固まっていなければ、膨張はここより大きく出る。**
    """
    from stock_ai.backtest.event_window import EventSample, event_sample
    from stock_ai.backtest.multiplicity import (
        HYPOTHESIS_BUDGET,
        MEASURED_INFLATION,
        calibrated_t,
    )
    from stock_ai.backtest.power import estimate_power
    from stock_ai.backtest.rehearsal import calibrate, placebo_events
    from stock_ai.core.logging import quiet_on_console
    from stock_ai.database.repository import list_securities

    settings = get_settings()
    configure_logging(settings.log_level)

    begin, finish = _parse_date(start), _parse_date(end)
    if begin is None or finish is None or begin >= finish:
        raise typer.BadParameter("--start は --end より前のこと。")
    if repeat < 1 or events < 2:
        raise typer.BadParameter("--repeat は 1 以上、--events は 2 以上。")

    console.print("[bold yellow]これは説ではない。陰性対照である。[/]")
    console.print(
        "[dim]#8・#5 が使っているイベント窓の関数そのものに通す。"
        "**月次で測った膨張が、ここにも当てはまるとは限らない。**[/]"
    )
    console.print()

    database = Database()
    database.create_all()
    with database.session() as session:
        price_repo = PriceRepository(session)
        bench = price_repo.get_raw_prices(benchmark)
        if bench.empty:
            console.print(f"[red]ベンチマーク {benchmark!r} の価格が無い。[/]")
            raise typer.Exit(code=1)
        days = [stamp.date() for stamp in bench.index if begin <= stamp.date() <= finish]
        symbols = [sym for sym, market in list_securities(session) if market == "JP"]

    if len(days) < holding + 2 or not symbols:
        console.print(f"[red]日が {len(days)}、銘柄が {len(symbols)} では測れない。[/]")
        raise typer.Exit(code=1)

    console.print(
        f"[dim]{days[0]} 〜 {days[-1]}（{len(days):,} 営業日）、{len(symbols):,} 銘柄から"
        f"毎回 {events:,} 件を引く。窓は {holding} 営業日。種 {seed}。[/]"
    )

    # **1度だけ作って400回ぶん使い回す。** 毎回作り直すと、同じものを400回
    # 計算することになる。乱数で変わるのは引くほうであって、引かれる相手ではない。
    deducted = _universe_to_subtract(
        database, subtract, holding, fraction=benchmark_fraction, seed=seed
    )

    scores: list[float] = []
    # **既に計算していて、捨てていた2つ。**
    #
    # `t` の SD が 1.09 出た理由を探すのに、`t` そのものしか見ていなかった。
    # `t = 平均 / 標準誤差` なので、**分母の形も見ないと、どちらが動いたのか
    # 分からない**（2026-09-18）。
    spreads: list[float] = []
    observations: list[int] = []
    # **400回ぶんの処分を足し上げる。** 1回ぶんでは件数が小さすぎて、
    # 上場廃止で落ちる割合が読めない。
    tally: dict[str, object] = {"values": [], "truncated": [], "drawn": 0}
    tally.update(dict.fromkeys(_DISPOSITIONS, 0))
    legs: list[tuple[float, float]] = []
    with (
        # **400回ぶんの1行記録をコンソールに出さない。** ファイルには残る。
        quiet_on_console("stock_ai.backtest.power"),
        Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeRemainingColumn(),
            console=console,
        ) as progress,
    ):
        task = progress.add_task("種を変えて回す", total=repeat)
        for index in range(repeat):
            progress.update(task, completed=index + 1)
            drawn = placebo_events(days, symbols, events, seed=seed + index)
            sample = event_sample(
                database, drawn, holding=holding, benchmark=benchmark, subtract=deducted
            )
            tally["drawn"] += sample.drawn
            for key in _DISPOSITIONS:
                tally[key] += getattr(sample, key)
            tally["values"].extend(sample.values)
            tally["truncated"].extend(sample.truncated)
            legs.append((sample.stock_leg, sample.bench_leg))
            values = sample.values
            if len(values) < 2:
                continue
            estimate = estimate_power(values, lags=holding)
            stderr = estimate.standard_error(len(values))
            if stderr > 0:
                scores.append(fmean(values) / stderr)
                spreads.append(estimate.inflation)
                observations.append(len(values))

    # **自分が引いた相手の線を出す。** 別の相手で測った線を並べると、
    # 比べているつもりで別のものを比べることになる。
    target = calibrated_t(HYPOTHESIS_BUDGET, inflation=_event_inflation(subtract))
    found = calibrate(scores, target)
    if not found.runs:
        console.print("[red]1回も測れなかった。[/]")
        raise typer.Exit(code=1)

    table = Table(title=f"イベント型の管での `t` の形（{found.runs} 回）")
    for column in ("項目", "実測", "帰無なら"):
        table.add_column(column, overflow="fold")
    table.add_row("t の SD", f"[bold]{found.spread:.2f}[/]", "1.00")
    table.add_row("t の平均", f"{found.mean:+.2f}", "0.00")
    table.add_row("|t| ≥ 1.96", f"{found.plain_share:.1%}", "5.0%")
    table.add_row("いちばん大きい t", f"{found.worst:+.2f}", "—")
    console.print(table)

    # **分子と分母を分けて見る。** `t` だけ見ていると、平均が動いたのか
    # 標準誤差が動いたのか分からない。0.94 → 1.09 のときに、そこで止まった。
    if spreads:
        shape = Table(title="`t` の分母の形")
        for column in ("項目", "平均", "SD"):
            shape.add_column(column, overflow="fold")
        shape.add_row(
            "重なりの膨張（Newey-West）",
            f"{fmean(spreads):.2f}",
            f"{stdev(spreads):.2f}" if len(spreads) > 1 else "—",
        )
        shape.add_row(
            "1回あたりの観測日数",
            f"{fmean(observations):,.0f}",
            f"{stdev(observations):,.0f}" if len(observations) > 1 else "—",
        )
        console.print(shape)
        console.print(
            "[dim]**膨張が 1 に近くて散らばっているなら、`t` の裾は分母の"
            "推定誤差から来ている**——重なりが無いところに 20 ラグを当てている"
            "ぶんである。**膨張そのものが大きいなら、重なりが残っている。**[/]"
        )

    console.print(
        f"[dim]月次の盤面で測った膨張は {MEASURED_INFLATION:.2f}、**ここは "
        f"{found.spread:.2f}。** 線は {target:.2f}（**1.0 を下回らせない**——補正は"
        "足りない分を足すためのもので、割り引くためのものではない）。[/]"
    )
    for line in found.warnings():
        console.print(f"[yellow]{line}[/]")

    # **散らばりではなく、中心のずれを見る。**
    #
    # 最初は SD しか警告にしていなかった。**`t` の平均が +0.49 出ているのに、
    # 表の1行に出しただけで素通りさせた**（2026-09-17）。そして「イベント型には
    # 別の数字を当てるべき」と、**線を緩める向き**に促した。
    #
    # **散らばりが素直でも、中心がずれていれば判定は歪む。**
    total = EventSample(
        stock_leg=fmean([leg for leg, _mark in legs]) if legs else float("nan"),
        bench_leg=fmean([mark for _leg, mark in legs]) if legs else float("nan"),
        **tally,  # type: ignore[arg-type]
    )
    _report_event_disposition(total, title=f"窓が捨てた件数（{found.runs} 回の合計）")

    # **+0.49 の出どころを、2つに分けて読む。**
    #
    # (A) 引く相手が時価総額加重の指数で、引くほうは一様抽選（実質等加重）。
    #     小型が勝っていれば、情報ゼロでも平均はプラスになる。
    # (B) 上場廃止で窓が切れたイベントが落ちる。落ちるのは悪く終わった側。
    #
    # **(B) は測れる**——落ちた割合と、落ちた側を足の在るところまでで測った
    # 超過との差である。残りは (A) に当たる。
    lifted = total.survivorship_bias()
    gap = (total.stock_leg - total.bench_leg) - (lifted or 0.0)
    if lifted is not None:
        console.print(
            # **桁を揃える。** 2桁で刷ると「差 +0.01% のうち … 残る +0.02%」
            # のように、丸めた部分が全体に足し合わない形で出る（2026-09-18）。
            # **足して合わない表は、読む側にどちらを信じるか決めさせる。**
            f"[dim]差 {total.stock_leg - total.bench_leg:+.3%} のうち、"
            f"上場廃止で落ちた分の押し上げが **{lifted:+.3%}**。"
            f"残る **{gap:+.3%}** は、引く相手が時価総額加重であることに当たる。[/]"
        )
    else:
        console.print(
            "[yellow]**落ちた側の超過を1件も測れていない。** "
            "上場廃止の押し上げが測れないので、差の出どころを分けられない。[/]"
        )

    if abs(found.mean) > 0.20:
        console.print(
            f"[red]**帰無の下で `t` の平均が {found.mean:+.2f} ある**（0.00 のはず）。[/] "
            "乱数で選んだ銘柄と日を持つだけで、指数に系統的に勝っている。"
            "**散らばりではなく中心のずれで、線を動かしても直らない。**"
        )
        # **疑いを名指ししない。** 同じ出力の上に分解が出ているのに、
        # 決め打ちの犯人を刷っていた（2026-09-17）。**表が否定しているものを、
        # その下の行が断定する**形になり、実際に外れた——生存フィルタの
        # 押し上げは -0.00%/件 だった。**読み上げるのは、測った分解のほうである。**
        if lifted is not None:
            console.print(
                f"[dim]同じ回の分解では、生存フィルタの押し上げが **{lifted:+.2%}/件**、"
                f"加重の違い（一様抽選 対 時価総額加重）が **{gap:+.2%}/件**。"
                "**大きいほうが、直すべきほうである。**[/]"
            )
        else:
            console.print(
                "[dim]**出どころを分けられていない。** 落ちた側の超過が1件も"
                "測れていないので、生存フィルタと加重の違いを切り分けられない。[/]"
            )


@app.command(name="rehearsal-calendar")
def rehearsal_calendar(  # noqa: PLR0913 - 日次の暦と同じ条件をすべて受け取る
    benchmark: str = typer.Option(BENCHMARK, "--benchmark", help="Which series to label."),
    seed: int = typer.Option(REHEARSAL_SEED, "--seed", help="Fixed, so the run reproduces."),
    repeat: int = typer.Option(400, "--repeat", help="Draws, for calibration."),
    start: str = typer.Option("2009-01-01", "--start", help="First month-turn to use."),
    end: str = typer.Option("2026-08-31", "--end", help="Last day any window may touch."),
) -> None:
    """Calibrate the daily-calendar pipe - the one #13 uses.

    **説ではない。陰性対照である。** 予算に数えない。

    **月次の 1.12 も、イベント型の 1.09 も、ここには当てはまらないかもしれない。**
    #13 は1本の系列の中で日どうしを比べる**別の推定量**である。

    **リターンは本物のまま、窓の位置だけを乱数にする。** 月次の対照が signal
    だけを乱数にしたのと同じ形である。**偽の窓は本物の窓の外に置く**——重ねると
    本物の効果が漏れ込む。

    API を1回も叩かない。
    """
    from stock_ai.backtest.multiplicity import (
        MEASURED_INFLATION,
        MEASURED_INFLATION_EVENT,
        required_t,
    )
    from stock_ai.backtest.power import estimate_power
    from stock_ai.backtest.rehearsal import calibrate, placebo_windows
    from stock_ai.backtest.turn_of_month import WINDOW_DAYS
    from stock_ai.backtest.turn_of_month import build_series as turn_series
    from stock_ai.core.logging import quiet_on_console

    settings = get_settings()
    configure_logging(settings.log_level)

    begin, finish = _parse_date(start), _parse_date(end)
    if begin is None or finish is None or begin >= finish:
        raise typer.BadParameter("--start は --end より前のこと。")
    if repeat < 1:
        raise typer.BadParameter("--repeat は 1 以上。")

    console.print("[bold yellow]これは説ではない。陰性対照である。[/]")
    console.print(
        "[dim]#13 が使う暦の管に、窓の位置だけ乱数にして通す。"
        "**月次の 1.12 も、イベント型の 1.09 も、ここに当てはまるとは限らない。**[/]"
    )
    console.print()

    returns, dates, month_ends = _calendar_pipe(benchmark)

    # **本物の窓は、偽の窓からも窓の外からも外す。**
    #
    # 偽の窓は本物を避けて置かれるので、偽の「窓の外」は**構成上かならず
    # 本物の窓をまたぐ。** 外さないと本物の効果が引き算する側に混ざり、
    # 「何も無いときの分布」にならない。しかも**混ざる向きから本物の符号が
    # 逆算できてしまう**（2026-09-19 に気付いて止めた）。
    real = frozenset(day for end in month_ends for day in range(end, end + WINDOW_DAYS))

    # **窓の外は、その隙間のふつうの日に限る。**
    #
    # `exclude` だけでは足りなかった。偽の窓が本物の窓を挟むと、「窓の外」が
    # **本物の窓だけになり、除外して空になる。** しかも長さが 4〜22日 と
    # ばらつく（本物は常に約16日）——**同じ推定量を測っていることにならない。**
    #
    # ここは前の本物の窓の直後から、今回の本物の窓の直前まで。**本物の日は
    # 1日も入らない。**
    pool = [
        (month_ends[max(index - 1, 0)] + WINDOW_DAYS, month_ends[index] - 1)
        for index in range(len(month_ends))
    ]

    # **使った範囲を出す。** 価格の全履歴を出していたので、2009年より前まで
    # 使ったように見えていた（実際は `USABLE_FROM` で切られている）。
    shape = turn_series(returns, dates, month_ends, source=benchmark, start=begin, end=finish)
    if len(shape.episodes) < 2:
        console.print("[red]月替わりが2回も作れない。[/]")
        raise typer.Exit(code=1)
    console.print(
        f"[dim]{shape.months[0]} 〜 {shape.months[-1]}（月替わり "
        f"{len(shape.months):,} 回）。窓は {WINDOW_DAYS} 営業日。種 {seed}。"
        f"**本物の窓 {len(real):,} 日は、偽の窓からも窓の外からも外してある。**[/]"
    )

    scores: list[float] = []
    # **`t` だけ見ない。** イベント型の +0.49 は `t` しか見ていなかったので、
    # 分解に2手かかった（2026-09-18）。**下にある量も一緒に出す。**
    levels: list[float] = []
    with (
        # **400回ぶんの1行記録をコンソールに出さない。** ファイルには残る。
        # 前回この出力が 112KB になった（2026-09-19）。
        quiet_on_console("stock_ai.backtest.turn_of_month", "stock_ai.backtest.power"),
        Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeRemainingColumn(),
            console=console,
        ) as progress,
    ):
        task = progress.add_task("種を変えて回す", total=repeat)
        for index in range(repeat):
            progress.update(task, completed=index + 1)
            windows = placebo_windows(month_ends, WINDOW_DAYS, seed=seed + index)
            drawn = turn_series(
                returns,
                dates,
                month_ends,
                source=benchmark,
                start=begin,
                end=finish,
                windows=windows,
                exclude=real,
                outside_pool=pool,
            )
            if len(drawn.episodes) < 2:
                continue
            estimate = estimate_power(drawn.episodes, lags=3)
            stderr = estimate.standard_error(len(drawn.episodes))
            if stderr > 0:
                scores.append(fmean(drawn.episodes) / stderr)
                levels.append(fmean(drawn.episodes))

    # **ここは線を「作る」側なので、校正済みの線を持てない。**
    #
    # 他のすべての判定箇所は `calibrated_t` を使う（`tests/test_rehearsal.py` の
    # `TestTheCalibratedLineIsUsedEverywhere` が見ている）。**ここだけが例外で、
    # 例外である理由は「まだ測っていないものを、測る前に当てられない」ため。**
    #
    # `target` と名付けない。**判定に見える名前を、判定でないものに付けない。**
    plain_line = required_t(HYPOTHESIS_BUDGET)
    found = calibrate(scores, plain_line)
    if not found.runs:
        console.print("[red]1回も測れなかった。[/]")
        raise typer.Exit(code=1)

    table = Table(title=f"日次の暦の管での `t` の形（{found.runs} 回）")
    for column in ("項目", "実測", "帰無なら"):
        table.add_column(column, overflow="fold")
    table.add_row("t の SD", f"[bold]{found.spread:.2f}[/]", "1.00")
    table.add_row("t の平均", f"{found.mean:+.2f}", "0.00")
    table.add_row("|t| ≥ 1.96", f"{found.plain_share:.1%}", "5.0%")
    table.add_row(f"|t| ≥ {plain_line:.2f}（素の線）", f"{found.strict_share:.2%}", "0.25%")
    table.add_row("いちばん大きい t", f"{found.worst:+.2f}", "—")
    if levels:
        # **`t` の下にある量。** 中心がずれたとき、分子が動いたのか分母が
        # 動いたのかを1回で分けられるようにする。
        table.add_row("1月替わりあたりの差（年率）", f"{fmean(levels) * 12:+.2%}", "0.00%")
    console.print(table)

    line = plain_line * max(found.spread, 1.0)
    console.print(
        f"[dim]月次は {MEASURED_INFLATION:.2f}、イベント型は {MEASURED_INFLATION_EVENT:.2f}、"
        f"**ここは {found.spread:.2f}。** 線は **{line:.2f}**"
        "（**1.0 を下回らせない**——補正は足りない分を足すためのもので、"
        "割り引くためのものではない）。[/]"
    )
    console.print(
        f"[yellow]**これを `MEASURED_INFLATION_CALENDAR` に書き写すのは、"
        f"こちらの仕事である。** いまは {found.spread:.2f} が定数に入っていない。[/]"
    )
    for warning in found.warnings():
        console.print(f"[yellow]{warning}[/]")
    if abs(found.mean) > 0.20:
        console.print(
            f"[red]**帰無の下で `t` の平均が {found.mean:+.2f} ある**（0.00 のはず）。[/] "
            "**散らばりではなく中心のずれで、線を動かしても直らない。**"
        )


def _calendar_pipe(benchmark: str) -> tuple[list[float], list[dt.date], list[int]]:
    """Read what both #13 and its control read - the calendar pipe's inputs.

    **2通り持たない。** 月の切れ目も日次リターンも、ここ1箇所で作る。

    Args:
        benchmark: 暦とリターンを取る銘柄。

    Returns:
        ``(日次リターン, 日付, 月末の位置)``。

    Raises:
        typer.Exit: 価格が無い。
    """
    from stock_ai.backtest.lowvol_census import formation_dates
    from stock_ai.backtest.turn_of_month import daily_returns
    from stock_ai.data.schema import CLOSE, split_adjusted

    database = Database()
    database.create_all()
    with database.session() as session:
        raw = PriceRepository(session).get_raw_prices(benchmark)
    if raw.empty:
        console.print(f"[red]{benchmark} の価格が無い。暦を決められない。[/]")
        raise typer.Exit(code=1)

    frame = split_adjusted(raw)
    returns = daily_returns(frame[CLOSE].to_numpy(dtype=float))
    dates = [stamp.date() for stamp in frame.index[1:]]
    month_ends = [position - 1 for position in formation_dates(frame.index) if position >= 1]
    return returns, dates, month_ends


@app.command(name="turn-of-month-power")
def turn_of_month_power(
    benchmark: str = typer.Option(BENCHMARK, "--benchmark", help="The series the line applies to."),
    is_end: str = typer.Option("2017-12-31", "--is-end", help="Last day of the IS window."),
    oos_periods: int = typer.Option(104, "--oos-periods", help="Month-turns the judgement has."),
    universe: bool = typer.Option(True, "--universe/--no-universe", help="Also read equal weight."),
) -> None:
    """Measure the IS window for #13, so the gate table can be filled - not judge it.

    **段2（自分の IS から推定する）の材料を出す。** 文献は読めないので、見込みは
    ここから置く（`docs/PREREG_TURN_OF_MONTH_JP.md` §0）。

    **売買しない。** 窓の中の合計と、日数を揃えた窓の外の平均の差を測るだけで
    ある。費用は引かない——**その代わり §0 の線を、将来売買するときの費用から
    置いてある。**

    **判定ではない。** IS は 2009-01〜2017-12 で、OOS（2018-01〜2026-08）には
    1日も触れない。
    """
    from stock_ai.backtest.power import estimate_power
    from stock_ai.backtest.turn_of_month import WINDOW_DAYS
    from stock_ai.backtest.turn_of_month import build_series as turn_series

    settings = get_settings()
    configure_logging(settings.log_level)

    cut = _parse_date(is_end)
    if cut is None:
        raise typer.BadParameter(f"--is-end must be YYYY-MM-DD; got {is_end!r}.")

    console.print(
        f"[dim]IS は {cut} まで。OOS には1日も触れない。"
        f"窓は月末最終営業日から翌月3営業日目まで（{WINDOW_DAYS} 営業日）。[/]"
    )

    returns, dates, month_ends = _calendar_pipe(benchmark)
    built = [turn_series(returns, dates, month_ends, source=benchmark, end=cut)]
    if universe:
        built.append(_universe_calendar(month_ends, dates, cut))

    for series in built:
        console.print(series.summary())
        for line in series.warnings():
            console.print(f"[yellow]{line}[/]")
    if not built[0].episodes:
        raise typer.Exit(code=1)

    table = Table(title="§0 に入れる材料（IS から。判定ではない）")
    columns = ("項目", *[series.source for series in built], "どこから")
    for column in columns:
        table.add_column(column, overflow="fold")

    # **この管の線は測ってある**（2026-09-19、400回で SD 1.05）。
    # 以前は月次の線を仮に当てていた。対照を回す前だったからである。
    target = line_for("calendar")
    stats = []
    for series in built:
        estimate = estimate_power(series.episodes, lags=3)
        stats.append(
            (
                estimate.daily_sd,
                estimate.inflation,
                fmean(series.episodes),
                estimate.standard_error(len(series.episodes)),
                estimate.detectable(oos_periods, target_t=target),
            )
        )

    table.add_row("1期あたりのSD", *[f"{row[0]:.2%}" for row in stats], "IS の月替わりごと")
    table.add_row("重なりの膨張", *[f"{row[1]:.2f}x" for row in stats], "Newey-West(3)。実測")
    table.add_row(
        "検出できる差",
        *[f"年 {row[4] * 12:.1%}" for row in stats],
        f"t≥{target:.2f}・{oos_periods}期。**この管で測った線**",
    )
    table.add_row("判定に使える期数", f"{oos_periods}", *["—"] * (len(built) - 1), "OOS の月数")
    console.print(table)

    tail = Table(title=f"裾（{built[0].source}）")
    for column in ("項目", "値", "なぜ見るか"):
        tail.add_column(column, overflow="fold")
    tail.add_row("いちばん悪かった月替わり", f"{built[0].worst_month():+.2%}", "1回の事故の大きさ")
    tail.add_row("下位5%の平均", f"{built[0].left_tail():+.2%}", "**1点ではなく帯で見る**")
    tail.add_row("正だった割合", f"{built[0].hit_rate():.1%}", "平均だけで語らない")
    console.print(tail)

    mean, stderr = stats[0][2], stats[0][3]
    low, high = mean - 1.96 * stderr, mean + 1.96 * stderr
    console.print(
        f"[bold]IS の差（{built[0].source}）: 年 {mean * 12:+.2%}[/] "
        f"[dim]（95% の幅 年 {low * 12:+.2%} 〜 {high * 12:+.2%}）[/]"
    )
    if len(built) > 1:
        console.print(
            f"[dim]等加重でも併記する: 年 {stats[1][2] * 12:+.2%}。"
            "**線を当てるのは指数のほうである**——§2 でそう決めた。[/]"
        )

    # **測る前にコミットした線である。** 動かさない（事前登録 §0）。
    console.print()
    if mean * 12 < TURN_OF_MONTH_FLOOR:
        console.print(
            f"[red]封印しない。[/] IS の推定 年 {mean * 12:+.2%} が、"
            f"**測る前にコミットした線 年 {TURN_OF_MONTH_FLOOR:.1%} を下回った。**"
        )
        console.print(
            "[dim]事前登録 §0 にそう書いてある。**下回ったら、将来売買しても"
            "費用を賄えない。** 線は動かさない。[/]"
        )
        return

    console.print(
        f"[green]線（年 {TURN_OF_MONTH_FLOOR:.1%}）は上回った。[/] 次は §0 のゲートである。"
    )
    console.print(
        "[dim]uv run stock-ai power-gate "
        f"--sd {stats[0][0] * 100:.2f} --periods {oos_periods} "
        f"--low {low * 12 * 100:.2f} --high {high * 12 * 100:.2f} "
        f"--inflation {stats[0][1]:.2f} --budget {HYPOTHESIS_BUDGET} --pipe calendar[/]"
    )


def _universe_calendar(month_ends: list[int], dates: list[dt.date], cut: dt.date) -> object:
    """Build the same month-turn series on the equal-weighted universe.

    **併記用である。** 線を当てるのは指数のほう（事前登録 §2）。

    **日付で揃える。** 指数と同じ暦・同じ月末の位置を使い、その日の等加重平均を
    並べる。足の無い日は `nan` にして、平均から外れるようにする。

    Args:
        month_ends: 月末の位置（指数の暦の中）。
        dates: 指数の暦（リターンと同じ長さ）。
        cut: IS の最終日。

    Returns:
        :class:`~stock_ai.backtest.turn_of_month.TurnOfMonthSeries`。
    """
    from stock_ai.backtest.turn_of_month import build_series as turn_series
    from stock_ai.backtest.universe_benchmark import equal_weighted_daily

    database = Database()
    database.create_all()
    console.print("[dim]等加重の日次を作っています（全銘柄の足を1度だけ読みます）...[/]")
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("銘柄を読む", total=None)
        daily = equal_weighted_daily(
            database,
            progress=lambda done, total: progress.update(task, completed=done, total=total),
        )
    values = [daily.get(when, float("nan")) for when in dates]
    return turn_series(values, dates, month_ends, source="universe", end=cut)


@app.command(name="rehearsal")
def rehearsal(  # noqa: PLR0913 - 本物と同じ条件をすべて受け取る
    is_start: str = typer.Option("2009-01-01", "--is-start", help="First day of the IS window."),
    is_end: str = typer.Option("2017-12-31", "--is-end", help="Last day of the IS window."),
    oos_end: str = typer.Option("2026-08-31", "--oos-end", help="Last day of the OOS window."),
    seed: int = typer.Option(REHEARSAL_SEED, "--seed", help="Fixed, so the run reproduces."),
    repeat: int = typer.Option(1, "--repeat", help="Draw this many signals, for calibration."),
    window: int = typer.Option(DEFAULT_WINDOW, "--window", help="Volatility window in sessions."),
    min_symbols: int = typer.Option(MIN_SYMBOLS_PER_MONTH, "--min-symbols", help="Per month."),
    lags: int = typer.Option(LOWVOL_LAGS, "--lags", help="Newey-West lags, in months."),
) -> None:
    """Run a random signal through the whole pipe - the negative control.

    **説ではない。** 乱数の signal は世界について何も主張していないので、
    **多重検定の予算に入らない。** `§0` も通さない——§0 は「一度きりの判定を
    弱い設計に使わない」ための関門で、**判定を消費しないものには守るものが無い。**

    **signal だけを乱数にする。** 月も universe もリターンも本物のままである。
    全部を乱数にすると、重なりも自己相関も消えて、**いちばん確かめたい部分が
    消える。**

    問いは1つ。**何も無いときに、この仕組みは合格を出すか。** 出したら仕組みが
    壊れている。

    `--repeat` を付けると、種を変えて回して **`t` の分布**を出す。帰無なら SD は
    1.0 のはずで、**1.15 なら補正は足りていない。** 1回では分からない——
    `t ≥ 3.02` を越える確率は 0.125% で、400回の期待値が 0.5 回だからである。
    """
    from stock_ai.backtest.cross_section import beta_to_benchmark, build_estimators
    from stock_ai.backtest.factor_panel import build_panel
    from stock_ai.backtest.multiplicity import (
        HYPOTHESIS_BUDGET,
        MEASURED_INFLATION,
        calibrated_t,
        required_t,
    )
    from stock_ai.backtest.power import estimate_power
    from stock_ai.backtest.rehearsal import calibrate, oos_seed, placebo_sections
    from stock_ai.core.logging import quiet_on_console

    settings = get_settings()
    configure_logging(settings.log_level)

    begin, cut, finish = _parse_date(is_start), _parse_date(is_end), _parse_date(oos_end)
    if begin is None or cut is None or finish is None or not begin < cut < finish:
        raise typer.BadParameter("--is-start < --is-end < --oos-end のこと。")
    if repeat < 1:
        raise typer.BadParameter("--repeat must be at least 1.")

    target = calibrated_t(HYPOTHESIS_BUDGET)
    console.print("[bold yellow]これは説ではない。陰性対照である。[/]")
    console.print(
        "[dim]乱数の signal を、**本物と同じ管**に通す。別の管を作ったら、"
        "確かめたことにならない。**予算には数えない。**[/]"
    )
    console.print()

    database = Database()
    database.create_all()

    def panel_for(start: dt.date, end: dt.date):
        return build_panel(
            database,
            factors=("低ボラ",),
            start=start,
            end=end,
            window=window,
            min_symbols=min_symbols,
        )

    try:
        inside = panel_for(begin, cut)
        outside = panel_for(cut + dt.timedelta(days=1), finish)
    except ValueError as error:
        console.print(f"[red]盤面を作れなかった: {error}[/]")
        raise typer.Exit(code=1) from error

    def score(panel, draw: int) -> float:
        """Score one placebo draw through the same path a real factor takes."""
        built = build_estimators(
            placebo_sections(panel.sections, seed=draw),
            panel.benchmark,
            higher_is_better=True,
        )
        if built.months < 2:
            return float("nan")
        beta = beta_to_benchmark(built.quantile_spread, built.benchmark)
        values = built.alpha(built.quantile_spread, beta)
        estimate = estimate_power(values, lags=lags)
        stderr = estimate.standard_error(len(values))
        return fmean(values) / stderr if stderr > 0 else float("nan")

    console.print(
        f"[dim]IS {begin} 〜 {cut}（{len(inside.months)}ヶ月）、"
        f"OOS {cut} 〜 {finish}（{len(outside.months)}ヶ月）。"
        f"種は IS {seed} / OOS {oos_seed(seed)}（**同じ流れを使わない**）。[/]"
    )

    # --- 1回だけ、端から端まで ----------------------------------------------
    # **同じ種を両方に使わない。** 乱数の流れが共有されると、2つが独立な引きに
    # ならない。最初はそうしていて、IS +1.24・OOS +1.29 が揃って見えた——
    # **偶然か共有のせいかを区別できなかった**（2026-09-17）。
    inside_t = score(inside, seed)
    outside_t = score(outside, oos_seed(seed))

    table = Table(title="陰性対照を端から端まで（**説ではない**）")
    for column in ("段", "何をしたか", "結果"):
        table.add_column(column, overflow="fold")
    table.add_row("IS", "乱数 signal で分位を組み、α の t を出す", f"t {inside_t:+.2f}")
    table.add_row("封印", "**§0 は通さない**（判定を消費しないので守るものが無い）", "—")
    table.add_row("OOS", "**一度だけ**回す", f"[bold]t {outside_t:+.2f}[/]")
    table.add_row(
        "判定",
        f"合格は `t ≥ {target:.2f}`"
        f"（素の線 {required_t(HYPOTHESIS_BUDGET):.2f} × 測った膨張 {MEASURED_INFLATION:.2f}）",
        "",
    )
    console.print(table)

    passed = outside_t >= target
    if passed:
        console.print(
            f"[bold red]合格が出た。[/] **仕組みが壊れている。** "
            f"乱数の signal に `t {outside_t:+.2f}` が出るのは、"
            "起きるとしても 0.125% のはずである。**種を変えて確かめること。**"
        )
    else:
        console.print(
            "[green]不合格。[/] **何も無いところに合格は出なかった。** "
            "これが 4本の「封印せず」より強い保証になる——"
            "**関門ではなく、判定そのものを通した結果である。**"
        )

    if repeat < 2:
        console.print()
        console.print(
            "[dim]**1回では校正できない。** `t ≥ 3.02` を越える確率は 0.125% で、"
            "400回の期待値が 0.5 回である。`-Repeat 400` で `t` の分布を見ること。[/]"
        )
        return

    # --- 何度も回して、t の形を見る ------------------------------------------
    scores: list[float] = []
    with (
        # **400回ぶんの1行記録をコンソールに出さない。** ファイルには残る。
        quiet_on_console("stock_ai.backtest.power"),
        Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeRemainingColumn(),
            console=console,
        ) as progress,
    ):
        task = progress.add_task("種を変えて回す", total=repeat)
        for index in range(repeat):
            progress.update(task, completed=index + 1)
            scores.append(score(outside, oos_seed(seed) + index))

    found = calibrate(scores, target)
    shape = Table(title=f"帰無の下での `t` の形（{found.runs} 回）")
    for column in ("項目", "実測", "帰無なら"):
        shape.add_column(column, overflow="fold")
    shape.add_row("t の SD", f"[bold]{found.spread:.2f}[/]", "1.00")
    shape.add_row("t の平均", f"{found.mean:+.2f}", "0.00")
    shape.add_row("|t| ≥ 1.96", f"{found.plain_share:.1%}", "5.0%")
    shape.add_row(f"|t| ≥ {target:.2f}", f"{found.strict_share:.2%}", "0.25%")
    shape.add_row("いちばん大きい t", f"{found.worst:+.2f}", "—")
    console.print(shape)

    console.print(
        f"[dim]実測の散らばりの下では、`t ≥ {target:.2f}` は本当は "
        f"**両側 {found.implied_level(target):.2%}** に当たる（設計は 0.25%）。[/]"
    )
    for line in found.warnings():
        console.print(f"[yellow]{line}[/]")
    if found.calibrated and not found.warnings():
        console.print(
            "[green]`t` は素直に効いている。[/] **判定の線は、見かけどおりの意味を持つ。**"
        )


@app.command(name="value-reconcile")
def value_reconcile(  # noqa: PLR0913 - 揃える条件をすべて受け取る
    rosters: str = typer.Option(
        str(DEFAULT_SNAPSHOT_DIR), "--rosters", help="Where the dated rosters live."
    ),
    valuation: str | None = typer.Option(None, "--valuation", help="Month-end PBR file."),
    is_start: str = typer.Option("2009-01-01", "--is-start", help="First day of the IS window."),
    is_end: str = typer.Option("2017-12-31", "--is-end", help="Last day of the IS window."),
    window: int = typer.Option(DEFAULT_WINDOW, "--window", help="Volatility window in sessions."),
    min_symbols: int = typer.Option(MIN_SYMBOLS_PER_MONTH, "--min-symbols", help="Per month."),
    lags: int = typer.Option(3, "--lags", help="Newey-West lags, in months."),
) -> None:
    """Find out why the IS value spread reads t=2.26 one way and t=0.76 another.

    **判定ではない。** 同じ IS（2009-01〜2017-12）のバリューを2度測って、
    `t` が **2.26**（#9、`antivalue-estimate`）と **+0.76**（#11、
    `composite-gate` の脚）に割れた。**universe と推定量が違うだけである。**

    **2つの推定が3倍食い違っているとき、信じるべきはその不安定さのほうで、
    高いほうの数字ではない。** どちらが効いているのかを、1つずつ動かして出す。

    **最初に両端を再現する。** 再現できなければそこで止まる——揃っていない2つを
    比べても、差は「推定量の差」ではなく「フィルタの差」になる。

    **判定を消費しない。** 見るのは IS だけで、効果ではなく**どこで数字が動くか**
    を測っている。
    """
    from stock_ai.backtest.antivalue import build_series as antivalue_series
    from stock_ai.backtest.cross_section import beta_to_benchmark, build_estimators
    from stock_ai.backtest.factor_panel import build_panel
    from stock_ai.backtest.power import estimate_power
    from stock_ai.data.valuation_monthly import DEFAULT_PATH
    from stock_ai.data.valuation_monthly import read as read_valuation

    settings = get_settings()
    configure_logging(settings.log_level)

    begin, cut = _parse_date(is_start), _parse_date(is_end)
    if begin is None or cut is None or begin >= cut:
        raise typer.BadParameter("--is-start/--is-end must be YYYY-MM-DD and in order.")

    frame = read_valuation(Path(valuation) if valuation else DEFAULT_PATH)
    if frame.empty:
        console.print("[red]月末の PBR が無い。[/]")
        raise typer.Exit(code=1)
    snapshots = membership(Path(rosters))
    if not snapshots:
        console.print("[red]名簿が無い。[/] **渡さないと生存バイアスが入る。**")
        raise typer.Exit(code=1)

    console.print("[bold yellow]これは判定ではない。[/] 食い違いの出どころを探している。")
    console.print(
        "[dim]同じ IS を2度測って t が 2.26 と 0.76 に割れた。"
        "**信じるべきはその不安定さのほうである。**[/]"
    )
    console.print()

    database = Database()
    database.create_all()

    def score(values: list[float]) -> float:
        """Newey-West t for a monthly series - the sign is left as given."""
        if len(values) < 2:
            return float("nan")
        estimate = estimate_power(values, lags=lags)
        stderr = estimate.standard_error(len(values))
        return fmean(values) / stderr if stderr > 0 else float("nan")

    # --- #9 の経路 -----------------------------------------------------------
    #
    # **向きはバリュー（低PBR を買う）にそろえる。** `spread()` は #9 の格言の
    # 向き（高PBR − 低PBR）なので、符号を反転する。**反転はここ1箇所。**
    series = antivalue_series(database, frame, start=begin, end=cut, snapshots=snapshots)
    if not series.months:
        console.print("[red]#9 の経路で月が1つも作れなかった。[/]")
        raise typer.Exit(code=1)
    cost = series.cost_per_month()
    beta = series.beta_to_benchmark()
    nine_raw = [-value for value in series.spread()]
    nine_alpha = [-value for value in series.alpha(beta)]

    # --- 盤面の経路 ----------------------------------------------------------
    panels: dict[str, object] = {}
    for label, factors in (
        ("盤面（低ボラ＋バリュー）", ("低ボラ", "バリュー")),
        ("盤面（バリューだけ）", ("バリュー",)),
    ):
        try:
            panels[label] = build_panel(
                database,
                factors=factors,
                start=begin,
                end=cut,
                window=window,
                min_symbols=min_symbols,
                valuation=frame,
            )
        except ValueError as error:
            console.print(f"[yellow]{label}: 作れなかった（{error}）[/]")

    rows: list[tuple[str, int, float, float]] = []
    rows.append(
        (
            "#9 の universe（PBR ファイル＋名簿）",
            len(series.months),
            score(nine_raw),
            score(nine_alpha),
        )
    )
    for label, panel in panels.items():
        built = build_estimators(
            panel.column("バリュー"),
            panel.benchmark,
            higher_is_better=True,  # type: ignore[attr-defined]
        )
        if built.months < 2:
            continue
        panel_beta = beta_to_benchmark(built.quantile_spread, built.benchmark)
        rows.append(
            (
                label,
                built.months,
                score(built.quantile_spread),
                score(built.alpha(built.quantile_spread, panel_beta)),
            )
        )

    # --- 検算 ----------------------------------------------------------------
    #
    # **揃っていない2つを比べても、差はフィルタの差になる。** 先に両端を出す。
    console.print("[bold]検算：両端を再現できるか。[/]")
    nine_with_cost = score([value - cost for value in nine_raw])
    # **比べる相手を間違えない。** #9 の出力から導いた +2.26 は**格言の向き**
    # （高PBR − 低PBR）の t である。バリュー向きに反転すると費用が逆向きの
    # 引き算になり、平均だけ 2×cost ぶん小さくなる（SD は動かない）。
    # **期待値は +2.03 である**（2026-09-17 に書き間違えていた）。
    console.print(
        f"[dim]#9（バリュー向き・費用引き後）: t {nine_with_cost:+.2f}"
        "（**期待値 +2.03**。#9 の出力の +2.26 は格言の向きで、"
        "反転すると費用の引き算も向きが変わる）[/]"
    )
    for label, _months, _raw, alpha_t in rows:
        if label.startswith("盤面（低ボラ"):
            console.print(
                f"[dim]#11（α・費用引き前）: t {alpha_t:+.2f}（**#11 の出力は +0.76**）[/]"
            )
    console.print()

    table = Table(title="バリューの t が、どこで動くか（IS のみ。**判定ではない**）")
    for column in ("universe", "月数", "生のスプレッド", "α（β を引く）"):
        table.add_column(column, overflow="fold")
    for label, months, raw_t, alpha_t in rows:
        table.add_row(label, f"{months}", f"{raw_t:+.2f}", f"{alpha_t:+.2f}")
    console.print(table)
    console.print(
        f"[dim]表は**すべて費用引き前**にそろえてある（費用は月 {cost:.3%}、"
        f"年 {cost * 12:.2%}）。上の検算だけ、それぞれ元の流儀で出している。"
        f"β は #9 の経路で {beta:+.2f}。[/]"
    )

    console.print()
    console.print(
        "[dim]**横に動けば推定量が、縦に動けば universe が効いている。** "
        "どちらでも大きく動くなら、**効果は設計の選び方に対して頑健でない**——"
        "それ自体が効果に対する反証寄りの情報である。[/]"
    )


@app.command(name="estimator-gain")
def estimator_gain(  # noqa: PLR0913 - 校正が固定した条件をすべて受け取る
    factor: str = typer.Option("低ボラ", "--factor", help="Which factor to calibrate on."),
    is_start: str = typer.Option("2009-01-01", "--is-start", help="First day of the IS window."),
    is_end: str = typer.Option("2017-12-31", "--is-end", help="Last day of the IS window."),
    valuation: str | None = typer.Option(None, "--valuation", help="Month-end PBR file."),
    window: int = typer.Option(DEFAULT_WINDOW, "--window", help="Volatility window in sessions."),
    min_symbols: int = typer.Option(MIN_SYMBOLS_PER_MONTH, "--min-symbols", help="Per month."),
    lags: int = typer.Option(LOWVOL_LAGS, "--lags", help="Newey-West lags, in months."),
) -> None:
    """Measure r, the t gain from taking sector and size out of the returns.

    **判定ではない。** 推定量の校正であって、説の合否ではない。見るのは IS だけ
    である。

    **1つ目の校正（分位ソート → 横断回帰）は 0.93倍で終わった。** これが2つ目
    で、**残っている最後の手**である——どの説も市場βしか引いていない。

    しきい値は測る前に確定してある（`docs/HYPOTHESES.md`「2つ目の校正」）。
    **r ≥ 1.4 で動いた、1.2 ≤ r < 1.4 は曖昧域で打ち切り、r < 1.2 は動かない。**
    1.4 の出どころは #7 で、**1.38倍あれば足りていた。**

    **`t` 比で測る。SD 比ではない。** 推定量を変えると効果も一緒に縮むので、
    SD 比だと改善が無料で出たように見える。

    **判定済みの説を測り直すのに使わない**（2026-09-16、ユーザーの判断）。
    答えを見た後で測り方を変えることになる。
    """
    from stock_ai.backtest.cross_section import beta_to_benchmark, build_estimators, neutralise
    from stock_ai.backtest.cross_section import t_ratio as ratio_of
    from stock_ai.backtest.factor_panel import NEEDS_VALUATION, build_panel
    from stock_ai.backtest.power import (
        NEUTRAL_AMBIGUOUS,
        NEUTRAL_PROCEED,
        estimate_power,
        neutral_verdict,
    )
    from stock_ai.data.valuation_monthly import DEFAULT_PATH
    from stock_ai.data.valuation_monthly import read as read_valuation

    settings = get_settings()
    configure_logging(settings.log_level)

    begin, cut = _parse_date(is_start), _parse_date(is_end)
    if begin is None or cut is None or begin >= cut:
        raise typer.BadParameter("--is-start/--is-end must be YYYY-MM-DD and in order.")

    frame = None
    if factor in NEEDS_VALUATION:
        frame = read_valuation(Path(valuation) if valuation else DEFAULT_PATH)
        if frame.empty:
            console.print("[red]月末の PBR が無い。[/]")
            raise typer.Exit(code=1)

    console.print("[bold yellow]これは判定ではない。[/] 推定量の校正である。")
    console.print(
        "[dim]しきい値は測る前に確定済み（`docs/HYPOTHESES.md`）。"
        "**r ≥ 1.4 で動いた、1.2〜1.4 は打ち切り。** 見てから動かさない。[/]"
    )
    console.print()

    database = Database()
    database.create_all()
    try:
        panel = build_panel(
            database,
            factors=(factor,),
            start=begin,
            end=cut,
            window=window,
            min_symbols=min_symbols,
            valuation=frame,
        )
    except ValueError as error:
        console.print(f"[red]盤面を作れなかった: {error}[/]")
        raise typer.Exit(code=1) from error

    column = panel.column(factor)

    # **月を揃える。** 中立化できない月は両方から落とす。揃っていない系列の
    # t を比べると、推定量の差ではなく期間の差を測ることになる。
    plain: list[list[tuple[float, float]]] = []
    neutral: list[list[tuple[float, float]]] = []
    bench: list[float] = []
    skipped = 0
    explained: list[float] = []
    for month, (rows, meta, mark) in enumerate(
        zip(column, panel.context, panel.benchmark, strict=True)
    ):
        forwards = [forward for _signal, forward in rows]
        taken = neutralise(forwards, [item.sector for item in meta], [item.size for item in meta])
        if taken is None:
            skipped += 1
            continue
        plain.append(rows)
        neutral.append(
            [
                (signal, residual)
                for (signal, _forward), residual in zip(rows, taken.residuals, strict=True)
            ]
        )
        bench.append(mark)
        explained.append(taken.explained)
        del month

    if len(plain) < 2:
        console.print(f"[red]中立化できた月が {len(plain)} しかない。[/]")
        raise typer.Exit(code=1)

    # (a) いまの方法 — 生のスプレッドから市場βを引く。
    built = build_estimators(plain, bench, higher_is_better=True)
    beta = beta_to_benchmark(built.quantile_spread, built.benchmark)
    before = built.alpha(built.quantile_spread, beta)
    # (b) 中立版 — 断面回帰の残差から、**さらに市場βを引く。**
    #
    # 最初は引いていなかった。「定数項が入るので市場は自動で抜ける」と書いたが、
    # **ロングショートのスプレッドでは定数項は相殺される**——全銘柄から同じ値を
    # 引いても、上位平均 − 下位平均は変わらない。**(b) だけ市場が残ったまま
    # 比べていた**（2026-09-16）。低ボラの Q1−Q5 は β が負なので、(b) を不利に
    # する向きだった。
    #
    # コミットした文書は「市場β ＋ 業種 ＋ 規模」と書いてある。**実装のほうを
    # 合わせる。しきい値は動かさない。**
    after_built = build_estimators(neutral, bench, higher_is_better=True)
    after_beta = beta_to_benchmark(after_built.quantile_spread, after_built.benchmark)
    after = after_built.alpha(after_built.quantile_spread, after_beta)

    rows_out = []
    for label, values in (("(a) 市場βだけ引く", before), ("(b) 業種・規模も抜く", after)):
        estimate = estimate_power(values, lags=lags)
        stderr = estimate.standard_error(len(values))
        rows_out.append((label, estimate, fmean(values) / stderr if stderr > 0 else float("nan")))

    table = Table(title=f"共通因子を抜いた利得（{factor}・IS のみ。**判定ではない**）")
    for name in ("推定量", "月数", "1期あたりのSD", "重なりの膨張", "t"):
        table.add_column(name, overflow="fold")
    for label, estimate, value in rows_out:
        table.add_row(
            label,
            f"{estimate.observations}",
            f"{estimate.daily_sd:.2%}",
            f"{estimate.inflation:.2f}x",
            f"[bold]{value:+.2f}[/]",
        )
    console.print(table)
    share = median(explained)
    console.print(
        f"[dim]業種と規模で説明できた断面の分散: 中央値 {share:.0%}"
        f"（中立化できずに落とした月 {skipped}）。**0% なら抜けていない。**[/]"
    )
    # **ここが天井である。** 分散を x しか説明していないものを完全に抜いても、
    # 分散低減から来る t の改善は √(1/(1−x)) を超えない。**比を見る前に、
    # 届きうるかどうかがここで決まる。**
    if 0.0 < share < 1.0:
        ceiling = (1.0 / (1.0 - share)) ** 0.5
        console.print(
            f"[dim]**分散低減だけから来る t の天井は {ceiling:.2f}倍**である"
            f"（√(1/(1−{share:.0%})））。しきい値の下端は "
            f"{NEUTRAL_AMBIGUOUS:.1f} で、**天井がその下なら比を見るまでもない。**[/]"
        )

    gain = ratio_of(rows_out[1][2], rows_out[0][2])
    verdict, reading = neutral_verdict(gain)
    console.print()
    console.print(f"[bold]r = {gain:.2f}[/]" if gain is not None else "[bold]r は出せない[/]")
    colour = "green" if verdict == NEUTRAL_PROCEED else "red"
    console.print(f"[bold {colour}]{verdict}[/] {reading}")
    console.print(
        "[dim]**SD 比ではなく t 比である。** 推定量を変えると効果も一緒に縮む。"
        "SD 比を見ると、改善が無料で出たように見える。[/]"
    )


@app.command(name="margin-census")
def margin_census(  # noqa: PLR0913 - §2 が固定した絞り込みをすべて受け取る
    directory: str = typer.Option(
        str(DEFAULT_ARCHIVE_DIR), "--dir", help="Where the archived originals live."
    ),
    rosters: str = typer.Option(
        str(DAILY_SNAPSHOT_DIR), "--rosters", help="Dated rosters, for the lending flag."
    ),
    benchmark: str = typer.Option(BENCHMARK, "--benchmark", help="Sets the calendar."),
    min_turnover: float = typer.Option(MIN_TURNOVER, "--min-turnover", help="Liquidity floor."),
    limit: int | None = typer.Option(None, "--limit", help="Read only the first N originals."),
) -> None:
    """Count the margin-restriction events - no return is computed here.

    **#8 の §2 の表と、§3 の保有窓を埋める。**

    窓はここで**機械的に決まる**——「規制が解けるまでの営業日数の中央値、上限
    20営業日」。事前登録 §3 の一行そのものを `window_from` が持っている。
    **中央値を見てから窓を選び直さない。**

    **リターンを1つも計算しない。** だから判定を消費しない。

    貸借区分は**その日の値**で引く。「いま貸借銘柄か」で引くと、2026-09-08 に
    踏んだ形（市場区分を最後に見えた姿で引いた）と同じになる。**名簿が届いて
    いない日は「貸借でない」ではなく「分からない」**として数える。
    """
    from stock_ai.backtest.margin_census import census, lending_index
    from stock_ai.backtest.pead import TURNOVER_WINDOW
    from stock_ai.data.jquants_margin import from_archive as margin_from_archive
    from stock_ai.data.schema import VOLUME

    settings = get_settings()
    configure_logging(settings.log_level)

    source = Path(directory)
    if not source.is_dir():
        console.print(f"[red]{source} が無い。[/] **原本が要る。** 取りには行かない。")
        raise typer.Exit(code=1)

    console.print("[dim]リターンは1つも計算しない。判定は消費しない。[/]")
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("原本を読む", total=None)

        def step(index: int, total: int, key: str) -> None:
            progress.update(task, total=total, completed=index)

        alerts = margin_from_archive(source, limit=limit, progress=step)

    if not alerts:
        console.print(
            f"[red]{source} に `/markets/margin-alert` の原本が無い。[/] "
            "**Premium の週に取ったはずのものである。** `checks\\解約前の棚卸し.bat` で確かめる。"
        )
        raise typer.Exit(code=1)

    database = Database()
    database.create_all()
    with database.session() as session:
        price_repo = PriceRepository(session)
        bench = price_repo.get_raw_prices(benchmark)
        if bench.empty:
            console.print(f"[red]ベンチマーク {benchmark!r} の価格が無い。[/] 暦を決められない。")
            raise typer.Exit(code=1)
        calendar = [stamp.date() for stamp in bench.index]

        # **流動性は、その日までの実測で引く。** イベント日を含めると、その日の
        # 出来高を知っていることになる。
        liquid: dict[tuple[str, dt.date], bool] = {}
        symbols = sorted({alert.symbol for alert in alerts})
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("売買代金を読む", total=len(symbols))
            for index, symbol in enumerate(symbols, start=1):
                progress.update(task, completed=index)
                raw = price_repo.get_raw_prices(symbol)
                if raw.empty:
                    continue
                rolling = (
                    (raw[CLOSE] * raw[VOLUME]).rolling(TURNOVER_WINDOW).mean().shift(1).dropna()
                )
                for stamp, value in rolling.items():
                    liquid[(symbol, stamp.date())] = bool(value >= min_turnover)

    lending = lending_index(Path(rosters))

    def liquid_on(symbol: str, on: dt.date) -> bool:
        return liquid.get((symbol, on), False)

    found = census(alerts, calendar, lending_on=lending, liquid_on=liquid_on)
    console.print(found.summary())

    if lending.covers is None:
        console.print(
            "[red]名簿が1枚も無い。[/] **貸借区分で絞れていない。** "
            "`checks\\営業日ごとの名簿を作る.bat` が先。"
        )
    else:
        console.print(
            f"[dim]名簿が覆うのは {lending.covers[0]} 〜 {lending.covers[1]}。"
            "**その前の発動は、貸借かどうかが分からない。**[/]"
        )

    table = Table(title="§2 の件数センサス（**リターンは入っていない**）")
    for column in ("測るもの", "値"):
        table.add_column(column, overflow="fold")
    table.add_row("発動イベント数（全期間）", f"{found.events:,}")
    table.add_row("イベントのあった日", f"{found.days_with_events:,}")
    table.add_row("上位1割の日の占有", f"{found.busiest_share:.0%}")
    table.add_row("同じ日に重なる発動（中央値）", f"{found.same_day_median:,}")
    table.add_row("貸借銘柄に絞った後", f"{found.after_lending:,}")
    table.add_row("うち貸借区分が読めなかった", f"{found.lending_unknown:,}")
    table.add_row("流動性の下限を通した後", f"{found.after_liquidity:,}")
    table.add_row("　うち IS（推定に使う）", f"{found.events_is:,}")
    table.add_row("[bold]　うち OOS（判定。§0 の期数）[/]", f"[bold]{found.events_oos:,}[/]")
    table.add_row("解除まで測れた", f"{found.resolved:,}")
    table.add_row("解除日が分からない", f"{found.censored:,}")
    days = found.release_days_median
    table.add_row(
        "[bold]規制が解けるまでの営業日（中央値）[/]",
        f"[bold]{days if days is not None else '測れない'}[/]",
    )
    table.add_row("[bold]§3 の保有窓 N[/]", f"[bold]{found.window} 営業日[/]")
    console.print(table)

    if found.by_year:
        years = Table(title="年ごとの発動件数（**平均だけ見ない**）")
        for column in ("年", "件数"):
            years.add_column(column, justify="right")
        for year, count in found.by_year.items():
            years.add_row(str(year), f"{count:,}")
        console.print(years)

    for line in found.warnings():
        console.print(f"[yellow]{line}[/]")

    console.print()
    console.print(
        f"[dim]IS と OOS の境は {found.split_on}（原本が覆う期間の真ん中。**件数の"
        "半分ではない**）。[/]"
    )
    console.print(
        "[dim]窓は式から出る——中央値と 20営業日の小さいほう。**見てから選び直さない。**"
        " 次は散らばりの実測（§0）で、そこで初めてリターンを触る。[/]"
    )


@app.command(name="composite-gate")
def composite_gate(  # noqa: PLR0913 - 複合型のルールが固定する条件をすべて受け取る
    components: str = typer.Option(..., "--components", help="ID:factor pairs, comma separated."),
    registry: str = typer.Option("docs/HYPOTHESES.md", "--registry", help="The registry file."),
    valuation: str | None = typer.Option(None, "--valuation", help="Month-end PBR file."),
    is_start: str = typer.Option("2009-01-01", "--is-start", help="First day of the IS window."),
    is_end: str = typer.Option("2017-12-31", "--is-end", help="Last day of the IS window."),
    oos_periods: int = typer.Option(104, "--oos-periods", help="Months the judgement will have."),
    tries: int = typer.Option(1, "--tries", help="Combinations you will try in IS. Fixed first."),
    window: int = typer.Option(DEFAULT_WINDOW, "--window", help="Volatility window in sessions."),
    min_symbols: int = typer.Option(MIN_SYMBOLS_PER_MONTH, "--min-symbols", help="Per month."),
    lags: int = typer.Option(LOWVOL_LAGS, "--lags", help="Newey-West lags, in months."),
) -> None:
    """Apply the composite rules as a gate, before anything is sealed.

    **判定ではない。** `docs/PURPOSE.md`「複合型のルール」を、文章ではなく
    **落ちる関門**にして当てる。人が読んで当てはめると、封印の前でも基準が動く。

    当てるのは6つ。**構成要素が登録済みか／IS で試す通り数を超えていないか／
    件数と流動性がどこまで落ちたか／§0 を通るか／種類が1つに偏っていないか／
    1通りを1本として数えること。**

    **合格条件「最良の構成要素単独を上回る」は、ここでは当てない。** それは
    OOS の判定で当たる。ここで出すのは IS の材料だけである。

    **`composite-gain` とは別物である。** あちらの r は 2026-09-05 に測って
    閉じてあり、「曖昧域で財務系の再測定はしない」と書いてある。ここは複合型を
    **それ自体として登録して判定する**ための関門で、r を測り直しはしない。
    """
    from stock_ai.backtest.antivalue import USABLE_FROM
    from stock_ai.backtest.composite import PASS as COMPOSITE_PASS
    from stock_ai.backtest.composite import (
        Component,
        Coverage,
        Design,
        beats_best,
        kinds,
        over_budget,
        single_kind,
        unregistered,
    )
    from stock_ai.backtest.cross_section import beta_to_benchmark, build_estimators
    from stock_ai.backtest.factor_panel import NEEDS_VALUATION, build_panel
    from stock_ai.backtest.multiplicity import (
        HYPOTHESIS_BUDGET,
        calibrated_t,
    )
    from stock_ai.backtest.power import estimate_power, gate
    from stock_ai.data.valuation_monthly import DEFAULT_PATH
    from stock_ai.data.valuation_monthly import read as read_valuation
    from stock_ai.hypotheses import read_registry

    settings = get_settings()
    configure_logging(settings.log_level)

    cut = _parse_date(is_end)
    if cut is None:
        raise typer.BadParameter(f"--is-end must be YYYY-MM-DD; got {is_end!r}.")
    begin = _parse_date(is_start)
    if begin is None:
        raise typer.BadParameter(f"--is-start must be YYYY-MM-DD; got {is_start!r}.")
    if begin >= cut:
        raise typer.BadParameter(f"--is-start ({begin}) must come before --is-end ({cut}).")

    parsed: list[Component] = []
    for chunk in components.split(","):
        name, _, factor = chunk.strip().partition(":")
        if not name or not factor:
            raise typer.BadParameter(f"--components wants ID:factor pairs; got {chunk!r}.")
        parsed.append(Component(name.strip(), factor.strip()))
    try:
        design = Design(tuple(parsed), (1.0,) * len(parsed), tries_in_is=tries)
    except ValueError as error:
        console.print(f"[red]{error}[/]")
        raise typer.Exit(code=1) from error

    wants_pbr = any(factor in NEEDS_VALUATION for factor in design.factors)
    if wants_pbr:
        # **2009年より前は使わない。** `pbr` が 92% 以上埋まるのは 2009年からで、
        # 2008年は 63% しかない。**埋まっている銘柄だけが選ばれると断面が歪む。**
        #
        # 最初はここを渡し忘れていて、盤面が 2008-09 まで遡った。事前登録 §6 が
        # 108ヶ月と決めているのに **112ヶ月**が出た（2026-09-16）。**例外は
        # 出ない。** 月数を数えて初めて分かる。
        begin = max(begin, USABLE_FROM)

    source = Path(registry)
    if not source.is_file():
        console.print(f"[red]{source} が無い。[/]")
        raise typer.Exit(code=1)
    known = read_registry(source)
    kind_of = {item.identifier: item.kind for item in known}

    console.print("[bold yellow]これは判定ではない。[/] 封印の前に当てる関門である。")
    console.print(
        f"[dim]IS は {begin} 〜 {cut}。OOS（{oos_periods}ヶ月）には1日も触れない。"
        f"IS で試す通り数は {tries} と決めてある。[/]"
    )
    console.print()

    # --- 規則1: 構成要素は登録済みか -----------------------------------------
    missing = unregistered(design, [item.identifier for item in known])
    if missing:
        console.print(
            f"[red]封印しない。[/] 登録が無い構成要素がある: {missing}。"
            "**その脚が何を主張しているのかが文書に残らない。**"
        )
        raise typer.Exit(code=1)

    spread_of_kinds = kinds(design, kind_of)
    console.print(
        "[green]構成要素は全部登録済み。[/] 種類の内訳: "
        + "、".join(f"{key} {count}" for key, count in sorted(spread_of_kinds.items()))
    )
    if single_kind(design, kind_of):
        console.print(
            "[yellow]種類が1つしか入っていない。[/] "
            "**2026-09-05 に束ねた3本も3本とも technical だった。** "
            "禁止ではないが、種類をまたぐ複合が手つかずのままになる。"
        )

    # --- 規則2: IS で試す通り数 ----------------------------------------------
    if over_budget(design, tries):  # pragma: no cover - tries は自分自身
        console.print("[red]封印しない。[/] IS で決めた通り数を超えている。")
        raise typer.Exit(code=1)

    frame = None
    if wants_pbr:
        frame = read_valuation(Path(valuation) if valuation else DEFAULT_PATH)
        if frame.empty:
            console.print("[red]月末の PBR が無い。[/] `checks\\月末のPBRを抜き出す.bat` が先。")
            raise typer.Exit(code=1)

    database = Database()
    database.create_all()

    # --- 規則3: 単独も、同じ盤面から取る -------------------------------------
    #
    # **盤面を1つしか作らない。** 別々に組むと、比が「合成の利得」ではなく
    # 「universe の差」を含む。
    try:
        panel = build_panel(
            database,
            factors=design.factors,
            start=begin,
            end=cut,
            window=window,
            min_symbols=min_symbols,
            valuation=frame,
        )
        # **比べる相手は、同じ窓で組む。** 窓が違うと「脚を足して失ったもの」
        # ではなく「窓の差」を測る。最初はここも `start` を渡しておらず、
        # 低ボラだけの盤面が 2002年まで遡って 184ヶ月出た。合成は 112ヶ月なので
        # 「月が 39% 減った」と警告したが、**減ったのではなく最初から別の窓を
        # 見ていた。**
        alone = build_panel(
            database,
            factors=design.factors[:1],
            start=begin,
            end=cut,
            window=window,
            min_symbols=min_symbols,
            valuation=frame,
        )
    except ValueError as error:
        console.print(f"[red]盤面を作れなかった: {error}[/]")
        raise typer.Exit(code=1) from error

    # --- 規則6: 件数と流動性の内訳 -------------------------------------------
    coverage = Coverage(
        months=len(panel.months),
        median_symbols=int(median([len(month) for month in panel.sections]))
        if panel.sections
        else 0,
        excluded_thin=panel.excluded_thin,
        excluded_no_history=panel.excluded_no_history,
        excluded_discontinuity=panel.excluded_discontinuity,
        excluded_no_pbr=panel.excluded_no_pbr,
        months_single=len(alone.months),
        median_symbols_single=int(median([len(month) for month in alone.sections]))
        if alone.sections
        else 0,
    )
    table = Table(title="件数と流動性の内訳（**AND条件は件数が急減する**）")
    for column in ("項目", "合成", f"{design.factors[0]} だけ"):
        table.add_column(column, overflow="fold")
    table.add_row("月数", f"{coverage.months}", f"{coverage.months_single}")
    table.add_row(
        "1ヶ月あたり銘柄（中央値）",
        f"{coverage.median_symbols:,}",
        f"{coverage.median_symbols_single:,}",
    )
    table.add_row("流動性で外した銘柄月", f"{panel.excluded_thin:,}", "—")
    table.add_row("履歴が足りない銘柄月", f"{panel.excluded_no_history:,}", "—")
    table.add_row("不連続で外した銘柄月", f"{panel.excluded_discontinuity:,}", "—")
    table.add_row("PBR が無い銘柄月", f"{panel.excluded_no_pbr:,}", "—")
    console.print(table)
    for line in coverage.warnings():
        console.print(f"[yellow]{line}[/]")

    if not panel.months:
        console.print("[red]封印しない。[/] 月が1つも残っていない。**比べていない。**")
        raise typer.Exit(code=1)

    # --- 規則4の材料: 合成と、脚ごとの単独を、同じ盤面から -------------------
    #
    # **向きは「大きいほど買う側」。** 既定のままだと分位5を買う。例外は出ない
    # （2026-09-05 に実際に出て行った形）。
    built = build_estimators(panel.composite(), panel.benchmark, higher_is_better=True)
    if built.months < 2:
        console.print("[red]封印しない。[/] 断面がばらつく月が2つ未満。")
        raise typer.Exit(code=1)
    beta = beta_to_benchmark(built.quantile_spread, built.benchmark)
    net = built.alpha(built.quantile_spread, beta)

    target = calibrated_t(HYPOTHESIS_BUDGET)
    estimate = estimate_power(net, lags=lags)
    detectable = estimate.detectable(oos_periods, target_t=target)
    mean = fmean(net)
    stderr = estimate.standard_error(len(net))
    low, high = mean - 1.96 * stderr, mean + 1.96 * stderr

    zero = Table(title="§0 に入れる材料（IS から。判定ではない）")
    for column in ("項目", "1期あたり", "年あたり"):
        zero.add_column(column, overflow="fold")
    zero.add_row("1期あたりのSD（α）", f"{estimate.daily_sd:.2%}", "—")
    zero.add_row("重なりの膨張", f"{estimate.inflation:.2f}x", "—")
    zero.add_row("判定に使える期数", f"{oos_periods}", f"{oos_periods / 12:.1f}年")
    zero.add_row("検出できる差", f"{detectable:.3%}", f"年 {detectable * 12:.1%}")
    zero.add_row("見込みの下限", f"{low:.3%}", f"年 {low * 12:+.1%}")
    zero.add_row("見込みの上限", f"{high:.3%}", f"年 {high * 12:+.1%}")
    zero.add_row("β", f"{beta:+.2f}", "IS で推定")
    console.print(zero)

    # --- 規則4の比較対象: 脚ごとの単独（同じ盤面） ---------------------------
    composite_t = mean / stderr if stderr > 0 else float("nan")
    singles = Table(title="合成と、脚ごとの単独（**同じ盤面・同じ月**）")
    for column in ("脚", "説", "IS の t（α）"):
        singles.add_column(column, overflow="fold")
    singles.add_row("[bold]合成[/]", "—", f"[bold]{composite_t:+.2f}[/]")
    leg_t_of: dict[str, float] = {}
    for component in design.components:
        column = panel.column(component.factor)
        leg = build_estimators(column, panel.benchmark, higher_is_better=True)
        if leg.months < 2:
            singles.add_row(component.factor, component.hypothesis_id, "測れない")
            continue
        leg_beta = beta_to_benchmark(leg.quantile_spread, leg.benchmark)
        leg_net = leg.alpha(leg.quantile_spread, leg_beta)
        leg_power = estimate_power(leg_net, lags=lags)
        leg_t_of[component.factor] = fmean(leg_net) / leg_power.standard_error(len(leg_net))
        singles.add_row(
            component.factor, component.hypothesis_id, f"{leg_t_of[component.factor]:+.2f}"
        )
    console.print(singles)

    # **合成の t を、読む側に計算させない。**
    #
    # 最初は脚だけを刷って、合成は見込みの幅から逆算するしかなかった。
    # **出した数字を自分で見る**（`CLAUDE.md`）。並べておけば目に入る。
    if leg_t_of:
        margin, reason = beats_best(composite_t, leg_t_of)
        colour = "green" if margin == COMPOSITE_PASS else "yellow"
        console.print(f"[{colour}]IS では: {reason}[/]")
    console.print(
        "[dim]**これは合格線であって、判定ではない。** 合格には「最良の単独を"
        "上回る」ことが要る（`docs/PURPOSE.md`）が、当てるのは **OOS** である。"
        "IS で上回っていることは、OOS で上回ることを意味しない。[/]"
    )

    # --- 規則5 と §0 の当てはめ ----------------------------------------------
    console.print()
    decision = gate(detectable, low, high)
    colour = "green" if decision.passed else "red"
    console.print(f"[bold {colour}]{decision.verdict}[/] {decision.reading}")
    console.print(
        f"[dim]必要な t は {target:.2f}（予算 {HYPOTHESIS_BUDGET} 本）。"
        f"この複合は累計に **{design.counts_as} 本**として数える"
        "——構成要素の数ではない。[/]"
    )


@app.command(name="hypothesis-report")
def hypothesis_report(
    registry: str = typer.Option("docs/HYPOTHESES.md", "--registry", help="The registry file."),
    into: str = typer.Option("reports", "--into", help="Where reports/<ID>/ go."),
) -> None:
    """Write reports/<ID>/ for every hypothesis, judged or not.

    **判定に関係なく出す**（`docs/PURPOSE.md`）。不合格も成果である。

    **書き直さない。** 構造のある項目は表から、経緯は `HYPOTHESES.md` の節を
    そのまま切り出して並べる。元を直せばレポートも直る——二重管理を作らない。

    **空欄を埋めない。** 出典が「未記載」ならレポートにもそう出る。
    """
    from stock_ai.hypotheses import consumed, read_registry, write_reports

    settings = get_settings()
    configure_logging(settings.log_level)

    source = Path(registry)
    if not source.is_file():
        console.print(f"[red]{source} が無い。[/]")
        raise typer.Exit(code=1)

    found = read_registry(source)
    if not found:
        console.print(
            f"[red]{source} から1本も読めなかった。[/] "
            "**「### 登録」の表が見つからないか、形が変わっている。**"
        )
        raise typer.Exit(code=1)

    written = write_reports(source, Path(into))
    console.print(f"{len(written)} 本ぶんを {into}/<ID>/README.md に書いた。")

    table = Table(title="説ごとの状態")
    for column in ("ID", "種類", "判定", "出典"):
        table.add_column(column, overflow="fold")
    for hypothesis in found:
        table.add_row(
            hypothesis.identifier,
            hypothesis.kind,
            hypothesis.verdict if hypothesis.judged else f"[dim]{hypothesis.verdict}[/]",
            "あり"
            if hypothesis.source_traceable
            else ("[dim]たどれない[/]" if hypothesis.source_recorded else "[yellow]未記載[/]"),
        )
    console.print(table)

    # **陰性対照は予算に数えない。** 世界について何も主張していないので、
    # 「当たりを引こうとした回数」に入らない——補正が数えたいのはそれである。
    counted = [hypothesis for hypothesis in found if hypothesis.counted]
    judged = consumed(found)
    controls = len(found) - len(counted)
    extra = f"（ほかに陰性対照が {controls} 本。**予算に数えない**）" if controls else ""
    console.print(
        f"判定を消費したのは [bold]{len(judged)}[/] 本、登録は {len(counted)} 本{extra}。"
        "[dim] 多重検定はこの本数で考える（`power-budget`）。[/]"
    )
    blank = [hypothesis for hypothesis in found if not hypothesis.source_recorded]
    if blank:
        console.print(f"[yellow]出典が何も記録されていない説が {len(blank)} 本ある。[/]")
    vague = [
        hypothesis
        for hypothesis in found
        if hypothesis.source_recorded and not hypothesis.source_traceable
    ]
    if vague:
        # **埋めない。数える。** あとから論文を探して埋めるのは、いま無い出典を
        # 作ることである。空欄のほうが正直である。
        console.print(
            f"[dim]出所は分かるがたどれない説が {len(vague)} 本。"
            "**あとから論文を探して埋めない。** これから登録するものに "
            "URL・主張の一文・見た日を書く。[/]"
        )


@app.command(name="power-budget")
def power_budget(
    alpha: float = typer.Option(
        FAMILY_ALPHA, "--alpha", help="Family-wise two-sided level to split."
    ),
) -> None:
    """Show what multiple testing costs, before a budget is chosen.

    **当たりを引こうとした回数が多いほど、まぐれ当たりも増える。**
    `docs/PURPOSE.md` の「合格判定では多重検定を考慮する」はそのことである。

    ここは決めない。**代償を並べるだけ。** 1本だけ選んで出すと、その線がどこ
    から来たのか分からなくなる。予算を増やすことの値段が読める形にしておく。

    決めた予算は**事前登録に書いてから封印する。** あとから動かさないため。
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    table = Table(title="何本試すつもりかで、合格の線がどう動くか")
    for column, justify in (
        ("予算（本）", "right"),
        ("1本あたり α", "right"),
        ("必要な t", "right"),
        ("補正なしとの差", "right"),
    ):
        table.add_column(column, justify=justify)
    for step in ladder():
        table.add_row(
            f"{step.budget}",
            f"{step.alpha:.4f}",
            f"{step.required_t:.2f}",
            f"{step.cost_in_t:+.2f}" if step.cost_in_t else "—",
        )
    console.print(table)
    console.print(
        f"[dim]全体の有意水準は両側 {alpha:.2f}。"
        "**すでに封印した説の線は動かさない**——判定が出たあとに基準を変える"
        "ことになる。掛かるのは、これから封印する説だけである。[/]"
    )
    console.print(
        "[dim]予算は `power-gate --budget N` に渡すと、検出できる差に効く。"
        "**封印前に事前登録へ書くこと。**[/]"
    )


@app.command(name="power-gate")
def power_gate(
    sd: float = typer.Option(..., "--sd", help="Per-period SD, in percent (e.g. 1.84)."),
    periods: int = typer.Option(..., "--periods", help="Periods the judgment would have."),
    low: float = typer.Option(..., "--low", help="Plausible effect, annual percent, floor."),
    high: float = typer.Option(..., "--high", help="Plausible effect, annual percent, ceiling."),
    inflation: float = typer.Option(1.0, "--inflation", help="Overlap inflation. 1.0 if none."),
    per_year: int = typer.Option(12, "--per-year", help="Periods a year. 12 monthly, 250 daily."),
    target_t: float = typer.Option(TARGET_T, "--target-t", help="t required to pass."),
    budget: int | None = typer.Option(
        None, "--budget", help="How many hypotheses you plan to judge in total."
    ),
    pipe: str = typer.Option(
        "monthly", "--pipe", help="Which pipe was measured: monthly, event or event-index."
    ),
) -> None:
    """Decide whether a test is worth sealing at all - before it is sealed.

    Two registrations found out they were underpowered only after sealing. The
    third knew before sealing, wrote "a coin flip" into the document, ran it
    anyway, and landed on the losing side. **Knowing was not enough; there was
    nowhere to stop.** This is that place.

    The rule is one line: **the floor of the plausible effect must clear the
    detectable difference.** Not the midpoint - a test that only passes when
    the anomaly comes in at the top of its range is a test decided by luck.

    Nothing here touches returns. The SD comes from periods the judgment will
    not use, exactly as the power estimate does, so running this spends no part
    of the one judgment.

    When it refuses, the output says how many periods the design would need.
    "Underpowered" leaves nobody with a move; "21 years for a 3% effect" can be
    subtracted from the years actually in hand.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    if periods < 1:
        raise typer.BadParameter(f"--periods must be at least 1; got {periods}.")

    # **何本試すつもりかを、封印前に決める。**
    #
    # 当たりを引こうとした回数が多いほど、まぐれ当たりも増える。20本試せば、
    # 本当は何も無くても1本は両側5%を越える。
    #
    # **「いま何本目か」で線を決めない。** 本数は増えるので、封印のたびに線が
    # 動いてしまう。先に予算を決めて割れば、線は動かない。
    if budget is not None:
        if budget < 1:
            raise typer.BadParameter(f"--budget must be at least 1; got {budget}.")
        adjusted = adjust(budget)
        console.print(f"[dim]{adjusted.summary()}[/]")
        # **校正した線を当てる。** ここだけ `required_t`（3.02）のままだった
        # ——他の判定箇所は全部 `calibrated_t` に移してあったのに、**この関門
        # だけ取り残されていた**（2026-09-19 に #12 で気付いた）。
        #
        # **緩める向きの取り違えである。** 線が低ければ検出できる差も小さく
        # 出るので、**通ってはいけない設計が §0 を通る。**
        try:
            target_t = line_for(pipe, budget)
        except ValueError as problem:
            raise typer.BadParameter(str(problem)) from problem
        console.print(
            f"[dim]線は `t ≥ {target_t:.2f}`（{pipe} の管。素の "
            f"{adjusted.required_t:.2f} に、陰性対照で測った膨張を掛けた）。[/]"
        )
        if budget != HYPOTHESIS_BUDGET:
            console.print(
                f"[yellow]このプロジェクトが決めた予算は {HYPOTHESIS_BUDGET} 本である。[/] "
                "**違う予算で封印するなら、その理由を事前登録に書くこと。**"
            )
    elif target_t == TARGET_T:
        # **黙って線を変えない。黙って忘れさせもしない。**
        #
        # 既定を 3.02 にすると、封印済みの事前登録を再現しようとした人が、
        # 当時と違う答えを受け取る。かといって何も言わないと、多重検定を
        # 決めたこと自体が忘れられる。
        console.print(f"[yellow]予算を渡していない。この線（t≥{TARGET_T}）は補正なしである。[/]")
        console.print(
            f"[dim]これから封印するなら `--budget {HYPOTHESIS_BUDGET}`"
            f"（t≥{adjust(HYPOTHESIS_BUDGET).required_t:.2f}）。"
            "補正なしは、封印済みのものを再現するときだけ。[/]"
        )

    per_period_sd = sd / 100.0
    floor = low / 100.0 / per_year
    ceiling = high / 100.0 / per_year

    try:
        detectable = target_t * (per_period_sd * inflation) / math.sqrt(periods)
        result = gate(detectable, floor, ceiling)
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc

    table = Table(title="§0 検出可能性ゲート（平均は見ない）")
    for column in ("項目", "1期あたり", "年あたり"):
        table.add_column(column, justify="left" if column == "項目" else "right")
    table.add_row("1期あたりのSD", f"{per_period_sd * 100:.2f}%", "")
    table.add_row("重なりの膨張", f"{inflation:.2f}x", "")
    table.add_row("判定に使える期数", f"{periods:,}", f"{periods / per_year:.1f}年")
    table.add_row(
        "[bold]検出できる差[/]",
        f"[bold]{detectable * 100:.3f}%[/]",
        f"[bold]年 {detectable * per_year * 100:.1f}%[/]",
    )
    table.add_row("見込みの下限", f"{floor * 100:.3f}%", f"年 {low:.1f}%")
    table.add_row("見込みの上限", f"{ceiling * 100:.3f}%", f"年 {high:.1f}%")
    console.print(table)

    console.print()
    if result.passed:
        console.print(f"[green][bold]{result.verdict}[/][/] {result.reading}")
    else:
        console.print(f"[red][bold]{result.verdict}[/][/] {result.reading}")

    # **足りないときは「何期あれば足りるか」を出す。** 「検出力不足」だけでは
    # 打ち手が浮かばない。年数にすれば、手元の年数と引き算ができる。
    if not result.passed:
        console.print()
        lines: list[tuple[str, bool, int, float]] = []
        for annual in sorted({low, (low + high) / 2, high}):
            if annual <= 0:
                continue
            count = periods_needed(per_period_sd, inflation, annual / 100.0 / per_year, target_t)
            # **§0 が見るのは下限だけ。** 中央も上限も参考である。
            lines.append((f"年 {annual:.1f}%", annual == low, count, count / per_year))
        console.print(
            _needed_table("§0 を通すのに要る期数", "期数", lines, periods, periods / per_year)
        )
        console.print()
        if floor <= 0:
            # **下限が 0 以下なら「何倍改善すれば通る」は計算できない。**
            #
            # 負の数を何倍しても正にはならない。ここで落ちていた——
            # `required_improvement` が例外を投げ、traceback がそのまま出た
            # （2026-09-16、#11）。**下限が 0 をまたぐのは珍しい形ではない。**
            # 段2 で自分の IS から見込みを置けば、効かない設計では普通に起きる。
            console.print(
                "[yellow]**「推定量を何倍改善すれば通るか」は計算できない。**[/] "
                f"見込みの下限が 年 {low:.1f}% で、0 をまたいでいる。"
            )
            console.print(
                "[dim]倍率は「下限を検出できる差まで持ち上げる比」である。"
                "**下限が負なら、持ち上げる先が無い。** 下限が 0 をまたぐのは"
                "「効果が小さい」ではなく、**向きすら決まっていない**ということ"
                "である。改善の倍率ではなく、設計そのものを見ること。[/dim]"
            )
        else:
            factor = required_improvement(detectable, floor)
            console.print(
                f"期数を増やせないなら、**推定量を [bold]{factor:.2f} 倍[/]"
                "改善するしかない**（見込みの下限で通すために）。"
            )
            console.print(
                "[dim]その改善は **t の比**で測る。SD の比ではない。分位スプレッドは"
                "断面が正規なら ``2.8 × 1σチルト`` にあたり、**推定量を変えると SD も"
                "効果も一緒に縮む。** SD 比を掛けたところに文献の分位スプレッドの"
                "効果量を当てると、2.8倍の改善が無料で出たように見える。[/dim]"
            )

    console.print(
        "[dim]このゲートは平均を見ない。**「効果がありそうだから通す」は書けない。**[/dim]"
    )
    raise typer.Exit(code=0 if result.passed else 2)


@app.command(name="composite-gain")
def composite_gain(
    start: str = typer.Option("2014-01-01", "--start", help="First month, matching the judgment."),
    window: int = typer.Option(DEFAULT_WINDOW, "--window", help="Volatility window in sessions."),
    min_symbols: int = typer.Option(MIN_SYMBOLS_PER_MONTH, "--min-symbols", help="Per month."),
    min_turnover: float = typer.Option(MIN_TURNOVER, "--min-turnover", help="Liquidity floor."),
    lags: int = typer.Option(LOWVOL_LAGS, "--lags", help="Newey-West lags, in months."),
    benchmark: str = typer.Option(BENCHMARK, "--benchmark", help="Sets the calendar."),
) -> None:
    """Measure r, the t gain from combining factors rather than using one.

    **This is not a judgement.** The thresholds were fixed before this ran and
    are written in ``docs/HYPOTHESES.md``: r >= 2.0 proceeds to the financial
    extraction, 1.5 <= r < 2.0 is the ambiguous band and **stops**, r < 1.5
    stops. The 1.5 line only separates "clearly short" from "close" in the
    record; the action either side of it is the same.

    **The ambiguous band stops on purpose.** "It is an underestimate because
    price factors correlate, so adding the financial ones would clear it" is an
    argument that only becomes available after seeing the number, and using it
    is indistinguishable from changing the measurement until it agrees.

    r is the composite's t divided by the **best** single factor's t. Picking
    the best after the fact inflates the denominator, so r comes out
    conservative. That direction is the safe one.

    Everything is measured on alpha with beta fixed from the estimation period,
    on the quintile sort - the cross-sectional estimator was calibrated at 0.93
    and is not used. Same universe for every column, because a composite
    compared against a factor built on a different universe measures the
    universe, not the combination.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    begin = _parse_date(start)
    if begin is None:
        raise typer.BadParameter(f"--start must be YYYY-MM-DD; got {start!r}.")

    console.print(
        "[bold yellow]これは判定ではない。[/] 閾値は測る前に確定済み（`docs/HYPOTHESES.md`）。"
    )
    console.print(
        "[dim]r ≥ 2.0 通過 / 1.5 ≤ r < 2.0 曖昧域→打ち切り / r < 1.5 不足。"
        "**測定後に変更しない。曖昧域で財務系の再測定はしない。**[/dim]"
    )
    console.print()

    database = Database()
    database.create_all()

    # **最初に検算する。** 低ボラだけの盤面が #7 を再現しなければ、フィルタが
    # 揃っていない。揃っていない盤面で比を取っても、それは合成の利得ではない。
    console.print("[bold]検算：低ボラだけの盤面が #7 を再現するか。[/]")
    try:
        check_panel = build_panel(
            database,
            factors=("低ボラ",),
            benchmark=benchmark,
            start=begin,
            window=window,
            min_turnover=min_turnover,
            min_symbols=min_symbols,
        )
        check_prior = build_panel(
            database,
            factors=("低ボラ",),
            benchmark=benchmark,
            end=begin - dt.timedelta(days=1),
            window=window,
            min_turnover=min_turnover,
            min_symbols=min_symbols,
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc

    reproduced = _alpha_of(check_panel, check_prior, "低ボラ", lags)
    console.print(
        f"  低ボラ単独: β {reproduced[0]:.3f}、α {reproduced[1].mean * 100:+.3f}%、"
        f"t {reproduced[1].t_statistic:+.2f}"
        f"  [dim]（#7 は β 0.542、α +0.242%、t +1.70）[/dim]"
    )
    close_enough = abs(reproduced[1].t_statistic - LOWVOL_JUDGED_T) <= 0.05
    if close_enough:
        console.print("  [green]再現した。フィルタは揃っている。[/]")
    else:
        console.print(
            "  [yellow]再現しない。**どこかで違うフィルタを通している。**[/] "
            "比を取っても合成の利得にならないので、先に揃える。"
        )
        raise typer.Exit(code=2)

    console.print()
    try:
        panel = build_panel(
            database,
            benchmark=benchmark,
            start=begin,
            window=window,
            min_turnover=min_turnover,
            min_symbols=min_symbols,
        )
        prior = build_panel(
            database,
            benchmark=benchmark,
            end=begin - dt.timedelta(days=1),
            window=window,
            min_turnover=min_turnover,
            min_symbols=min_symbols,
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc

    console.print(
        f"合成の盤面: {len(panel.months):,} ヶ月"
        f"（{panel.months[0]} 〜 {panel.months[-1]}）、"
        f"因子 {'＋'.join(panel.factors)}。"
    )
    console.print(
        "[dim]モメンタムが252営業日を要るので、低ボラ単独の盤面より月数・銘柄数が"
        "少ない。**単一因子もこの盤面から取る**ので、比には効かない。[/dim]"
    )

    table = Table(title="単一因子と合成（α、β は推定期間で固定、分位ソート）")
    for column in ("signal", "β", "α の平均", "標準誤差(NW)", "t"):
        table.add_column(column, justify="left" if column == "signal" else "right")

    singles: dict[str, float] = {}
    for name in panel.factors:
        beta, result = _alpha_of(panel, prior, name, lags)
        singles[name] = result.t_statistic
        table.add_row(
            name,
            f"{beta:.3f}",
            f"{result.mean * 100:+.3f}%",
            f"{result.standard_error * 100:.3f}%",
            f"{result.t_statistic:+.2f}",
        )

    combined_beta, combined = _alpha_of(panel, prior, None, lags)
    table.add_row(
        "[bold]合成（等加重）[/]",
        f"{combined_beta:.3f}",
        f"[bold]{combined.mean * 100:+.3f}%[/]",
        f"{combined.standard_error * 100:.3f}%",
        f"[bold]{combined.t_statistic:+.2f}[/]",
    )
    console.print(table)

    best_name = max(singles, key=lambda key: singles[key])
    best = singles[best_name]
    console.print(
        f"[dim]最も良い単一因子は **{best_name}**（t {best:+.2f}）。"
        "**後から選んでいるので分母が大きくなる方向**で、r は控えめに出る。[/dim]"
    )

    ratio = t_ratio(combined.t_statistic, best)
    console.print()
    if ratio is None:
        console.print(
            "[yellow]符号が違うので比を出さない。[/] **「何倍良い」が意味を持たない。** "
            "閾値の表では r < 1.5 と同じ扱いになる。"
        )
        verdict, reading = COMPOSITE_STOP, "比が取れない。4' へ。"
    else:
        console.print(f"**r = [bold]{ratio:.2f}[/]**")
        verdict, reading = composite_verdict(ratio)

    console.print()
    style = "green" if verdict == COMPOSITE_PROCEED else "red"
    console.print(f"[{style}][bold]{verdict}[/][/] {reading}")
    console.print(
        "[dim]この当てはめは測る前に確定させた表による（`docs/HYPOTHESES.md`）。"
        "結果を見てから線を引き直していない。[/dim]"
    )


def _alpha_of(panel, prior, factor: str | None, lags: int):
    """Build the alpha series from a panel, with beta fixed out of period.

    ``factor`` が ``None`` なら等加重の合成。**β は必ず推定期間から取る。**
    判定期間から取れば、その期間に合う調整を選んだことになる。

    ``factor_panel`` の signal は**大きいほど買う側**にそろえてある（低ボラは
    符号を反転済み）。``build_estimators`` の既定は「小さいほど買う側」なので、
    **``higher_is_better=True`` を渡さないと分位5を買う。** 例外は出ない——
    2026-09-05 に既定のまま渡して β 1.442、α −0.944%、t −2.18 が出た。
    """
    sections = panel.column(factor) if factor else panel.composite()
    prior_sections = prior.column(factor) if factor else prior.composite()

    now = build_estimators(sections, panel.benchmark, higher_is_better=True)
    before = build_estimators(prior_sections, prior.benchmark, higher_is_better=True)
    beta = beta_to_benchmark(before.quantile_long_only, before.benchmark)
    return beta, judge(now.alpha(now.quantile_long_only, beta), lags=lags)


@app.command(name="lowvol-estimator")
def lowvol_estimator(
    start: str = typer.Option("2014-01-01", "--start", help="First month, matching the judgment."),
    window: int = typer.Option(DEFAULT_WINDOW, "--window", help="Volatility window in sessions."),
    min_symbols: int = typer.Option(MIN_SYMBOLS_PER_MONTH, "--min-symbols", help="Per month."),
    min_turnover: float = typer.Option(MIN_TURNOVER, "--min-turnover", help="Liquidity floor."),
    lags: int = typer.Option(LOWVOL_LAGS, "--lags", help="Newey-West lags, in months."),
    benchmark: str = typer.Option(BENCHMARK, "--benchmark", help="Sets the calendar."),
) -> None:
    """Calibrate the estimator: quintile sort against cross-sectional regression.

    **This is not a judgement.** #6 and #7 are decided and closed. The t values
    here calibrate one estimator against another; they are not evidence for or
    against any hypothesis, and nothing here is compared to a pass threshold.
    Declared in ``docs/HYPOTHESES.md`` before this was written.

    **Ratios are taken on t, never on the standard deviation.** A quintile
    spread is roughly 2.8x a one-sigma tilt when the cross-section is normal,
    because the top quintile averages about +1.40 sigma and the bottom -1.40.
    Change the estimator and the spread and its noise shrink together. Matching
    standard deviations alone and then applying a published quintile-spread
    effect size would manufacture that 2.8x as a free improvement - the same
    trap this project keeps avoiding, moved from the noise to the effect. t is
    dimensionless, so it does not happen.

    Long-short is compared with long-short. The quintile spread and the tilt
    both hold zero net, so neither needs a beta. The long-only pair is reported
    separately and **carries no beta adjustment**, which is why it cannot be set
    beside the judgement's alpha of 1.70.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    begin = _parse_date(start)
    if begin is None:
        raise typer.BadParameter(f"--start must be YYYY-MM-DD; got {start!r}.")

    console.print(
        "[bold yellow]これは判定ではない。[/] #6 と #7 は判定済みで閉じている。"
        "**この数字を合否の主張に使わない。**"
    )
    console.print(
        "[dim]比べるのは t（無次元）。SD の比は取らない——推定量を変えると"
        "SD も効果も一緒に縮むので、比を掛けたところに文献の分位スプレッドの"
        "効果量を当てると 2.8倍が無料で出る。[/dim]"
    )
    console.print()

    database = Database()
    database.create_all()
    try:
        series = build_lowvol_series(
            database,
            Period.ALL,
            benchmark=benchmark,
            start=begin,
            window=window,
            min_turnover=min_turnover,
            min_symbols=min_symbols,
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc

    estimators = build_estimators(series.cross_sections, series.benchmark)
    if estimators.months < 2:
        console.print("[red]比べられる月が足りない。[/]")
        raise typer.Exit(code=1)

    console.print(
        f"{estimators.months:,} ヶ月（{series.months[0]} 〜 {series.months[-1]}）、"
        f"1ヶ月あたり中央値 {int(median(series.counts)):,} 銘柄。"
        + (
            f"[dim]断面がばらつかず落とした月 {estimators.skipped_flat}。[/dim]"
            if estimators.skipped_flat
            else ""
        )
    )

    # **同じ断面から作っている。** 別経路で組み直すと、比が「推定量の差」では
    # なく「フィルタの差」を含む。
    measured = {
        name: judge(values, lags=lags)
        for name, values in (
            ("分位1 − 分位5", estimators.quantile_spread),
            ("1σチルト（横断回帰）", estimators.tilt),
            ("分位1（等金額）", estimators.quantile_long_only),
            ("上側チルト（傾きで加重）", estimators.long_only_tilt),
        )
    }

    pairs = (
        ("ロングショート（β不要）", "分位1 − 分位5", "1σチルト（横断回帰）"),
        ("ロングオンリー（**β を引いていない**）", "分位1（等金額）", "上側チルト（傾きで加重）"),
    )
    for title, old_name, new_name in pairs:
        table = Table(title=title)
        for column in ("推定量", "平均", "標準誤差(NW)", "t"):
            table.add_column(column, justify="left" if column == "推定量" else "right")
        for name in (old_name, new_name):
            result = measured[name]
            table.add_row(
                name,
                f"{result.mean * 100:+.3f}%",
                f"{result.standard_error * 100:.3f}%",
                f"[bold]{result.t_statistic:+.2f}[/]",
            )
        console.print(table)

        ratio = t_ratio(measured[new_name].t_statistic, measured[old_name].t_statistic)
        if ratio is None:
            console.print(
                "  [yellow]符号が違うので比を出さない。[/] **「何倍良い」が意味を持たない。**"
            )
        else:
            console.print(f"  **t 比 = [bold]{ratio:.2f}[/] 倍**")
        console.print()

    console.print(
        "[dim]上の2つ目は β を引いていない。**標準誤差が市場リスクに支配される**"
        "ので、推定量の差はそこに埋もれる。#7 の α（t +1.70）とも直接比べられ"
        "ない。[/dim]"
    )
    console.print()

    # **α で比べる。** ゲートに掛けたい合成はロングオンリーで、その指標は α
    # である。生のロングオンリーは市場リスクに埋もれて推定量の差が見えない。
    console.print(
        "[bold]β を引いてもう一度比べる。[/] "
        "[dim]β は**推定期間から取って固定する**（#7 と同じ規律）。"
        "判定期間から取ると、その期間に合う調整を選んだことになる。[/dim]"
    )
    try:
        estimation = build_lowvol_series(
            database,
            Period.ALL,
            benchmark=benchmark,
            end=begin - dt.timedelta(days=1),
            window=window,
            min_turnover=min_turnover,
            min_symbols=min_symbols,
        )
    except ValueError as exc:
        console.print(f"[yellow]推定期間の系列を作れなかった: {exc}[/]")
        raise typer.Exit(code=1) from exc

    prior = build_estimators(estimation.cross_sections, estimation.benchmark)
    if prior.months < 2:
        console.print("[yellow]推定期間の月が足りない。β を固定できない。[/]")
        raise typer.Exit(code=1)

    console.print(
        f"[dim]推定期間 {estimation.months[0]} 〜 {estimation.months[-1]}、"
        f"{prior.months:,} ヶ月から β を取る。[/dim]"
    )

    alpha_table = Table(title="ロングオンリー（β を引いた＝#7 の主要指標と同じ形）")
    for column in ("推定量", "β（推定期間で固定）", "α の平均", "標準誤差(NW)", "t"):
        alpha_table.add_column(column, justify="left" if column == "推定量" else "right")

    alphas: dict[str, object] = {}
    for name, attribute in (
        ("分位1（等金額）", "quantile_long_only"),
        ("上側チルト（傾きで加重）", "long_only_tilt"),
    ):
        fixed = beta_to_benchmark(getattr(prior, attribute), prior.benchmark)
        result = judge(estimators.alpha(getattr(estimators, attribute), fixed), lags=lags)
        alphas[name] = result
        alpha_table.add_row(
            name,
            f"{fixed:.3f}",
            f"{result.mean * 100:+.3f}%",
            f"{result.standard_error * 100:.3f}%",
            f"[bold]{result.t_statistic:+.2f}[/]",
        )
    console.print(alpha_table)

    ratio = t_ratio(
        alphas["上側チルト（傾きで加重）"].t_statistic,
        alphas["分位1（等金額）"].t_statistic,
    )
    if ratio is None:
        console.print("  [yellow]符号が違うので比を出さない。[/]")
    else:
        console.print(f"  **t 比 = [bold]{ratio:.2f}[/] 倍**")

    console.print()
    console.print(
        "[bold]§0 ゲートに掛けるのは、この α の t 比である。[/] "
        "ゲートに掛けたい合成はロングオンリーで、その指標は α だからである。"
    )
    console.print(
        "[dim]ロングショートの比も併記しているが、そちらは建てられる形が違う"
        "（空売りの実行可能性は確かめていない）。[/dim]"
    )


@app.command(name="lowvol-bias")
def lowvol_bias(
    directory: str = typer.Option(
        str(DEFAULT_SNAPSHOT_DIR), "--dir", help="Where the dated rosters live."
    ),
    beta: float = typer.Option(SEALED_BETA, "--beta", help="Sealed beta. Do not refit it here."),
    window: int = typer.Option(DEFAULT_WINDOW, "--window", help="Volatility window in sessions."),
    min_symbols: int = typer.Option(MIN_SYMBOLS_PER_MONTH, "--min-symbols", help="Per month."),
    min_turnover: float = typer.Option(MIN_TURNOVER, "--min-turnover", help="Liquidity floor."),
    benchmark: str = typer.Option(BENCHMARK, "--benchmark", help="Sets the calendar."),
) -> None:
    """Measure the survivorship bias in the low-vol test - after the verdict.

    Runs the same months twice, changing exactly one thing: the universe. Once
    with the dated rosters, which hold the companies later delisted, and once
    with only the names still listed today. The difference is the bias.

    **This deliberately runs inside the judged period, and has no out-of-sample
    guard.** ``reversal-bias`` refuses to reach OOS because looking there would
    spend the one judgment. Here the judgment is already spent (§18, 2026-09-05)
    and the rosters only start in 2021-09, which is inside it. Copying that
    guard across would block the measurement the registration asks for.

    **It cannot change the verdict.** §11 pre-registered it as a diagnostic to
    read alongside the result, and the result is already recorded. Nothing here
    is compared against a threshold.

    The registration expects a smaller number than reversal's -0.040% per 20
    sessions: a company heading for delisting gets *more* volatile, so it lands
    in quintile 5 rather than the quintile this hypothesis buys. That is a
    prediction, which is why it is worth measuring rather than asserting.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    snapshots = membership(Path(directory))
    if len(snapshots) < 2:
        console.print(
            f"[yellow]名簿が {len(snapshots)} 件しかない。[/] "
            "先に [cyan]checks\\廃止銘柄の取り込み.bat[/] を実行する。"
        )
        raise typer.Exit(code=1)

    # **名簿の実際の先頭から測る。** 定数を信じて名簿の無い月を含めると、その
    # 期間だけ universe が空になり、静かに落ちる。
    begin = min(snapshots)
    console.print(
        f"名簿 {len(snapshots)} 件（{begin} 〜 {max(snapshots)}）。"
        f"測る期間は [bold]{begin} 以降[/]。"
    )
    console.print(
        "[dim]**判定期間の内側である。** これは §11 で事前登録した診断であって、"
        "判定の入力ではない。判定は §18 に記録済み。[/dim]"
    )

    database = Database()
    database.create_all()
    runs: dict[str, object] = {}
    for label, survivors_only in (("名簿あり", False), ("生存者のみ", True)):
        try:
            runs[label] = build_lowvol_series(
                database,
                Period.ALL,
                benchmark=benchmark,
                start=begin,
                window=window,
                min_turnover=min_turnover,
                min_symbols=min_symbols,
                snapshots=snapshots,
                survivors_only=survivors_only,
            )
        except ValueError as exc:
            console.print(f"[red]{label}: {exc}[/]")
            raise typer.Exit(code=1) from exc

    clean = runs["名簿あり"]
    survivors = runs["生存者のみ"]

    counts = Table(title="universe を差し替えると何が変わるか")
    for column in ("universe", "月数", "1ヶ月あたり中央値"):
        counts.add_column(column, justify="left" if column == "universe" else "right")
    for label, run in (("名簿あり", clean), ("生存者のみ", survivors)):
        counts.add_row(
            label,
            f"{len(run.months):,}",
            f"{int(median(run.counts)) if run.counts else 0:,}",
        )
    console.print(counts)

    # **同じ月で揃える。** 片方にしか無い月を混ぜると、バイアスではなく期間の
    # 違いを測ることになる。
    table = Table(title=f"名簿あり − 生存者のみ（月あたり、β={beta:.3f} 固定）")
    for column in ("指標", "名簿あり", "生存者のみ", "差", "標準誤差(NW)", "t", "月数"):
        table.add_column(column, justify="left" if column == "指標" else "right")

    gaps: dict[str, object] = {}
    for label, values in (
        ("α（分位1 − β×ベンチ）", lambda run: run.beta_adjusted(beta)),
        ("生の差（分位1 − ベンチ）", lambda run: run.long_only()),
    ):
        left = dict(zip(clean.months, values(clean), strict=True))
        right = dict(zip(survivors.months, values(survivors), strict=True))
        shared = sorted(set(left) & set(right))
        if not shared:
            console.print("[red]両方に共通する月が無い。[/]")
            raise typer.Exit(code=1)
        gap = survivorship_gap([left[m] for m in shared], [right[m] for m in shared])
        # **差にも誤差を付ける。** 平均だけ出すと、0 と区別できるのかが
        # 分からないまま「バイアスは +0.059% だった」と引用されていく。#6 の
        # −0.040% も誤差なしで登録に載った。同じことを繰り返さない。
        measured = judge(gap, lags=LOWVOL_LAGS)
        gaps[label] = measured
        table.add_row(
            label,
            f"{sum(left[m] for m in shared) / len(shared) * 100:+.3f}%",
            f"{sum(right[m] for m in shared) / len(shared) * 100:+.3f}%",
            f"[bold]{measured.mean * 100:+.3f}%[/]",
            f"{measured.standard_error * 100:.3f}%",
            f"{measured.t_statistic:+.2f}",
            f"{len(shared):,}",
        )
    console.print(table)

    measured = gaps["α（分位1 − β×ベンチ）"]
    primary = measured.mean
    console.print()
    if primary < 0:
        console.print(
            f"[bold]α のバイアスは {primary * 100:+.3f}%／月[/]（負）＝"
            "**生存者だけで測ると効果を大きく見せる。**"
        )
    elif primary > 0:
        console.print(
            f"[bold]α のバイアスは {primary * 100:+.3f}%／月[/]（正）＝"
            "**生存者だけで測ると効果を小さく見せる。** "
            "TOB・完全子会社化がプレミアム付きで消えるぶんが効いている可能性がある。"
        )
    else:
        console.print("[bold]α のバイアスは 0 だった。[/]")

    # **符号を読む前に、0 と区別が付くかを見る。** 付かないなら「符号はこちら
    # だった」も言えない。#6 の −0.040% は誤差なしで登録に載っている。
    if abs(measured.t_statistic) < TARGET_T:
        console.print(
            f"  [yellow]ただし t = {measured.t_statistic:+.2f} で、0 と区別が付かない。[/] "
            "**符号の向きも含めて、この期間では確かめられていない。** "
            f"月数は {measured.days} しかない。"
        )
    else:
        console.print(
            f"  t = {measured.t_statistic:+.2f}。**0 とは区別が付く。** "
            "符号の向きは、この期間については確かめられた。"
        )

    console.print(
        f"[dim]#6（リバーサル）は −0.040%／20営業日だった（**あちらも誤差は"
        f"測っていない**）。低ボラは破綻に向かう銘柄が分位5に落ちるので小さい"
        f"はず、と §11 に書いた。{abs(primary) * 100:.3f}% がその答えである。[/dim]"
    )
    console.print(
        "[yellow]判定は変わらない。[/] §18 に記録済みで、これは結果に添える数字である（§11）。"
    )


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
    # **差にも誤差を付ける。** この数字は「プロジェクト全体で使い回せる」ものと
    # して他の登録に引用される。平均だけ渡すと、0 と区別が付くのか分からない
    # まま次の設計の前提になる。重なりは判定と同じラグで織り込む。
    measured = judge(gap, lags=holding)
    average = measured.mean

    console.print()
    console.print(
        f"共通の {len(shared):,} 営業日で、[bold]名簿あり − 生存者のみ = "
        f"{average * 100:+.3f}%[/]（保有{holding}営業日あたり）"
        f"　標準誤差(NW) {measured.standard_error * 100:.3f}%、t {measured.t_statistic:+.2f}"
    )
    if abs(measured.t_statistic) < TARGET_T:
        console.print(
            f"  [yellow]t = {measured.t_statistic:+.2f} で 0 と区別が付かない。[/] "
            "**符号の向きも含めて、この期間では確かめられていない。**"
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


@app.command(name="high-screen")
def high_screen(
    is_end: str = typer.Option(
        HIGH_IS_END.isoformat(), "--is-end", help="Last day of IS. Cannot go past the sealed date."
    ),
    min_turnover: float = typer.Option(
        MIN_TURNOVER, "--min-turnover", help="Liquidity floor in yen."
    ),
) -> None:
    """Estimate the 52-week-high effect on IS only, and apply the sealed line.

    **§0 の段2 である。** 同じ設計の文献値が無いので、見込みの下限を自分の IS
    から推定する。封印しない線は**推定を1つも見ないうちに置いてある**
    （`HIGH_SEAL_FLOOR` = 1.26%、`docs/NEXT_CANDIDATES.md`「決定3」）。

    **OOS には触れない。** IS の終わりより後を渡すと終了コード2で止まる。
    渡し間違えたときに黙って OOS を混ぜないための関門で、**混ざったことは
    数字を見ても分からない。**
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    try:
        cutoff = dt.date.fromisoformat(is_end.strip())
    except ValueError as exc:
        raise typer.BadParameter(f"--is-end must be YYYY-MM-DD; got {is_end!r}.") from exc

    if cutoff > HIGH_IS_END:
        console.print(
            f"[red]--is-end {cutoff} は封印済みの IS の終わり {HIGH_IS_END} より後です。[/]\n"
            "  **OOS を混ぜることになります。** 混ざったことは数字を見ても分かりません。"
        )
        raise typer.Exit(code=2)

    database = Database()
    database.create_all()
    floor = min_turnover or MIN_TURNOVER

    console.print(
        f"§0 の段2。**IS（{cutoff} まで）だけで推定する。** "
        f"窓 {HIGH_HOLDING}営業日、売買代金 {floor / 1e8:.0f}億円以上。"
    )
    console.print(
        f"[bold]封印しない線は {HIGH_SEAL_FLOOR * 100:.2f}%[/]"
        "（推定を見る前に置いた。測定後に変更しない）。"
    )

    values = high_event_returns(database, holding=HIGH_HOLDING, min_turnover=floor, until=cutoff)
    if len(values) < 2:
        console.print(f"[red]IS のイベント日が足りません（{len(values)}）。[/]")
        raise typer.Exit(code=1)

    estimate = sum(values) / len(values)
    result = judge(values, lags=HIGH_HOLDING)

    table = Table(title=f"IS の推定（{cutoff} まで）")
    for column in ("イベント日", "1イベントあたり", "標準誤差(NW)", "t"):
        table.add_column(column, justify="right")
    table.add_row(
        f"{len(values):,}",
        f"[bold]{estimate * 100:+.2f}%[/]",
        f"{result.standard_error * 100:.2f}%",
        f"{result.t_statistic:+.2f}",
    )
    console.print(table)
    console.print(
        "[dim]**この t は合否ではない。** IS は選別に使う期間で、判定は OOS で"
        "一度だけ行う。ここでの t は推定の精度を読むためだけにある。[/]"
    )

    verdict, reading = high_verdict(estimate)
    colour = "green" if verdict == HIGH_SEAL else "yellow"
    console.print()
    console.print(f"[{colour}][bold]{verdict}[/][/] {reading}")
    console.print(
        f"[dim]当てはめた線は推定前に確定させたものである"
        f"（{HIGH_SEAL_FLOOR * 100:.2f}% ＝ OOS 1,197日での検出できる差 0.86% ＋ 費用 0.40%）。"
        "結果を見てから引き直していない。[/]"
    )


@app.command(name="high-power")
def high_power(
    holdings: list[int] = typer.Option(  # noqa: B008 - typer builds the default list
        [1, 5, 20], "--holding", help="Sessions held. Repeat to compare windows."
    ),
    min_turnover: float = typer.Option(
        MIN_TURNOVER, "--min-turnover", help="Liquidity floor in yen."
    ),
) -> None:
    """Measure the spread of the 52-week-high basket, printing no mean.

    §0 に入れる「検出できる差」は実測から取る、と `docs/NEXT_CANDIDATES.md` に
    書いてある。その実測がこれである。

    **分散を測ることは判定を消費しない。** 効果の大きさではなく散らばりを測って
    いるからで、#6・#7 と同じ手順・同じ理由による。**平均は表示しない。**
    表示すれば、封印の前に答えを見たことになる。

    保有日数を複数出すのは、**窓を選ぶのは分散を見てからでよい**ためである。
    分散は効果ではないので、ここで比べても多重検定にはならない。**封印する
    のは1本だけ**で、それを選ぶ根拠がこの表になる。
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    windows = sorted({holding for holding in holdings if holding >= 1})
    if not windows:
        raise typer.BadParameter("--holding must be at least 1.")

    database = Database()
    database.create_all()
    floor = min_turnover or MIN_TURNOVER

    console.print(
        f"52週高値更新の等加重バスケット。更新日の翌日の寄付きで買い、"
        f"保有日数ぶん後の終値で降りる。ベンチマーク控除。売買代金 {floor / 1e8:.0f}億円以上。"
    )
    console.print("[dim]**平均は出さない。** 分散と重なりの膨張だけを測る。[/]")

    table = Table(title="重なりを織り込んだ検出力（平均は含まない）")
    table.add_column("保有", justify="right")
    for column in ("イベント日", "日次SD", "上位1%を除いたSD", "重なりの膨張", "標準誤差"):
        table.add_column(column, justify="right")
    table.add_column(f"t≥{TARGET_T} に必要な差", justify="right")
    table.add_column("費用", justify="right")

    rows: list[tuple[int, float, float]] = []
    for holding in windows:
        values = high_event_returns(database, holding=holding, min_turnover=floor)
        if len(values) < 2:
            console.print(f"[yellow]保有{holding}日: イベント日が足りない（{len(values)}）。[/]")
            continue
        estimate = estimate_power(values, lags=holding)
        needed = estimate.detectable(len(values))
        trimmed, _dropped = trimmed_variance(values, fraction=0.01)
        # 毎日入って holding 日持つので、1日あたり 1/holding だけ入れ替わる。
        # 1イベントあたりの費用はロングオンリーの往復。
        cost = COST_ROUND_TRIP
        rows.append((holding, needed, cost))
        table.add_row(
            f"{holding}日",
            f"{len(values):,}",
            f"{estimate.daily_sd * 100:.2f}%",
            f"{trimmed**0.5 * 100:.2f}%",
            f"{estimate.inflation:.2f}x",
            f"{estimate.standard_error(len(values)) * 100:.3f}%",
            f"[bold]{needed * 100:.2f}%[/]",
            f"{cost * 100:.2f}%",
        )
    console.print(table)

    if not rows:
        return

    console.print(
        "[dim]「必要な差」は**1イベントあたり**である。費用を引いた残りが、"
        "文献の報告する効果量に届くかを見る。[/]"
    )
    for holding, needed, cost in rows:
        if needed + cost > 0.02:
            console.print(
                f"[yellow]保有{holding}日: 必要な差 {needed * 100:.2f}% ＋ 費用 "
                f"{cost * 100:.2f}% ＝ [bold]{(needed + cost) * 100:.2f}%[/]。[/] "
                "イベント型の報告値としては大きい。"
            )
        else:
            console.print(
                f"保有{holding}日: 必要な差 {needed * 100:.2f}% ＋ 費用 "
                f"{cost * 100:.2f}% ＝ [bold]{(needed + cost) * 100:.2f}%[/]。"
            )

    console.print()
    console.print(
        "[dim]この表は §0 の入力である。**封印するのは1本だけ。** どの窓にするかを"
        "決めてから、`stock-ai power-gate` に見込みの下限と一緒に掛ける。[/]"
    )


@app.command(name="event-census")
def event_census(
    what: str = typer.Option("all", "--what", help="limit | halt | high | all."),
    min_turnover: float = typer.Option(
        MIN_TURNOVER, "--min-turnover", help="Liquidity floor in yen. Same as the other censuses."
    ),
) -> None:
    """Count event populations without computing any returns.

    7本すべてが、検出力の最も不利な角にいた——長い保有窓と少ない独立観測。
    ここで数えるのはどれも保有1〜5営業日で、価格だけから検出できる。

    **数えるのが先である。** #1 では条件を満たす銘柄の 97.5% が流動性フィルタで
    消え、それが分かったのは検証を組んだあとだった。ここでは順序を逆にする。
    判定は消費しない。
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    chosen = what.strip().lower()
    if chosen not in {"limit", "halt", "high", "all"}:
        raise typer.BadParameter(f"--what must be limit, halt, high or all; got {what!r}.")

    database = Database()
    database.create_all()
    floor = min_turnover or MIN_TURNOVER

    console.print(
        f"売買代金 {floor / 1e8:.0f}億円以上。**リターンは計算しない。**判定は消費しない。"
    )

    results = []
    if chosen in {"limit", "all"}:
        results.append(count_limit_moves(database, min_turnover=floor))
    if chosen in {"halt", "all"}:
        results.append(count_halt_resumptions(database, min_turnover=floor))
    if chosen in {"high", "all"}:
        results.append(count_52w_highs(database, min_turnover=floor))

    summary = Table(title="母集団")
    for column in ("イベント", "フィルタ前", "フィルタ後", "通過率", "日数", "上位1割の日の占有"):
        summary.add_column(column, justify="right")
    for census in results:
        summary.add_row(
            census.kind,
            f"{census.raw_events:,}",
            f"{census.events:,}",
            f"{census.survival:.1%}",
            f"{census.trading_days:,}",
            f"{census.concentration():.0%}" if census.events else "—",
        )
    console.print(summary)
    console.print(
        "[dim]**日数が独立観測である。** 同じ日の銘柄は同じ市場の動きを共有するので、"
        "件数をそのまま独立観測に数えると検出できる差を小さく見積もる。[/]"
    )

    for census in results:
        if census.events == 0:
            console.print(f"[yellow]{census.kind}: 1件も残らなかった。[/]")
            continue

        table = Table(title=f"{census.kind}／年別")
        for column in ("年", "件数", "日数", "1日あたり"):
            table.add_column(column, justify="right")
        for year, count, day_count in census.by_year():
            table.add_row(str(year), f"{count:,}", f"{day_count:,}", f"{count / day_count:,.1f}")
        console.print(table)
        console.print(
            "1日あたり: "
            + "、".join(f"{name} {value:,}" for name, value in census.breadth())
            + "　／　売買代金（億円）: "
            + "、".join(f"{name} {value:,.1f}" for name, value in census.turnover_quantiles())
        )

        if census.fillable or census.unfillable:
            console.print(
                f"[bold]執行[/]: 翌日に買えたのは {census.fillable:,} 件。"
                f"翌日も張り付いて買えなかった [bold]{census.unfillable:,}[/] 件"
                f"（{census.unfillable / census.events:.1%}）、翌日の足が無い "
                f"{census.no_next_bar:,} 件。"
            )
            gaps = census.gap_quantiles()
            if gaps:
                console.print(
                    "翌日始値のギャップ: "
                    + "、".join(f"{name} {value:+.2%}" for name, value in gaps)
                    + "　／　当日の高安での位置: "
                    + "、".join(
                        f"{name} {value:.2f}" for name, value in census.open_position_quantiles()
                    )
                )
                median = dict(gaps).get("p50", 0.0)
                if median > ONE_WAY_COST * 2:
                    console.print(
                        f"[yellow]中央値のギャップ {median:.2%} が、他の説で使っている往復費用 "
                        f"{ONE_WAY_COST * 2:.2%} を超えている。[/] "
                        "この前提のまま封印すると、**実行できない合格が出る。**"
                    )
                else:
                    console.print(
                        f"[dim]中央値のギャップは往復費用 {ONE_WAY_COST * 2:.2%} に"
                        "収まっている。[/]"
                    )

    limits = next((c for c in results if c.kind == "値幅制限"), None)
    if limits is not None and limits.events:
        histogram = limits.move_histogram()
        if histogram:
            table = Table(title="値幅制限／前日比の分布")
            table.add_column("前日比")
            table.add_column("件数", justify="right")
            for label, count in histogram:
                table.add_row(label, f"{count:,}")
            console.print(table)
            console.print(
                "[dim]**近似の当たり具合はこの表では判定できない。** 制限幅は円建て"
                "なので、正しく拾えていてもパーセントでは連続的に散る。[/]"
            )

    halts = next((c for c in results if c.kind == "売買停止明け"), None)
    if halts is not None:
        lengths = halts.length_histogram()
        if lengths:
            console.print(
                "売買停止の長さ: " + "、".join(f"{label} {count:,}" for label, count in lengths)
            )
        if halts.crossed_discontinuity:
            console.print(
                f"[yellow]うち {halts.crossed_discontinuity:,} 件は再開日が不連続だった[/]"
                "（分割・併合による停止）。除外はしていない。"
            )

    console.print()
    console.print("[dim]件数と分布のみ。リターンは計算していない。[/]")


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
