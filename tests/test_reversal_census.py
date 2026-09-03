"""短期リバーサルの母集団を数える手順が、意図どおりに絞っているか。

決算ドリフトと違い、リバーサルはイベント駆動ではない。全銘柄が毎営業日
「直近5日でどれだけ下げたか」を持つので、観測は銘柄×営業日になる。
そのぶん「1日に何銘柄通るか」が効いてくる。
"""

from __future__ import annotations

import pandas as pd
import pytest

from stock_ai.backtest.pead import Period
from stock_ai.backtest.reversal_census import run_census
from stock_ai.data.schema import ADJ_CLOSE, CLOSE, HIGH, LOW, OPEN, VOLUME
from stock_ai.database.engine import Database
from stock_ai.database.repository import PriceRepository

_BARS = 120


def _frame(bars: int = _BARS, *, close: float = 1_000.0, volume: float = 200_000.0):
    index = pd.bdate_range("2024-01-01", periods=bars, name="date")
    return pd.DataFrame(
        {OPEN: close, HIGH: close, LOW: close, CLOSE: close, ADJ_CLOSE: close, VOLUME: volume},
        index=index,
    )


def _database(frames: dict[str, pd.DataFrame]) -> Database:
    database = Database("sqlite:///:memory:")
    database.create_all()
    with database.session() as session:
        for symbol, frame in frames.items():
            PriceRepository(session).upsert_prices(symbol, frame, market="JP")
    return database


def test_every_symbol_day_with_a_full_window_becomes_an_observation() -> None:
    # 通るには4つ揃う必要がある。**売買代金の20営業日平均が、いちばん長い
    # 履歴を要求する。** 5日の起点より先に、こちらで落ちる。
    #
    #   0-4    起点(5営業日前)が無い         5本
    #   5-19   売買代金の20営業日平均が NaN  15本
    #   20-98  通る                          79本
    #   99-119 保有期間ぶんの終値が無い      21本
    database = _database({"7203": _frame()})

    report = run_census(database, Period.ALL)

    assert report.observations == 79
    assert report.trading_days == 79
    assert report.excluded_no_lookback == 5
    assert report.excluded_thin == 15  # 薄いのではなく、履歴が足りない
    assert report.excluded_no_window == 21
    counted = report.observations + report.excluded_no_lookback
    counted += report.excluded_thin + report.excluded_no_window
    assert counted == _BARS  # どの経路にも数えられずに消えたものは無い


def test_a_thin_symbol_is_excluded_by_turnover_not_by_the_window() -> None:
    # 終値1,000円 × 出来高1万株 = 売買代金1,000万円。下限1億円に届かない。
    database = _database({"7203": _frame(volume=10_000.0)})

    report = run_census(database, Period.ALL)

    assert report.observations == 0
    assert report.excluded_thin > 0
    assert report.excluded_no_window == 0  # 流動性で先に落ちる


def test_the_sorting_variable_is_the_trailing_return_over_the_lookback() -> None:
    frame = _frame()
    # 40本目で10%下げ、その後は横ばい。5日リターンは40〜44本目で −10%。
    frame.loc[frame.index[40] :, [OPEN, HIGH, LOW, CLOSE, ADJ_CLOSE]] *= 0.9
    database = _database({"7203": frame})

    report = run_census(database, Period.ALL)

    assert min(report.returns) == pytest.approx(-0.10)
    # 下げた直後の5日ぶんだけが負になる。それ以外は横ばいなのでゼロ。
    assert sum(1 for value in report.returns if value < -0.001) == 5


def test_breadth_counts_symbols_per_day_because_a_thin_day_cannot_be_split() -> None:
    # 5分位に切るには1日5銘柄が要る。2銘柄しかない日は差を取れない。
    database = _database({"7203": _frame(), "6758": _frame()})

    report = run_census(database, Period.ALL)

    assert max(report.per_day.values()) == 2
    assert report.thin_days == report.trading_days


def test_the_period_split_uses_the_decision_day() -> None:
    frame = _frame(bars=800)  # 2024-01 から 2027 年ごろまで
    database = _database({"7203": frame})

    inside = run_census(database, Period.IS)
    outside = run_census(database, Period.OOS)
    everything = run_census(database, Period.ALL)

    # IS は 2024-01-01 より前なので、この価格系列では1件も入らない。
    assert inside.observations == 0
    assert outside.observations == everything.observations


def test_turnover_profile_needs_enough_observations_to_be_meaningful() -> None:
    # 観測が少なすぎるときに分位を切ると、1銘柄の値がそのまま中央値になる。
    # 数字が出ないほうが、もっともらしい嘘より良い。
    database = _database({"7203": _frame()})

    assert run_census(database, Period.ALL).turnover_profile() == []
