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
import pytest

from stock_ai.backtest.antivalue import (
    ROUND_TRIP_COST,
    USABLE_FROM,
    AntiValueSeries,
    build_series,
)
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


class TestTheMarketIsTakenOutBeforeMeasuringTheSpread:
    """**ロング・ショートでも β は 0 ではない。**

    §0 の SD を生の差で測っていた。事前登録 §5 は「β を引いた α も併記する」と
    書いてある。**書いてあるのに落としていた。** 割安な側は市場への感応度が
    高いことが多いので、市場が動いた月はスプレッドが一方向に出る。**その上下動
    が分散のほとんどを作り、検出力を食う。**
    """

    @staticmethod
    def _series(spread_of) -> AntiValueSeries:
        """スプレッドとベンチマークだけを持つ系列を組む。"""
        bench = [0.05, -0.04, 0.03, -0.02, 0.06, -0.05, 0.01, -0.03, 0.04, -0.01]
        quantiles = [(0.0, 0.0, 0.0, 0.0, spread_of(value)) for value in bench]
        return AntiValueSeries(
            months=[dt.date(2009, month, 28) for month in range(1, 11)],
            quantiles=quantiles,
            members=[(frozenset(), frozenset())] * len(bench),
            counts=[500] * len(bench),
            benchmark=bench,
            skipped_thin=0,
            skipped_no_pbr=0,
        )

    def test_a_spread_that_is_only_market_has_a_beta_of_one(self) -> None:
        series = self._series(lambda bench: bench)

        assert series.beta_to_benchmark() == pytest.approx(1.0)

    def test_taking_the_market_out_leaves_almost_nothing(self) -> None:
        """**分散のほとんどが市場だったなら、α はその分だけ小さくなる。**"""
        series = self._series(lambda bench: bench)

        alpha = series.alpha(series.beta_to_benchmark())

        assert max(abs(value) for value in alpha) < 1e-9

    def test_a_spread_with_no_market_in_it_is_left_alone(self) -> None:
        """**β を引くことが、いつでも分散を下げるわけではない。**"""
        fixed = [0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01]
        series = self._series(lambda bench: 0.01)
        assert series.spread() == pytest.approx(fixed)

        alpha = series.alpha(series.beta_to_benchmark())

        assert alpha == pytest.approx(fixed)

    def test_the_same_least_squares_is_not_written_twice(self) -> None:
        """正本は `cross_section.beta_to_benchmark` である。**呼ぶ側で書き直さない。**"""
        from stock_ai.backtest.cross_section import beta_to_benchmark

        series = self._series(lambda bench: 2.0 * bench + 0.01)

        assert series.beta_to_benchmark() == pytest.approx(
            beta_to_benchmark(series.spread(), series.benchmark)
        )


class TestTheCommandRefusesBelowTheCommittedFloor:
    """**線は測る前にコミットした。** 下回ったら、封印しない。

    事前登録 `docs/PREREG_ANTIVALUE_JP.md` に「費用引き後で年 1.0% を下回ったら
    封印しない」と書いてある。**書いてあるだけでは止まらない。** 止まるのは
    ここである。

    そして**組み立てを1本通す。** 部品（並び・先読み・名簿・入れ替わり）は
    上で13本試したが、CLI が名簿を配線し忘れていれば、そのどれも鳴らない。
    """

    @staticmethod
    def _database(drift_of, count: int = 120) -> tuple[Database, list[str]]:
        """銘柄番号ごとに向きを変えられる価格。**PBR の順と連動させる。**"""
        database = Database("sqlite:///:memory:")
        database.create_all()
        symbols = [f"{1300 + index:04d}" for index in range(count)]
        with database.session() as session:
            repo = PriceRepository(session)
            repo.upsert_prices("1306", _frame(seed=999), market="JP")
            for index, symbol in enumerate(symbols):
                repo.upsert_prices(
                    symbol, _frame(seed=index, drift=drift_of(index, count)), market="JP"
                )
        return database, symbols

    @classmethod
    def _run(cls, tmp_path, monkeypatch, drift_of):
        from typer.testing import CliRunner

        from stock_ai import cli

        database, symbols = cls._database(drift_of)
        path = tmp_path / "valuation_monthly.csv.gz"
        frame = _valuation(symbols)
        for column in ("per", "bps", "market_cap"):
            frame[column] = 1.0
        frame.to_csv(path, index=False, compression="gzip")

        asked: list[str] = []

        def fake_membership(directory):
            asked.append(str(directory))
            return {day.date(): set(symbols) for day in _INDEX}

        monkeypatch.setattr(cli, "Database", lambda *a, **k: database)
        monkeypatch.setattr(cli, "membership", fake_membership)

        result = CliRunner().invoke(
            cli.app,
            [
                "antivalue-estimate",
                "--valuation",
                str(path),
                "--rosters",
                str(tmp_path / "rosters"),
                "--is-end",
                "2009-12-31",
            ],
        )
        return result, asked

    @staticmethod
    def _proverb_holds(index: int, count: int) -> float:
        """割高（PBR の高いほう）ほど上がる。**格言が正しい世界。**"""
        return 0.002 if index >= count // 2 else -0.002

    @staticmethod
    def _proverb_fails(index: int, count: int) -> float:
        """割安ほど上がる。**世間の常識どおりの世界。**"""
        return -0.002 if index >= count // 2 else 0.002

    def test_a_losing_is_window_is_not_sealed(self, tmp_path, monkeypatch) -> None:
        """格言と逆向きに出た回。**線を下回れば、封印しない。**"""
        result, _ = self._run(tmp_path, monkeypatch, self._proverb_fails)

        assert result.exit_code == 0, result.output
        assert "封印しない" in result.output
        assert "1.0%" in result.output
        assert "ゲートである" not in result.output

    def test_a_winning_is_window_goes_on_to_the_gate(self, tmp_path, monkeypatch) -> None:
        """**線を上回っても、そこで封印ではない。** 次が §0 のゲートである。"""
        result, _ = self._run(tmp_path, monkeypatch, self._proverb_holds)

        assert result.exit_code == 0, result.output
        assert "封印しない" not in result.output
        assert "ゲートである" in result.output
        assert "power-gate" in result.output
        assert "--budget 20" in result.output

    def test_the_rosters_are_read_rather_than_assumed(self, tmp_path, monkeypatch) -> None:
        """**渡さないと生存バイアスが入る。** CLI が配線を落としていないこと。"""
        _, asked = self._run(tmp_path, monkeypatch, self._proverb_holds)

        assert asked == [str(tmp_path / "rosters")]

    def test_the_out_of_sample_window_is_never_touched(self, tmp_path, monkeypatch) -> None:
        """**判定は一度きりである。** IS を測るのに OOS を読まない。"""
        result, _ = self._run(tmp_path, monkeypatch, self._proverb_holds)

        assert "2009-12-31" in result.output
        assert "OOS" in result.output

    def test_alpha_is_printed_beside_the_raw_spread(self, tmp_path, monkeypatch) -> None:
        """**事前登録 §5 が「併記する」と書いている。** 片方だけ出さない。"""
        result, _ = self._run(tmp_path, monkeypatch, self._proverb_holds)

        assert "α" in result.output
        assert "β" in result.output
        assert "検出できる差" in result.output


class TestReconcilingTheTwoValueMeasurements:
    """**同じ IS を2度測って t が 2.26 と 0.76 に割れた。**

    universe と推定量のどちらが効いているのかを、1つずつ動かして出す道具。
    **固定するのは「揃っていないものを比べない」ことだけである。**
    """

    def test_the_sign_is_flipped_once_to_the_value_direction(self) -> None:
        """`spread()` は #9 の格言の向き（高PBR − 低PBR）である。

        **バリューは逆向きなので反転が要る。反転は1箇所だけ。** 2箇所に置くと、
        どちらで反転したのか分からなくなる（#8 で同じ規則を書いた）。
        """
        import inspect

        from stock_ai import cli

        body = inspect.getsource(cli.value_reconcile)

        assert body.count("-value for value in series.spread()") == 1
        assert body.count("-value for value in series.alpha(") == 1

    def test_the_table_is_gross_and_says_so(self) -> None:
        """**費用を片方だけ引かない。** 費用は平均を動かすので t が動く。"""
        import inspect

        from stock_ai import cli

        body = inspect.getsource(cli.value_reconcile)

        assert "すべて費用引き前" in body

    def test_both_ends_are_reproduced_before_they_are_compared(self) -> None:
        """**揃っていない2つを比べると、差はフィルタの差になる。**

        `composite-gain` が「最初に検算します」と書いてあるのと同じ形。
        """
        import inspect

        from stock_ai import cli

        body = inspect.getsource(cli.value_reconcile)

        assert "検算" in body
        assert "+0.76" in body
        # **格言の向きの +2.26 を、バリュー向きの期待値にしない。**
        #
        # 反転すると費用が逆向きの引き算になり、平均だけ 2×cost ぶん小さく
        # なる（SD は動かない）。**期待値は +2.03 である。**
        assert "+2.03" in body
        assert "格言の向き" in body

    def test_it_runs_end_to_end(self, tmp_path, monkeypatch) -> None:
        """**部品が全部緑でも、繋ぎ忘れは出る。**"""
        from typer.testing import CliRunner

        from stock_ai import cli

        database, symbols = _database(count=120)
        path = tmp_path / "valuation_monthly.csv.gz"
        frame = _valuation(symbols)
        for column in ("per", "bps", "market_cap"):
            frame[column] = 1.0
        frame.to_csv(path, index=False, compression="gzip")

        monkeypatch.setenv("COLUMNS", "200")
        monkeypatch.setattr(cli, "Database", lambda *a, **k: database)
        monkeypatch.setattr(
            cli, "membership", lambda directory: {day.date(): set(symbols) for day in _INDEX}
        )

        result = CliRunner().invoke(
            cli.app,
            [
                "value-reconcile",
                "--valuation",
                str(path),
                "--rosters",
                str(tmp_path / "rosters"),
                "--is-start",
                "2009-01-05",
                "--is-end",
                "2009-12-31",
                "--window",
                "60",
                "--min-symbols",
                "10",
            ],
        )

        assert result.exit_code == 0, result.output
        assert "検算" in result.output
        assert "どこで動くか" in result.output
