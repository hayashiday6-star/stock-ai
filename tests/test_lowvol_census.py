"""低ボラの母集団を数える手順が、意図どおりに絞っているか。

#6 と違い月次リバランスなので、観測は銘柄×営業日ではなく**銘柄×月**になる。
数え口が1つでも漏れると、通らなかった銘柄月がどこへ行ったか分からなくなる。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stock_ai.backtest.lowvol_census import formation_dates, run_census
from stock_ai.backtest.pead import Period
from stock_ai.data.schema import ADJ_CLOSE, CLOSE, HIGH, LOW, OPEN, VOLUME
from stock_ai.database.engine import Database
from stock_ai.database.repository import PriceRepository

_BARS = 400
_INDEX = pd.bdate_range("2024-01-01", periods=_BARS, name="date")


def _frame(seed: int = 0, volatility: float = 0.01, volume: float = 500_000.0):
    """乱数で日次リターンを作った系列。ボラティリティだけを狙って変える。"""
    rng = np.random.default_rng(seed)
    steps = rng.normal(0.0, volatility, _BARS)
    close = 1_000.0 * np.exp(np.cumsum(steps))
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


def _universe(count: int = 12) -> dict[str, pd.DataFrame]:
    """ベンチマーク（1306）と、それに衝突しないコードの銘柄群。

    最初は 1300 番台を使って 1306 と重なり、対象が1つ少なくなっていた。
    数え口の合計が合わない形で出たので気付けた。
    """
    frames = {"1306": _frame(seed=99)}
    for index in range(count):
        frames[f"{7200 + index:04d}"] = _frame(seed=index, volatility=0.005 + 0.002 * index)
    return frames


def test_formation_dates_are_the_last_session_of_each_month() -> None:
    """**暦はベンチマークのもの。** 銘柄ごとに月末を決めると窓がずれる。"""
    calendar = pd.bdate_range("2024-01-01", periods=70, name="date")
    positions = formation_dates(calendar)

    months = {calendar[position].strftime("%Y-%m") for position in positions}
    assert months == {"2024-01", "2024-02", "2024-03"}
    # 最後の月は「次の月がある」ことが確認できないので含めない。
    assert calendar[positions[0]].day >= 29


def test_every_symbol_month_lands_in_exactly_one_counter() -> None:
    """**どの経路にも数えられずに消えるものが無いこと。**

    通らなかった銘柄月がどこへ行ったか分からないと、件数が想定と違うときに
    推測するしかなくなる。#1 と #6 でそれをやって外した。
    """
    database = _database(_universe())
    (census,) = run_census(database, Period.ALL, windows=(60,))

    total = (
        len(census.volatilities)
        + census.excluded_no_history
        + census.excluded_thin
        + census.excluded_no_window
        + census.excluded_discontinuity
    )
    assert total == census.months * 12  # 12銘柄 × 組み替え日


def test_a_longer_window_admits_fewer_symbol_months() -> None:
    """**窓が長いほど履歴を要求する。** どれを使うかはこの減り方を見て決める。"""
    database = _database(_universe())
    short, long_ = run_census(database, Period.ALL, windows=(60, 250))

    assert len(short.volatilities) > len(long_.volatilities)
    assert long_.excluded_no_history > short.excluded_no_history


def test_a_thin_symbol_is_excluded_by_turnover() -> None:
    """終値1,000円 × 出来高1万株 = 1,000万円。下限1億円に届かない。"""
    frames = _universe()
    frames["7200"] = _frame(seed=0, volume=10_000.0)

    database = _database(frames)
    (census,) = run_census(database, Period.ALL, windows=(60,))

    assert census.excluded_thin > 0


def test_a_window_spanning_a_discontinuity_is_excluded() -> None:
    """**またぐ窓のボラティリティは、値動きではなく尺度の変わり目を測る。**

    #6 で見つけた 8308（1:1000 の併合）と同じ形。規則も定数も #6 と共有する。
    """
    frames = _universe()
    broken = frames["7200"].copy()
    broken.iloc[200:, broken.columns.get_indexer([OPEN, HIGH, LOW, CLOSE, ADJ_CLOSE])] *= 1_000.0
    frames["7200"] = broken

    database = _database(frames)
    (census,) = run_census(database, Period.ALL, windows=(60,))

    assert census.excluded_discontinuity > 0


def test_the_volatility_ordering_matches_how_the_series_were_built() -> None:
    """**分位1は低いほう（買う側）。** 符号を取り違えると全部が裏返る。"""
    database = _database(_universe())
    (census,) = run_census(database, Period.ALL, windows=(60,))

    frame = pd.DataFrame({"month": census.months_key, "vol": census.volatilities})
    assert frame["vol"].min() < frame["vol"].max()
    assert census.volatility_quantiles()[0][1] < census.volatility_quantiles()[-1][1]


def test_the_census_reports_sector_and_turnover_by_quantile() -> None:
    """業種の偏りも規模の偏りも、**測ってから言う。**"""
    database = _database(_universe())
    (census,) = run_census(database, Period.ALL, windows=(60,))

    assert len(census.turnover_profile()) == 5
    assert len(census.sector_profile()) == 5


def test_the_census_needs_a_benchmark_for_the_calendar() -> None:
    frames = {f"{7200 + index:04d}": _frame(seed=index) for index in range(12)}

    with pytest.raises(ValueError, match="ベンチマーク"):
        run_census(_database(frames), Period.ALL, windows=(60,))


def test_persistence_is_measured_not_assumed() -> None:
    """**この説を選んだ理由そのものを測る。**

    月次リバランスでも構成が変わらないなら、実効費用は残存率で割られる。
    当て推量で登録すると、判定の意味が変わってしまう。
    """
    database = _database(_universe())
    (census,) = run_census(database, Period.ALL, windows=(60,))

    kept, compared = census.quantile_persistence()

    assert compared > 0
    assert 0.0 <= kept <= 1.0


def test_persistence_is_one_when_the_ordering_never_changes() -> None:
    """ボラティリティの順序が動かない系列なら、分位1は入れ替わらない。"""
    frames = {"1306": _frame(seed=99)}
    for index in range(10):
        # 各銘柄のボラティリティを大きく離すと、順序は月をまたいでも安定する。
        frames[f"{7200 + index:04d}"] = _frame(seed=index, volatility=0.004 * (index + 1))

    (census,) = run_census(_database(frames), Period.ALL, windows=(60,))
    kept, compared = census.quantile_persistence()

    assert compared > 0
    assert kept > 0.9


def test_thin_months_are_counted_against_a_threshold() -> None:
    """1ヶ月10銘柄では5分位が2銘柄ずつになる。最低銘柄数は封印前に決める。"""
    database = _database(_universe())
    (census,) = run_census(database, Period.ALL, windows=(60,))

    thin, total = census.thin_months(minimum=1_000)
    assert thin == total  # 12銘柄しかないので全部が「薄い」
    assert census.thin_months(minimum=1)[0] == 0
