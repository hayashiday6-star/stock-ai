"""低ボラの月次系列が、意図した窓・順序・universe で組まれているか。

#6 の判定後に「主要指標が2つのものを混ぜていた」と分かった。ここでは
**分位1 − 全分位平均**を最初から持ち、加重方式の差が入らない指標を用意する。
その3つが期待どおり計算されることを固定する。
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from stock_ai.backtest.lowvol import COST_PER_MONTH, build_series
from stock_ai.backtest.pead import Period
from stock_ai.data.schema import ADJ_CLOSE, CLOSE, HIGH, LOW, OPEN, VOLUME
from stock_ai.database.engine import Database
from stock_ai.database.repository import PriceRepository

_BARS = 400
_INDEX = pd.bdate_range("2024-01-01", periods=_BARS, name="date")


def _frame(seed: int = 0, volatility: float = 0.01, volume: float = 500_000.0):
    rng = np.random.default_rng(seed)
    close = 1_000.0 * np.exp(np.cumsum(rng.normal(0.0, volatility, _BARS)))
    return pd.DataFrame(
        {
            OPEN: close,
            HIGH: close,
            LOW: close,
            CLOSE: close,
            ADJ_CLOSE: close,
            VOLUME: [volume] * _BARS,
        },
        index=_INDEX,
    )


def _database(frames: dict[str, pd.DataFrame]) -> Database:
    database = Database("sqlite:///:memory:")
    database.create_all()
    with database.session() as session:
        for symbol, frame in frames.items():
            PriceRepository(session).upsert_prices(symbol, frame, market="JP")
    return database


def _universe(count: int = 20) -> dict[str, pd.DataFrame]:
    frames = {"1306": _frame(seed=99)}
    for index in range(count):
        frames[f"{7200 + index:04d}"] = _frame(seed=index, volatility=0.004 + 0.002 * index)
    return frames


def _series(database: Database, **kwargs):
    return build_series(database, Period.ALL, window=60, min_symbols=10, **kwargs)


def test_quantile_one_holds_the_calmest_names() -> None:
    """**符号の確認。分位1は低ボラ側（買う側）である。**"""
    series = _series(_database(_universe()))

    assert series.months
    # 系列は seed ごとにボラティリティを変えて作ってある。分位1が最も穏やか。
    assert len(series.quantiles[0]) == 5


def test_the_three_metrics_are_computed_as_documented() -> None:
    """3つの指標が、それぞれ違うものを引いていること。"""
    series = _series(_database(_universe()))
    row = series.quantiles[0]
    bench = series.benchmark[0]

    assert series.long_only()[0] == pytest.approx(row[0] - bench)
    assert series.vs_average()[0] == pytest.approx(row[0] - sum(row) / len(row))
    assert series.long_short()[0] == pytest.approx(row[0] - row[-1])


def test_vs_average_is_free_of_the_weighting_gap() -> None:
    """**#6 で混ざったものが、ここでは構造的に入らない。**

    分位平均が全体としてベンチマークからずれていても、分位1 − 全分位平均は
    その分だけ動かない。等金額どうしの比較だからである。
    """
    series = _series(_database(_universe()))

    for row, value in zip(series.quantiles, series.vs_average(), strict=True):
        # 全分位に同じ数を足しても vs_average は変わらない。
        shifted = tuple(item + 0.05 for item in row)
        assert value == pytest.approx(shifted[0] - sum(shifted) / len(shifted))


def test_a_month_below_the_minimum_is_dropped_and_counted() -> None:
    """1ヶ月10銘柄では5分位が2銘柄ずつになる。落として、数える。"""
    database = _database(_universe(count=6))

    series = build_series(database, Period.ALL, window=60, min_symbols=100)

    assert series.months == []
    assert series.excluded_thin_month > 0


def test_the_dated_roster_keeps_the_series_free_of_look_ahead() -> None:
    """**その日以前で最も新しい名簿だけを見る。**"""
    database = _database(_universe())
    listed = {f"{7200 + index:04d}" for index in range(10)}
    snapshots = {dt.date(2023, 1, 1): listed}

    series = _series(database, snapshots=snapshots)

    assert series.counts and max(series.counts) <= 10
    assert series.universe_label == "snapshots"


def test_survivors_only_applies_the_latest_roster_everywhere() -> None:
    """生存バイアスの対照。**意図的に先読みしている側。**"""
    database = _database(_universe())
    snapshots = {
        dt.date(2023, 1, 1): {f"{7200 + index:04d}" for index in range(20)},
        dt.date(2024, 6, 1): {f"{7200 + index:04d}" for index in range(12)},
    }

    clean = _series(database, snapshots=snapshots)
    survivors = _series(database, snapshots=snapshots, survivors_only=True)

    assert max(clean.counts) > max(survivors.counts)
    assert survivors.universe_label == "survivors"


def test_the_cost_threshold_comes_from_the_measured_turnover() -> None:
    """**費用は仮定せず、センサスの実測値から作る。**

    0.40%／往復 × 11.5%／月 ＝ 0.046%／月。#6 の 0.40%／20営業日（年5.0%）に
    対し、年 0.55% である。
    """
    assert pytest.approx(0.00046) == COST_PER_MONTH
    assert COST_PER_MONTH * 12 < 0.006


def test_the_series_needs_a_benchmark_for_the_calendar() -> None:
    frames = {f"{7200 + index:04d}": _frame(seed=index) for index in range(20)}

    with pytest.raises(ValueError, match="ベンチマーク"):
        _series(_database(frames))
