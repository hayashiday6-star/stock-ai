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
import inspect
import textwrap

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
        item = Missing(8, "噂で買って事実で売る", "初出時点を取る口が無い")

        assert item.reason

    def test_the_command_lists_them(self) -> None:
        import inspect

        from stock_ai import cli

        body = inspect.getsource(cli.wall_survey)

        # **書き方に賭けない。** 折り返しで `Missing(7` は分かれる（`--help` の
        # 幅と同じ形）。**数えるほうで見る。**
        assert body.count("Missing(") == 1

    def test_the_two_that_turned_out_measurable_are_gone(self) -> None:
        """**「測れない」と書いたことも、出力に出ない。**

        候補6（心理指標）と候補7（需給）は「材料が無い」と書いてあったが、
        **どちらも原本に在った**（2026-09-21）。オプションの予想変動率と
        投資部門別である。**書いた側が確かめるまで、在る材料は見えない
        ままになる。**
        """
        import inspect

        from stock_ai import cli

        body = inspect.getsource(cli.wall_survey)

        assert "心理指標" not in body, "**まだ「材料が無い」と書いている。**"
        assert "設計が決まっていない" not in body


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
    def _archive(where):
        """**候補A・B の原本を、この暦に合わせて置く。**

        fixture の実物は 2026-01 と 2008-01 で、**この盤面（2010〜2018）に
        1日も掛からない。** 掛からないまま「壁を出せない」で終わったのを
        疎通の確認と読まない（`CLAUDE.md`「到達しない疎通確認を『疎通した』
        と読まない」）。**列名は実物から採る。**
        """
        import csv
        import gzip
        import io as _io
        import pathlib as _pathlib

        from stock_ai.data.jquants_archive import MANIFEST, MANIFEST_COLUMNS

        root = _pathlib.Path(where) / "archive"
        rng = np.random.default_rng(7)

        # --- オプション（1日2行。跳ねる日を作る）--------------------------
        names = (
            _pathlib.Path("tests/fixtures/jquants_options_225_sample.csv")
            .read_text(encoding="utf-8-sig")
            .splitlines()[0]
            .split(",")
        )
        out = _io.StringIO()
        writer = csv.DictWriter(out, fieldnames=names, lineterminator="\n")
        writer.writeheader()
        level = 20.0
        for position, stamp in enumerate(_LONG):
            # **10日に1度、跳ねさせる。** 事象が0件だと枝に届かない。
            level = (
                level * 1.30 if position % 10 == 0 else level * float(np.exp(rng.normal(0.0, 0.01)))
            )
            sq = (stamp + pd.Timedelta(days=30)).date()
            for side in ("1", "2"):
                writer.writerow(
                    {
                        **dict.fromkeys(names, ""),
                        "Date": stamp.date().isoformat(),
                        "Code": f"1310100{side}8",
                        "PCDiv": side,
                        "Strike": "20000.0",
                        "UnderPx": "20000.0",
                        "SQD": sq.isoformat(),
                        "IV": f"{level:.4f}",
                        "BaseVol": f"{level:.4f}",
                    }
                )
        key = "derivatives/bars/daily/options/225/options_225_test.csv.gz"
        target = root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(gzip.compress(out.getvalue().encode("utf-8")))

        # --- 投資部門別（週に1行）------------------------------------------
        flow_names = (
            _pathlib.Path("tests/fixtures/jquants_investor_types_sample.csv")
            .read_text(encoding="utf-8-sig")
            .splitlines()[0]
            .split(",")
        )
        out = _io.StringIO()
        writer = csv.DictWriter(out, fieldnames=flow_names, lineterminator="\n")
        writer.writeheader()
        for stamp in pd.date_range(_LONG[0], _LONG[-1], freq="W-FRI"):
            end = stamp.date()
            writer.writerow(
                {
                    **dict.fromkeys(flow_names, ""),
                    # **公表は週の終わりより後。** そこで入る。
                    "PubDate": (stamp + pd.Timedelta(days=5)).date().isoformat(),
                    "StDate": (stamp - pd.Timedelta(days=4)).date().isoformat(),
                    "EnDate": end.isoformat(),
                    "Section": "TokyoNagoya",
                    "TotTot": "1000000.0",
                    "FrgnBal": f"{rng.normal(0.0, 10_000.0):.1f}",
                    "IndBal": f"{rng.normal(0.0, 10_000.0):.1f}",
                }
            )
        flow_key = "equities/investor-types/investor_types_test.csv.gz"
        target = root / flow_key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(gzip.compress(out.getvalue().encode("utf-8")))

        (root / MANIFEST).write_text(
            ",".join(MANIFEST_COLUMNS)
            + "\n"
            + f"/{key},1,1,x,,2026-09-21\n"
            + f"/{flow_key},1,1,x,,2026-09-21\n",
            encoding="utf-8",
        )
        return root

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
            archive=str(self._archive(tmp_path)),
        )

        body = target.read_text(encoding="utf-8")
        assert "生成物である" in body
        # **6つの設計すべてが表に出ること。** 1つでも手前で終わっていたら、
        # その枝は通っていない。
        for candidate in (
            "Sell in May",
            "52週高値",
            "窓は埋まる",
            "落ちるナイフ",
            "恐怖指数の跳ね上がり",
            "需給はすべての材料に優先する",
        ):
            assert candidate in body, body
        # **測れなかった候補も出る。** 無いことは、出力に出ない。
        assert "噂" in body

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
            archive=str(self._archive(tmp_path)),
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
                archive=str(_pathlib.Path(tmp_path) / "archive"),
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


class TestTheIndexOnlyFoldsAreFixedInAdvance:
    """**候補A・B の畳み方は、壁を測る前に1つに決めてある。**

    複数試して良いほうを採ると、その時点で #10 と同じところに落ちる。
    ここが確かめるのは「決めたとおりに畳んでいるか」だけで、
    **どちらが良い設計かは見ない。**
    """

    def test_a_spike_is_measured_against_the_previous_observation(self) -> None:
        """**暦の前日ではなく、原本に在る前の日と比べる。**"""
        from stock_ai.backtest.wall import volatility_spikes

        levels = {
            dt.date(2015, 3, 2): 20.0,
            # 休みを挟んでも「前の観測」である
            dt.date(2015, 3, 6): 25.0,
            dt.date(2015, 3, 9): 26.0,
        }

        assert volatility_spikes(levels, rise=0.20) == [dt.date(2015, 3, 6)]

    def test_exactly_the_threshold_counts(self) -> None:
        """**割り算をしない。** `#16` はちょうど −20% を取りこぼしていた。"""
        from stock_ai.backtest.wall import volatility_spikes

        levels = {dt.date(2015, 3, 2): 20.0, dt.date(2015, 3, 3): 24.0}

        assert volatility_spikes(levels, rise=0.20) == [dt.date(2015, 3, 3)]

    def test_a_smaller_rise_is_not_a_spike(self) -> None:
        """**この検査が落ちる条件を、実際に1つ作る。**"""
        from stock_ai.backtest.wall import volatility_spikes

        levels = {dt.date(2015, 3, 2): 20.0, dt.date(2015, 3, 3): 23.0}

        assert volatility_spikes(levels, rise=0.20) == []

    def test_the_window_starts_the_next_trading_day(self) -> None:
        """**イベント日そのものには入らない。** 翌営業日の始まりからである。"""
        from stock_ai.backtest.wall import forward_windows

        dates = [dt.date(2015, 3, day) for day in (2, 3, 4, 5, 6)]
        returns = [0.01, 0.02, 0.04, 0.08, 0.16]

        used, values = forward_windows(returns, dates, [dt.date(2015, 3, 3)], 2)

        assert used == [dt.date(2015, 3, 4)]
        assert values == pytest.approx([0.04 + 0.08])

    def test_a_window_that_runs_off_the_end_is_dropped(self) -> None:
        """**窓が最後まで在るものだけ。** 途中で切れた窓を混ぜない。"""
        from stock_ai.backtest.wall import forward_windows

        dates = [dt.date(2015, 3, day) for day in (2, 3, 4)]
        returns = [0.01, 0.02, 0.04]

        _used, values = forward_windows(returns, dates, [dt.date(2015, 3, 3)], 5)

        assert values == []

    def test_the_same_day_is_not_entered_twice(self) -> None:
        """**独立な観測を、件数で数えない。** 同じ日に2回入らない。"""
        from stock_ai.backtest.wall import forward_windows

        dates = [dt.date(2015, 3, day) for day in (2, 3, 4, 5)]
        returns = [0.01, 0.02, 0.04, 0.08]
        # 2つのイベントが同じ翌営業日を指す（3/2 の翌 = 3/3、3/3 の翌 = 3/4）
        used, _values = forward_windows(returns, dates, [dt.date(2015, 3, 1)] * 3, 1)

        assert used == [dt.date(2015, 3, 2)]

    def test_mismatched_lengths_are_refused(self) -> None:
        from stock_ai.backtest.wall import forward_windows

        with pytest.raises(ValueError, match="長さが違う"):
            forward_windows([0.1, 0.2], [dt.date(2015, 3, 2)], [], 1)

    def test_the_flow_enters_on_the_publication_not_the_week(self) -> None:
        """**週末で入ると先読みになる。** 公表は10日ほど後である。"""
        from stock_ai.backtest.wall import flow_entries

        weeks = [(dt.date(2015, 3, 12), 0.01), (dt.date(2015, 3, 19), -0.01)]

        assert flow_entries(weeks) == [dt.date(2015, 3, 12)]

    def test_the_other_side_can_be_counted_too(self) -> None:
        """**両向きに置く。** 片側しか数えない形でも緑にならないように。"""
        from stock_ai.backtest.wall import flow_entries

        weeks = [(dt.date(2015, 3, 12), 0.01), (dt.date(2015, 3, 19), -0.01)]

        assert flow_entries(weeks, positive=False) == [dt.date(2015, 3, 19)]

    def test_the_thresholds_are_the_ones_that_were_committed(self) -> None:
        """**定数を書き写さない。** 決めた値がそのまま出ていること。"""
        from stock_ai.backtest.wall import FLOW_HOLDING, IV_SPIKE

        assert IV_SPIKE == pytest.approx(0.20)  # noqa: SIM300 - 決めた値が左
        assert FLOW_HOLDING == 5  # noqa: PLR2004 - 週に1回なので1週

    def test_the_fold_is_written_down_before_it_is_measured(self) -> None:
        """**書いてから測る。** 半年後に「何を選んだか」が読めること。"""
        import inspect

        from stock_ai.backtest import wall

        source = inspect.getsource(wall)

        assert "候補A・B の畳み方は、壁を測る前に1つに決めてある" in source
        assert "出典は無い" in source


class TestTheThinSideIsNamed:
    """**IS が薄いことと、OOS が薄いことは別である。**

    `wall-survey` のコメントは「**IS が薄ければ**壁の高さそのものが当てに
    ならない」と書いていたのに、**検査は OOS の `observations` を見て
    いた**（2026-09-21 に気付いた）。**コメントが主張していることと、
    コードが守っていることが別だった。**

    候補6（予想変動率）は `IV` が 2016-07-19 からしか無いので、IS が
    1年半しかない。**そこに当たる。**
    """

    @staticmethod
    def _printed(walls) -> str:
        import io as _io
        from unittest import mock

        from rich.console import Console

        from stock_ai import cli

        console = Console(file=_io.StringIO(), width=120, no_color=True)
        source = inspect.getsource(cli.wall_survey)
        start = source.index("for wall in walls:\n        if wall.observations")
        end = source.index("\n\n", start)
        with mock.patch.object(cli, "console", console):
            exec(  # noqa: S102 - 本物の枝をそのまま動かす。写すと2つ目になる
                textwrap.dedent(source[start:end]),
                {"walls": walls, "console": console, "THIN_OBSERVATIONS": cli.THIN_OBSERVATIONS},
            )
        return console.file.getvalue()

    def test_a_thin_is_is_named_even_when_the_oos_is_thick(self) -> None:
        """**片方だけ見る形だと、ここが黙る。**"""
        printed = self._printed([_wall(observations=2_000, sample=6)])

        assert "IS の観測が 6" in printed
        assert "判定に使える観測が" not in printed

    def test_a_thin_oos_is_named_even_when_the_is_is_thick(self) -> None:
        """**両向きに置く。** 逆に倒しても鳴ること。"""
        printed = self._printed([_wall(observations=4, sample=2_000)])

        assert "判定に使える観測が 4" in printed
        assert "IS の観測が" not in printed

    def test_neither_is_named_when_both_are_thick(self) -> None:
        """**常に点く旗は、何も区別しない。**"""
        assert self._printed([_wall(observations=2_000, sample=2_000)]) == ""

    def test_the_two_reasons_are_said_apart(self) -> None:
        """**SD が薄いのか n が薄いのかで、言うことが違う。**"""
        printed = self._printed([_wall(observations=4, sample=6)])

        assert "壁の高さ（n）" in printed
        assert "壁の高さ（SD）" in printed
