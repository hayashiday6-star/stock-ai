"""Tests for the accumulation screen (no network, no gateway).

The machine this is developed on cannot reach a price feed or an OpenD, so
every test here drives the phases with data handed to them. That constraint
shaped the code as much as the tests: the phases are pure functions and all
network access sits behind an injected callable.

The test that matters most is the last one. Everything else checks a
calculation; that one checks that a metric which was never measured cannot
reach the report looking like one that was.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest
from rich.console import Console
from typer.testing import CliRunner

from stock_ai import cli
from stock_ai.accumulation.analysis import (
    completion_score,
    flow_metrics,
    institutional_metrics,
    short_metrics,
    technical_metrics,
)
from stock_ai.accumulation.breakout import classify, evaluate
from stock_ai.accumulation.pipeline import Run, business_days_until, next_earnings
from stock_ai.accumulation.pipeline import run as run_accumulation
from stock_ai.accumulation.report import print_report
from stock_ai.accumulation.screen import (
    MIN_HISTORY_BARS,
    Thresholds,
    compute_metrics,
    passes_price_filters,
    run_screen,
)
from stock_ai.accumulation.types import (
    Absence,
    Missing,
    insufficient,
    is_value,
    render,
    render_pct,
    unavailable,
)
from stock_ai.accumulation.universe import (
    Listing,
    excluded_reason,
    load_universe,
    parse_nasdaq_listed,
    parse_other_listed,
    to_yahoo_symbol,
)
from stock_ai.broker.moomoo import MoomooConfig
from stock_ai.data.schema import DATE, OHLCV_COLUMNS

runner = CliRunner()

LAST_SESSION = "2026-08-28"


def price_frame(
    *,
    bars: int = 300,
    base: float = 20.0,
    volume: float = 900_000.0,
    spike: float = 6.0,
    breakout: bool = False,
) -> pd.DataFrame:
    """A year of decline into a tight base, optionally breaking out at the end."""
    index = pd.bdate_range(end=LAST_SESSION, periods=bars, name=DATE)
    rng = np.random.default_rng(7)
    close = np.concatenate(
        [np.linspace(base * 2, base * 1.02, bars - 20), np.full(20, base * 1.02)]
    )
    close = close + rng.normal(0, base * 0.003, bars)
    volumes = np.full(bars, volume) + rng.normal(0, volume * 0.04, bars)
    volumes[-1] = volume * spike
    if breakout:
        close[-2], close[-1] = base * 1.09, base * 1.11
    opens = np.roll(close, 1)
    opens[0] = close[0]
    frame = pd.DataFrame(
        {
            "open": opens,
            "high": close * 1.008,
            "low": close * 0.992,
            "close": close,
            "adj_close": close,
            "volume": volumes,
        },
        index=index,
    )
    return frame[OHLCV_COLUMNS]


def flow_frame(*, large_positive: bool = True, sessions: int = 10) -> pd.DataFrame:
    days = pd.bdate_range(end=LAST_SESSION, periods=sessions)
    sign = 1.0 if large_positive else -1.0
    n = len(days)
    return pd.DataFrame(
        {
            "in_flow": np.full(n, sign * 3e6),
            "super_in_flow": np.full(n, sign * 2e6),
            "big_in_flow": np.full(n, sign * 1e6),
            "mid_in_flow": np.full(n, -0.5e6),
            "sml_in_flow": np.full(n, -0.5e6),
            "main_in_flow": np.full(n, sign * 3e6),
            "capital_flow_item_time": [d.strftime("%Y-%m-%d 00:00:00") for d in days],
        }
    )


# --- universe -----------------------------------------------------------


def test_funds_test_issues_and_derivatives_are_excluded() -> None:
    text = (
        "Symbol|Security Name|Market Category|Test Issue|"
        "Financial Status|Round Lot Size|ETF|NextShares\n"
        "AAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N\n"
        "SQQQ|ProShares UltraPro Short QQQ|G|N|N|100|Y|N\n"
        "ZZZZT|NASDAQ TEST STOCK|G|Y|N|100|N|N\n"
        "ABCDW|Some Co - Warrant|S|N|N|100|N|N\n"
        "XYZ|Global Acquisition Corp - Class A|S|N|N|100|N|N\n"
        "BIDU|Baidu - American Depositary Shares|Q|N|N|100|N|N\n"
        "File Creation Time: 0829202606:01|||||||\n"
    )
    assert [listing.symbol for listing in parse_nasdaq_listed(text)] == ["AAPL"]


def test_only_nyse_and_amex_come_from_the_other_file() -> None:
    """Arca and Cboe list funds almost exclusively; keeping them undoes the ETF filter."""
    text = (
        "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\n"
        "JNJ|Johnson & Johnson Common Stock|N|JNJ|N|100|N|JNJ\n"
        "UAMY|United States Antimony Corp|A|UAMY|N|100|N|UAMY\n"
        "ARCA1|Some Arca Listing|P|ARCA1|N|100|N|ARCA1\n"
    )
    assert {listing.exchange for listing in parse_other_listed(text)} == {"NYSE", "AMEX"}


@pytest.mark.parametrize("symbol", ["BRK.B", "BF.B", "CSGP", "HEI.A"])
def test_share_classes_and_p_suffixes_survive(symbol: str) -> None:
    """The usual ticker-shape shortcuts drop real common stock; these must not."""
    assert excluded_reason(symbol, f"{symbol} Common Stock") is None


def test_share_classes_are_respelled_for_the_price_provider() -> None:
    """A dotted symbol returns nothing rather than an error, so it must be converted."""
    assert to_yahoo_symbol("BRK.B") == "BRK-B"
    assert to_yahoo_symbol("AAPL") == "AAPL"


def test_universe_survives_one_file_failing() -> None:
    good = (
        "Symbol|Security Name|Market Category|Test Issue|"
        "Financial Status|Round Lot Size|ETF|NextShares\n"
        "AAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N\n"
    )

    def fetch(url: str) -> str:
        if "nasdaqlisted" in url:
            return good
        raise OSError("network")

    assert [listing.symbol for listing in load_universe(fetch=fetch)] == ["AAPL"]


# --- phase 1 ------------------------------------------------------------


def test_the_volume_multiple_excludes_the_day_it_is_judging() -> None:
    """A self-inclusive mean scores a true 6x day at about 4.6x."""
    metrics = compute_metrics(price_frame(spike=6.0, volume=1_000_000.0))
    assert is_value(metrics)
    assert metrics.volume_multiple == pytest.approx(6.0, rel=0.05)


def test_a_short_history_is_insufficient_not_a_perfect_score() -> None:
    """A three-week-old listing is trivially near its 52-week low."""
    result = compute_metrics(price_frame(bars=MIN_HISTORY_BARS - 1))
    assert isinstance(result, Missing)
    assert result.kind is Absence.INSUFFICIENT


def test_the_ladder_relaxes_only_as_far_as_it_must_and_says_so() -> None:
    listings = [Listing("AAA", "Alpha", "NASDAQ")]
    metrics = {"AAA": compute_metrics(price_frame(spike=3.5))}

    result = run_screen(
        listings,
        metrics,
        market_cap_of=lambda symbols: dict.fromkeys(symbols, 1000000000.0),
        sector_of=lambda symbols: dict.fromkeys(symbols, "Industrials"),
    )

    assert [c.symbol for c in result.candidates] == ["AAA"]
    assert result.relaxation_level == 1
    assert result.relaxations_applied == ["① 出来高3倍以上"]
    assert "①" in result.relaxation_label


def test_an_unreadable_market_cap_keeps_the_symbol_and_marks_it() -> None:
    """Dropping it would apply a filter that never ran, indistinguishably from failing."""
    listings = [Listing("AAA", "Alpha", "NASDAQ")]
    metrics = {"AAA": compute_metrics(price_frame())}

    result = run_screen(
        listings,
        metrics,
        market_cap_of=lambda symbols: {
            s: insufficient("時価総額が提供されていない") for s in symbols
        },
        sector_of=lambda symbols: dict.fromkeys(symbols, "Industrials"),
    )

    assert [c.symbol for c in result.candidates] == ["AAA"]
    assert isinstance(result.candidates[0].market_cap, Missing)


def test_the_sector_call_is_made_only_for_the_rows_that_print() -> None:
    """It is one request per symbol; asking about every survivor is the slow bug."""
    listings = [Listing(f"S{i:02d}", f"Name {i}", "NASDAQ") for i in range(12)]
    metrics = {listing.symbol: compute_metrics(price_frame()) for listing in listings}
    asked: list[list[str]] = []

    def sector_of(symbols):
        asked.append(list(symbols))
        return dict.fromkeys(symbols, "Industrials")

    run_screen(
        listings,
        metrics,
        market_cap_of=lambda symbols: dict.fromkeys(symbols, 1000000000.0),
        sector_of=sector_of,
        limit=3,
    )

    assert [len(call) for call in asked] == [3]


def test_price_filters_reject_a_penny_stock() -> None:
    metrics = compute_metrics(price_frame(base=2.0))
    assert not passes_price_filters(metrics, Thresholds())


# --- phase 2 ------------------------------------------------------------


def test_large_is_super_plus_big() -> None:
    flow = flow_metrics(flow_frame(), turnover_10d=1e9)
    assert flow.large_net_in == pytest.approx(30e6)  # (2M + 1M) over ten sessions


def test_a_refused_flow_is_missing_not_zero() -> None:
    """Zero would read as "no institutional interest" - a finding it is not."""
    flow = flow_metrics(None, turnover_10d=1e9)
    assert isinstance(flow.large_net_in, Missing)
    assert flow.large_net_in.kind is Absence.INSUFFICIENT


def test_the_volume_share_the_brief_asks_for_is_declared_unformable() -> None:
    """Net currency and traded volume are different quantities; no ratio exists."""
    flow = flow_metrics(flow_frame(), turnover_10d=1e9)
    assert isinstance(flow.large_share_of_volume, Missing)
    assert flow.large_share_of_volume.kind is Absence.UNAVAILABLE


def test_short_interest_reports_one_change_not_two() -> None:
    """The feed publishes two snapshots, so "the last two changes" is one change."""
    short = short_metrics(
        {
            "sharesShort": 8_000_000,
            "sharesShortPriorMonth": 6_000_000,
            "floatShares": 100_000_000,
            "shortRatio": 4.2,
        }
    )
    assert short.short_interest_of_float == pytest.approx(0.08)
    assert short.short_interest_change == pytest.approx(0.02)
    assert isinstance(short.short_interest_change_prior, Missing)
    assert isinstance(short.borrow_fee, Missing)


def test_the_volume_condition_is_measured_not_inherited_from_phase_one() -> None:
    """Admitted at 3x by the ladder, a symbol must not be scored as a 5x day."""
    completion = completion_score(
        flow=flow_metrics(flow_frame(), turnover_10d=1e9),
        technical=technical_metrics(price_frame()),
        above_52w_low=0.05,
        range_20d=0.04,
        volume_multiple=3.2,
        institutional=institutional_metrics(),
    )
    condition = next(c for c in completion.conditions if "出来高5倍" in c.label)
    assert condition.met is False
    assert "3.20倍" in condition.detail


def test_an_unmeasurable_condition_is_not_counted_as_failed() -> None:
    completion = completion_score(
        flow=flow_metrics(flow_frame(), turnover_10d=1e9),
        technical=technical_metrics(price_frame()),
        above_52w_low=0.05,
        range_20d=0.04,
        volume_multiple=6.0,
        institutional=institutional_metrics(),
    )
    seventh = completion.conditions[-1]
    assert isinstance(seventh.met, Missing)
    assert completion.judgeable == 6
    assert completion.percent < float(completion.percent_of_judgeable)


# --- phase 3 ------------------------------------------------------------


def test_a_breakout_on_the_latest_bar_leaves_the_next_day_undecided() -> None:
    """Failing it would downgrade the freshest signal for being fresh."""
    breakout = evaluate("AAA", price_frame(breakout=True), flow_frame())
    fourth = next(c for c in breakout.checks if c.label.startswith("④"))
    assert isinstance(fourth.met, Missing)
    assert fourth.mark == "-"
    assert "以上" in fourth.needed


def test_an_unmet_first_condition_names_the_price_it_needs() -> None:
    breakout = evaluate("AAA", price_frame(breakout=False), flow_frame())
    first = next(c for c in breakout.checks if c.label.startswith("①"))
    assert first.met is False
    assert first.needed.startswith("終値 $")


def test_a_breakout_carries_three_stop_levels() -> None:
    breakout = evaluate("AAA", price_frame(breakout=True), flow_frame())
    assert is_value(breakout.stop_bb_middle)
    assert is_value(breakout.stop_20d_low)
    assert is_value(breakout.stop_atr)
    assert float(breakout.stop_20d_low) < float(breakout.last_close)


def test_negative_large_flow_fails_the_third_condition() -> None:
    breakout = evaluate("AAA", price_frame(breakout=True), flow_frame(large_positive=False))
    third = next(c for c in breakout.checks if c.label.startswith("③"))
    assert third.met is False


@pytest.mark.parametrize(
    ("completion", "score", "expected"),
    [
        (100.0, 5, "A=ブレイクアウト確定"),
        (85.0, 3, "B=初動確認"),
        (71.4, 2, "C=仕込み継続中"),
        (57.1, 5, "D=見送り"),
    ],
)
def test_the_buckets_follow_the_brief(completion: float, score: int, expected: str) -> None:
    assert classify(completion, score) == expected


# --- earnings -----------------------------------------------------------


def test_business_days_until_skips_the_weekend() -> None:
    """Saturday to the following Wednesday is three sessions, not five days."""
    assert business_days_until(dt.date(2026, 9, 2), dt.date(2026, 8, 29)) == 3
    assert business_days_until(dt.date(2026, 8, 31), dt.date(2026, 8, 29)) == 1


def test_a_missing_earnings_date_stays_missing() -> None:
    assert isinstance(next_earnings({}), Missing)
    assert isinstance(next_earnings(insufficient("no profile")), Missing)


# --- the whole run ------------------------------------------------------


def _run(**overrides) -> Run:
    listings = [Listing("ALFA", "Alfa Industries Inc", "NASDAQ")]
    frames = {"ALFA": price_frame(breakout=True)}
    kwargs = {
        "config": MoomooConfig(trd_market="US"),
        "listings": listings,
        "price_loader": lambda symbols: frames,
        "market_cap_loader": lambda symbols: dict.fromkeys(symbols, 1200000000.0),
        "sector_loader": lambda symbols: dict.fromkeys(symbols, "Industrials"),
        "info_loader": lambda symbol: {
            "sharesShort": 8_000_000,
            "sharesShortPriorMonth": 6_500_000,
            "floatShares": 100_000_000,
            "shortRatio": 4.2,
            "sector": "Industrials",
            "earningsTimestampStart": int(dt.datetime(2026, 9, 2, tzinfo=dt.UTC).timestamp()),
        },
        "flow_loader": lambda config, symbol: flow_frame(),
        "deep_limit": 1,
        "today": dt.date(2026, 8, 29),
    }
    kwargs.update(overrides)
    return run_accumulation(**kwargs)


def test_a_full_run_reaches_phase_three() -> None:
    result = _run()
    row = result.rows[0]
    assert row.deep is not None
    assert row.breakout is not None
    assert result.data_as_of == dt.date(2026, 8, 28)


def test_symbols_beyond_the_deep_limit_say_why_they_have_no_flow() -> None:
    """Silence there would read as zero flow rather than as an unasked question."""
    listings = [Listing(f"S{i:02d}", f"Name {i}", "NASDAQ") for i in range(4)]
    frames = {listing.symbol: price_frame() for listing in listings}
    result = _run(listings=listings, price_loader=lambda symbols: frames, deep_limit=1)

    later = result.rows[1].candidate.flow_net_in_10d
    assert isinstance(later, Missing)
    assert "レート制限" in later.reason


def test_no_missing_metric_is_ever_rendered_as_a_number() -> None:
    """The guarantee the whole package exists for.

    Every absent metric has to reach the page as its marker. If one ever
    rendered as 0, or as a blank cell, a reader would take it for a
    measurement - and on this report that reader is deciding where to put
    money.
    """
    console = Console(width=200, record=True, force_terminal=False)
    print_report(console, _run(), dt.date(2026, 8, 29))
    text = console.export_text()

    for marker in (Absence.UNAVAILABLE, Absence.NOT_IMPLEMENTED):
        assert str(marker) in text
    # Each unobtainable item appears by name in the closing table, so nothing
    # is quietly dropped from the report either.
    for label in ("ダークプール比率", "ブロック取引", "借株コスト", "Form 4"):
        assert label in text
    assert "None" not in text
    assert "nan" not in text.lower()


def test_render_never_turns_an_absence_into_a_figure() -> None:
    missing = unavailable("no source")
    assert render(missing) == str(Absence.UNAVAILABLE)
    assert render_pct(missing) == str(Absence.UNAVAILABLE)
    with pytest.raises(TypeError):
        missing + 1  # type: ignore[operator]


def test_cli_takes_symbols_on_the_command_line(monkeypatch: pytest.MonkeyPatch) -> None:
    """The documented first run must not need a file that does not exist yet.

    The README said `--symbols-file watchlist.txt` and the repository ships no
    such file, so the first thing a reader ran failed on a missing path.
    """
    frames = {"ALFA": price_frame(breakout=True)}
    monkeypatch.setattr(cli, "download_prices", lambda symbols, period="1y": frames)
    monkeypatch.setattr(
        cli, "run_accumulation", lambda **kwargs: _run(price_loader=lambda symbols: frames)
    )

    result = runner.invoke(cli.app, ["accumulation", "ALFA"])

    assert result.exit_code == 0
    assert "最終統合サマリー" in result.output


def test_cli_still_asks_for_something_to_screen_when_a_file_is_empty(tmp_path) -> None:
    path = tmp_path / "empty.txt"
    path.write_text("# nothing here\n", encoding="utf-8")

    result = runner.invoke(cli.app, ["accumulation", "--symbols-file", str(path)])

    assert result.exit_code != 0


def test_cli_runs_the_screen_from_a_symbols_file(tmp_path) -> None:
    path = tmp_path / "symbols.txt"
    path.write_text("ALFA\n", encoding="utf-8")
    frames = {"ALFA": price_frame(breakout=True)}

    import stock_ai.accumulation.pipeline as pipeline

    original = pipeline.download_prices
    try:
        cli.download_prices = lambda symbols, period="1y": frames  # type: ignore[assignment]
        cli.run_accumulation = lambda **kwargs: _run(  # type: ignore[assignment]
            price_loader=lambda symbols: frames
        )
        result = runner.invoke(cli.app, ["accumulation", "--symbols-file", str(path)])
    finally:
        cli.download_prices = original  # type: ignore[assignment]
        cli.run_accumulation = run_accumulation  # type: ignore[assignment]

    assert result.exit_code == 0
    assert "最終統合サマリー" in result.output
    assert "取得不可" in result.output
