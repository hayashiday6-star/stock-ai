"""#9「買いにくい相場は高い」— PBR で並べた分位。

**格言は、割高な側が割安な側を上回ると言っている。** 世間の常識と逆向きである。

ここで押さえるのは4つ。

1. **並べる向きを取り違えない。** 添字0が最も割安、末尾が最も割高で、
   差は「高PBR − 低PBR」である。符号を逆にすると、格言の当否が反転する
2. **先読みを入れない。** 組み替え日より後の PBR を使わない
3. **生存バイアスを入れない。** 名簿を渡さなければ、その月に上場していない
   銘柄まで入る
4. **入れ替わり率は測る。** #7 の 11.5% を写さない
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from stock_ai.backtest.antivalue import ROUND_TRIP_COST, USABLE_FROM, build_series
from stock_ai.backtest.pead import Period
from stock_ai.data.schema import ADJ_CLOSE, CLOSE, HIGH, LOW, OPEN, VOLUME
from stock_ai.database.engine import Database
from stock_ai.database.repository import PriceRepository

_BARS = 260
_INDEX = pd.bdate_range("2009-01-01", periods=_BARS, name="date")


def _frame(seed: int, drift: float = 0.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 1_000.0 * np.exp(np.cumsum(rng.normal(drift, 0.01, _BARS)))
    return pd.DataFrame(
        {
            OPEN: close,
            HIGH: close,
            LOW: close,
            CLOSE: close,
            ADJ_CLOSE: close,
            VOLUME: [500_000.0] * _BARS,
        },
        index=_INDEX,
    )


def _database(count: int = 60) -> tuple[Database, list[str]]:
    database = Database("sqlite:///:memory:")
    database.create_all()
    symbols = [f"{1300 + index:04d}" for index in range(count)]
    with database.session() as session:
        repo = PriceRepository(session)
        repo.upsert_prices("1306", _frame(seed=999), market="JP")
        for index, symbol in enumerate(symbols):
            repo.upsert_prices(symbol, _frame(seed=index), market="JP")
    return database, symbols


def _valuation(symbols: list[str], pbr_of=None) -> pd.DataFrame:
    """月末ごとに、銘柄に PBR を振る。既定は銘柄番号順。"""
    rows = []
    months = pd.Series(_INDEX).dt.to_period("M").unique()
    for month in months:
        last = max(day for day in _INDEX if day.to_period("M") == month)
        for index, symbol in enumerate(symbols):
            value = pbr_of(index, month) if pbr_of else 0.5 + index * 0.01
            rows.append({"date": last.date(), "symbol": symbol, "pbr": value})
    return pd.DataFrame(rows)


class TestTheSortRunsCheapToExpensive:
    def test_the_last_quantile_holds_the_highest_pbr(self) -> None:
        database, symbols = _database()
        # 割高な銘柄ほど上がる形を作る（格言が正しい世界）
        drifts = {symbol: 0.0004 * index for index, symbol in enumerate(symbols)}
        with database.session() as session:
            repo = PriceRepository(session)
            for symbol, drift in drifts.items():
                repo.upsert_prices(
                    symbol, _frame(seed=hash(symbol) % 999, drift=drift), market="JP"
                )

        series = build_series(database, _valuation(symbols), symbols=symbols, min_symbols=10)

        assert series.months
        # 最も割高な分位の顔ぶれが、番号の大きい銘柄であること
        richest = series.members[0][1]
        assert min(int(symbol) for symbol in richest) >= 1300 + len(symbols) // 2

    def test_the_spread_is_expensive_minus_cheap(self) -> None:
        """**符号を逆にすると、格言の当否が反転する。**"""
        database, symbols = _database()
        series = build_series(database, _valuation(symbols), symbols=symbols, min_symbols=10)

        for row, spread in zip(series.quantiles, series.spread(), strict=True):
            assert spread == row[-1] - row[0]


class TestNoLookahead:
    def test_a_pbr_dated_after_the_rebalance_is_not_used(self) -> None:
        """組み替え日より後の PBR を使わない。**先読みである。**

        日付を後ろへずらすと、同じ月の中で組み替え日を追い越す銘柄月が出る。
        **そこが落ちること**を、ずらす前と比べて見る。「全部落ちる」ほど強い
        主張にすると、ずらしが月をまたいで別の月で拾われたときに、守りが
        効いていなくても通ってしまう。
        """
        database, symbols = _database()
        plain = _valuation(symbols)
        shifted = plain.assign(date=plain["date"].apply(lambda day: day + dt.timedelta(days=3)))

        before = build_series(database, plain, symbols=symbols, min_symbols=10)
        after = build_series(database, shifted, symbols=symbols, min_symbols=10)

        assert after.skipped_no_pbr > before.skipped_no_pbr
        assert sum(after.counts) < sum(before.counts)

    def test_a_month_with_no_pbr_is_counted_not_silently_dropped(self) -> None:
        database, symbols = _database()
        valuation = _valuation(symbols)
        valuation = valuation[valuation["symbol"] != symbols[0]]

        series = build_series(database, valuation, symbols=symbols, min_symbols=10)

        assert series.skipped_no_pbr > 0


class TestSurvivorship:
    def test_a_symbol_absent_from_the_roster_is_left_out(self) -> None:
        database, symbols = _database()
        rosters = {dt.date(2008, 12, 1): set(symbols[:30])}

        series = build_series(
            database, _valuation(symbols), symbols=symbols, min_symbols=10, snapshots=rosters
        )

        assert series.months
        assert all(count <= 30 for count in series.counts)

    def test_without_a_roster_everything_is_included(self) -> None:
        database, symbols = _database()

        series = build_series(database, _valuation(symbols), symbols=symbols, min_symbols=10)

        assert max(series.counts) > 30


class TestTurnoverIsMeasuredNotCopied:
    def test_a_sort_that_never_changes_has_no_turnover(self) -> None:
        database, symbols = _database()

        series = build_series(database, _valuation(symbols), symbols=symbols, min_symbols=10)

        assert series.turnover() == 0.0

    def test_a_sort_that_reshuffles_every_month_has_high_turnover(self) -> None:
        database, symbols = _database()
        rng = np.random.default_rng(0)

        def shuffled(index: int, month) -> float:
            return float(rng.random())

        series = build_series(
            database, _valuation(symbols, shuffled), symbols=symbols, min_symbols=10
        )

        assert series.turnover() > 0.5

    def test_the_cost_follows_the_measured_turnover(self) -> None:
        """**#7 の 11.5% を写さない。** 並べ方が違えば入れ替わりも違う。"""
        database, symbols = _database()

        series = build_series(database, _valuation(symbols), symbols=symbols, min_symbols=10)

        assert series.cost_per_month() == ROUND_TRIP_COST * series.turnover()


class TestTheWindowIsWhatThePreregSaid:
    def test_nothing_before_2009_is_used(self) -> None:
        """2008年は `pbr` が 63% しか埋まらない。**断面が歪む。**"""
        database, symbols = _database()

        series = build_series(
            database,
            _valuation(symbols),
            symbols=symbols,
            min_symbols=10,
            start=dt.date(2000, 1, 1),
        )

        assert all(month >= USABLE_FROM for month in series.months)

    def test_the_period_filter_reaches_the_grid(self) -> None:
        database, symbols = _database()

        everything = build_series(
            database, _valuation(symbols), Period.ALL, symbols=symbols, min_symbols=10
        )

        assert everything.months

    def test_a_thin_month_is_counted_rather_than_silently_skipped(self) -> None:
        database, symbols = _database(count=15)

        series = build_series(database, _valuation(symbols), symbols=symbols, min_symbols=100)

        assert series.months == []
        assert series.skipped_thin > 0
        assert "比べていない" in series.summary()
