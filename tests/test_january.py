"""#14「1月効果」— 小型 − 大型の差が、1月だけ大きいか。

ここで押さえるのは5つ。

1. **並べる向きを取り違えない。** 上端が最小の時価総額で、差は「小型 − 大型」
   である。逆にすると、説の当否が反転する
2. **「1月」の札を1つずらさない。** 12月末を組み替え日とする月次リターンが
   1月である。ずれれば12月を1月と呼ぶことになり、**例外は出ない**
3. **引く相手に1月を入れない。** 入れれば本物が引く側に混ざる（#13 で踏んだ形）
4. **削った側を数える。** 日商1億円の線は小型株をまるごと削る
5. **警告は早期 return しない。** 理由を隠す形を2度踏んでいる
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from stock_ai.backtest.january import (
    JANUARY,
    OTHER_MONTHS,
    USABLE_FROM,
    JanuarySeries,
    annual_episodes,
    build_series,
    realised_month,
    smallness_by_month,
)
from stock_ai.data.schema import ADJ_CLOSE, CLOSE, HIGH, LOW, OPEN, VOLUME
from stock_ai.database.engine import Database
from stock_ai.database.repository import PriceRepository

_INDEX = pd.bdate_range("2008-12-01", "2011-12-31", name="date")
_BARS = len(_INDEX)


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


def _database(count: int = 60, drift_of=None, volume_of=None) -> tuple[Database, list[str]]:
    database = Database("sqlite:///:memory:")
    database.create_all()
    symbols = [f"{1300 + index:04d}" for index in range(count)]
    with database.session() as session:
        repo = PriceRepository(session)
        repo.upsert_prices("1306", _frame(seed=999), market="JP")
        for index, symbol in enumerate(symbols):
            repo.upsert_prices(
                symbol,
                _frame(
                    seed=index,
                    drift=drift_of(index) if drift_of else 0.0,
                    volume=volume_of(index) if volume_of else 500_000.0,
                ),
                market="JP",
            )
    return database, symbols


def _valuation(symbols: list[str], cap_of=None) -> pd.DataFrame:
    """月末ごとに、銘柄に時価総額を振る。既定は銘柄番号順に大きくなる。"""
    rows = []
    months = pd.Series(_INDEX).dt.to_period("M").unique()
    for month in months:
        last = max(day for day in _INDEX if day.to_period("M") == month)
        for index, symbol in enumerate(symbols):
            rows.append(
                {
                    "date": last.date(),
                    "symbol": symbol,
                    "market_cap": cap_of(index, month) if cap_of else 1_000.0 + index * 100.0,
                }
            )
    return pd.DataFrame(rows)


class TestTheSortPutsTheSmallestAtTheTop:
    """**上端が小型でないと、スプレッドが「大型 − 小型」になる。**"""

    def test_the_top_quantile_holds_the_smallest_companies(self) -> None:
        database, symbols = _database()

        series = build_series(database, _valuation(symbols), symbols=symbols, min_symbols=10)

        assert series.months
        smallest = series.members[0][1]
        assert max(int(symbol) for symbol in smallest) < 1300 + len(symbols) // 2

    def test_the_bottom_quantile_holds_the_largest(self) -> None:
        database, symbols = _database()

        series = build_series(database, _valuation(symbols), symbols=symbols, min_symbols=10)

        largest = series.members[0][0]
        assert min(int(symbol) for symbol in largest) >= 1300 + len(symbols) // 2

    def test_the_spread_is_positive_when_small_wins(self) -> None:
        """**作った盤面で証明する。** 小型だけ上げれば、差は正に出るはず。"""
        database, symbols = _database(drift_of=lambda index: 0.002 if index < 12 else -0.002)

        series = build_series(database, _valuation(symbols), symbols=symbols, min_symbols=10)

        assert float(np.mean(series.spread())) > 0

    def test_it_flips_when_the_material_is_not_negated(self) -> None:
        """**この検査が落ちる条件を、実際に1つ作る。**

        小ささではなく時価総額そのもので並べたら、同じ盤面で符号が逆になる。
        """
        from stock_ai.backtest.quantile_series import build_panel, monthly_values

        database, symbols = _database(drift_of=lambda index: 0.002 if index < 12 else -0.002)
        wrong = build_panel(
            database,
            monthly_values(_valuation(symbols), "market_cap"),
            symbols=symbols,
            start=USABLE_FROM,
            min_symbols=10,
        )

        flipped = [row[-1] - row[0] for row in wrong.quantiles]

        assert float(np.mean(flipped)) < 0


class TestSmallnessIsTheNegatedMarketCap:
    def test_the_sign_is_flipped(self) -> None:
        frame = pd.DataFrame(
            [{"date": dt.date(2009, 1, 30), "symbol": "1301", "market_cap": 2_500.0}]
        )

        found = smallness_by_month(frame)

        assert found[("1301", pd.Period("2009-01", freq="M"))] == (dt.date(2009, 1, 30), -2_500.0)

    def test_a_smaller_company_sorts_later(self) -> None:
        """**小さいほど「小ささ」が大きい。** 昇順で末尾に来る。"""
        frame = pd.DataFrame(
            [
                {"date": dt.date(2009, 1, 30), "symbol": "big", "market_cap": 9_000.0},
                {"date": dt.date(2009, 1, 30), "symbol": "small", "market_cap": 10.0},
            ]
        )

        found = smallness_by_month(frame)
        big = found[("big", pd.Period("2009-01", freq="M"))][1]
        small = found[("small", pd.Period("2009-01", freq="M"))][1]

        assert small > big

    def test_a_missing_column_is_named(self) -> None:
        with pytest.raises(KeyError, match="market_cap"):
            smallness_by_month(pd.DataFrame([{"date": dt.date(2009, 1, 30), "symbol": "x"}]))


class TestTheSharedReaderDropsEmptyValues:
    """**並べ替えの鍵に `nan` を入れない。**

    以前の `pbr_by_month` は `float(nan)` をそのまま表に入れていた。実データ
    では `valuation_monthly` が書く前に落としているので出なかったが、
    **出れば、その月の分位が黙って狂う。**
    """

    def test_a_blank_value_is_left_out(self) -> None:
        frame = pd.DataFrame(
            [
                {"date": dt.date(2009, 1, 30), "symbol": "blank", "market_cap": None},
                {"date": dt.date(2009, 1, 30), "symbol": "there", "market_cap": 5.0},
            ]
        )

        found = smallness_by_month(frame)

        assert ("blank", pd.Period("2009-01", freq="M")) not in found
        assert ("there", pd.Period("2009-01", freq="M")) in found

    def test_a_nan_is_left_out_too(self) -> None:
        frame = pd.DataFrame(
            [{"date": dt.date(2009, 1, 30), "symbol": "nan", "market_cap": float("nan")}]
        )

        assert smallness_by_month(frame) == {}


class TestWhichMonthIsJanuary:
    """**12月末に仕込んだものが1月である。** 1つずれれば12月を1月と呼ぶ。"""

    def test_december_lands_in_the_next_january(self) -> None:
        assert realised_month(dt.date(2008, 12, 30)) == (2009, JANUARY)

    def test_november_lands_in_december(self) -> None:
        assert realised_month(dt.date(2017, 11, 30)) == (2017, 12)

    def test_january_lands_in_february(self) -> None:
        assert realised_month(dt.date(2009, 1, 30)) == (2009, 2)

    def test_the_year_rolls_over_only_in_december(self) -> None:
        for month in range(1, 12):
            year, _ = realised_month(dt.date(2010, month, 28))

            assert year == 2010


def _months(first: dt.date, count: int) -> list[dt.date]:
    """月末らしい日を並べる。**中身は日付の札だけ。**"""
    found = []
    when = first
    for _ in range(count):
        found.append(when)
        when = (when.replace(day=28) + dt.timedelta(days=7)).replace(day=28)
    return found


class TestTheEpisodeIsJanuaryMinusTheRestOfTheYear:
    def test_the_arithmetic(self) -> None:
        months = _months(dt.date(2008, 12, 28), 13)
        # 添字0 が 2009年1月、残り12個が 2009年2月〜2010年1月
        spread = [0.10] + [0.01] * 11 + [0.20]

        series = annual_episodes(months, spread)

        assert series.years == [2009]
        assert series.episodes[0] == pytest.approx(0.10 - 0.01)
        assert series.januaries == [0.10]
        assert series.others[0] == pytest.approx(0.01)
        assert series.other_counts == [11]

    def test_january_is_not_in_the_subtracted_side(self) -> None:
        """**入れれば本物が引く側に混ざる。** #13 で踏んだ形である。"""
        months = _months(dt.date(2008, 12, 28), 12)
        spread = [1.0] + [0.0] * 11

        series = annual_episodes(months, spread)

        assert series.others[0] == 0.0
        assert series.episodes[0] == pytest.approx(1.0)

    def test_two_years_give_two_observations(self) -> None:
        months = _months(dt.date(2008, 12, 28), 24)
        spread = [0.0] * 24

        series = annual_episodes(months, spread)

        assert series.years == [2009, 2010]

    def test_a_year_without_january_is_counted_not_dropped_silently(self) -> None:
        months = _months(dt.date(2009, 1, 28), 6)  # 2009年2月〜7月。1月が無い
        spread = [0.0] * 6

        series = annual_episodes(months, spread)

        assert series.years == []
        assert series.skipped_no_january == [2009]

    def test_a_january_with_nothing_to_subtract_is_counted(self) -> None:
        months = [dt.date(2008, 12, 28)]
        spread = [0.05]

        series = annual_episodes(months, spread)

        assert series.years == []
        assert series.skipped_no_others == [2009]

    def test_the_year_filter_cuts_on_the_realised_month(self) -> None:
        """**組み替え日ではなく、実現した月で切る。**

        2008-12 の断面は、2009年の観測を作るのに要る。**年で切って落とさない。**
        """
        months = _months(dt.date(2008, 12, 28), 24)
        spread = [0.0] * 24

        series = annual_episodes(months, spread, first_year=2009, last_year=2009)

        assert series.years == [2009]

    def test_mismatched_lengths_are_refused(self) -> None:
        with pytest.raises(ValueError, match="長さが違う"):
            annual_episodes(_months(dt.date(2009, 1, 28), 3), [0.0, 0.0])

    def test_mismatched_counts_are_refused(self) -> None:
        with pytest.raises(ValueError, match="長さが違う"):
            annual_episodes(_months(dt.date(2009, 1, 28), 3), [0.0] * 3, counts=[1, 2])


class TestTheWarningsSayWhatIsMissing:
    @staticmethod
    def _series(**changed) -> JanuarySeries:
        base = {
            "years": [2009, 2010],
            "episodes": [0.01, -0.01],
            "januaries": [0.02, 0.0],
            "others": [0.01, 0.01],
            "other_counts": [OTHER_MONTHS, OTHER_MONTHS],
            "january_symbols": [1_000, 1_000],
            "skipped_no_january": [],
            "skipped_no_others": [],
        }
        return JanuarySeries(**{**base, **changed})

    def test_a_clean_series_is_quiet(self) -> None:
        assert self._series().warnings() == []

    def test_a_short_year_is_named(self) -> None:
        found = self._series(other_counts=[OTHER_MONTHS, 6]).warnings()

        assert any("2010年 6ヶ月" in line for line in found)

    def test_a_thin_january_is_named(self) -> None:
        """**2008-12 の断面は薄い。** 薄ければ、それは偏りである。"""
        found = self._series(january_symbols=[300, 1_000]).warnings()

        assert any("2009年" in line and "薄い" in line for line in found)

    def test_the_warnings_do_not_stop_at_the_first(self) -> None:
        """**早期 return しない。** 理由を隠す形を2度踏んでいる。"""
        found = self._series(
            other_counts=[OTHER_MONTHS, 6],
            january_symbols=[300, 1_000],
            skipped_no_january=[2011],
        ).warnings()

        assert len(found) >= 3

    def test_an_empty_series_says_so(self) -> None:
        empty = JanuarySeries([], [], [], [], [], [], [], [])

        assert empty.warnings() == ["**1月の観測を1回も作れなかった。**"]
        assert "比べていない" in empty.summary()


class TestTheAutocorrelationIsMeasuredNotAssumed:
    def test_a_flat_series_has_none(self) -> None:
        series = JanuarySeries([1, 2, 3], [0.0, 0.0, 0.0], [], [], [], [], [], [])

        assert series.autocorrelation() == 0.0

    def test_an_alternating_series_is_negative(self) -> None:
        values = [0.1, -0.1] * 5
        series = JanuarySeries(list(range(10)), values, [], [], [], [], [], [])

        assert series.autocorrelation() < -0.5

    def test_too_few_points_give_zero_rather_than_an_exception(self) -> None:
        series = JanuarySeries([1, 2], [0.1, -0.1], [], [], [], [], [], [])

        assert series.autocorrelation() == 0.0


class TestWhatTheLiquidityFilterRemoves:
    """**削った側は、残ったものからは見えない。**

    日商1億円の線は小型株をまるごと削る。**何を削ったのかを数で出す**
    （事前登録 §2・§9）。
    """

    def test_the_small_ones_it_cut_are_counted(self) -> None:
        # 番号の小さい銘柄＝小型。そのうち半分を薄商いにする
        database, symbols = _database(volume_of=lambda index: 10.0 if index < 6 else 500_000.0)

        series = build_series(database, _valuation(symbols), symbols=symbols, min_symbols=10)

        assert series.illiquid > 0
        assert series.illiquid_in_small_range > 0

    def test_nothing_is_counted_when_everything_trades(self) -> None:
        """**この検査が落ちる条件を、実際に1つ作る。**"""
        database, symbols = _database()

        series = build_series(database, _valuation(symbols), symbols=symbols, min_symbols=10)

        assert series.illiquid == 0
        assert series.illiquid_in_small_range == 0

    def test_the_warning_names_the_count(self) -> None:
        database, symbols = _database(volume_of=lambda index: 10.0 if index < 6 else 500_000.0)

        series = build_series(database, _valuation(symbols), symbols=symbols, min_symbols=10)

        assert any("何も主張しない" in line for line in series.warnings())


class TestTheWholeThingRunsOnRealPrices:
    """**部品だけでなく、組み立てを1本通す。**

    `equal_weighted_daily` は**書いたつもりで書いていなかった**のに、部品の
    テストは全部緑だった（2026-09-19）。
    """

    def test_a_populated_database_gives_annual_observations(self) -> None:
        database, symbols = _database()

        series = build_series(database, _valuation(symbols), symbols=symbols, min_symbols=10)
        annual = annual_episodes(series.months, series.spread(), series.counts)

        assert annual.years, series.summary()
        assert 2009 in annual.years
        assert all(count > 0 for count in annual.january_symbols)

    def test_the_end_date_keeps_the_next_year_out(self) -> None:
        """**`end` は退場日まで見る。** IS と OOS の排他はここが担保している。"""
        database, symbols = _database()

        series = build_series(
            database,
            _valuation(symbols),
            symbols=symbols,
            min_symbols=10,
            end=dt.date(2009, 12, 31),
        )
        annual = annual_episodes(series.months, series.spread(), series.counts)

        assert annual.years == [2009]
        assert max(series.months) < dt.date(2009, 12, 1)


class TestTheCommandRunsOnARealDatabase:
    """**本物のコマンドを、中身の入った DB で1本通す。**

    空の DB で叩いて手前で終わるのを「疎通した」と読まない（2026-09-19、
    `equal_weighted_daily` が**書いたつもりで書いていなかった**件）。
    """

    @staticmethod
    def _populate(where, symbols: list[str]) -> None:
        import pathlib

        from stock_ai.data.delisted import SecurityProfile, write_snapshot
        from stock_ai.data.valuation_monthly import DEFAULT_PATH

        database = Database(f"sqlite:///{where / 'stock_ai.db'}")
        database.create_all()
        with database.session() as session:
            repo = PriceRepository(session)
            repo.upsert_prices("1306", _frame(seed=999), market="JP")
            for index, symbol in enumerate(symbols):
                repo.upsert_prices(symbol, _frame(seed=index), market="JP")

        write_snapshot(
            pathlib.Path(where) / "rosters",
            dt.date(2008, 12, 1),
            [SecurityProfile(symbol=symbol, market="JP") for symbol in symbols],
        )
        frame = _valuation(symbols)
        target = pathlib.Path(where) / DEFAULT_PATH.name
        frame.to_csv(target, index=False, compression="gzip")

    def test_it_runs_all_the_way_through(self, tmp_path, monkeypatch) -> None:
        import pathlib

        from stock_ai import cli
        from stock_ai.data.valuation_monthly import DEFAULT_PATH
        from stock_ai.database import engine

        monkeypatch.setattr(engine, "DATA_DIR", tmp_path)
        # **`min_symbols` に届く数を置く。** コマンドはそこに口を開けていない
        # （事前登録が固定している）ので、足りなければ手前で終わる——それを
        # 「通した」と読まないための数である。
        symbols = [f"{1300 + index:04d}" for index in range(120)]
        self._populate(tmp_path, symbols)

        cli.january_power(
            rosters=str(pathlib.Path(tmp_path) / "rosters"),
            valuation=str(pathlib.Path(tmp_path) / DEFAULT_PATH.name),
            is_end="2011-12-31",
            oos_periods=9,
        )

    def test_it_refuses_an_empty_valuation_file_by_name(self, tmp_path, monkeypatch) -> None:
        """**手前で止まったことを「通した」と読まない。** ここは止まる側。"""
        import pathlib

        import typer

        from stock_ai import cli
        from stock_ai.database import engine

        monkeypatch.setattr(engine, "DATA_DIR", tmp_path)
        symbols = [f"{1300 + index:04d}" for index in range(60)]
        self._populate(tmp_path, symbols)
        empty = pathlib.Path(tmp_path) / "empty.csv.gz"
        pd.DataFrame(columns=["date", "symbol", "market_cap"]).to_csv(
            empty, index=False, compression="gzip"
        )

        with pytest.raises(typer.Exit):
            cli.january_power(
                rosters=str(pathlib.Path(tmp_path) / "rosters"),
                valuation=str(empty),
                is_end="2011-12-31",
                oos_periods=9,
            )


class TestTheGateUsesTheLineForNineObservations:
    """**n=9 では `t` が正規から離れる。** SD 方式では 24% 甘い。"""

    def test_the_command_asks_for_the_student_line(self) -> None:
        import inspect

        from stock_ai import cli

        body = inspect.getsource(cli.january_power)

        assert "student_t_line(oos_periods - 1, HYPOTHESIS_BUDGET)" in body
        assert "1.96" in body, "95% の幅を t で取っている説明が消えている"

    def test_the_band_is_not_taken_from_the_normal(self) -> None:
        """**`mean ± 1.96 × se` を書き戻さない。** n=9 では 2.31 である。"""
        import inspect

        from stock_ai import cli

        body = inspect.getsource(cli.january_power)

        assert "mean - 1.96 * stderr" not in body
        assert "width = student_t_line(len(annual.episodes) - 1, budget=1)" in body

    def test_the_floor_is_the_committed_one(self) -> None:
        from stock_ai.cli import JANUARY_FLOOR

        assert pytest.approx(0.004) == JANUARY_FLOOR

    def test_the_floor_is_the_cost_of_one_round_trip(self) -> None:
        """**線がどこから来たかを、式で確かめる。** 年1往復ぶんである。"""
        from stock_ai.backtest.quantile_series import QuantileSeries
        from stock_ai.cli import JANUARY_FLOOR

        assert pytest.approx(QuantileSeries.round_trip_cost * 1.0) == JANUARY_FLOOR
