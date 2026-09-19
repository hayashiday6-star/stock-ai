"""#12「トレンドは友」— 過去12ヶ月（直近1ヶ月を除く）で並べた分位。

**格言は、上がっている銘柄は上がり続けると言っている。**

ここで押さえるのは5つ。

1. **並べる向きを取り違えない。** 添字0が最も負けている、末尾が最も勝って
   いる。差は「勝者 − 敗者」である
2. **スキップ月を形成期間に入れない。** 入れれば短期反転と混ざり、
   「モメンタムを測った」ことにならない
3. **先読みを入れない。** 組み替え日より後の価格を1つも読まない
4. **古すぎる価格を黙って使わない。** 代用は認めるが、1ヶ月以上古ければ外す
5. **部品ではなく、組み立てを1本通す。** `factor_panel` は部品を13個テスト
   していて、`build_panel` 自体を一度も呼んでいなかった
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from stock_ai.backtest.momentum import (
    FORMATION_MONTHS,
    SKIP_MONTHS,
    STALE_LIMIT_DAYS,
    USABLE_FROM,
    MomentumSeries,
    build_series,
    momentum_on,
)
from stock_ai.backtest.pead import Period
from stock_ai.data.schema import ADJ_CLOSE, CLOSE, HIGH, LOW, OPEN, VOLUME
from stock_ai.database.engine import Database
from stock_ai.database.repository import PriceRepository

# **形成12ヶ月 + スキップ1ヶ月 + 判定する月**で、最低14ヶ月分の月末が要る。
# 3年ぶん置いて、断面が何本か立つようにする。
_BARS = 780
_INDEX = pd.bdate_range("2008-01-01", periods=_BARS, name="date")


def _frame(seed: int, drift: float = 0.0, volume: float = 500_000.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 1_000.0 * np.exp(np.cumsum(rng.normal(drift, 0.01, _BARS)))
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


def _database(count: int = 30) -> tuple[Database, list[str]]:
    database = Database("sqlite:///:memory:")
    database.create_all()
    symbols = [f"{1300 + index:04d}" for index in range(count)]
    with database.session() as session:
        repo = PriceRepository(session)
        repo.upsert_prices("1306", _frame(seed=999), market="JP")
        for index, symbol in enumerate(symbols):
            repo.upsert_prices(symbol, _frame(seed=index), market="JP")
    return database, symbols


def _snapshots(symbols: list[str]) -> dict[dt.date, set[str]]:
    return {_INDEX[0].date(): set(symbols)}


class TestTheSignalLooksBackwardsOnly:
    """**組み替え日より後の価格を1つも読まない。**"""

    @staticmethod
    def _calendar() -> tuple[pd.DatetimeIndex, list[int]]:
        from stock_ai.backtest.lowvol_census import formation_dates

        return _INDEX, formation_dates(_INDEX)

    def test_the_skip_month_is_excluded_from_the_formation(self) -> None:
        """**直近1ヶ月は形成期間に入らない。**

        最後の月だけ大きく動かす。スキップが効いていれば、その動きは
        signal に**現れない。**
        """
        calendar, formations = self._calendar()
        flat = np.ones(len(calendar)) * 100.0
        spiked = flat.copy()
        # **スキップされるのは `formations[19]` の「後」の1ヶ月**である。
        # `formations[19]` そのものは形成期間の終点なので、そこを動かせば
        # signal は当然変わる——最初そこを含めて書いて落ちた（テストのほうが
        # 間違っていた）。
        spiked[formations[19] + 1 : formations[20] + 1] = 200.0

        quiet, _ = momentum_on(calendar, flat, calendar, formations, 20)
        loud, _ = momentum_on(calendar, spiked, calendar, formations, 20)

        assert quiet == pytest.approx(0.0)
        assert loud == pytest.approx(0.0), "スキップ月の動きが signal に漏れている"

        # **この検査が何かを守っていることを、同じ場所で見せる。**
        # スキップを外せば同じ盤面が +100% になる。落ちないなら、この検査は
        # 何も守っていない。
        without_skip, _ = momentum_on(calendar, spiked, calendar, formations, 20, skip_months=0)
        assert without_skip == pytest.approx(1.0)

    def test_the_formation_window_is_the_one_the_prereg_fixed(self) -> None:
        """形成は ``index-13`` の月末から ``index-1`` の月末まで。"""
        calendar, formations = self._calendar()
        closes = np.ones(len(calendar)) * 100.0
        # 形成の始点だけ半分にすれば、モメンタムは +100% になる。
        closes[formations[20 - SKIP_MONTHS - FORMATION_MONTHS]] = 50.0

        found, reason = momentum_on(calendar, closes, calendar, formations, 20)

        assert reason == "ok"
        assert found == pytest.approx(1.0)

    def test_the_rebalance_day_price_is_not_read(self) -> None:
        """**組み替え日そのものの価格は使わない。** スキップとはそういう意味。"""
        calendar, formations = self._calendar()
        closes = np.ones(len(calendar)) * 100.0
        closes[formations[20]] = 10_000.0

        found, _ = momentum_on(calendar, closes, calendar, formations, 20)

        assert found == pytest.approx(0.0)

    def test_too_little_history_is_its_own_answer(self) -> None:
        """**新規上場は「履歴が無い」であって「モメンタム 0」ではない。**"""
        calendar, formations = self._calendar()
        closes = np.ones(len(calendar)) * 100.0

        found, reason = momentum_on(calendar, closes, calendar, formations, 5)

        assert found is None
        assert reason == "no_history"

    def test_a_stale_price_is_refused_not_used(self) -> None:
        """**何年も前の価格が「その月の値」として黙って入らない。**"""
        calendar, formations = self._calendar()
        # 形成の始点より後に足が1本も無い暦を作る。
        short = calendar[: formations[20 - SKIP_MONTHS - FORMATION_MONTHS] + 1]
        closes = np.ones(len(short)) * 100.0

        found, reason = momentum_on(short, closes, calendar, formations, 20)

        assert found is None
        assert reason == "stale"

    def test_the_staleness_limit_comes_from_the_calendar(self) -> None:
        """**31 は暦から出した値である。** 月末は約30日おき。"""
        assert STALE_LIMIT_DAYS == 31

    def test_a_negative_window_is_refused(self) -> None:
        """**この検査が落ちる条件を、実際に1つ作る。**"""
        calendar, formations = self._calendar()
        closes = np.ones(len(calendar)) * 100.0

        with pytest.raises(ValueError, match="formation_months"):
            momentum_on(calendar, closes, calendar, formations, 20, formation_months=0)


class TestTheDirectionIsWinnersMinusLosers:
    """**添字0が最も負けている、末尾が最も勝っている。**

    符号を逆にすると、格言の当否が反転する。
    """

    def test_the_spread_is_the_top_minus_the_bottom(self) -> None:
        series = MomentumSeries(
            months=[dt.date(2010, 1, 29)],
            quantiles=[(0.01, 0.02, 0.03, 0.04, 0.09)],
            members=[(frozenset({"a"}), frozenset({"b"}))],
            counts=[100],
            benchmark=[0.0],
        )

        assert series.spread() == [pytest.approx(0.08)]

    def test_the_bottom_group_holds_the_lowest_momentum(self) -> None:
        """**組み立てで確かめる。** 部品だけでは向きを取り違えても気付けない。"""
        database, symbols = _database()
        series = build_series(
            database,
            symbols=symbols,
            end=_INDEX[-1].date(),
            snapshots=_snapshots(symbols),
            min_symbols=10,
        )

        assert series.months
        # 添字0の顔ぶれと末尾の顔ぶれは重ならない。
        for low, high in series.members:
            assert not (low & high)


class TestTheWholeThingRunsEndToEnd:
    """**部品だけでなく、組み立てを1本通す。**

    `factor_panel` は部品を13個テストしていたのに `build_panel` を一度も
    呼んでいなかった。存在しない引数が本番まで出て行き、**その間テストは
    全部緑だった。**
    """

    def test_it_produces_months(self) -> None:
        database, symbols = _database()

        series = build_series(
            database,
            symbols=symbols,
            end=_INDEX[-1].date(),
            snapshots=_snapshots(symbols),
            min_symbols=10,
        )

        assert series.months
        assert len(series.quantiles) == len(series.months)
        assert len(series.benchmark) == len(series.months)

    def test_the_first_month_is_not_before_the_usable_floor(self) -> None:
        database, symbols = _database()

        series = build_series(
            database,
            symbols=symbols,
            end=_INDEX[-1].date(),
            snapshots=_snapshots(symbols),
            min_symbols=10,
        )

        assert series.months[0] >= USABLE_FROM

    def test_the_summary_names_what_it_dropped(self) -> None:
        database, symbols = _database()

        series = build_series(
            database,
            symbols=symbols,
            end=_INDEX[-1].date(),
            snapshots=_snapshots(symbols),
            min_symbols=10,
        )

        assert "履歴が足りず外した銘柄月" in series.summary()
        assert "足が古くて外した銘柄月" in series.summary()

    def test_the_in_sample_window_never_reaches_the_out_of_sample_one(self) -> None:
        """**1日も重ねない。**"""
        database, symbols = _database()
        cut = dt.date(2009, 12, 31)

        series = build_series(
            database,
            symbols=symbols,
            end=cut,
            snapshots=_snapshots(symbols),
            min_symbols=10,
        )

        assert series.months
        assert max(series.months) <= cut

    def test_a_period_with_no_rebalance_is_refused(self) -> None:
        database, symbols = _database()

        with pytest.raises(ValueError):
            build_series(
                database,
                symbols=symbols,
                period=Period.OOS,
                end=dt.date(2009, 6, 30),
                snapshots=_snapshots(symbols),
            )

    def test_a_missing_benchmark_is_refused(self) -> None:
        database = Database("sqlite:///:memory:")
        database.create_all()

        with pytest.raises(ValueError, match="ベンチマーク"):
            build_series(database, symbols=["1301"])


class TestSurvivorshipNeedsTheRoster:
    def test_without_a_roster_more_symbols_get_in(self) -> None:
        """**名簿を渡さなければ、その月に上場していない銘柄まで入る。**"""
        database, symbols = _database()
        late = {_INDEX[-1].date(): set(symbols)}

        with_roster = build_series(
            database, symbols=symbols, end=_INDEX[-1].date(), snapshots=late, min_symbols=1
        )
        without = build_series(
            database, symbols=symbols, end=_INDEX[-1].date(), snapshots=None, min_symbols=1
        )

        assert sum(with_roster.counts) < sum(without.counts)


class TestTheDroppedAreCountedSeparately:
    """**理由ごとに独立に数える。** 1つにまとめると、どちらで落ちたか分からない。"""

    def test_a_short_history_lands_in_no_history_not_stale(self) -> None:
        database, symbols = _database(count=20)
        with database.session() as session:
            # 途中から始まる銘柄。形成期間が埋まらない月が出る。
            short = _frame(seed=77).iloc[400:]
            PriceRepository(session).upsert_prices("9999", short, market="JP")

        series = build_series(
            database,
            symbols=[*symbols, "9999"],
            end=_INDEX[-1].date(),
            snapshots=_snapshots([*symbols, "9999"]),
            min_symbols=5,
        )

        assert series.skipped_no_history > 0

    def test_the_counters_are_independent(self) -> None:
        """**0 を「異常なし」と読まない。** 別々のカウンタであること。"""
        series = MomentumSeries(
            months=[dt.date(2010, 1, 29)],
            quantiles=[(0.0,) * 5],
            members=[(frozenset(), frozenset())],
            counts=[100],
            benchmark=[0.0],
            skipped_no_history=7,
            skipped_stale=3,
        )

        assert (series.skipped_no_history, series.skipped_stale) == (7, 3)


class TestTheTailIsReported:
    """**平均が同じでも、裾が違えば別の戦略である**（事前登録 §5）。"""

    @staticmethod
    def _series(spread: list[float]) -> MomentumSeries:
        return MomentumSeries(
            months=[dt.date(2010, 1, 29)] * len(spread),
            quantiles=[(0.0, 0.0, 0.0, 0.0, value) for value in spread],
            members=[(frozenset(), frozenset())] * len(spread),
            counts=[100] * len(spread),
            benchmark=[0.0] * len(spread),
        )

    def test_the_worst_month_is_the_minimum(self) -> None:
        assert self._series([0.05, -0.30, 0.02]).worst_month() == pytest.approx(-0.30)

    def test_the_left_tail_averages_a_band_not_a_point(self) -> None:
        """**1点だと、1回の事故かそういう性質かが分からない。**"""
        found = self._series([-0.30, -0.20, 0.01, 0.02, 0.03]).left_tail(share=0.4)

        assert found == pytest.approx(-0.25)

    def test_the_hit_rate_is_the_share_of_positive_months(self) -> None:
        assert self._series([0.01, -0.01, 0.02, 0.03]).hit_rate() == pytest.approx(0.75)

    def test_a_share_outside_the_range_is_refused(self) -> None:
        with pytest.raises(ValueError, match="share"):
            self._series([0.01]).left_tail(share=0.0)

    def test_an_empty_series_says_nan_rather_than_zero(self) -> None:
        """**測れていないことを 0 と読まない。**"""
        empty = self._series([])

        assert np.isnan(empty.worst_month())
        assert np.isnan(empty.hit_rate())


class TestTheDesignIsFixedByThePrereg:
    """**この表から外れたことは一切しない**（事前登録 §3）。

    #10 は設計の選び方で IS の `t` が 2.8倍振れて、封印できなかった。
    """

    def test_the_formation_and_skip_are_the_standard_ones(self) -> None:
        assert (FORMATION_MONTHS, SKIP_MONTHS) == (12, 1)

    def test_the_command_exposes_no_knob_for_them(self) -> None:
        """**動かせると、試して選ぶことになる。**"""
        from tests.test_cli import declared_options

        options = declared_options("momentum-power")

        assert "--formation" not in options
        assert "--skip" not in options
        assert "--quantiles" not in options
