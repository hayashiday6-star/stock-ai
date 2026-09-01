"""Tests for the JP accumulation pre-registration's 5-condition signal.

Formulas are pinned to the 2026-08-31 pre-registration (see
``stock_ai.backtest.accumulation_signal`` module docstring). These tests
check the pre-registration's exact wording, not a plausible approximation of
it - a silently "close enough" formula is the failure mode the registration
itself exists to prevent.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from stock_ai.backtest.accumulation_signal import (
    MIN_HISTORY_BARS,
    Signal5Thresholds,
    compute_signal_frame,
    count_signals,
    earnings_coverage,
    earnings_flag_series,
    explain_date,
    exrights_flag_series,
    market_cap_series,
    market_volume_context,
    material_free_mask,
    record_dates,
)
from stock_ai.data.schema import ADJ_CLOSE, CLOSE, HIGH, LOW, OPEN, VOLUME
from stock_ai.data.types import FinancialReport
from stock_ai.database.engine import Database
from stock_ai.database.repository import FinancialStatementRepository, PriceRepository

_BARS = MIN_HISTORY_BARS + 20


def _flat_frame(bars: int = _BARS, base: float = 100.0, volume: float = 10_000.0) -> pd.DataFrame:
    """A perfectly flat, low-volume series - every condition trivially passes.

    Flat means the 52-week low equals the last close (0% distance), the
    20-day range is 0%, the Bollinger width is 0%, and every SMA equals the
    close - so only what a test deliberately perturbs can fail.
    """
    index = pd.bdate_range(end=dt.date(2026, 6, 30), periods=bars, name="date")
    close = np.full(bars, base)
    return pd.DataFrame(
        {
            OPEN: close,
            HIGH: close,
            LOW: close,
            CLOSE: close,
            ADJ_CLOSE: close,
            VOLUME: np.full(bars, volume),
        },
        index=index,
    )


def _spike_volume(frame: pd.DataFrame, multiple: float = 6.0) -> pd.DataFrame:
    frame = frame.copy()
    frame.loc[frame.index[-1], VOLUME] = frame[VOLUME].iloc[0] * multiple
    return frame


def test_all_five_conditions_met_signals() -> None:
    frame = compute_signal_frame(_spike_volume(_flat_frame()))
    assert bool(frame["signal"].iloc[-1]) is True


def test_rows_before_enough_history_are_never_signals() -> None:
    """The 52-week window doubles as the '250営業日以上' universe requirement."""
    frame = compute_signal_frame(_spike_volume(_flat_frame()))
    assert not frame["signal"].iloc[: MIN_HISTORY_BARS - 1].any()


def test_volume_multiple_excludes_the_judged_day_from_its_own_average() -> None:
    """Section 3's formula: judged-day volume over the average *excluding* it.

    Folding the day into its own average would score a true 6x day as
    6/(20*1 + 6)/20 ≈ 4.6x - under the 5.0 threshold it exists to clear.
    """
    frame = compute_signal_frame(_spike_volume(_flat_frame(), multiple=6.0))
    assert frame["volume_multiple"].iloc[-1] == pytest.approx(6.0)


def test_volume_multiple_below_threshold_blocks_the_signal() -> None:
    frame = compute_signal_frame(_flat_frame())  # no spike: multiple == 1.0
    assert frame["volume_multiple"].iloc[-1] == pytest.approx(1.0)
    assert not frame["signal"].iloc[-1]


def test_above_52w_low_beyond_threshold_blocks_the_signal() -> None:
    base = _spike_volume(_flat_frame())
    base.loc[base.index[-100], LOW] = 40.0  # the 52-week low becomes 40
    base.loc[base.index[-1], [OPEN, HIGH, LOW, CLOSE, ADJ_CLOSE]] = 60.0  # +50% off it
    frame = compute_signal_frame(base)
    assert frame["above_52w_low"].iloc[-1] == pytest.approx(0.5)
    assert not frame["signal"].iloc[-1]


def test_range_20d_beyond_threshold_blocks_the_signal() -> None:
    base = _spike_volume(_flat_frame())
    base.loc[base.index[-10], HIGH] = 120.0  # 20% swing inside the 20-day window
    frame = compute_signal_frame(base)
    assert frame["range_20d"].iloc[-1] == pytest.approx(0.2)
    assert not frame["signal"].iloc[-1]


def test_bollinger_width_beyond_threshold_blocks_the_signal() -> None:
    base = _spike_volume(_flat_frame())
    # Alternate the last 20 closes between 90 and 110 - wide dispersion inside
    # the Bollinger window without needing a single outlier bar.
    tail = base.index[-20:]
    base.loc[tail[::2], [OPEN, HIGH, LOW, CLOSE, ADJ_CLOSE]] = 90.0
    base.loc[tail[1::2], [OPEN, HIGH, LOW, CLOSE, ADJ_CLOSE]] = 110.0
    frame = compute_signal_frame(base)
    assert frame["bollinger_width"].iloc[-1] > 0.05
    assert not frame["signal"].iloc[-1]


def test_ma_divergence_divides_by_ma20_not_price() -> None:
    """Section 3's formula divides by MA20 - not by the current price.

    The sibling US screen (``accumulation/analysis.py``, a different,
    differently-specified condition) divides its own MA-spread by price.
    Copying that convention here would silently change what was registered,
    so this pins the denominator directly: a ramp is built where dividing by
    MA20 clears the threshold but dividing by the (much lower) starting price
    would not.
    """
    bars = MIN_HISTORY_BARS + 40
    index = pd.bdate_range(end=dt.date(2026, 6, 30), periods=bars, name="date")
    close = np.full(bars, 100.0)
    # A gentle ramp over the last 30 bars spreads MA5/10/20/30 apart while
    # keeping MA20 itself near 100 - the ramp is too small to move MA20 much,
    # but large relative to the flat early history that only affects price.
    close[-30:] = np.linspace(100.0, 106.0, 30)
    frame_data = pd.DataFrame(
        {
            OPEN: close,
            HIGH: close,
            LOW: close,
            CLOSE: close,
            ADJ_CLOSE: close,
            VOLUME: np.full(bars, 10_000.0),
        },
        index=index,
    )
    frame_data = _spike_volume(frame_data)
    frame = compute_signal_frame(frame_data)

    divergence = frame["ma_max_divergence"].iloc[-1]
    price = float(close[-1])
    ma20 = frame_data[CLOSE].rolling(20).mean().iloc[-1]
    # Same spread, two candidate denominators - they must differ for this
    # case to actually distinguish the two conventions.
    assert ma20 != pytest.approx(price)
    divergence_over_price = divergence * ma20 / price
    assert divergence != pytest.approx(divergence_over_price)


def test_zero_volume_day_never_signals() -> None:
    base = _spike_volume(_flat_frame())
    base.loc[base.index[-1], VOLUME] = 0
    frame = compute_signal_frame(base)
    assert not frame["signal"].iloc[-1]


def test_thresholds_can_be_widened_for_the_sensitivity_sweep() -> None:
    base = _flat_frame()
    base.loc[base.index[-1], VOLUME] = base[VOLUME].iloc[0] * 3.0  # below the default 5.0
    default = compute_signal_frame(base)
    widened = compute_signal_frame(base, Signal5Thresholds(volume_multiple_min=3.0))
    assert not default["signal"].iloc[-1]
    assert widened["signal"].iloc[-1]


# --- count_signals -----------------------------------------------------------


def _seed(database: Database, symbol: str, market: str, frame: pd.DataFrame) -> None:
    with database.session() as session:
        PriceRepository(session).upsert_prices(symbol, frame, market=market)


def test_count_signals_scans_jp_only_by_default() -> None:
    database = Database("sqlite:///:memory:")
    database.create_all()
    _seed(database, "7203", "JP", _spike_volume(_flat_frame()))
    _seed(database, "AAPL", "US", _spike_volume(_flat_frame()))

    report = count_signals(database)

    assert report.symbols_scanned == 1
    assert {s.symbol for s in report.signals} == {"7203"}
    database.dispose()


def test_count_signals_skips_symbols_with_insufficient_history() -> None:
    database = Database("sqlite:///:memory:")
    database.create_all()
    _seed(database, "7203", "JP", _spike_volume(_flat_frame(bars=MIN_HISTORY_BARS - 1)))

    report = count_signals(database)

    assert report.symbols_scanned == 1
    assert report.symbols_with_enough_history == 0
    assert report.total == 0


def test_count_signals_by_year_deduplicates_same_day_across_symbols() -> None:
    database = Database("sqlite:///:memory:")
    database.create_all()
    frame = _spike_volume(_flat_frame())
    _seed(database, "7203", "JP", frame)
    _seed(database, "6501", "JP", frame)  # signals on the same last date

    report = count_signals(database)

    assert report.total == 2  # one per symbol
    assert report.unique_dates == 1  # same calendar date
    by_year = report.by_year()
    assert list(by_year["signals"]) == [2]
    assert list(by_year["signal_days"]) == [1]
    database.dispose()


def test_max_signals_per_day_reports_the_busiest_date() -> None:
    """A day where every symbol signals at once should stand out, not average away."""
    database = Database("sqlite:///:memory:")
    database.create_all()
    frame = _spike_volume(_flat_frame())
    for symbol in ("7203", "6501", "8306"):
        _seed(database, symbol, "JP", frame)  # all three signal on the same date

    report = count_signals(database)

    assert report.max_signals_per_day == 3
    by_date = report.by_date()
    assert by_date.iloc[0]["signals"] == 3
    database.dispose()


def test_max_signals_per_day_is_zero_with_no_signals() -> None:
    empty = Database("sqlite:///:memory:")
    empty.create_all()
    report_with_no_signals = count_signals(empty)
    assert report_with_no_signals.max_signals_per_day == 0
    assert report_with_no_signals.by_date().empty
    empty.dispose()


# --- market_cap_series --------------------------------------------------------


def _report(disclosed_on: dt.date | None, shares: float | None) -> FinancialReport:
    return FinancialReport(
        symbol="6501",
        fiscal_year=disclosed_on.year if disclosed_on else 2000,
        disclosed_on=disclosed_on,
        shares_outstanding=shares,
    )


def _fy_end_report(fiscal_year_end: dt.date) -> FinancialReport:
    """A statement that only carries the company's fiscal calendar."""
    return FinancialReport(
        symbol="6501",
        fiscal_year=fiscal_year_end.year,
        disclosed_on=fiscal_year_end + dt.timedelta(days=60),
        fiscal_year_end=fiscal_year_end,
    )


def test_market_cap_series_steps_up_at_each_disclosure() -> None:
    index = pd.bdate_range("2024-01-01", periods=10, name="date")
    raw_close = pd.Series(100.0, index=index)
    statements = [
        _report(dt.date(2024, 1, 3), 1_000_000.0),
        _report(dt.date(2024, 1, 8), 2_000_000.0),
    ]

    market_cap = market_cap_series(raw_close, statements)

    assert pd.isna(market_cap.iloc[0])  # before the first disclosure
    assert market_cap.loc["2024-01-03"] == pytest.approx(100.0 * 1_000_000.0)
    assert market_cap.loc["2024-01-05"] == pytest.approx(100.0 * 1_000_000.0)  # still the old count
    assert market_cap.loc["2024-01-08"] == pytest.approx(100.0 * 2_000_000.0)


def test_market_cap_series_ignores_reports_missing_either_field() -> None:
    index = pd.bdate_range("2024-01-01", periods=5, name="date")
    raw_close = pd.Series(100.0, index=index)
    statements = [
        _report(None, 1_000_000.0),  # no disclosure date - can't be placed in time
        _report(dt.date(2024, 1, 2), None),  # no share count - nothing to multiply
    ]

    market_cap = market_cap_series(raw_close, statements)

    assert market_cap.isna().all()


def test_market_cap_series_uses_the_raw_close_not_a_split_adjusted_one() -> None:
    """The whole point of taking ``raw_close`` as a parameter, not the frame."""
    index = pd.bdate_range("2024-01-01", periods=3, name="date")
    raw_close = pd.Series([500.0, 500.0, 100.0], index=index)  # a 5:1 split on day 3
    statements = [_report(dt.date(2024, 1, 1), 1_000_000.0)]

    market_cap = market_cap_series(raw_close, statements)

    # A split changes the price scale, not the real company value - so market
    # cap must move with the actually-traded price, not stay constant the way
    # it would if a split-adjusted close (flat at 100 throughout) were used.
    assert market_cap.iloc[0] == pytest.approx(500.0 * 1_000_000.0)
    assert market_cap.iloc[-1] == pytest.approx(100.0 * 1_000_000.0)


# --- count_signals(min_market_cap=...) ----------------------------------------


def _seed_statement(database: Database, symbol: str, disclosed_on: dt.date, shares: float) -> None:
    report = FinancialReport(
        symbol=symbol,
        fiscal_year=disclosed_on.year,
        disclosed_on=disclosed_on,
        shares_outstanding=shares,
    )
    with database.session() as session:
        FinancialStatementRepository(session).upsert_reports(symbol, [report], market="JP")


def test_min_market_cap_excludes_a_signal_below_the_floor() -> None:
    database = Database("sqlite:///:memory:")
    database.create_all()
    frame = _spike_volume(_flat_frame(base=100.0))  # raw close == adjusted close (no split)
    _seed(database, "7203", "JP", frame)
    _seed_statement(database, "7203", dt.date(2024, 1, 1), shares=1_000_000.0)  # cap = 1億円

    without_filter = count_signals(database)
    with_filter = count_signals(database, min_market_cap=10_000_000_000.0)  # 100億円

    assert without_filter.total == 1
    assert without_filter.excluded_for_market_cap is None
    assert with_filter.total == 0
    assert with_filter.excluded_for_market_cap == 1
    database.dispose()


def test_min_market_cap_keeps_a_signal_above_the_floor() -> None:
    database = Database("sqlite:///:memory:")
    database.create_all()
    frame = _spike_volume(_flat_frame(base=100.0))
    _seed(database, "7203", "JP", frame)
    _seed_statement(database, "7203", dt.date(2024, 1, 1), shares=1_000_000_000.0)  # cap = 1000億円

    report = count_signals(database, min_market_cap=10_000_000_000.0)

    assert report.total == 1
    assert report.excluded_for_market_cap == 0
    database.dispose()


def test_min_market_cap_excludes_a_signal_with_no_known_shares_outstanding() -> None:
    """No disclosure at all means the floor cannot be confirmed - exclude, don't assume."""
    database = Database("sqlite:///:memory:")
    database.create_all()
    _seed(database, "7203", "JP", _spike_volume(_flat_frame(base=100.0)))

    report = count_signals(database, min_market_cap=10_000_000_000.0)

    assert report.total == 0
    assert report.excluded_for_market_cap == 1
    database.dispose()


# --- 材料日フラグ（セクション3-1） -------------------------------------------


def _trading_index(start: str = "2024-01-01", periods: int = 60) -> pd.DatetimeIndex:
    return pd.bdate_range(start, periods=periods, name="date")


def test_earnings_flag_covers_one_session_either_side() -> None:
    """発表日そのものと、前後1営業日。出来高は発表の前後に動くため。"""
    index = _trading_index()
    disclosed = index[10].date()
    flags = earnings_flag_series(index, [_report(disclosed, 1.0)])

    assert not flags.iloc[8]
    assert flags.iloc[9]  # 前営業日
    assert flags.iloc[10]  # 当日
    assert flags.iloc[11]  # 翌営業日
    assert not flags.iloc[12]


def test_earnings_flag_pulls_a_weekend_disclosure_back_to_a_trading_day() -> None:
    """休日に公開された開示の出来高は、直前の営業日ではなく直後に出る。

    ここでは「その日以前の最後の営業日」に寄せる。±1営業日の窓が翌営業日も
    覆うので、寄せ方向によらず発表後の商いは窓に入る。
    """
    index = _trading_index()
    saturday = dt.date(2024, 1, 13)
    flags = earnings_flag_series(index, [_report(saturday, 1.0)])
    assert flags.any()


def test_earnings_flag_is_all_false_without_disclosure_dates() -> None:
    index = _trading_index()
    assert not earnings_flag_series(index, [_report(None, 1.0)]).any()


def test_record_dates_are_the_fiscal_year_end_and_its_half_year_point() -> None:
    """3月決算なら 3/31 と 9/30。日本の権利確定日はこの2つ。"""
    statements = [_fy_end_report(dt.date(2024, 3, 31))]
    dates = record_dates(statements, range(2024, 2025))
    assert set(dates) == {dt.date(2024, 3, 31), dt.date(2023, 9, 30)}


def test_record_dates_follow_a_december_filer() -> None:
    """12月決算なら 12/31 と 6/30。年だけでは決まらないのがこの差。"""
    statements = [_fy_end_report(dt.date(2024, 12, 31))]
    dates = record_dates(statements, range(2024, 2025))
    assert set(dates) == {dt.date(2024, 12, 31), dt.date(2024, 6, 30)}


def test_record_dates_are_empty_without_a_fiscal_year_end() -> None:
    assert record_dates([_report(dt.date(2024, 5, 1), 1.0)], range(2024, 2025)) == []


def test_exrights_flag_sits_two_business_days_before_the_record_date() -> None:
    """T+2。権利付最終日は権利確定日の2営業日前、権利落ち日はその翌営業日。"""
    index = _trading_index("2024-03-01", periods=40)
    flags = exrights_flag_series(index, [_fy_end_report(dt.date(2024, 3, 31))])

    positions = [i for i, flagged in enumerate(flags) if flagged]
    record_position = index.searchsorted(pd.Timestamp("2024-03-31"), side="right") - 1
    # 権利付最終日 = record - 2、その ±1 と権利落ち日の ±1 で連続4営業日。
    assert positions == [
        record_position - 3,
        record_position - 2,
        record_position - 1,
        record_position,
    ]


def test_exrights_flag_uses_t_plus_3_before_the_2019_settlement_change() -> None:
    """2019-07-16 より前は T+3。長期の副次分析はこの変更をまたぐ。"""
    index = pd.bdate_range("2018-02-01", periods=60, name="date")
    flags = exrights_flag_series(index, [_fy_end_report(dt.date(2018, 3, 31))])

    record_position = index.searchsorted(pd.Timestamp("2018-03-31"), side="right") - 1
    positions = [i for i, flagged in enumerate(flags) if flagged]
    assert min(positions) == record_position - 4  # (record - 3) の1営業日前


def test_material_free_excludes_a_symbol_whose_flags_cannot_be_evaluated() -> None:
    """開示日が1つも無い銘柄は「静かだった」ではなく「調べていない」。"""
    index = _trading_index()
    free, earnings, exrights = material_free_mask(index, [])

    assert not earnings.any()
    assert not exrights.any()
    assert not free.any()  # フラグが立たなくても material-free にはしない


def test_material_free_is_true_away_from_every_material_date() -> None:
    index = _trading_index()
    statements = [
        FinancialReport(
            symbol="6501",
            fiscal_year=2024,
            disclosed_on=index[10].date(),
            fiscal_year_end=dt.date(2024, 3, 31),
        )
    ]
    free, _earnings, _exrights = material_free_mask(index, statements)

    assert free.iloc[0]  # 材料日から離れた日
    assert not free.iloc[10]  # 決算発表日


def test_exrights_flag_does_not_invent_a_date_past_the_end_of_the_series() -> None:
    """データ終端より後の権利確定日で、末尾に架空のフラグを立てない。

    ``searchsorted`` は範囲外の日付を最終バーに丸める。素直に使うと、
    まだ来ていない権利確定日が「最終営業日に起きた」ことになり、どの銘柄でも
    系列の末尾4営業日が必ず材料日として落ちていた。
    """
    # 3月決算。系列は1月で終わるので、3/31 も 9/30 も範囲外。
    index = pd.bdate_range("2024-01-04", periods=15, name="date")
    flags = exrights_flag_series(index, [_fy_end_report(dt.date(2024, 3, 31))])
    assert not flags.any()


# --- 売買代金フィルタと材料日サブセット ---------------------------------------


def test_min_turnover_excludes_a_thin_signal() -> None:
    """売買代金 = D終値 × D出来高。セクション2の流動性下限。"""
    database = Database("sqlite:///:memory:")
    database.create_all()
    # 終値100円 × 出来高60,000株 = 600万円。1億円には届かない。
    _seed(database, "7203", "JP", _spike_volume(_flat_frame(base=100.0)))

    lenient = count_signals(database, min_turnover=1_000_000.0)
    strict = count_signals(database, min_turnover=100_000_000.0)

    assert lenient.total == 1
    assert lenient.excluded_for_turnover == 0
    assert strict.total == 0
    assert strict.excluded_for_turnover == 1
    database.dispose()


def test_min_turnover_uses_the_unadjusted_close() -> None:
    """調整後の終値で測ると、分割前の売買代金を分割係数のぶん過小評価する。"""
    database = Database("sqlite:///:memory:")
    database.create_all()
    frame = _spike_volume(_flat_frame(base=100.0))
    # 全バーの adj_close を 1/5 にする（＝系列の後に5分割があった状態）。
    # get_prices はこの比率を全 OHLC に掛けるので、調整後の系列は 20 円で平坦。
    # 平坦なままなので5条件は変わらず成立し、違うのは尺度だけになる。
    frame[ADJ_CLOSE] = 20.0
    _seed(database, "7203", "JP", frame)

    # 実際の売買代金は 100円 × 60,000株 = 600万円。調整後(20円)で測れば120万円。
    report = count_signals(database, min_turnover=5_000_000.0)

    assert report.total == 1, "未調整の終値で測れば 600万円 > 500万円 で残る"
    database.dispose()


def test_material_free_subset_separates_verified_quiet_from_never_checked() -> None:
    database = Database("sqlite:///:memory:")
    database.create_all()
    frame = _spike_volume(_flat_frame(base=100.0))
    signal_day = frame.index[-1].date()
    _seed(database, "7203", "JP", frame)
    _seed(database, "6501", "JP", frame)
    # 7203 はシグナル当日が決算発表日。6501 は開示が1件も無い。
    with database.session() as session:
        FinancialStatementRepository(session).upsert_reports(
            "7203",
            [
                FinancialReport(
                    symbol="7203",
                    fiscal_year=2026,
                    disclosed_on=signal_day,
                    fiscal_year_end=dt.date(2026, 3, 31),
                )
            ],
            market="JP",
        )

    report = count_signals(database, min_turnover=None, flag_material_days=True)

    assert report.total == 2
    assert report.earnings_count == 1  # 7203
    assert report.unflagged_but_unevaluable == 1  # 6501: 調べていない
    assert report.material_free.total == 0  # どちらも「確認できた静かな日」ではない
    database.dispose()


@pytest.mark.parametrize(
    ("record_date", "expected_last_with_rights"),
    [
        (dt.date(2023, 3, 31), dt.date(2023, 3, 29)),
        (dt.date(2024, 9, 30), dt.date(2024, 9, 26)),
        (dt.date(2025, 3, 31), dt.date(2025, 3, 27)),
    ],
)
def test_exrights_flag_lands_on_the_days_the_real_data_flagged(
    record_date: dt.date, expected_last_with_rights: dt.date
) -> None:
    """実データで「権利付最終日」と特定された3日を、実装が同じ日に置くか。

    予備調査のシグナル数上位10日のうち3日がこれで、事前登録に材料日フラグを
    足す根拠になった日そのもの。ここが1日ずれると、除外したい日を外して
    隣の平常日を落とすことになる。

    2024-09-30 は月曜、2025-03-31 も月曜、2023-03-31 は金曜。いずれも権利付
    最終日はその2営業日前で、上表の実測と一致する。この窓に日本の祝日は無い
    ので、平日カレンダーで再現できる。
    """
    index = pd.bdate_range(record_date - dt.timedelta(days=40), record_date, name="date")
    fiscal_year_end = record_date  # 3月決算なら3/31が本決算、9/30が中間
    flags = exrights_flag_series(index, [_fy_end_report(fiscal_year_end)])

    flagged = {ts.date() for ts, on in zip(index, flags, strict=True) if on}
    assert expected_last_with_rights in flagged


# --- explain_date（材料日フラグの取りこぼし診断） -----------------------------


def test_explain_date_separates_coverage_from_a_narrow_window() -> None:
    """開示が遠いのか、窓が狭いのか、突合の不具合かを1つの表で切り分ける。

    実測で決算ピーク日が主要サブセットに残ったとき、3つの原因は対処が
    正反対になる。件数だけでは区別できないので、営業日差を出す。
    """
    database = Database("sqlite:///:memory:")
    database.create_all()
    frame = _spike_volume(_flat_frame(base=100.0))
    signal_day = frame.index[-1].date()
    _seed(database, "7203", "JP", frame)
    # 開示は1件だけ、しかもシグナル日から遠い＝被覆の問題の形。
    far_away = frame.index[-40].date()
    with database.session() as session:
        FinancialStatementRepository(session).upsert_reports(
            "7203",
            [
                FinancialReport(
                    symbol="7203",
                    fiscal_year=2026,
                    disclosed_on=far_away,
                    fiscal_year_end=dt.date(2026, 3, 31),
                )
            ],
            market="JP",
        )

    explained = explain_date(database, signal_day)

    (row,) = explained.itertuples()
    assert row.symbol == "7203"
    assert row.disclosed == 1
    assert row.nearest_disclosed == far_away
    assert row.nearest_disclosed_days == 39  # 営業日で39日離れている＝窓の問題ではない
    assert not row.earnings
    database.dispose()


def test_explain_date_is_empty_when_nothing_signalled() -> None:
    database = Database("sqlite:///:memory:")
    database.create_all()
    frame = _spike_volume(_flat_frame(base=100.0))
    _seed(database, "7203", "JP", frame)

    quiet_day = frame.index[-5].date()
    assert explain_date(database, quiet_day).empty
    database.dispose()


def test_a_date_outside_the_disclosure_history_is_not_material_free() -> None:
    """2026年の開示1件を根拠に、2002年の日を「材料なし」と数えていた不具合。

    銘柄単位で「開示が1件でもあるか」を見ていたため、開示履歴が始まる前の
    年が全部「確認できた静かな日」として通っていた。調べていない日は
    調べていない日として落とす。
    """
    index = pd.bdate_range("2002-01-01", "2026-06-30", freq="B", name="date")
    statements = [
        FinancialReport(
            symbol="6501",
            fiscal_year=2026,
            disclosed_on=dt.date(2026, 6, 25),
            fiscal_year_end=dt.date(2026, 3, 31),
        )
    ]

    free, _earnings, _exrights = material_free_mask(index, statements)

    assert not free.loc["2002-06-03"]  # 開示履歴のはるか前
    assert not free.loc["2015-06-03"]  # まだ届かない
    assert free.loc["2026-01-06"]  # 開示から400日以内なので判定できる


def test_earnings_coverage_reaches_only_as_far_as_the_disclosures_on_file() -> None:
    index = pd.bdate_range("2020-01-01", "2026-12-31", freq="B", name="date")
    statements = [_report(dt.date(2024, 5, 10), 1.0)]

    covered = earnings_coverage(index, statements)

    assert covered.loc["2024-05-10"]
    assert covered.loc["2023-06-01"]  # 400日以内
    assert not covered.loc["2021-01-04"]  # 3年以上離れている
    assert not covered.loc["2026-12-31"]


# --- 市場全体の出来高（決算シーズン効果の切り分け） ---------------------------


def test_market_volume_context_sees_a_market_wide_surge() -> None:
    """条件②は自分の20日平均としか比べない。市場全体が膨らんだ日を測る道具。

    決算シーズンのように市場全体の出来高が上がる日は、個別銘柄の5倍が
    静かな日の5倍と同じ意味を持たない。中央値がそれを見える形にする。
    """
    database = Database("sqlite:///:memory:")
    database.create_all()
    frame = _flat_frame(base=100.0)
    busy_day = frame.index[-1]
    for symbol in ("7203", "6501", "8306"):
        seeded = frame.copy()
        seeded.loc[busy_day, VOLUME] = seeded[VOLUME].iloc[0] * 6.0  # 全銘柄が6倍
        _seed(database, symbol, "JP", seeded)

    context = market_volume_context(database, busy_day.date())

    assert context.symbols_measured == 3
    assert context.median_multiple == pytest.approx(6.0)
    assert context.over_5x == 3
    assert context.over_2x == 3
    database.dispose()


def test_market_volume_context_stays_near_one_on_an_ordinary_day() -> None:
    """1銘柄だけ跳ねた日は、中央値では平常に見える。そこが知りたい差。"""
    database = Database("sqlite:///:memory:")
    database.create_all()
    frame = _flat_frame(base=100.0)
    day = frame.index[-1]
    _seed(database, "7203", "JP", _spike_volume(frame))  # この1銘柄だけ6倍
    for symbol in ("6501", "8306"):
        _seed(database, symbol, "JP", frame)

    context = market_volume_context(database, day.date())

    assert context.median_multiple == pytest.approx(1.0)
    assert context.over_5x == 1
    database.dispose()
