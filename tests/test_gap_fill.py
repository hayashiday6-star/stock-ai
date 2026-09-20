"""#15「窓は埋まる」— 下に開いた窓の後の20営業日。

ここで押さえるのは5つ。

1. **窓の定義は1つだけ。** 壁の下見（`wall`）も同じ規則を呼ぶ
2. **権利落ちを外す。** 3% の下窓は、権利落ちがまさにそう見える
3. **その日より前に公表されたものだけで外す。** 訂正を使えば先読みになる
4. **不連続を外す。** 調整漏れの分割も、大きな下窓に見える
5. **外した件数を別々に数える。** まとめると、どちらで落ちたか分からない
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from stock_ai.backtest.gap_fill import (
    GAP_DOWN,
    HOLDING,
    IS_END,
    IS_FROM,
    OOS_FROM,
    build_events,
    gap_positions,
    known_ex_dates,
    liquid_bars,
)
from stock_ai.data.schema import ADJ_CLOSE, CLOSE, HIGH, LOW, OPEN, VOLUME
from stock_ai.database.engine import Database
from stock_ai.database.repository import PriceRepository


class TestWhatCountsAsAGap:
    @staticmethod
    def _arrays(opens: list[float], closes: list[float]):
        return (
            np.array(opens, dtype=float),
            np.array(closes, dtype=float),
            np.ones(len(opens), dtype=bool),
        )

    def test_a_wide_enough_gap_is_one(self) -> None:
        opens, closes, liquid = self._arrays([100.0, 96.0], [100.0, 96.0])

        assert gap_positions(opens, closes, liquid).tolist() == [1]

    def test_a_narrow_one_is_not(self) -> None:
        """**この検査が落ちる条件を、実際に1つ作る。**"""
        opens, closes, liquid = self._arrays([100.0, 100.0 * (1 - GAP_DOWN / 2)], [100.0, 98.5])

        assert gap_positions(opens, closes, liquid).tolist() == []

    def test_an_upward_gap_is_not(self) -> None:
        """**下窓のみ**（事前登録 §3）。"""
        opens, closes, liquid = self._arrays([100.0, 110.0], [100.0, 110.0])

        assert gap_positions(opens, closes, liquid).tolist() == []

    def test_the_illiquid_day_is_dropped(self) -> None:
        opens, closes, _ = self._arrays([100.0, 90.0], [100.0, 90.0])
        liquid = np.array([True, False])

        assert gap_positions(opens, closes, liquid).tolist() == []

    def test_the_first_bar_can_never_be_one(self) -> None:
        """前日が無い。**そこを1件に数えると、上場初日が毎回入る。**"""
        opens, closes, liquid = self._arrays([1.0], [100.0])

        assert gap_positions(opens, closes, liquid).tolist() == []

    def test_mismatched_lengths_are_refused(self) -> None:
        with pytest.raises(ValueError, match="長さが違う"):
            gap_positions(np.array([1.0, 2.0]), np.array([1.0]), np.array([True, True], dtype=bool))

    def test_the_wall_survey_calls_the_same_rule(self) -> None:
        """**2つ持つと、下見で選んだ設計と判定に使う設計が黙ってずれる。**"""
        import inspect

        from stock_ai.backtest import wall

        source = inspect.getsource(wall)

        assert "gap_positions(" in source
        assert "opened[usable] / previous[usable]" not in source


class TestOnlyWhatWasAlreadyAnnounced:
    """**訂正を使えば先読みになる**（事前登録 §8）。"""

    _ANNOUNCED = [
        (dt.date(2015, 1, 10), dt.date(2015, 3, 30)),
        (dt.date(2015, 6, 10), dt.date(2015, 9, 29)),
    ]

    def test_an_earlier_announcement_counts(self) -> None:
        assert dt.date(2015, 3, 30) in known_ex_dates(self._ANNOUNCED, dt.date(2015, 3, 30))

    def test_a_later_announcement_does_not(self) -> None:
        """**後から出たものは見えていない。**"""
        assert dt.date(2015, 9, 29) not in known_ex_dates(self._ANNOUNCED, dt.date(2015, 3, 30))

    def test_nothing_announced_is_not_an_exception(self) -> None:
        assert known_ex_dates(None, dt.date(2015, 3, 30)) == set()


_INDEX = pd.bdate_range("2012-07-02", "2019-12-31", name="date")
_BARS = len(_INDEX)


def _at(when: str) -> int:
    """その日の位置。**盤面の仕込みを日付で書けるようにする。**"""
    return int(_INDEX.get_loc(pd.Timestamp(when)))


def _prices(seed: int, gaps: tuple[int, ...] = (), merger: int | None = None) -> pd.DataFrame:
    """乱数歩行に、下窓を決め打ちの位置で仕込む。

    **定数の足を置かない**（`CLAUDE.md`）——散らばりが 0 だと標準誤差も 0 に
    なる。
    """
    rng = np.random.default_rng(seed)
    close = 1_000.0 * np.exp(np.cumsum(rng.normal(0.0, 0.01, _BARS)))
    if merger is not None:
        close[merger:] *= 0.001
    # **寄り付きを終値と同じにしない。** 同じにすると窓＝その日のリターンに
    # なり、乱数歩行が勝手に 3% の窓を開ける（1,950本で数件出た）。**足場の
    # せいで落ちると、原因を探す時間が要る**（`CLAUDE.md`）。
    opens = close.copy()
    opens[1:] = close[:-1] * (1.0 + rng.normal(0.0, 0.002, _BARS - 1))
    for position in gaps:
        opens[position] = close[position - 1] * (1.0 - GAP_DOWN - 0.02)
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


def _database(count: int = 6, gaps=(), merger=None) -> tuple[Database, list[str]]:
    database = Database("sqlite:///:memory:")
    database.create_all()
    symbols = [f"{1400 + index:04d}" for index in range(count)]
    with database.session() as session:
        repo = PriceRepository(session)
        for index, symbol in enumerate(symbols):
            repo.upsert_prices(symbol, _prices(seed=index, gaps=gaps, merger=merger), market="JP")
    return database, symbols


_IS_GAP = "2015-03-30"
_IS_DAYS = ("2015-03-30", "2015-09-29", "2016-05-31", "2017-02-28")
_OOS_GAP = "2019-03-28"


class TestCollectingTheEvents:
    def test_it_finds_the_gaps_in_both_halves(self) -> None:
        database, symbols = _database(gaps=(_at(_IS_GAP), _at(_OOS_GAP)))

        found = build_events(database, {}, symbols=symbols)

        assert [when for _symbol, when in found.events] == [dt.date(2015, 3, 30)] * len(symbols)
        assert found.days_is == 1
        assert found.days_oos == 1
        assert found.events_oos == len(symbols)

    def test_an_ex_date_is_excluded_and_counted(self) -> None:
        """**3% の下窓は、権利落ちがまさにそう見える。**"""
        database, symbols = _database(gaps=(_at(_IS_GAP),))
        announced = {symbol: [(dt.date(2015, 1, 10), dt.date(2015, 3, 30))] for symbol in symbols}

        found = build_events(database, announced, symbols=symbols)

        assert found.events == []
        assert found.excluded_ex_date == len(symbols)
        assert found.excluded_broken == 0

    def test_an_ex_date_announced_later_does_not_exclude(self) -> None:
        """**この検査が落ちる条件を、実際に1つ作る。** 先読みを使わない。"""
        database, symbols = _database(gaps=(_at(_IS_GAP),))
        announced = {symbol: [(dt.date(2015, 4, 10), dt.date(2015, 3, 30))] for symbol in symbols}

        found = build_events(database, announced, symbols=symbols)

        assert found.events
        assert found.excluded_ex_date == 0

    def test_a_merger_is_excluded_and_counted_separately(self) -> None:
        """**外した件数を別々に数える。** まとめると、どちらで落ちたか分からない。"""
        where = _at(_IS_GAP)
        database, symbols = _database(gaps=(where,), merger=where + 3)

        found = build_events(database, {}, symbols=symbols)

        assert found.events == []
        assert found.excluded_broken == len(symbols)
        assert found.excluded_ex_date == 0

    def test_a_gap_before_the_window_is_not_collected(self) -> None:
        """**IS は 2013-01 から。** `ExDate` が 2012-12 からしか無い（§6）。"""
        database, symbols = _database(gaps=(_at("2012-09-28"),))

        found = build_events(database, {}, symbols=symbols)

        assert found.events == []

    def test_the_warning_fires_when_nothing_was_excluded(self) -> None:
        """**0 を「外した」と読まない。** 原本が無いのか、調整済みなのか。"""
        database, symbols = _database(gaps=(_at(_IS_GAP), _at(_OOS_GAP)))

        found = build_events(database, {}, symbols=symbols)

        assert any("権利落ちで外した件数が 0" in line for line in found.warnings())

    def test_an_empty_universe_is_refused(self) -> None:
        database = Database("sqlite:///:memory:")
        database.create_all()

        with pytest.raises(ValueError, match="銘柄が1つも無い"):
            build_events(database, {}, symbols=[])

    def test_the_summary_says_days_not_only_events(self) -> None:
        """**独立な観測は日である**（#5 で 1,827 件を 831 日と数え違えた）。"""
        database, symbols = _database(gaps=(_at(_IS_GAP), _at(_OOS_GAP)))

        found = build_events(database, {}, symbols=symbols)

        assert "日" in found.summary()


class TestTheLiquidityFilterIsTheSharedOne:
    def test_a_thin_name_is_dropped(self) -> None:
        closes = np.full(40, 100.0)
        volumes = np.full(40, 1.0)

        assert not liquid_bars(closes, volumes, 1e8).any()

    def test_a_liquid_one_is_not(self) -> None:
        closes = np.full(40, 1_000.0)
        volumes = np.full(40, 500_000.0)

        assert liquid_bars(closes, volumes, 1e8)[-1]


class TestTheCommandRunsOnARealDatabase:
    """**本物のコマンドを、中身の入った DB で1本通す。**"""

    def test_it_runs_all_the_way_through(self, tmp_path, monkeypatch) -> None:
        import gzip
        import pathlib

        from stock_ai import cli
        from stock_ai.data.jquants_archive import MANIFEST, MANIFEST_COLUMNS
        from stock_ai.database import engine

        monkeypatch.setattr(engine, "DATA_DIR", tmp_path)
        symbols = [f"{1400 + index:04d}" for index in range(40)]
        database = Database(f"sqlite:///{tmp_path / 'stock_ai.db'}")
        database.create_all()
        with database.session() as session:
            repo = PriceRepository(session)
            repo.upsert_prices("1306", _prices(seed=999), market="JP")
            for index, symbol in enumerate(symbols):
                repo.upsert_prices(
                    symbol,
                    # **1日だけだと `len(values)` が 1 で手前で終わる。**
                    # 散らばりを測るには日が要る——**到達しない疎通確認を
                    # 「疎通した」と読まない。**
                    _prices(seed=index, gaps=tuple(_at(day) for day in _IS_DAYS + (_OOS_GAP,))),
                    market="JP",
                )

        # **権利落ちの原本を1本置く。** 無いとコマンドは手前で止まる。
        from tests.test_jquants_dividend import TestHowManyExDatesTheArchiveCanSupply as Rows

        key = "fins/dividend/dividend_2015.csv.gz"
        target = pathlib.Path(tmp_path) / key
        target.parent.mkdir(parents=True, exist_ok=True)
        body = Rows._rows({"Code": "14000", "ExDate": "2015-06-29", "RefNo": "1"})
        target.write_bytes(gzip.compress(body.encode("utf-8")))
        (pathlib.Path(tmp_path) / MANIFEST).write_text(
            ",".join(MANIFEST_COLUMNS) + "\n" + f"/{key},1,1,x,,2026-09-19\n",
            encoding="utf-8",
        )

        cli.gap_fill_power(archive=str(tmp_path), benchmark="1306")

    def test_it_stops_when_there_are_no_ex_dates(self, tmp_path, monkeypatch) -> None:
        """**外さずには測らない。** 手前で止まる側も通す。"""
        import typer

        from stock_ai import cli
        from stock_ai.database import engine

        monkeypatch.setattr(engine, "DATA_DIR", tmp_path)

        with pytest.raises(typer.Exit):
            cli.gap_fill_power(archive=str(tmp_path), benchmark="1306")


class TestTheGateIsTheSharedOne:
    """**同じ管の §0 を2つ書けば、片方が緩む。**"""

    def test_the_command_calls_the_shared_report(self) -> None:
        import inspect

        from stock_ai import cli

        body = inspect.getsource(cli.gap_fill_power)

        assert "_event_gate(" in body

    def test_every_event_command_calls_it(self) -> None:
        """**3つ目の写しが `margin_power` に残っていた**（2026-09-20）。

        しかも `_events_needed` という**別の「要る件数」ヘルパー**を持って
        いて、**同語反復の行をそのまま抱えていた**——`_event_gate` のほうだけ
        直したので、**片方だけ緩んでいた。**
        """
        import inspect

        from stock_ai import cli

        for command in (cli.revision_power, cli.margin_power, cli.gap_fill_power):
            assert "_event_gate(" in inspect.getsource(command), command.__name__

    def test_there_is_no_second_needed_table_for_this_pipe(self) -> None:
        """**イベント型の表は1つだけ。** 2つあれば、片方が古くなる。

        `power-gate` にも同じ題の表が在るが、**あちらは見込みの帯（下限・
        中央・上限）を並べている**——「何段階か並べる表」で、同語反復の行は
        入っていない。**別物なので数えない。**
        """
        import pathlib as _pathlib

        body = (
            _pathlib.Path(__file__).resolve().parent.parent / "src" / "stock_ai" / "cli.py"
        ).read_text(encoding="utf-8")

        assert "_events_needed" not in body
        assert body.count("この設計で検出するのに要るイベント日数") == 1

    def test_no_table_ladders_over_the_detectable_difference(self) -> None:
        """**どの表も、検出できる差を「検出したい効果」に入れない。**

        入れると、答えが必ず「ちょうど足りる」になる行ができる。
        """
        import inspect

        from stock_ai import cli

        for command in (cli._event_gate, cli.power_gate):
            body = inspect.getsource(command)
            ladder = body[body.index("for effect") :] if "for effect" in body else ""
            ladder += body[body.index("for annual") :] if "for annual" in body else ""

            assert "detectable" not in ladder.split("console.print")[0], command.__name__

    def test_the_short_side_is_flipped_by_the_caller(self) -> None:
        """**符号の反転は呼ぶ側で1箇所だけ。** 表示の札は別に渡す。"""
        import inspect

        from stock_ai import cli

        body = inspect.getsource(cli.margin_power)

        assert "-value - COST_ROUND_TRIP" in body
        assert 'side="ショート"' in body

    def test_the_floor_is_three_times_the_cost(self) -> None:
        """**#5・#8 と同じ規則。** 同じ管なら線の置き方も揃える。"""
        import inspect

        from stock_ai import cli

        assert "committed=3 * COST_ROUND_TRIP" in inspect.getsource(cli.gap_fill_power)

    def test_the_periods_are_days_not_events(self) -> None:
        """**#5 は 1,827 件を 831 日と数え違えた。**"""
        import inspect

        from stock_ai import cli

        body = inspect.getsource(cli.gap_fill_power)

        assert "periods=found.days_oos" in body
        assert "periods=found.events_oos" not in body


def test_the_windows_match_the_preregistration() -> None:
    """**事前登録が固定した値であること。**"""
    assert pytest.approx(0.03) == GAP_DOWN
    assert HOLDING == 20  # noqa: PLR2004 - #5 と同じ物差し
    assert dt.date(2013, 1, 1) == IS_FROM
    assert dt.date(2017, 12, 31) == IS_END
    assert dt.date(2018, 1, 1) == OOS_FROM


class TestTheInflationMatchesWhatIsSubtracted:
    """**引く相手を替えたら数字が動いた**（0.94 → 1.09、どちらも400回）。

    **測った条件と違う条件の数字を当てない。** そして `_event_inflation` は
    **文字列**を取る——`UniverseBenchmark` を渡して落ちた（2026-09-19）。
    **中身の入った DB で1本通していなければ、本番まで出て行っていた。**
    """

    def test_the_command_names_the_universe(self) -> None:
        import inspect

        from stock_ai import cli

        assert '_event_inflation("universe")' in inspect.getsource(cli.gap_fill_power)

    def test_the_helper_refuses_something_that_is_not_a_mode(self) -> None:
        """**この検査が落ちる条件を、実際に1つ作る。**"""
        import typer

        from stock_ai import cli

        with pytest.raises(typer.BadParameter):
            cli._event_inflation("1306")

    def test_the_two_modes_differ(self) -> None:
        from stock_ai import cli

        assert cli._event_inflation("universe") != cli._event_inflation("index")


class TestTheCountsAreAllInTheSameUnit:
    """**同じ文の中で単位を変えない**（`CLAUDE.md`、2026-09-19 に3度目）。

    「権利落ちで外した 4,015 件、不連続で外した 216 件、流動性で外した
    **13,078,937 銘柄日**」——最後だけ全銘柄日を数えていた。
    """

    def test_the_liquidity_drop_is_counted_in_events(self) -> None:
        """**下窓だったが外した件数。** 足の数ではない。"""
        where = _at(_IS_GAP)
        database = Database("sqlite:///:memory:")
        database.create_all()
        symbols = ["1400"]
        with database.session() as session:
            repo = PriceRepository(session)
            frame = _prices(seed=1, gaps=(where,))
            frame[VOLUME] = 1.0  # 日商が足りない
            repo.upsert_prices("1400", frame, market="JP")

        found = build_events(database, {}, symbols=symbols)

        assert found.events == []
        # **1件。** 直す前は足の数（約1,900）だった。
        assert found.thin == 1

    def test_a_liquid_name_drops_nothing(self) -> None:
        """**この検査が落ちる条件を、実際に1つ作る。**"""
        database, symbols = _database(count=1, gaps=(_at(_IS_GAP),))

        found = build_events(database, {}, symbols=symbols)

        assert found.thin == 0

    def test_the_summary_does_not_say_symbol_days(self) -> None:
        database, symbols = _database(count=1, gaps=(_at(_IS_GAP),))

        found = build_events(database, {}, symbols=symbols)

        assert "銘柄日" not in found.summary()


class TestTheYearsColumnUsesTheSameUnitAsThePeriods:
    """`periods_needed` が返すのは**イベント日**である。**件数で割らない。**

    2,113日 ÷ 6,313件/年 = 0.33 で「0年」と出ていた（2026-09-19）。

    **この教室の最初の版は、直した先を取り違えていた**（2026-09-20）。
    「率は呼ぶ側に作らせない」まではよかったが、**`_event_gate` の中で
    `len(values) / span_years`（IS の率）を作っていることを、そのまま
    assert していた。** 名前は「`periods` と同じ単位で数える」と言って
    いるのに、**中身は逆を固定していた。**

    `periods` は OOS のイベント日なので、率も OOS から作る。
    """

    def test_the_rate_comes_from_the_judgement_window(self) -> None:
        """**推定に使った標本の件数から率を作らない。**"""
        import inspect

        from stock_ai import cli

        body = inspect.getsource(cli._event_gate)

        assert "len(values) /" not in body
        assert "power.requirement" in body or "requirement," in body

    def test_both_callers_pass_the_judgement_span(self) -> None:
        import inspect

        from stock_ai import cli

        for command in (cli.gap_fill_power, cli.revision_power):
            body = inspect.getsource(command)

            assert "period_years=" in body, command.__name__
            assert "per_year=" not in body, command.__name__
            assert "span_years=" not in body, command.__name__

    def test_two_effects_that_display_the_same_are_one_row(self) -> None:
        """**1.20% が2行並んでいた。** 表示して同じなら1行。

        **集合で持つ。** 丸めてから入れるので、表示が同じものは1つになる。
        """
        import inspect

        from stock_ai import cli

        body = inspect.getsource(cli._event_gate)

        assert "targets = {round(committed, 4)}" in body
        assert "targets.add(round(floor_estimate, 4))" in body


class TestTheNeededTableSaysSomething:
    """**検出できる差を「検出したい効果」に入れない**（2026-09-20、ユーザーが発見）。

    それに要る期数は、定義上いま在る期数そのものである——**答えが必ず
    「ちょうど足りる」になる行。** `CLAUDE.md`「落ちようのない検査を『合格』と
    読まない」の表版である。

    #15 では線（1.2%）と検出できる差（1.2043%）が偶然ほぼ一致し、**同じ
    1.20% が2行並んで「あと4件」に読めた。** 閉じた理由は向きであって、
    期数ではない。
    """

    @staticmethod
    def _run(values: list[float], capsys) -> str:
        from stock_ai import cli

        cli._event_gate(
            values,
            periods=2_109,
            target=3.30,
            committed=0.012,
            holding=20,
            reach="ためし",
            period_years=8.67,
        )
        return capsys.readouterr().out

    def test_the_detectable_difference_is_not_a_target(self) -> None:
        import inspect

        from stock_ai import cli

        body = inspect.getsource(cli._event_gate)

        assert "for value in (committed, detectable)" not in body
        assert "targets = {round(committed, 4)}" in body

    def test_a_wrong_sign_gets_no_table(self, capsys) -> None:
        """**向きが逆なら、期数の話ではない。**"""
        rng = np.random.default_rng(0)
        values = [float(value) - 0.02 for value in rng.normal(0.0, 0.05, 400)]

        out = self._run(values, capsys)

        assert "期数の問題ではない" in out
        assert "これは「あと少し」という意味ではない" in out
        assert "検出したい効果" not in out

    def test_a_small_positive_effect_gets_the_table(self, capsys) -> None:
        """**この検査が落ちる条件を、実際に1つ作る。** 正なら表が出る。"""
        rng = np.random.default_rng(0)
        values = [float(value) + 0.002 for value in rng.normal(0.0, 0.05, 400)]

        out = self._run(values, capsys)

        assert "検出したい効果" in out
        assert "期数の問題ではない" not in out

    def test_the_committed_line_is_always_one_row(self, capsys) -> None:
        rng = np.random.default_rng(0)
        values = [float(value) + 0.002 for value in rng.normal(0.0, 0.05, 400)]

        out = self._run(values, capsys)

        assert "1.20%" in out

    def test_the_table_carries_what_is_already_there(self, capsys) -> None:
        """**「いま在る」を表の中に置く。**

        外に置くと、読む側が別の標本の数字（脚注の「手元は IS 5.0年」）と
        突き合わせる——**それが「6.9年 要る」と「足りている」が並んだ形**
        である（2026-09-20、ユーザーが発見）。
        """
        rng = np.random.default_rng(0)
        values = [float(value) + 0.002 for value in rng.normal(0.0, 0.05, 400)]

        out = self._run(values, capsys)

        assert "いま在る" in out
        assert "8.7年" in out, "**判定に使う窓の年数が、表に出ていない。**"
