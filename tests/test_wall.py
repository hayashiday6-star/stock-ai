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
        "period_years": 104 / 12,
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
        wall = _wall(period_years=104 / 12)

        assert wall.annual == pytest.approx(wall.detectable * 12)

    def test_an_event_design_does_not(self) -> None:
        """**資金をどれだけ張るかを決めないと年率に直せない**（`docs/PASSING.md`）。"""
        assert _wall(period_years=None).annual is None


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
        """**測れなかった候補が、1つも出ない形にしない。**

        **件数は焼き付けない。** ここは `== 1` と書いてあった
        （2026-09-21 まで）。候補を足すたびに落ちる——`CLAUDE.md`
        「測った件数を、文面に焼き付けない」のテスト版である。
        """
        import inspect

        from stock_ai import cli

        body = inspect.getsource(cli.wall_survey)

        # **書き方に賭けない。** 折り返しで `Missing(7` は分かれる（`--help` の
        # 幅と同じ形）。**数えるほうで見る。**
        assert body.count("Missing(") >= 1

    def test_every_missing_row_says_why(self) -> None:
        """**理由の無い行を作れないこと。** 「測れない」だけでは一手にならない。"""
        import ast
        import inspect

        from stock_ai import cli

        silent: list[int] = []
        for node in ast.walk(ast.parse(inspect.getsource(cli.wall_survey))):
            if not isinstance(node, ast.Call) or getattr(node.func, "id", None) != "Missing":
                continue
            reason = node.args[2] if len(node.args) > 2 else None  # noqa: PLR2004 - 3つ目
            text = None
            if isinstance(reason, ast.Constant):
                text = reason.value
            elif isinstance(reason, ast.JoinedStr):
                text = "".join(
                    part.value for part in reason.values if isinstance(part, ast.Constant)
                )
            if not isinstance(text, str) or not text.strip():
                silent.append(node.lineno)

        assert not silent, f"**理由の無い `Missing` がある: {silent}**"

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
#:
#: **2019 年まで伸ばしてある。** 候補6 は IS/OOS がこの候補だけ別で、
#: OOS が 2019-07-19 から始まる（`wall.IV_OOS_FROM`）——2018 で切ると
#: **その枝に1日も届かない。**
_LONG = pd.bdate_range("2010-01-04", "2019-12-31", name="date")


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


class TestTheInflationTheWallUsedIsTheOneItShows:
    """**表に出す膨張と、壁を作った膨張が違うと、行が自分と食い違う。**

    候補6が **0.82x** を出した（2026-09-21）。床（`INFLATION_FLOOR`）で
    壁は 1.00x で作られるのに、**表には 0.82x と出ていた。**
    """

    def test_a_below_one_inflation_is_floored_for_the_wall(self) -> None:
        from stock_ai.backtest.power import detectable_difference

        wall = _wall(observations=76, sd=0.0528, inflation=0.82, line=3.17)

        assert wall.effective_inflation == pytest.approx(1.0)
        assert wall.floored
        assert wall.detectable == pytest.approx(detectable_difference(0.0528, 1.0, 76, 3.17))

    def test_a_normal_inflation_is_left_alone(self) -> None:
        """**両向きに置く。** 床がすべてを潰す形でも緑にならないように。"""
        wall = _wall(observations=2_110, sd=0.1028, inflation=1.82)

        assert wall.effective_inflation == pytest.approx(1.82)
        assert not wall.floored

    def test_the_measured_value_is_still_there(self) -> None:
        """**測った値は消さない。** 床を当てたことと、測ったことは別である。"""
        assert _wall(inflation=0.82).inflation == pytest.approx(0.82)

    def test_the_document_shows_both_when_the_floor_bit(self) -> None:
        """**行が自分と食い違わないこと。**"""
        from stock_ai import cli

        body = cli._wall_document([_wall(inflation=0.82, candidate=6)], [], "ためし")

        # **どの規則が効いたかも欄に出る**（2026-09-21 に足した）。
        assert "0.82x → 床 1.00x" in body

    def test_the_document_shows_one_when_it_did_not(self) -> None:
        from stock_ai import cli

        body = cli._wall_document([_wall(inflation=1.82, candidate=9)], [], "ためし")

        assert "1.82x" in body
        assert "→" not in body.split("1.82x")[1].split("|")[0]


class TestTheSpikeLineIsChosenByObservationsAlone:
    """**梯子から1つに決まる。** 効果は1つも見ない。

    **順に試して良いほうを採るのではない。** 上から見て**最初に条件を
    満たしたもの**を採るので、答えは1つに決まる——`CLAUDE.md`「#10 が
    封印できなくなったのは効果を見て設計を選べる形だったから」。
    """

    @staticmethod
    def _series(rises: dict[int, float], length: int = 400):
        """``rises`` の位置で跳ねる水準と、それに合う日次リターン。"""
        days = [stamp.date() for stamp in pd.bdate_range("2016-07-19", periods=length)]
        rng = np.random.default_rng(3)
        level = 20.0
        levels: dict[dt.date, float] = {}
        for position, when in enumerate(days):
            level = level * (1.0 + rises.get(position, 0.0))
            levels[when] = level
        return levels, list(rng.normal(0.0, 0.01, length)), days

    def test_the_strictest_line_that_clears_is_taken(self) -> None:
        """**満たす中でいちばん厳しいもの。** 満たしたらそこで止まる。"""
        from stock_ai.backtest.wall import choose_spike

        # +20% が十分な窓を作る盤面。**そこで止まること。**
        levels, returns, days = self._series(dict.fromkeys(range(0, 300, 2), 0.25))

        choice = choose_spike(levels, returns, days, holding=1, end=days[-1])

        assert choice.rise == pytest.approx(0.20)
        assert choice.cleared
        assert len(choice.tried) == 1, "**満たしたのに梯子を下り続けている。**"

    def test_it_goes_down_the_ladder_until_it_clears(self) -> None:
        """**両向きに置く。** 足りなければ次の段に行くこと。"""
        from stock_ai.backtest.wall import choose_spike

        # +20% では2回しか跳ねないが、+5% なら毎日跳ねる盤面。
        rises = dict.fromkeys(range(400), 0.06)
        rises[10] = 0.25
        rises[20] = 0.25
        levels, returns, days = self._series(rises)

        choice = choose_spike(levels, returns, days, holding=1, end=days[-1])

        assert choice.rise < 0.20  # noqa: PLR2004 - 下の段まで行った
        assert choice.cleared
        assert len(choice.tried) > 1

    def test_nothing_clearing_is_said_and_not_hidden(self) -> None:
        """**黙って空を返さない。** いちばん観測の多い線を返して旗を立てる。"""
        from stock_ai.backtest.wall import choose_spike

        levels, returns, days = self._series({10: 0.25})

        choice = choose_spike(levels, returns, days, holding=20, end=days[-1])

        assert not choice.cleared
        assert choice.needed == 200  # noqa: PLR2004 - lags 20 × SAMPLE_PER_LAG 10
        assert len(choice.tried) == 5  # noqa: PLR2004 - 梯子を最後まで下りた

    def test_it_carries_no_mean(self) -> None:
        """`Wall` と同じ。**効果を持てる形にしない。**"""
        import dataclasses

        from stock_ai.backtest.wall import SpikeChoice

        names = {field.name for field in dataclasses.fields(SpikeChoice)}

        assert not (names & {"mean", "effect", "average", "estimate", "t"})

    def test_an_empty_ladder_is_refused(self) -> None:
        from stock_ai.backtest.wall import choose_spike

        levels, returns, days = self._series({10: 0.25})

        with pytest.raises(ValueError, match="ladder"):
            choose_spike(levels, returns, days, holding=1, end=days[-1], ladder=())

    def test_the_ladder_is_ordered_strictest_first(self) -> None:
        """**梯子の向きが逆だと、いちばん緩い線が必ず採られる。**"""
        from stock_ai.backtest.wall import IV_SPIKE_LADDER

        assert list(IV_SPIKE_LADDER) == sorted(IV_SPIKE_LADDER, reverse=True)


class TestTheSixthCandidateHasItsOwnWindow:
    """**この候補だけ IS/OOS が違う。** `IV` が 2016-07 からしか無い。"""

    def test_the_window_is_three_years(self) -> None:
        from stock_ai.backtest.wall import IV_IS_END, IV_IS_FROM, IV_OOS_FROM

        assert dt.date(2016, 7, 19) == IV_IS_FROM
        assert dt.date(2019, 7, 18) == IV_IS_END
        # **OOS は IS の翌日から。** 1日も重ならず、1日も空かないこと。
        assert IV_OOS_FROM == IV_IS_END + dt.timedelta(days=1)  # noqa: SIM300 - 定義の順

    def test_the_command_says_the_window_is_different(self) -> None:
        """**他の説と並べるときに「別の窓」と分かること。**"""
        import inspect

        from stock_ai import cli

        body = inspect.getsource(cli._index_walls)

        assert "IS/OOS はこの候補だけ別である" in body
        assert "since=IV_OOS_FROM" in body


class TestTheFlowDesignsBothReachTheTable:
    """**早期 return が、下に足したものを黙らせないこと。**

    候補7 で `return walls` していたので、**下に足した候補11 が黙って
    落ちる形**になっていた（`CLAUDE.md` に書いてある規則である）。
    """

    def test_the_helper_has_no_early_return_before_the_last_design(self) -> None:
        import ast
        import inspect
        import textwrap

        from stock_ai import cli

        tree = ast.parse(textwrap.dedent(inspect.getsource(cli._index_walls)))
        function = tree.body[0]

        def own_returns(nodes) -> list[ast.Return]:
            """**入れ子の関数は見ない。** そちらの return は早期 return ではない。"""
            found: list[ast.Return] = []
            for node in nodes:
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
                    continue
                if isinstance(node, ast.Return):
                    found.append(node)
                found.extend(own_returns(ast.iter_child_nodes(node)))
            return found

        early = [node for node in own_returns(function.body) if node is not function.body[-1]]

        assert not early, "**早期 return が在る。** 下の設計が黙って落ちる。"


class TestTheCeilingReplacesAnUnreliableEstimate:
    """**推定できないときは、推定値の代わりに上限を置く**（2026-09-21）。

    当てにならない推定を掛けるより、**安全側の値を置いて「これは上限で
    ある」と書く**ほうが正直である。
    """

    def test_a_short_sample_switches_to_the_ceiling(self) -> None:
        from stock_ai.backtest.power import overlap_ceiling

        wall = _wall(inflation=1.62, undersampled=True, window=20, sample=147)

        assert wall.capped
        assert wall.effective_inflation == pytest.approx(overlap_ceiling(20))

    def test_a_long_enough_sample_keeps_the_estimate(self) -> None:
        """**両向きに置く。** 常に上限に倒しても緑にならないように。"""
        wall = _wall(inflation=1.82, undersampled=False, window=20, sample=1_220)

        assert not wall.capped
        assert wall.effective_inflation == pytest.approx(1.82)

    def test_the_ceiling_never_lowers_the_wall(self) -> None:
        """**上限は「これ以上は無い」と言うためのもの。** 低く見せない。"""
        wall = _wall(inflation=6.0, undersampled=True, window=20, sample=10)

        assert wall.effective_inflation == pytest.approx(6.0)
        assert not wall.capped

    def test_a_design_that_cannot_overlap_gets_no_ceiling(self) -> None:
        """**重ならない設計には当てない。**"""
        wall = _wall(inflation=1.04, undersampled=True, window=0, sample=5)

        assert not wall.capped
        assert wall.effective_inflation == pytest.approx(1.04)

    def test_the_wall_uses_the_effective_value(self) -> None:
        """**表に出す膨張と、壁を作った膨張が同じであること。**"""
        from stock_ai.backtest.power import detectable_difference

        wall = _wall(
            inflation=1.62, undersampled=True, window=20, sample=147, observations=379, sd=0.0423
        )

        assert wall.detectable == pytest.approx(
            detectable_difference(0.0423, wall.effective_inflation, 379, wall.line)
        )


class TestTheInflationColumnSaysWhichRuleBit:
    """**欄そのものに出す。** 注記だと、表だけ見た人には落ちる。"""

    @staticmethod
    def _cell(**changed) -> str:
        from stock_ai import cli

        return cli._inflation_cell(_wall(**changed))

    def test_the_ceiling_is_named_in_the_column(self) -> None:
        assert "上限" in self._cell(inflation=1.62, undersampled=True, window=20)

    def test_the_floor_is_named_in_the_column(self) -> None:
        assert "床" in self._cell(inflation=0.82)

    def test_a_plain_row_says_only_the_measured_value(self) -> None:
        """**常に出る印は、何も区別しない。**"""
        cell = self._cell(inflation=1.82)

        assert cell == "1.82x"
        assert "→" not in cell

    def test_the_table_and_the_document_use_the_same_renderer(self) -> None:
        """**2つ持つと、片方だけ直したときに行が自分と食い違う。**"""
        import ast
        import inspect

        from stock_ai import cli

        body = inspect.getsource(cli.wall_survey) + inspect.getsource(cli._wall_document)
        calls = [
            node.func.id
            for node in ast.walk(ast.parse(body))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        ]

        assert calls.count("_inflation_cell") == 2  # noqa: PLR2004 - 表と文書


class TestEveryEventWallCarriesItsWindow:
    """**上限を出すには窓が要る。** 渡し忘れると、黙って当たらない。

    経路ごとにテストを足す方式は、**次の1本を書き忘れた瞬間に同じことが
    起きる**（`test_deferred_imports` と同じ理由）。**AST で機械的に見る。**
    """

    def test_an_event_wall_passes_the_window(self) -> None:
        import ast
        import inspect

        from stock_ai import cli

        missing: list[int] = []
        for source in (cli.wall_survey, cli._index_walls):
            tree = ast.parse(inspect.getsource(source))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or getattr(node.func, "id", None) != "Wall":
                    continue
                named = {word.arg for word in node.keywords}
                unit = next(
                    (
                        word.value
                        for word in node.keywords
                        if word.arg == "unit" and isinstance(word.value, ast.Constant)
                    ),
                    None,
                )
                if unit is not None and unit.value == "イベント日" and "window" not in named:
                    missing.append(node.lineno)

        assert not missing, f"**イベント型なのに window= を渡していない: {missing}**"


class TestTheRateComesFromTheYears:
    """**率を焼き付けない。** 焼き付けると、絞る設計で観測と食い違う。

    `cli.py` が候補7 と候補11 の両方に ``per_year=52.0`` と書いていた
    （2026-09-21 に発覚）。**候補11 はそれで合っていた**——8.67年で 450 観測
    は年 51.9 回である。**候補7 は買い越し週にしか入らない**ので 213 観測、
    **年 24.6 回**だった。

    | | 記録した値 | 正しい値 |
    |---|---|---|
    | 候補7 の年率 | 年 32.0% | **年 15.1%** |

    **同じ行の2つの列が、別々の標本を指していた**——`CLAUDE.md` に5度書いて
    ある形である。`observations` は絞った後、`per_year` は絞る前。
    """

    def test_the_rate_is_built_from_the_observations(self) -> None:
        wall = _wall(observations=450, period_years=8.67)

        assert wall.per_year == pytest.approx(450 / 8.67)

    def test_a_filtered_design_has_a_lower_rate_than_the_raw_cadence(self) -> None:
        """**これが焼き付けで消えていた違いである。**"""
        every_week = _wall(observations=450, period_years=8.67)
        only_some = _wall(observations=213, period_years=8.67)

        assert only_some.per_year < every_week.per_year / 2
        assert only_some.annual is not None
        assert every_week.annual is not None

    def test_the_wall_carries_no_rate_of_its_own(self) -> None:
        """**混ぜた行が作れないこと。** 率は欄ではなく、導かれる値である。"""
        import dataclasses

        names = {field.name for field in dataclasses.fields(Wall)}

        assert "per_year" not in names, "**率を欄にすると、年数と食い違う行が作れる。**"
        assert "period_years" in names

    def test_a_design_without_years_annualises_to_nothing(self) -> None:
        assert _wall(period_years=None).per_year is None
        assert _wall(period_years=None).annual is None


class TestTheWallCarriesTheInvariant:
    """**検出できる差は、行どうしで比べられない。** 要る情報比は比べられる。"""

    def test_it_matches_the_one_formula(self) -> None:
        from stock_ai.backtest.power import required_information_ratio

        wall = _wall(observations=213, period_years=8.67, inflation=1.04)

        assert wall.required_ir == pytest.approx(
            required_information_ratio(wall.line, wall.effective_inflation, 8.67)
        )

    def test_filtering_does_not_move_it(self) -> None:
        """**絞っても上がらない。** 年率の欄はそう見せていた。

        候補7（年 32.0%）と候補11（年 24.5%）は、**要る情報比ではほとんど
        差が無い**——違いは効果ではなく、**市場に居る時間の割合**である。
        """
        common = {"period_years": 8.67, "line": 3.17}
        only_some = _wall(observations=213, sd=0.0272, inflation=1.04, **common)
        every_week = _wall(observations=450, sd=0.0311, inflation=1.01, **common)

        assert only_some.annual is not None
        assert every_week.annual is not None
        # **年率では 2割ちがう。**
        assert abs(only_some.annual - every_week.annual) > 0.05  # noqa: PLR2004
        # **要る情報比では、ほとんど差が無い。**
        assert only_some.required_ir is not None
        assert every_week.required_ir is not None
        assert abs(only_some.required_ir - every_week.required_ir) < 0.05  # noqa: PLR2004

    def test_it_uses_the_inflation_that_built_the_wall(self) -> None:
        """**上限に切り替えた行では、上限のほうを使う。**

        `Wall.detectable` が測った値を掛けていて、**上限が壁に届いて
        いなかった**のと同じ形である（2026-09-21）。
        """
        wall = _wall(inflation=1.62, undersampled=True, window=20, period_years=8.67)

        assert wall.required_ir is not None
        assert wall.required_ir == pytest.approx(wall.line * wall.ceiling / 8.67**0.5)

    def test_an_event_design_does_not_get_one(self) -> None:
        """**重なる窓は、取引できる系列ではない。** `annual` と同じ扱い。"""
        assert _wall(period_years=None).required_ir is None

    def test_the_table_and_the_document_use_the_same_renderer(self) -> None:
        """**2つ持つと、片方だけ直したときに行が自分と食い違う。**"""
        import ast
        import inspect

        from stock_ai import cli

        body = inspect.getsource(cli.wall_survey) + inspect.getsource(cli._wall_document)
        calls = [
            node.func.id
            for node in ast.walk(ast.parse(body))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        ]

        assert calls.count("_wall_ir_cell") == 2  # noqa: PLR2004 - 表と文書

    def test_no_wall_is_built_with_a_baked_rate(self) -> None:
        """**経路ごとにテストを足す方式は、次の1本で同じことが起きる。**

        `test_deferred_imports` と同じ理由で、**AST で機械的に見る。**
        """
        import ast
        import inspect

        from stock_ai import cli

        baked: list[int] = []
        for source in (cli.wall_survey, cli._index_walls):
            for node in ast.walk(ast.parse(inspect.getsource(source))):
                if not isinstance(node, ast.Call) or getattr(node.func, "id", None) != "Wall":
                    continue
                if any(word.arg == "per_year" for word in node.keywords):
                    baked.append(node.lineno)

        assert not baked, f"**率を焼き付けている: {baked}。** `period_years=` を渡すこと。"


class TestTheRoundNumberFold:
    """**節目の畳み方は、測る前に1つに決めた。** 出典は無い。"""

    def test_a_round_number_sits_at_zero(self) -> None:
        from stock_ai.backtest.wall import round_number_position

        assert round_number_position(1000.0) == pytest.approx(0.0)
        assert round_number_position(100.0) == pytest.approx(0.0)

    def test_just_below_a_round_number_sits_near_one(self) -> None:
        """**「1,000円の壁」はここに出る。**"""
        from stock_ai.backtest.wall import round_number_position

        assert round_number_position(1950.0) == pytest.approx(0.95)
        assert round_number_position(999.0) == pytest.approx(0.99)

    def test_the_decade_is_taken_from_the_price(self) -> None:
        """**桁を揃えてから見る。** 同じ「あと50円」でも意味が違う。"""
        from stock_ai.backtest.wall import round_number_position

        # 150円 は 100 刻みの半分、1,500円 は 1,000 刻みの半分。
        assert round_number_position(150.0) == pytest.approx(0.5)
        assert round_number_position(1500.0) == pytest.approx(0.5)

    def test_it_never_leaves_the_unit_interval(self) -> None:
        """**丸めで範囲の外を返さない。** 対数が 2.9999… になる盤面がある。"""
        from stock_ai.backtest.wall import round_number_position

        for price in (1.0, 9.999999, 10.0, 999.9999999, 1000.0, 1e6, 1e6 - 1e-9):
            found = round_number_position(price)
            assert found is not None
            assert 0.0 <= found < 1.0, price

    def test_a_price_that_cannot_be_logged_is_refused(self) -> None:
        from stock_ai.backtest.wall import round_number_position

        assert round_number_position(0.0) is None
        assert round_number_position(-5.0) is None


class TestTheTailOfTheYear:
    """**候補18。** 年に1観測しか作れない——#14 と同じ形である。"""

    @staticmethod
    def _december(year: int, days: int, value: float = 0.01):
        import datetime as dt

        dates = [dt.date(year, 12, day) for day in range(1, days + 1)]
        return [value] * days, dates

    def test_it_compounds_the_last_sessions(self) -> None:
        from stock_ai.backtest.wall import tail_episodes

        returns, dates = self._december(2017, 10)

        years, episodes = tail_episodes(returns, dates, sessions=5, end=dt.date(2017, 12, 31))

        assert years == [2017]
        assert episodes[0] == pytest.approx(1.01**5 - 1)

    def test_it_takes_the_last_sessions_not_the_calendar_end(self) -> None:
        """**大納会は年によって違う。** 暦の 12/31 を探さない。"""
        from stock_ai.backtest.wall import tail_episodes

        returns, dates = self._december(2017, 8)
        returns[-1] = 0.5  # いちばん最後の足

        _years, episodes = tail_episodes(returns, dates, sessions=2, end=dt.date(2017, 12, 31))

        assert episodes[0] == pytest.approx(1.01 * 1.5 - 1)

    def test_a_short_year_is_dropped_not_shortened(self) -> None:
        """**3日ぶんと5日ぶんを同じ列に並べない。** 散らばりが揃わない。"""
        from stock_ai.backtest.wall import tail_episodes

        returns, dates = self._december(2017, 3)

        years, episodes = tail_episodes(returns, dates, sessions=5, end=dt.date(2017, 12, 31))

        assert not years
        assert not episodes

    def test_only_december_counts(self) -> None:
        from stock_ai.backtest.wall import tail_episodes

        returns, dates = self._december(2017, 6)
        returns += [9.0]
        dates += [dt.date(2018, 1, 4)]

        years, _episodes = tail_episodes(returns, dates, sessions=5, end=dt.date(2018, 12, 31))

        assert years == [2017]

    def test_the_end_cuts_it_off(self) -> None:
        from stock_ai.backtest.wall import tail_episodes

        early, early_dates = self._december(2016, 6)
        late, late_dates = self._december(2017, 6)

        years, _episodes = tail_episodes(
            early + late, early_dates + late_dates, sessions=5, end=dt.date(2016, 12, 31)
        )

        assert years == [2016]

    def test_the_years_are_counted_the_same_way(self) -> None:
        """**数え方を揃える。** 12月が丸ごと入る年だけ。"""
        from stock_ai.backtest.wall import complete_tail_years

        assert complete_tail_years(dt.date(2018, 1, 1), dt.date(2026, 8, 31)) == 8  # noqa: PLR2004
        assert complete_tail_years(dt.date(2018, 1, 1), dt.date(2026, 12, 31)) == 9  # noqa: PLR2004
        assert complete_tail_years(dt.date(2026, 1, 1), dt.date(2026, 8, 31)) == 0

    def test_it_refuses_mismatched_lengths(self) -> None:
        from stock_ai.backtest.wall import tail_episodes

        with pytest.raises(ValueError, match="長さが違う"):
            tail_episodes([0.01], [dt.date(2017, 12, 1), dt.date(2017, 12, 2)])

    def test_it_refuses_zero_sessions(self) -> None:
        from stock_ai.backtest.wall import tail_episodes

        with pytest.raises(ValueError, match="at least 1"):
            tail_episodes([], [], sessions=0)


class TestTwoDesignsAreNotTheSameDesign:
    """**壁の表に実質同じ設計が2行在ると、後で良いほうを選んだのと区別が
    付かない。**
    """

    @staticmethod
    def _values(pairs):
        import pandas as pd

        return {
            (symbol, pd.Period("2017-01", freq="M")): (dt.date(2017, 1, 31), value)
            for symbol, value in pairs
        }

    def test_the_same_ordering_shows_up(self) -> None:
        from stock_ai.backtest.wall import signal_overlap

        left = self._values([("a", 1.0), ("b", 2.0), ("c", 3.0), ("d", 4.0)])
        right = self._values([("a", 10.0), ("b", 20.0), ("c", 30.0), ("d", 40.0)])

        shared, correlation = signal_overlap(left, right)

        assert shared == 4  # noqa: PLR2004
        assert correlation == pytest.approx(1.0)

    def test_it_looks_at_the_order_not_the_scale(self) -> None:
        """**円と比を並べても、桁で決まらないこと。**"""
        from stock_ai.backtest.wall import signal_overlap

        left = self._values([("a", 1.0), ("b", 2.0), ("c", 3.0)])
        right = self._values([("a", 1e9), ("b", 2e9), ("c", 3e9)])

        _shared, correlation = signal_overlap(left, right)

        assert correlation == pytest.approx(1.0)

    def test_a_different_ordering_shows_up(self) -> None:
        """**両向きに置く。** 常に 1.0 を返す形でも緑にならないように。"""
        from stock_ai.backtest.wall import signal_overlap

        left = self._values([("a", 1.0), ("b", 2.0), ("c", 3.0), ("d", 4.0)])
        right = self._values([("a", 4.0), ("b", 3.0), ("c", 2.0), ("d", 1.0)])

        _shared, correlation = signal_overlap(left, right)

        assert correlation == pytest.approx(-1.0)

    def test_two_points_say_nothing(self) -> None:
        """**2点の相関は必ず ±1 である。** 何も言っていない。"""
        from stock_ai.backtest.wall import signal_overlap

        left = self._values([("a", 1.0), ("b", 2.0)])
        right = self._values([("a", 5.0), ("b", 9.0)])

        shared, correlation = signal_overlap(left, right)

        assert shared == 2  # noqa: PLR2004
        assert correlation is None

    def test_only_the_shared_keys_are_compared(self) -> None:
        from stock_ai.backtest.wall import signal_overlap

        left = self._values([("a", 1.0), ("b", 2.0), ("c", 3.0), ("z", 9.0)])
        right = self._values([("a", 1.0), ("b", 2.0), ("c", 3.0)])

        shared, _correlation = signal_overlap(left, right)

        assert shared == 3  # noqa: PLR2004


class TestTheMarginFold:
    """**候補15。** 信用買い残の変化を、銘柄月ごとに1つ作る。

    押さえるのは3つ。

    1. **比で見る。** 株数そのものは銘柄の大きさで桁が変わる
    2. **公表の遅れを外す。** `Date` は金曜時点で、公表はその第2営業日
    3. **落ちたものを数える。** 合計だけ出すと、その中に紛れる
    """

    @staticmethod
    def _archive(tmp_path, rows: list[dict[str, str]]):
        import csv as csv_module
        import gzip
        import io

        from stock_ai.data.jquants_archive import MANIFEST, MANIFEST_COLUMNS

        out = io.StringIO()
        writer = csv_module.DictWriter(out, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        key = "markets/margin-interest/margin_sample.csv.gz"
        target = tmp_path / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(gzip.compress(out.getvalue().encode("utf-8")))
        (tmp_path / MANIFEST).write_text(
            ",".join(MANIFEST_COLUMNS) + "\n" + f"/{key},1,1,x,,2026-09-21\n",
            encoding="utf-8",
        )
        return tmp_path

    @staticmethod
    def _row(date: str, code: str, long_volume: float) -> dict[str, str]:
        """**実物から作った形である**（`jquants_margin_interest_sample.csv`）。"""
        return {
            "Date": date,
            "Code": code,
            "ShrtVol": "61800.0",
            "LongVol": str(long_volume),
            "ShrtNegVol": "31400.0",
            "LongNegVol": "57500.0",
            "ShrtStdVol": "30400.0",
            "LongStdVol": "171800.0",
            "IssType": "2",
        }

    def _weeks(self, code: str, volumes: list[float]) -> list[dict[str, str]]:
        """**日付は暦で足す。** 文字列で足すと 2022-01-35 が出る（実際に出した）。"""
        first = dt.date(2022, 1, 7)
        return [
            self._row((first + dt.timedelta(weeks=step)).isoformat(), code, volume)
            for step, volume in enumerate(volumes)
        ]

    def test_the_change_is_a_ratio(self, tmp_path) -> None:
        from stock_ai.backtest.wall import margin_change

        rows = self._weeks("86970", [100.0, 100.0, 100.0, 100.0, 120.0])

        values, _census = margin_change(self._archive(tmp_path, rows), weeks=4)

        assert len(values) == 1
        (_when, change) = next(iter(values.values()))
        assert change == pytest.approx(0.2)

    def test_it_waits_for_the_publication(self, tmp_path) -> None:
        """**金曜時点の値を、その日に使わない。** 公表は第2営業日である。"""
        from stock_ai.backtest.wall import margin_change

        rows = self._weeks("86970", [100.0, 100.0, 100.0, 100.0, 120.0])

        values, _census = margin_change(self._archive(tmp_path, rows), weeks=4, lag_days=4)
        ((_symbol, month), (when, _change)) = next(iter(values.items()))

        # 最後の週末は 2022-02-04、公表は 2022-02-08。
        assert when == dt.date(2022, 2, 8)
        assert str(month) == "2022-02"

    def test_a_longer_lag_can_push_it_into_the_next_month(self, tmp_path) -> None:
        """**両向きに置く。** 遅れを無視する形でも緑にならないように。"""
        from stock_ai.backtest.wall import margin_change

        rows = self._weeks("86970", [100.0, 100.0, 100.0, 100.0, 120.0])

        early, _ = margin_change(self._archive(tmp_path, rows), weeks=4, lag_days=0)
        late, _ = margin_change(self._archive(tmp_path, rows), weeks=4, lag_days=30)

        assert str(next(iter(early))[1]) == "2022-02"
        assert str(next(iter(late))[1]) == "2022-03"

    def test_the_newest_publication_of_the_month_wins(self, tmp_path) -> None:
        """**月に2回公表されたら、新しいほうを採る。**"""
        from stock_ai.backtest.wall import margin_change

        rows = self._weeks("86970", [100.0, 100.0, 100.0, 100.0, 120.0, 150.0])

        values, _census = margin_change(self._archive(tmp_path, rows), weeks=4)
        changes = {str(month): change for (_s, month), (_w, change) in values.items()}

        # 2022-02 には2つ入る（02-08 と 02-15）。**新しいほう。**
        assert changes["2022-02"] == pytest.approx(0.5)

    def test_a_short_history_is_counted_apart(self, tmp_path) -> None:
        """**合計だけ出すと、その中に紛れる。**"""
        from stock_ai.backtest.wall import margin_change

        rows = self._weeks("86970", [100.0, 110.0])

        values, census = margin_change(self._archive(tmp_path, rows), weeks=4)

        assert not values
        assert census.skipped_short == 1
        assert any("履歴が無い" in line for line in census.warnings())

    def test_a_nonpositive_balance_is_not_divided_by(self, tmp_path) -> None:
        from stock_ai.backtest.wall import margin_change

        rows = self._weeks("86970", [100.0, 100.0, 100.0, 100.0, 120.0])
        rows[0]["LongVol"] = "0.0"

        values, census = margin_change(self._archive(tmp_path, rows), weeks=4)

        # **0 の週は履歴から落ちる**ので、4公表ぶん遡れなくなる。
        assert not values
        assert census.skipped_short == 1

    def test_an_empty_archive_says_so(self, tmp_path) -> None:
        from stock_ai.backtest.wall import margin_change
        from stock_ai.data.jquants_archive import MANIFEST, MANIFEST_COLUMNS

        (tmp_path / MANIFEST).write_text(",".join(MANIFEST_COLUMNS) + "\n", encoding="utf-8")

        values, census = margin_change(tmp_path)

        assert not values
        assert any("1行も読めなかった" in line for line in census.warnings())

    def test_it_refuses_zero_weeks(self, tmp_path) -> None:
        from stock_ai.backtest.wall import margin_change

        with pytest.raises(ValueError, match="at least 1"):
            margin_change(tmp_path, weeks=0)

    def test_the_lookback_has_no_source(self) -> None:
        """**決めた値がそのまま出ていること。** 書き写さない。"""
        from stock_ai.backtest.wall import MARGIN_LOOKBACK

        assert MARGIN_LOOKBACK == 4  # noqa: PLR2004 - 決めた値そのもの
