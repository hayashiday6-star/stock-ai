"""壁の下見（`stock_ai.backtest.wall`）。

**事前登録を書く前に、検出できる差だけを出す道具である。**

ここで押さえるのは4つ。

1. **平均を持てる形にしない。** `Wall` に平均の欄が在ると、「効果がありそう
   だから通す」が書ける。`PowerEstimate` と同じ設計にする
2. **壁の式を書き写さない。** `線 × SD ÷ √n` が動けば、表の数字も動く
3. **冬と夏を取り違えない。** 11月・12月は**翌年の冬**である
4. **走査が、実際に事象を拾えること。** 拾えないまま「0件」と出ても、
   コードは落ちない
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from stock_ai.backtest.wall import (
    GAP_DOWN,
    HIGH_WINDOW,
    IS_END,
    KNIFE_DAYS,
    KNIFE_DROP,
    OOS_END,
    OOS_FROM,
    Missing,
    Wall,
    complete_halloween_years,
    halloween_episodes,
    scan,
    usable_rebalances,
)
from stock_ai.data.schema import ADJ_CLOSE, CLOSE, HIGH, LOW, OPEN, VOLUME
from stock_ai.database.engine import Database
from stock_ai.database.repository import PriceRepository


def _wall(**changed) -> Wall:
    base = {
        "candidate": 5,
        "name": "ためし",
        "pipe": "月次",
        "unit": "月",
        "observations": 104,
        "sd": 0.05,
        "inflation": 1.0,
        "line": 3.39,
        "source": "ためし",
        "per_year": 12.0,
    }
    return Wall(**{**base, **changed})


class TestTheWallCannotHoldAnEffect:
    """**平均を持てる形にしない。** `PowerEstimate` と同じ理由である。"""

    def test_no_field_looks_like_a_mean(self) -> None:
        import dataclasses

        names = {field.name for field in dataclasses.fields(Wall)}
        forbidden = {"mean", "effect", "average", "estimate", "t", "t_statistic"}

        assert not (names & forbidden), names

    def test_the_module_never_averages_a_series(self) -> None:
        """**この検査が守っているのは、書く側の手である。**

        `estimate_power` は分散を出すために内部で平均を使うが、**返さない。**
        そこが境界で、ここより上で平均を作らない。
        """
        import inspect

        from stock_ai.backtest import wall

        source = inspect.getsource(wall)

        for call in ("fmean(", "np.mean(", ".mean()", "statistics.mean"):
            assert call not in source, call

    def test_the_command_does_not_average_either(self) -> None:
        import inspect

        from stock_ai import cli

        body = inspect.getsource(cli.wall_survey)

        for call in ("fmean(", "np.mean(", ".mean()"):
            assert call not in body, call

    def test_it_says_why_this_is_not_peeking(self) -> None:
        """**理由を文書に残す。** 半年後に読んで分かること。"""
        import inspect

        from stock_ai.backtest import wall

        source = inspect.getsource(wall)

        assert "散らばりと観測数だけで決まる" in source


class TestTheWallIsComputedNotTranscribed:
    def test_the_formula(self) -> None:
        wall = _wall(observations=104, sd=0.05, line=3.39)

        assert wall.detectable == pytest.approx(3.39 * 0.05 / np.sqrt(104))

    def test_the_overlap_inflation_is_not_dropped(self) -> None:
        """**1度落とした**（2026-09-19）。20日保有なら理屈の上で4倍前後になる。

        **緩む向きである**——壁が数倍低く出る。
        """
        assert _wall(inflation=4.0).detectable == pytest.approx(_wall(inflation=1.0).detectable * 4)

    def test_it_agrees_with_the_canonical_estimate(self) -> None:
        """**同じ式を3つ書けば、1つは間違える。** ここで縛る。"""
        from stock_ai.backtest.power import PowerEstimate

        estimate = PowerEstimate(observations=100, lags=20, variance=0.01, omega=0.16)
        wall = _wall(
            observations=100,
            sd=estimate.daily_sd,
            inflation=estimate.inflation,
            line=3.30,
        )

        assert wall.detectable == pytest.approx(estimate.detectable(100, target_t=3.30))

    def test_it_agrees_with_the_passing_document(self) -> None:
        """`passing.Shape` とも同じ式であること。**3つ目がここだった。**"""
        import dataclasses

        from stock_ai.backtest.passing import SHAPES

        shape = dataclasses.replace(SHAPES[0], inflation=1.4)
        wall = _wall(
            observations=shape.periods,
            sd=shape.sd,
            inflation=shape.inflation,
            line=3.39,
        )

        assert wall.detectable == pytest.approx(shape.required(3.39))

    def test_more_observations_lower_it(self) -> None:
        assert _wall(observations=416).detectable == pytest.approx(
            _wall(observations=104).detectable / 2
        )

    def test_a_higher_line_raises_it(self) -> None:
        assert _wall(line=4.33).detectable > _wall(line=3.02).detectable

    def test_a_monthly_design_annualises(self) -> None:
        wall = _wall(per_year=12.0)

        assert wall.annual == pytest.approx(wall.detectable * 12)

    def test_an_event_design_does_not(self) -> None:
        """**資金をどれだけ張るかを決めないと年率に直せない**（`docs/PASSING.md`）。"""
        assert _wall(per_year=None).annual is None


class TestWinterAndSummer:
    """**11月・12月は翌年の冬である。** 1つずらすと別の量を測る。"""

    @staticmethod
    def _series(values: dict[dt.date, float]) -> tuple[list[float], list[dt.date]]:
        days = sorted(values)
        return [values[day] for day in days], days

    def test_november_belongs_to_the_next_year(self) -> None:
        returns, dates = self._series(
            {
                dt.date(2008, 11, 3): 0.10,
                dt.date(2009, 3, 2): 0.0,
                dt.date(2009, 6, 1): 0.0,
            }
        )

        years, episodes = halloween_episodes(returns, dates)

        assert years == [2009]
        assert episodes[0] == pytest.approx(0.10)

    def test_summer_is_subtracted(self) -> None:
        returns, dates = self._series({dt.date(2009, 2, 2): 0.05, dt.date(2009, 7, 1): 0.03})

        _years, episodes = halloween_episodes(returns, dates)

        assert episodes[0] == pytest.approx(0.02)

    def test_a_year_with_only_one_half_is_dropped(self) -> None:
        """**端の半年だけで1観測を作らない。**"""
        returns, dates = self._series({dt.date(2009, 7, 1): 0.03})

        years, episodes = halloween_episodes(returns, dates)

        assert years == []
        assert episodes == []

    def test_it_stops_at_the_is_boundary(self) -> None:
        """**OOS のリターンを1つも読まない。**"""
        returns, dates = self._series(
            {
                dt.date(2017, 2, 1): 0.01,
                dt.date(2017, 7, 1): 0.01,
                dt.date(2018, 2, 1): 9.99,
                dt.date(2018, 7, 1): 9.99,
            }
        )

        years, _episodes = halloween_episodes(returns, dates, end=IS_END)

        assert years == [2017]

    def test_mismatched_lengths_are_refused(self) -> None:
        with pytest.raises(ValueError, match="長さが違う"):
            halloween_episodes([0.0, 0.0], [dt.date(2009, 1, 1)])

    def test_the_judgement_window_holds_seven_years(self) -> None:
        """**冬は前年11月に始まる。** 期間の頭の1年は作れない。"""
        assert complete_halloween_years(OOS_FROM, OOS_END) == 7

    def test_a_year_needs_both_halves_inside(self) -> None:
        """**この検査が落ちる条件を、実際に1つ作る。**"""
        assert complete_halloween_years(dt.date(2018, 1, 1), dt.date(2018, 12, 31)) == 0
        assert complete_halloween_years(dt.date(2017, 11, 1), dt.date(2018, 10, 31)) == 1


_INDEX = pd.bdate_range("2016-07-01", "2018-12-31", name="date")
_BARS = len(_INDEX)


def _prices(
    seed: int,
    gaps: tuple[int, ...] = (),
    crashes: tuple[int, ...] = (),
    index: pd.DatetimeIndex | None = None,
    merger: int | None = None,
) -> pd.DataFrame:
    """乱数歩行に、下窓と急落を**決め打ちの位置**で仕込む。

    **定数の足を置かない**（`CLAUDE.md`）——散らばりが 0 だと標準誤差も 0 に
    なり、落ちた理由を探す時間が要る。
    """
    index = _INDEX if index is None else index
    bars = len(index)
    rng = np.random.default_rng(seed)
    steps = rng.normal(0.0, 0.01, bars)
    for start in crashes:
        # **定数から出す。** 幅を書き写すと、定数を動かしたとき仕込みだけ古くなる。
        steps[start : start + KNIFE_DAYS] = np.log(1.0 - KNIFE_DROP) / KNIFE_DAYS * 1.2
    close = 1_000.0 * np.exp(np.cumsum(steps))
    if merger is not None:
        # **調整漏れの併合。** #6 の 8308 と同じ形——1営業日で桁が変わる。
        # **これは値動きではない**（`discontinuity`）。
        close[merger:] *= 0.001
    opens = close.copy()
    for position in gaps:
        # **前日終値から当日始値が落ちる形。** 終値そのものは動かさない。
        opens[position] = close[position - 1] * (1.0 - GAP_DOWN - 0.02)
    return pd.DataFrame(
        {
            OPEN: opens,
            HIGH: np.maximum(close, opens),
            LOW: np.minimum(close, opens),
            CLOSE: close,
            ADJ_CLOSE: close,
            VOLUME: [500_000.0] * bars,
        },
        index=index,
    )


def _is_position() -> int:
    """IS に入る足の位置。**52週ぶん過ぎたところ。**"""
    return next(
        offset
        for offset, stamp in enumerate(_INDEX)
        if offset > HIGH_WINDOW and stamp.date() <= IS_END
    )


def _oos_position() -> int:
    return next(offset for offset, stamp in enumerate(_INDEX) if stamp.date() >= OOS_FROM) + 5


def _database(count: int = 40, thin: bool = False) -> tuple[Database, list[str]]:
    database = Database("sqlite:///:memory:")
    database.create_all()
    symbols = [f"{1400 + index:04d}" for index in range(count)]
    with database.session() as session:
        repo = PriceRepository(session)
        repo.upsert_prices("1306", _prices(seed=999), market="JP")
        for index, symbol in enumerate(symbols):
            frame = _prices(
                seed=index,
                gaps=(_is_position(), _oos_position()),
                crashes=(_is_position() + 30, _oos_position() + 30),
            )
            if thin:
                frame[VOLUME] = 1.0
            repo.upsert_prices(symbol, frame, market="JP")
    return database, symbols


class TestTheScanFindsWhatItSaysItFinds:
    def test_it_picks_up_the_gaps(self) -> None:
        database, symbols = _database()

        found = scan(database, symbols=symbols)

        assert found.gaps_is, "IS の下窓を1件も拾えていない"
        assert found.gaps_oos_days > 0, "OOS の下窓の日数が 0"

    def test_it_picks_up_the_crashes(self) -> None:
        database, symbols = _database()

        found = scan(database, symbols=symbols)

        assert found.knives_is, "IS の急落を1件も拾えていない"
        assert found.knives_oos_days > 0

    def test_the_liquidity_filter_removes_them(self) -> None:
        """**この検査が落ちる条件を、実際に1つ作る。**

        絞りを外すと拾える事象が、絞りを当てると消えること。
        """
        database, symbols = _database(thin=True)

        found = scan(database, symbols=symbols)

        assert not found.gaps_is
        assert not found.knives_is

    def test_the_closeness_is_a_ratio_at_or_below_one(self) -> None:
        """**終値 ÷ 52週高値**である。1 を超える値は作りようがない。"""
        database, symbols = _database()

        found = scan(database, symbols=symbols)

        assert found.high52
        for _key, (_when, value) in found.high52.items():
            assert 0.0 < value <= 1.0 + 1e-9

    def test_it_takes_the_last_bar_of_each_month(self) -> None:
        """**暦の月末を探さない。** その銘柄の、その月の最後の観測である。"""
        database, symbols = _database(count=1)

        found = scan(database, symbols=symbols)
        month = pd.Period("2017-06", freq="M")
        when, _value = found.high52[(symbols[0], month)]

        expected = max(stamp.date() for stamp in _INDEX if stamp.to_period("M") == month)
        assert when == expected

    def test_a_short_history_is_counted_not_dropped_silently(self) -> None:
        database = Database("sqlite:///:memory:")
        database.create_all()
        with database.session() as session:
            repo = PriceRepository(session)
            short = _prices(seed=1).iloc[: HIGH_WINDOW - 10]
            repo.upsert_prices("1401", short, market="JP")

        found = scan(database, symbols=["1401"])

        assert found.skipped_short == 1
        assert not found.high52

    def test_an_empty_universe_is_refused(self) -> None:
        database = Database("sqlite:///:memory:")
        database.create_all()

        with pytest.raises(ValueError, match="銘柄が1つも無い"):
            scan(database, symbols=[])


class TestADiscontinuityIsNotAnEvent:
    """**5営業日で −20% は、調整漏れの分割がそう見える形である。**

    #6 は不連続を外すだけで SD が 24.42% → 3.64% になった。ここで外さないと、
    **壁の高さが桁で変わる**——実データで 273%／イベント日 が出た
    （2026-09-19）。
    """

    @staticmethod
    def _database(merger: int | None) -> tuple[Database, list[str]]:
        database = Database("sqlite:///:memory:")
        database.create_all()
        symbols = [f"{1400 + index:04d}" for index in range(5)]
        with database.session() as session:
            repo = PriceRepository(session)
            for index, symbol in enumerate(symbols):
                repo.upsert_prices(symbol, _prices(seed=index, merger=merger), market="JP")
        return database, symbols

    def test_a_merger_does_not_become_a_falling_knife(self) -> None:
        where = _is_position()
        database, symbols = self._database(merger=where)

        found = scan(database, symbols=symbols)

        broke = _INDEX[where].date()
        assert broke not in [when for _symbol, when in found.knives_is]
        assert found.dropped_broken > 0

    def test_without_the_merger_that_day_is_an_event(self) -> None:
        """**この検査が落ちる条件を、実際に1つ作る。**

        同じ位置に、不連続ではない急落を置く。**外れるのは不連続のほうだけ。**
        """
        where = _is_position()
        database = Database("sqlite:///:memory:")
        database.create_all()
        symbols = [f"{1400 + index:04d}" for index in range(5)]
        with database.session() as session:
            repo = PriceRepository(session)
            for index, symbol in enumerate(symbols):
                repo.upsert_prices(
                    symbol,
                    _prices(seed=index, crashes=(where - KNIFE_DAYS,)),
                    market="JP",
                )

        found = scan(database, symbols=symbols)

        assert found.knives_is
        assert found.dropped_broken == 0


class TestCountingTheJudgementWindow:
    """**月末の数ではない。** 最後の月末は、降りる先が無いので使えない。"""

    def test_three_months_give_one_rebalance(self) -> None:
        """1月末に入り、2月末の翌営業日に降りる。**それで1回。**"""
        calendar = pd.bdate_range("2018-01-01", "2018-03-31", name="date")

        assert usable_rebalances(calendar, dt.date(2018, 1, 1), dt.date(2018, 3, 31)) == 1

    def test_a_full_year_gives_ten(self) -> None:
        """**12 ではない。** 12月末は暦の終わりで組み替え日にならず、11月末は
        降りる先（12月末の翌営業日）が期間の外に出る。
        """
        calendar = pd.bdate_range("2018-01-01", "2018-12-31", name="date")

        assert usable_rebalances(calendar, dt.date(2018, 1, 1), dt.date(2018, 12, 31)) == 10

    def test_a_shorter_window_gives_fewer(self) -> None:
        calendar = pd.bdate_range("2018-01-01", "2018-12-31", name="date")

        assert usable_rebalances(calendar, dt.date(2018, 7, 1), dt.date(2018, 12, 31)) < 10

    def test_a_window_with_no_room_gives_zero_rather_than_an_exception(self) -> None:
        """**1回も作れないのは例外ではない。** 期間が短ければ当たり前に起きる。"""
        calendar = pd.bdate_range("2018-01-01", "2018-12-31", name="date")

        assert usable_rebalances(calendar, dt.date(2018, 1, 1), dt.date(2018, 1, 20)) == 0


class TestTheMissingOnesAreOnTheTable:
    """**無いことは、出力に出ない。** だから測れなかったほうも表にする。"""

    def test_a_missing_row_carries_its_reason(self) -> None:
        item = Missing(6, "悲観の中に生まれ", "心理指標を持っていない")

        assert item.reason

    def test_the_command_lists_them(self) -> None:
        import inspect

        from stock_ai import cli

        body = inspect.getsource(cli.wall_survey)

        # **書き方に賭けない。** 折り返しで `Missing(7` は分かれる（`--help` の
        # 幅と同じ形）。**数えるほうで見る。**
        assert body.count("Missing(") == 3


#: 1本通すときの暦。**Sell in May の枝に届く長さが要る。**
#:
#: 短い暦で叩いて「3年に満たない」で終わったのを、疎通の確認と読まない
#: （`CLAUDE.md`「到達しない疎通確認を『疎通した』と読まない」）。
_LONG = pd.bdate_range("2010-01-04", "2018-12-31", name="date")


class TestTheCommandRunsOnARealDatabase:
    """**本物のコマンドを、中身の入った DB で1本通す。**

    **枝ごとに届くこと。** Sell in May・52週高値・イベント型2種が、どれも
    表に出るところまで行くかを見る。
    """

    @staticmethod
    def _populate(where) -> list[str]:
        import pathlib as _pathlib

        from stock_ai.data.delisted import SecurityProfile, write_snapshot

        # **`min_symbols`（100）に届く数を置く。** コマンドはそこに口を開けて
        # いないので、足りなければ手前で終わる。
        symbols = [f"{1400 + index:04d}" for index in range(110)]
        gaps = (400, 2_000)
        crashes = (500, 2_100)
        database = Database(f"sqlite:///{where / 'stock_ai.db'}")
        database.create_all()
        with database.session() as session:
            repo = PriceRepository(session)
            repo.upsert_prices("1306", _prices(seed=999, index=_LONG), market="JP")
            for index, symbol in enumerate(symbols):
                repo.upsert_prices(
                    symbol,
                    _prices(seed=index, gaps=gaps, crashes=crashes, index=_LONG),
                    market="JP",
                )
        write_snapshot(
            _pathlib.Path(where) / "rosters",
            dt.date(2010, 1, 4),
            [SecurityProfile(symbol=symbol, market="JP") for symbol in symbols],
        )
        return symbols

    def test_it_runs_all_the_way_through_and_writes_the_document(
        self, tmp_path, monkeypatch, capsys
    ) -> None:
        import pathlib as _pathlib

        from stock_ai import cli
        from stock_ai.database import engine

        monkeypatch.setattr(engine, "DATA_DIR", tmp_path)
        self._populate(tmp_path)
        target = _pathlib.Path(tmp_path) / "WALL.md"

        cli.wall_survey(
            into=str(target),
            benchmark="1306",
            rosters=str(_pathlib.Path(tmp_path) / "rosters"),
        )

        body = target.read_text(encoding="utf-8")
        assert "生成物である" in body
        # **4つの設計すべてが表に出ること。** 1つでも手前で終わっていたら、
        # その枝は通っていない。
        for candidate in ("Sell in May", "52週高値", "窓は埋まる", "落ちるナイフ"):
            assert candidate in body, body
        # **測れなかった候補も出る。** 無いことは、出力に出ない。
        for candidate in ("悲観の中に生まれ", "需給", "噂"):
            assert candidate in body

    def test_the_document_carries_no_effect(self, tmp_path, monkeypatch) -> None:
        """**平均が1つも載らないこと。** 載ったら、それは答えを見たことになる。"""
        import pathlib as _pathlib
        import re

        from stock_ai import cli
        from stock_ai.database import engine

        monkeypatch.setattr(engine, "DATA_DIR", tmp_path)
        self._populate(tmp_path)
        target = _pathlib.Path(tmp_path) / "WALL.md"

        cli.wall_survey(
            into=str(target),
            benchmark="1306",
            rosters=str(_pathlib.Path(tmp_path) / "rosters"),
        )

        body = target.read_text(encoding="utf-8")

        assert "効果（平均）は1つも出していない" in body
        # **符号付きの数字が出ていないこと。** 壁も SD も正の量である。
        assert not re.search(r"[+−-]\d+\.\d+%", body), body

    def test_an_empty_roster_stops_it(self, tmp_path, monkeypatch) -> None:
        """**手前で止まったことを「通した」と読まない。** ここは止まる側。"""
        import pathlib as _pathlib

        import typer

        from stock_ai import cli
        from stock_ai.database import engine

        monkeypatch.setattr(engine, "DATA_DIR", tmp_path)

        with pytest.raises(typer.Exit):
            cli.wall_survey(
                into=None,
                benchmark="1306",
                rosters=str(_pathlib.Path(tmp_path) / "missing"),
            )


class TestTheTailSensitivityIsShown:
    """**壁の高さが数日で決まっていないか。**

    実データで 273%／イベント日 が出た（2026-09-19）。不連続を外したうえで
    なお裾が効いているなら、**その壁は当てにならない。**
    """

    def test_the_command_measures_it(self) -> None:
        import inspect

        from stock_ai import cli

        body = inspect.getsource(cli.wall_survey)

        assert "trimmed_variance(" in body

    def test_the_threshold_says_where_it_came_from(self) -> None:
        """**根拠の無い数字を、根拠があるように書かない。**"""
        import inspect

        from stock_ai import cli

        source = inspect.getsource(cli)

        assert "2 に根拠は無い" in source

    def test_a_tail_driven_series_is_caught(self) -> None:
        """**この検査が落ちる条件を、実際に1つ作る。**"""
        from stock_ai.backtest.power import estimate_power, trimmed_variance
        from stock_ai.cli import TAIL_DRIVEN

        quiet = [0.01, -0.01] * 100
        spiky = [*quiet, 50.0, -50.0]

        plain = estimate_power(spiky, lags=0).daily_sd
        trimmed = trimmed_variance(spiky, fraction=0.01)[0] ** 0.5

        assert plain / trimmed > TAIL_DRIVEN
        assert (
            estimate_power(quiet, lags=0).daily_sd
            / trimmed_variance(quiet, fraction=0.01)[0] ** 0.5
        ) < TAIL_DRIVEN
