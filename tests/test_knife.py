"""#16「落ちるナイフをつかむな」— 急落した銘柄を買うと、その後も下回るか。

ここで押さえるのは5つ。

1. **急落の定義は1つだけ。** 壁の下見（`wall`）も同じ規則を呼ぶ
2. **不連続を外す。** 1:2 の分割は −50% で、まさに「急落」に見える
3. **権利落ちも外す。** 「0 が想定」と書いたが実データは 819 件だった
   ——**配当は 20% を作らなくてよく、線の向こうに押し出せば足りる**
   （2026-09-20）。**驚く理由を決め打たない**
4. **符号の反転は1箇所だけ。** 測るのはショートの取り高である
5. **窓は5営業日。** 格言が急落直後の話だからで、検出力のためではない
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from stock_ai.backtest.knife import (
    HOLDING,
    KNIFE_DAYS,
    KNIFE_DROP,
    KnifeEvents,
    build_events,
    knife_positions,
)
from stock_ai.data.schema import ADJ_CLOSE, CLOSE, HIGH, LOW, OPEN, VOLUME, split_adjusted
from stock_ai.database.engine import Database
from stock_ai.database.repository import PriceRepository


class TestWhatCountsAsACrash:
    @staticmethod
    def _closes(*values: float) -> tuple[np.ndarray, np.ndarray]:
        closes = np.array(values, dtype=float)
        return closes, np.ones(len(closes), dtype=bool)

    def test_a_deep_enough_fall_is_one(self) -> None:
        closes, liquid = self._closes(100.0, 100.0, 100.0, 100.0, 100.0, 75.0)

        assert knife_positions(closes, liquid).tolist() == [5]

    def test_a_shallower_one_is_not(self) -> None:
        """**この検査が落ちる条件を、実際に1つ作る。**"""
        closes, liquid = self._closes(100.0, 100.0, 100.0, 100.0, 100.0, 85.0)

        assert knife_positions(closes, liquid).tolist() == []

    def test_it_is_measured_over_five_sessions(self) -> None:
        """**5営業日で測る。** 同じ下げでも、もっとゆっくりなら事象ではない。"""
        slow = np.array([100.0, 98.0, 96.0, 94.0, 92.0, 90.0, 88.0, 86.0], dtype=float)

        assert knife_positions(slow, np.ones(len(slow), dtype=bool)).tolist() == []

    def test_a_rise_is_not(self) -> None:
        closes, liquid = self._closes(100.0, 100.0, 100.0, 100.0, 100.0, 140.0)

        assert knife_positions(closes, liquid).tolist() == []

    def test_the_illiquid_day_is_dropped(self) -> None:
        closes, _ = self._closes(100.0, 100.0, 100.0, 100.0, 100.0, 75.0)
        liquid = np.ones(6, dtype=bool)
        liquid[5] = False

        assert knife_positions(closes, liquid).tolist() == []

    def test_the_first_days_can_never_be_one(self) -> None:
        """前が足りない。**そこを数えると、上場直後が毎回入る。**"""
        closes, liquid = self._closes(100.0, 50.0, 40.0)

        assert knife_positions(closes, liquid).tolist() == []

    def test_mismatched_lengths_are_refused(self) -> None:
        with pytest.raises(ValueError, match="長さが違う"):
            knife_positions(np.array([1.0, 2.0]), np.array([True]))

    def test_zero_days_is_refused(self) -> None:
        with pytest.raises(ValueError, match="days must be at least 1"):
            knife_positions(np.ones(10), np.ones(10, dtype=bool), days=0)

    def test_the_wall_survey_calls_the_same_rule(self) -> None:
        """**2つ持つと、下見で選んだ設計と判定に使う設計が黙ってずれる。**"""
        import inspect

        from stock_ai.backtest import wall

        source = inspect.getsource(wall)

        assert "knife_positions(" in source
        assert "after[usable] / before[usable]" not in source


_INDEX = pd.bdate_range("2012-07-02", "2019-12-31", name="date")
_BARS = len(_INDEX)


def _at(when: str) -> int:
    return int(_INDEX.get_loc(pd.Timestamp(when)))


def _prices(seed: int, crashes: tuple[int, ...] = (), merger: int | None = None) -> pd.DataFrame:
    """乱数歩行に、急落を決め打ちの位置で仕込む。

    **定数の足を置かない**（`CLAUDE.md`）。
    """
    rng = np.random.default_rng(seed)
    steps = rng.normal(0.0, 0.01, _BARS)
    for start in crashes:
        # **定数から出す。** 幅を書き写すと、定数を動かしたとき仕込みが古くなる。
        steps[start : start + KNIFE_DAYS] = np.log(1.0 - KNIFE_DROP) / KNIFE_DAYS * 1.3
    close = 1_000.0 * np.exp(np.cumsum(steps))
    if merger is not None:
        close[merger:] *= 0.001
    opens = close.copy()
    opens[1:] = close[:-1] * (1.0 + rng.normal(0.0, 0.002, _BARS - 1))
    return pd.DataFrame(
        {
            OPEN: opens,
            HIGH: np.maximum(close, opens),
            LOW: np.minimum(close, opens),
            CLOSE: close,
            ADJ_CLOSE: close,
            VOLUME: [500_000.0] * _BARS,
        },
        index=_INDEX,
    )


_IS_CRASH = "2015-03-23"
_OOS_CRASH = "2019-03-21"


def _database(
    count: int = 6, crashes=(), merger=None, turnover: float | None = None
) -> tuple[Database, list[str]]:
    database = Database("sqlite:///:memory:")
    database.create_all()
    symbols = [f"{1400 + index:04d}" for index in range(count)]
    with database.session() as session:
        repo = PriceRepository(session)
        for index, symbol in enumerate(symbols):
            frame = _prices(seed=index, crashes=crashes, merger=merger)
            if turnover is not None:
                # **売買代金を狙った水準に合わせる。** 終値で割って出来高を決める。
                frame[VOLUME] = turnover / frame[CLOSE].to_numpy(dtype=float)
            repo.upsert_prices(symbol, frame, market="JP")
    return database, symbols


class TestCollectingTheCrashes:
    def test_it_finds_them_in_both_halves(self) -> None:
        database, symbols = _database(crashes=(_at(_IS_CRASH), _at(_OOS_CRASH)))

        found = build_events(database, {}, symbols=symbols)

        assert found.events
        assert found.days_is >= 1
        assert found.days_oos >= 1

    def test_a_merger_is_not_a_crash(self) -> None:
        """**1:2 の分割は −50%。** 調整漏れは、まさに急落に見える。"""
        where = _at(_IS_CRASH)
        database, symbols = _database(merger=where)

        found = build_events(database, {}, symbols=symbols)

        assert _INDEX[where].date() not in [when for _symbol, when in found.events]
        assert found.excluded_broken > 0

    def test_an_ex_date_inside_the_fall_is_excluded(self) -> None:
        """**0 が想定だが、口は開けてある。**"""
        where = _at(_IS_CRASH)
        database, symbols = _database(crashes=(where,))
        crash_day = _INDEX[where + KNIFE_DAYS].date()
        announced = {symbol: [(dt.date(2013, 1, 10), crash_day)] for symbol in symbols}

        found = build_events(database, announced, symbols=symbols)

        assert found.excluded_ex_date > 0

    def test_an_ex_date_announced_later_does_not_exclude(self) -> None:
        """**この検査が落ちる条件を、実際に1つ作る。**"""
        where = _at(_IS_CRASH)
        database, symbols = _database(crashes=(where,))
        crash_day = _INDEX[where + KNIFE_DAYS].date()
        announced = {symbol: [(dt.date(2019, 1, 10), crash_day)] for symbol in symbols}

        found = build_events(database, announced, symbols=symbols)

        assert found.excluded_ex_date == 0
        assert found.events

    def test_only_an_unadjustable_dividend_excludes(self) -> None:
        """**額が公表されていれば外さない。** 価格から落とすほうが先である。

        「権利落ちが窓に在れば外す」は §3 の**代理**で、実データで本物の
        急落を 189 件巻き込んでいた（2026-09-20、ユーザーが指摘）。
        """
        database, symbols, first = self._one_crash()
        inside = _INDEX[first - KNIFE_DAYS + 1].date()
        announced = {symbols[0]: [(dt.date(2013, 1, 10), inside)]}
        # 額が分かっていれば、落として測る——外さない。
        rates = {symbols[0]: [(dt.date(2013, 1, 10), inside, 1.0)]}

        with_rate = build_events(database, announced, rates, symbols=symbols)
        without = build_events(database, announced, symbols=symbols)

        assert with_rate.excluded_ex_date == 0, "**落とせるのに外している。**"
        assert without.excluded_ex_date > 0, "**落とせないのに外していない。**"

    def test_the_warning_names_the_one_reason_left(self) -> None:
        """残るのは「**権利落ち日までに額が引けなかった**」だけ。

        **「額が一度も公表されていない」ではない**（2026-09-21 に直した）。
        後から公表されているものが含まれる——**札が、数えているものと違う
        ことを言っていた。**
        """
        database, symbols, first = self._one_crash()
        inside = _INDEX[first - KNIFE_DAYS + 1].date()
        announced = {symbols[0]: [(dt.date(2013, 1, 10), inside)]}

        told = " ".join(build_events(database, announced, symbols=symbols).warnings())

        assert "額を落とせない権利落ち" in told
        assert "権利落ち日までに額が引けなかった権利落ち" in told
        assert "「一度も公表されていない」ではない" in told

    def test_a_dividend_in_the_holding_window_excludes_too(self) -> None:
        """**急落側だけ外すと、非対称が残る。**

        ショートは配当を払う側なので、保有する窓の権利落ちも同じに扱う
        （2026-09-20、ユーザーが指摘）。
        """
        database, symbols, first = self._one_crash()
        inside = _INDEX[first + 2].date()
        announced = {symbols[0]: [(dt.date(2013, 1, 10), inside)]}

        found = build_events(database, announced, symbols=symbols)

        assert found.excluded_ex_date > 0, "**保有窓の権利落ちを見ていない。**"

    def test_a_dividend_past_the_holding_window_does_not(self) -> None:
        """**両向きに置く。** 窓の外まで外したら、何も区別していない。"""
        database, symbols, first = self._one_crash()
        outside = _INDEX[first + HOLDING + 3].date()
        announced = {symbols[0]: [(dt.date(2013, 1, 10), outside)]}

        found = build_events(database, announced, symbols=symbols)

        assert found.excluded_ex_date == 0

    def test_a_dividend_on_the_base_day_does_not_exclude(self) -> None:
        """**基準日の配当は、比を1つも動かさない。**

        `closes[index]` と `closes[index - days]` の**どちらにも同じだけ
        乗る**ので、外す理由になりえない。窓を `days + 1` にしていて、
        実データで 158 件をそれで外していた（2026-09-20）。
        """
        database, symbols, first = self._one_crash()
        # **最初の急落の基準日。** そこに置いた配当は比を動かさない。
        base_day = _INDEX[first - KNIFE_DAYS].date()
        clean = build_events(database, {}, symbols=symbols)
        announced = {symbols[0]: [(dt.date(2013, 1, 10), base_day)]}

        found = build_events(database, announced, symbols=symbols)

        assert found.excluded_ex_date == 0
        assert len(found.events) == len(clean.events), "**外す理由になりえない日で外している。**"

    def test_a_dividend_one_day_after_the_base_does_exclude(self) -> None:
        """**片側だけ見ない。** 隣の日は比を動かすので、外す。"""
        database, symbols, first = self._one_crash()
        inside = _INDEX[first - KNIFE_DAYS + 1].date()
        announced = {symbols[0]: [(dt.date(2013, 1, 10), inside)]}

        found = build_events(database, announced, symbols=symbols)

        assert found.excluded_ex_date > 0

    @staticmethod
    def _one_crash(turnover: float | None = None) -> tuple[Database, list[str], int]:
        """1銘柄だけの盤面と、**実際に検出された最初の急落の位置。**

        **位置を決め打たない。** 仕込んだ日と、−20% を割る日は違う
        （乱数歩行なので銘柄ごとにもずれる）。
        """
        database, symbols = _database(
            count=1, crashes=(_at(_IS_CRASH), _at(_OOS_CRASH)), turnover=turnover
        )
        with database.session() as session:
            raw = PriceRepository(session).get_raw_prices(symbols[0])
        closes = split_adjusted(raw)[CLOSE].to_numpy(dtype=float)
        found = knife_positions(closes, np.ones(len(closes), dtype=bool))
        return database, symbols, int(found[0])

    def test_a_dividend_does_not_push_a_symbol_below_the_turnover_floor(self) -> None:
        """**流動性は配当を落とす前の値で見る。**

        配当調整はリターンのためのもので、**規模のためのものではない。**
        落とした値で売買代金を測ると実際より小さく出て、**古い足ほど強く
        削られる**（14年・年2回・利回り 1.3% なら 0.69倍）。**1億円の線の
        上下にいる銘柄が、時期によって違う基準で落ちる。**

        実データで、除外を 303 件やめたのに事象が 169 件**減った**ことから
        見つかった（2026-09-20、ユーザーが指摘）。
        """
        from stock_ai.backtest.pead import MIN_TURNOVER

        # **線のすぐ上に売買代金を置く。** 少しでも縮めば落ちる。
        database, symbols, first = self._one_crash(turnover=MIN_TURNOVER * 1.05)
        clean = build_events(database, {}, symbols=symbols)
        assert clean.events, "**足場が線の上に乗っていない。** 検査にならない。"

        # 履歴じゅうに配当を置く（実データと同じく、古い足ほど強く縮む）。
        paid = [
            (dt.date(2012, 7, 2), _INDEX[step].date(), 20.0) for step in range(60, len(_INDEX), 120)
        ]
        found = build_events(database, {}, {symbols[0]: paid}, symbols=symbols)

        assert len(found.events) == len(clean.events), "**配当のせいで流動性から落ちている。**"

    def test_the_excluded_crashes_come_back(self) -> None:
        """**「中身を見ること」と言うなら、中身を返す。**

        件数だけ返していたので、外したものが何だったかを後から調べられ
        なかった（2026-09-20、ユーザーが指摘）。
        """
        database, symbols, first = self._one_crash()
        inside = _INDEX[first - KNIFE_DAYS + 1].date()
        announced = {symbols[0]: [(dt.date(2013, 1, 10), inside)]}

        found = build_events(database, announced, symbols=symbols)

        assert found.excluded_ex_date > 0
        assert len(found.ex_date_events) == found.excluded_ex_date

    def test_a_count_that_disagrees_with_the_contents_is_refused(self) -> None:
        """**数と中身がずれたら、作った時点で落ちる。**"""
        with pytest.raises(ValueError, match="合わない"):
            KnifeEvents(
                events=[],
                days_is=0,
                days_oos=0,
                events_oos=0,
                excluded_ex_date=5,
                excluded_broken=0,
                symbols=1,
                thin=0,
                ex_date_events=[("1301", dt.date(2013, 3, 28))],
            )

    def test_a_clean_run_says_nothing_about_dividends(self) -> None:
        database, symbols = _database(crashes=(_at(_IS_CRASH), _at(_OOS_CRASH)))

        found = build_events(database, {}, symbols=symbols)

        assert not any("配当" in line for line in found.warnings())

    def test_the_liquidity_drop_is_counted_in_events(self) -> None:
        """**急落だったが外した件数。** 足の数ではない。"""
        database = Database("sqlite:///:memory:")
        database.create_all()
        with database.session() as session:
            frame = _prices(seed=1, crashes=(_at(_IS_CRASH),))
            frame[VOLUME] = 1.0
            PriceRepository(session).upsert_prices("1400", frame, market="JP")

        found = build_events(database, {}, symbols=["1400"])

        assert found.events == []
        assert 0 < found.thin < 10  # noqa: PLR2004 - 足の数（約1,900）ではない

    def test_an_empty_universe_is_refused(self) -> None:
        database = Database("sqlite:///:memory:")
        database.create_all()

        with pytest.raises(ValueError, match="銘柄が1つも無い"):
            build_events(database, {}, symbols=[])


class TestTheShortSideAndTheWindow:
    def test_the_command_flips_the_sign_once(self) -> None:
        """**符号の反転は1箇所だけ**（#8 と同じ作法）。"""
        import inspect

        from stock_ai import cli

        body = inspect.getsource(cli.knife_power)

        assert body.count("-value - COST_ROUND_TRIP") == 1
        assert 'side="ショート"' in body

    def test_the_command_uses_the_shared_gate(self) -> None:
        import inspect

        from stock_ai import cli

        assert "_event_gate(" in inspect.getsource(cli.knife_power)

    def test_the_periods_are_days_not_events(self) -> None:
        import inspect

        from stock_ai import cli

        body = inspect.getsource(cli.knife_power)

        assert "periods=found.days_oos" in body
        assert "periods=found.events_oos" not in body

    def test_it_says_the_line_was_measured_at_another_window(self) -> None:
        """**線 3.30 は窓20営業日で測った値である**（事前登録 §0・§10）。"""
        import inspect

        from stock_ai import cli

        assert "5営業日の対照を回してから封印する" in inspect.getsource(cli.knife_power)

    def test_the_window_matches_the_preregistration(self) -> None:
        assert HOLDING == KNIFE_DAYS
        assert pytest.approx(0.20) == KNIFE_DROP


class TestTheCommandRunsOnARealDatabase:
    """**本物のコマンドを、中身の入った DB で1本通す。**"""

    def test_it_runs_all_the_way_through(self, tmp_path, monkeypatch) -> None:
        import gzip
        import pathlib

        from stock_ai import cli
        from stock_ai.data.jquants_archive import MANIFEST, MANIFEST_COLUMNS
        from stock_ai.database import engine
        from tests.test_jquants_dividend import TestHowManyExDatesTheArchiveCanSupply as Rows

        monkeypatch.setattr(engine, "DATA_DIR", tmp_path)
        symbols = [f"{1400 + index:04d}" for index in range(40)]
        # **1日だけだと手前で終わる。** 散らばりを測るには日が要る。
        days = ("2015-03-23", "2015-09-21", "2016-05-23", "2017-02-20")
        database = Database(f"sqlite:///{tmp_path / 'stock_ai.db'}")
        database.create_all()
        with database.session() as session:
            repo = PriceRepository(session)
            repo.upsert_prices("1306", _prices(seed=999), market="JP")
            for index, symbol in enumerate(symbols):
                repo.upsert_prices(
                    symbol,
                    _prices(seed=index, crashes=tuple(_at(day) for day in (*days, _OOS_CRASH))),
                    market="JP",
                )

        key = "fins/dividend/dividend_2015.csv.gz"
        target = pathlib.Path(tmp_path) / key
        target.parent.mkdir(parents=True, exist_ok=True)
        body = Rows._rows({"Code": "14000", "ExDate": "2015-06-29", "RefNo": "1"})
        target.write_bytes(gzip.compress(body.encode("utf-8")))
        (pathlib.Path(tmp_path) / MANIFEST).write_text(
            ",".join(MANIFEST_COLUMNS) + "\n" + f"/{key},1,1,x,,2026-09-20\n",
            encoding="utf-8",
        )

        cli.knife_power(archive=str(tmp_path), benchmark="1306")

    def test_it_stops_when_there_are_no_ex_dates(self, tmp_path, monkeypatch) -> None:
        """**外さずには測らない。** 手前で止まる側も通す。"""
        import typer

        from stock_ai import cli
        from stock_ai.database import engine

        monkeypatch.setattr(engine, "DATA_DIR", tmp_path)

        with pytest.raises(typer.Exit):
            cli.knife_power(archive=str(tmp_path), benchmark="1306")


class TestTheHoldingWindowDividendsAreBrokenDown:
    """**合計だけ出すと、その中に紛れる。**

    急落側には理由ごとの表が在るのに、**保有窓側だけ「当てた 11,991 件、
    当てなかった 923 件」の合計のままだった**（2026-09-21、ユーザーが指摘）。
    **7.1% が当たっていないのに、理由ごとの数が出ていない。**
    """

    def test_the_command_prints_the_breakdown(self) -> None:
        """**置いたことと、経路に載ったことは別である。**"""
        import ast
        import inspect

        from stock_ai import cli

        tree = ast.parse(inspect.getsource(cli.knife_power))
        called = [
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        ]

        assert "_print_dividend_breakdown" in called
        assert "_print_raw_dividend_rows" in called

    def test_the_total_only_line_is_gone(self) -> None:
        """**直したら、古い形が戻らないようにする。**"""
        import inspect

        from stock_ai import cli

        body = inspect.getsource(cli.knife_power)

        assert "当てなかった {netting.skipped" not in body
