"""Wiring: where the numbers come from, and the order the phases run in.

Everything that touches a network lives here behind a callable, so the phases
themselves are pure functions of data and can be tested without a gateway or a
price feed - which matters more than usual on this project, because the machine
that develops it has no route to either.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

import pandas as pd

from stock_ai.accumulation.analysis import Deep, analyse, fetch_flow, pace_flow_calls
from stock_ai.accumulation.breakout import Breakout, classify, evaluate
from stock_ai.accumulation.screen import (
    Candidate,
    Metrics,
    ScreenResult,
    Thresholds,
    compute_metrics,
    run_screen,
)
from stock_ai.accumulation.types import Measure, Missing, insufficient, is_value, not_implemented
from stock_ai.accumulation.universe import Listing, load_universe, to_yahoo_symbol
from stock_ai.broker.moomoo import MoomooConfig
from stock_ai.data.schema import normalize_ohlcv

logger = logging.getLogger(__name__)

#: yfinance batches well but not without limit; 150 keeps each request inside
#: the URL length the provider accepts while still cutting a 6,000-name
#: universe down to about forty requests.
DOWNLOAD_CHUNK = 150


@dataclass
class Row:
    """One symbol carried through all three phases."""

    candidate: Candidate
    deep: Deep | None = None
    breakout: Breakout | None = None
    next_earnings: dt.date | Missing = field(default_factory=lambda: not_implemented("not fetched"))

    @property
    def symbol(self) -> str:
        """The listing symbol."""
        return self.candidate.symbol

    @property
    def classification(self) -> str:
        """The brief's A/B/C/D bucket, or D when phase 2 never ran."""
        if self.deep is None:
            return "D=見送り"
        return classify(self.deep.completion.percent, self.breakout.score if self.breakout else 0)


@dataclass
class Run:
    """A whole run: the screen, the deep dives, and when the data was as of."""

    screen: ScreenResult
    rows: list[Row]
    generated_at: dt.datetime
    data_as_of: dt.date | None
    notes: list[str] = field(default_factory=list)
    #: Per-symbol fetch failures, so the report can say why a metric is absent
    #: rather than only that it is.
    fetch_failures: list[tuple[str, str, str]] = field(default_factory=list)


# --------------------------------------------------------------------------
# Data access (yfinance / moomoo). Each is injectable.
# --------------------------------------------------------------------------


def download_prices(symbols: Sequence[str], *, period: str = "1y") -> dict[str, pd.DataFrame]:
    """Bulk daily OHLCV for many symbols, in the project's canonical schema.

    Symbols the provider does not answer for are dropped rather than raised
    on: over thousands of tickers a handful of delistings is normal, and one
    of them must not end the run.
    """
    import yfinance as yf  # optional at import time; heavy

    frames: dict[str, pd.DataFrame] = {}
    for start in range(0, len(symbols), DOWNLOAD_CHUNK):
        chunk = list(symbols[start : start + DOWNLOAD_CHUNK])
        yahoo = {to_yahoo_symbol(symbol): symbol for symbol in chunk}
        raw = yf.download(
            list(yahoo),
            period=period,
            interval="1d",
            group_by="ticker",
            auto_adjust=False,
            actions=False,
            threads=True,
            progress=False,
        )
        if raw is None or raw.empty:
            continue
        for ticker, symbol in yahoo.items():
            try:
                block = raw[ticker] if isinstance(raw.columns, pd.MultiIndex) else raw
                frames[symbol] = normalize_ohlcv(block)
            except Exception:  # a delisted or unpriced name, not a failed run
                continue
    return frames


def market_caps(symbols: Iterable[str]) -> dict[str, Measure]:
    """Market capitalisation per symbol, from the provider's light endpoint."""
    import yfinance as yf

    out: dict[str, Measure] = {}
    for symbol in symbols:
        try:
            value = yf.Ticker(to_yahoo_symbol(symbol)).fast_info.get("marketCap")
        except Exception as exc:
            out[symbol] = insufficient(f"時価総額を取得できない: {type(exc).__name__}")
            continue
        out[symbol] = float(value) if value else insufficient("時価総額が提供されていない")
    return out


#: The profile endpoint needs a cookie and a crumb, and it is the first thing
#: the provider throttles after a bulk download of several thousand symbols.
#: A market-wide run therefore reaches it in exactly the state where it fails,
#: which is how sector, short interest and the earnings date all came back
#: empty on a run whose prices were fine.
INFO_ATTEMPTS = 3

#: Seconds to wait before each retry. Two seconds was measured to be useless:
#: after a 5,279-symbol download the provider was still refusing profiles six
#: seconds later, and the whole-market run reported "決算 データ不足" on a
#: symbol whose profile had loaded fine minutes earlier in a one-symbol run.
INFO_BACKOFF_SECONDS = (5.0, 15.0)

#: Waiting is bounded across the whole run, not per symbol. A systematically
#: throttled run would otherwise spend the full backoff on every symbol in
#: turn, and a screen that takes five extra minutes to fail the same way is
#: worse than one that fails quickly and says the budget ran out.
INFO_RETRY_BUDGET_SECONDS = 90.0


@dataclass
class RetryBudget:
    """A pool of waiting time shared by every profile fetch in one run."""

    remaining: float = INFO_RETRY_BUDGET_SECONDS

    def wait(self, seconds: float) -> bool:
        """Sleep for ``seconds`` if the budget allows; report whether it did."""
        if seconds > self.remaining:
            return False
        self.remaining -= seconds
        time.sleep(seconds)
        return True


def _info(
    symbol: str,
    *,
    attempts: int = INFO_ATTEMPTS,
    budget: RetryBudget | None = None,
) -> dict[str, Any] | Missing:
    """The provider's profile blob for one symbol, retried through a throttle.

    The failure is reported with the provider's own message rather than just a
    marker. "データ不足" alone cannot be acted on: a throttled request and a
    symbol that genuinely has no profile look the same, and only one of them is
    fixed by waiting.
    """
    import yfinance as yf

    budget = budget if budget is not None else RetryBudget()
    last = "理由不明"
    for attempt in range(attempts):
        try:
            blob = dict(yf.Ticker(to_yahoo_symbol(symbol)).info)
        except Exception as exc:
            last = f"{type(exc).__name__}: {exc}"[:160]
        else:
            if blob:
                return blob
            last = "プロファイルが空で返った"
        if attempt + 1 >= attempts:
            break
        pause = INFO_BACKOFF_SECONDS[min(attempt, len(INFO_BACKOFF_SECONDS) - 1)]
        if not budget.wait(pause):
            return insufficient(f"プロファイル取得に失敗し、待機時間の上限に達した: {last}")
    return insufficient(f"プロファイル取得に{attempts}回失敗: {last}")


def company_name(info: dict[str, Any] | Missing, fallback: str) -> str:
    """The company name from a profile blob, or the ticker if there is none."""
    if isinstance(info, Missing):
        return fallback
    for key in ("longName", "shortName", "displayName"):
        name = info.get(key)
        if name:
            return str(name)
    return fallback


def sector_from(info: dict[str, Any] | Missing) -> str | Missing:
    """The sector label out of a profile blob."""
    if isinstance(info, Missing):
        return info
    return str(info.get("sector") or "") or insufficient("セクター未提供")


def sectors(symbols: Iterable[str]) -> dict[str, str | Missing]:
    """Sector label per symbol."""
    return {symbol: sector_from(_info(symbol)) for symbol in symbols}


def next_earnings(info: dict[str, Any] | Missing) -> dt.date | Missing:
    """The next scheduled earnings date, if the profile carries one."""
    if isinstance(info, Missing):
        return info
    for key in ("earningsTimestampStart", "earningsTimestamp"):
        stamp = info.get(key)
        if stamp:
            try:
                return dt.datetime.fromtimestamp(int(stamp), tz=dt.UTC).date()
            except (TypeError, ValueError, OSError):
                continue
    return insufficient("決算予定日が提供されていない")


def business_days_until(day: dt.date | Missing, today: dt.date) -> Measure:
    """Business days from ``today`` to ``day``; negative when it has passed."""
    if isinstance(day, Missing):
        return day
    step = 1 if day >= today else -1
    count, cursor = 0, today
    while cursor != day:
        cursor += dt.timedelta(days=step)
        if cursor.weekday() < 5:
            count += step
    return count


# --------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------


def run(
    *,
    config: MoomooConfig,
    listings: Sequence[Listing] | None = None,
    price_loader: Callable[[Sequence[str]], dict[str, pd.DataFrame]] = download_prices,
    market_cap_loader: Callable[[Iterable[str]], dict[str, Measure]] = market_caps,
    sector_loader: Callable[[Iterable[str]], dict[str, str | Missing]] | None = None,
    info_loader: Callable[[str], dict[str, Any] | Missing] = _info,
    flow_loader: Callable[[MoomooConfig, str], pd.DataFrame | None] = fetch_flow,
    universe_loader: Callable[[], Sequence[Listing]] = load_universe,
    thresholds: Thresholds | None = None,
    screen_limit: int = 10,
    deep_limit: int = 5,
    today: dt.date | None = None,
) -> Run:
    """Run phases 1 to 3 and return everything needed to render the report."""
    generated_at = dt.datetime.now(tz=dt.UTC)
    today = today or generated_at.date()
    notes: list[str] = []

    listings = list(listings if listings is not None else universe_loader())
    frames = price_loader([listing.symbol for listing in listings])

    # The profile is asked for twice per symbol otherwise - once for the sector
    # column, once for the deep dive - which doubles the exposure to the very
    # throttle that makes it fail.
    info_cache: dict[str, dict[str, Any] | Missing] = {}
    budget = RetryBudget()

    def cached_info(symbol: str) -> dict[str, Any] | Missing:
        if symbol not in info_cache:
            info_cache[symbol] = (
                info_loader(symbol) if info_loader is not _info else _info(symbol, budget=budget)
            )
        return info_cache[symbol]

    metrics: dict[str, Metrics] = {}
    for symbol, frame in frames.items():
        result = compute_metrics(frame)
        if isinstance(result, Missing):
            continue
        metrics[symbol] = result
    if len(metrics) < len(frames):
        notes.append(f"{len(frames) - len(metrics)} 銘柄は履歴不足のため測定対象外")

    screen = run_screen(
        listings,
        metrics,
        market_cap_of=market_cap_loader,
        sector_of=(
            sector_loader
            if sector_loader is not None
            else lambda symbols: {s: sector_from(cached_info(s)) for s in symbols}
        ),
        base=thresholds,
        limit=screen_limit,
    )

    # Symbols named on the command line arrive with the ticker standing in for
    # the company name, because there is no listing file to read it from. The
    # profile was fetched for the sector column anyway, so the real name is
    # already in hand - and the brief asks for a 社名 column, not a second
    # ticker column.
    for candidate in screen.candidates:
        if candidate.listing.name == candidate.symbol:
            candidate.listing = replace(
                candidate.listing,
                name=company_name(cached_info(candidate.symbol), candidate.symbol),
            )

    rows = [Row(candidate=candidate) for candidate in screen.candidates]
    for index, row in enumerate(rows[:deep_limit]):
        pace_flow_calls(index)
        frame = frames[row.symbol]
        flow_frame = flow_loader(config, row.symbol)
        info = cached_info(row.symbol)
        row.deep = analyse(
            row.symbol,
            frame,
            flow_frame,
            info,
            above_52w_low=row.candidate.metrics.above_52w_low,
            range_20d=row.candidate.metrics.range_20d,
            volume_multiple=row.candidate.metrics.volume_multiple,
        )
        row.candidate.flow_net_in_10d = row.deep.flow.large_net_in
        row.breakout = evaluate(row.symbol, frame, flow_frame)
        row.next_earnings = next_earnings(info)

    for row in rows[deep_limit:]:
        row.candidate.flow_net_in_10d = not_implemented(
            f"深掘りは上位{deep_limit}銘柄のみ（moomooのレート制限）"
        )

    failures = [
        (symbol, "プロファイル（セクター・空売り・決算日）", info.reason)
        for symbol, info in info_cache.items()
        if isinstance(info, Missing)
    ]
    return Run(
        screen=screen,
        rows=rows,
        generated_at=generated_at,
        data_as_of=screen.as_of,
        notes=notes,
        fetch_failures=failures,
    )


__all__ = [
    "Row",
    "Run",
    "business_days_until",
    "download_prices",
    "is_value",
    "market_caps",
    "next_earnings",
    "run",
    "sectors",
]
