"""#8 の件数センサス（`stock_ai.backtest.margin_census`）。

**リターンを1つも計算しない。** ここで固定するのは、破っても例外が出ない点。

- 保有窓は**式から出る**。中央値を見てから選び直せない
- 解除日が**分からない**ことを、**長い**とも**短い**とも読まない
- 貸借区分は**その日の値**で引く。「いま貸借か」で引かない
- 名簿が届いていない日を「貸借でない」に数えない
"""

from __future__ import annotations

import datetime as dt

import pytest

from stock_ai.backtest.margin_census import (
    CENSORED_LIMIT,
    MAX_WINDOW,
    LendingIndex,
    _is_lending,
    census,
    sessions_between,
    spells,
    window_from,
)
from stock_ai.data.jquants_margin import MarginAlert

_CALENDAR = [dt.date(2020, 1, 6) + dt.timedelta(days=offset) for offset in range(0, 200)]


def _alert(symbol: str, day: dt.date, restricted: bool, regulation: str | None = None):
    return MarginAlert(
        symbol=symbol,
        published=day,
        as_of=day - dt.timedelta(days=1),
        reasons=frozenset({"Restricted"}) if restricted else frozenset(),
        regulation=regulation,
        short_outstanding=None,
        long_outstanding=None,
        short_change=None,
        long_change=None,
        ratio=None,
    )


def _run(days: list[tuple[int, bool]], symbol: str = "1301"):
    return [_alert(symbol, _CALENDAR[offset], flag) for offset, flag in days]


class TestTheWindowComesFromTheFormula:
    """**中央値を見てから窓を選び直さない。** 式が関数になっている。"""

    def test_a_short_spell_gives_a_short_window(self) -> None:
        assert window_from(5) == 5

    def test_a_long_spell_is_capped(self) -> None:
        """**解除されない銘柄が多いと窓が発散する。** 上限で止める。"""
        assert window_from(400) == MAX_WINDOW

    def test_an_unmeasurable_median_falls_back_to_the_cap(self) -> None:
        assert window_from(None) == MAX_WINDOW

    def test_the_window_is_never_zero(self) -> None:
        """**0営業日の保有は売買ではない。**"""
        assert window_from(0) == 1


class TestASpellIsOneRegulationNotManyDays:
    def test_the_flag_going_up_starts_a_spell(self) -> None:
        found = spells(_run([(0, False), (1, True), (2, True)]))

        assert len(found) == 1
        assert found[0].onset == _CALENDAR[1]

    def test_the_flag_coming_down_ends_it(self) -> None:
        found = spells(_run([(0, False), (1, True), (2, True), (3, False)]))

        assert found[0].released == _CALENDAR[3]
        assert not found[0].censored

    def test_the_release_code_also_ends_it(self) -> None:
        """`TSEMrgnRegCls` の ``101`` は規制解除である。**数として扱わない。**"""
        alerts = _run([(0, False), (1, True)])
        alerts.append(_alert("1301", _CALENDAR[2], True, regulation="101"))

        found = spells(alerts)

        assert found[0].released == _CALENDAR[2]

    def test_a_symbol_that_vanishes_is_censored_not_resolved(self) -> None:
        """**分からないのであって、長いのでも短いのでもない。**"""
        found = spells(_run([(0, False), (1, True), (2, True)]))

        assert found[0].censored
        assert found[0].released is None
        assert found[0].last_seen == _CALENDAR[2]

    def test_the_first_observation_is_never_an_onset(self) -> None:
        """**取り込み開始日に人為的な山ができる。** 件数は増えて見栄えは良くなる。"""
        found = spells(_run([(0, True), (1, True)]))

        assert found == []

    def test_two_spells_on_one_symbol_are_counted_separately(self) -> None:
        found = spells(_run([(0, False), (1, True), (2, False), (3, True), (4, False)]))

        assert len(found) == 2
        assert [spell.onset for spell in found] == [_CALENDAR[1], _CALENDAR[3]]


class TestDaysAreCountedOnTheCalendar:
    def test_sessions_are_counted_not_calendar_days(self) -> None:
        """**暦日を混ぜると、年末年始の発動だけ窓が短くなる。**"""
        sparse = [_CALENDAR[0], _CALENDAR[5], _CALENDAR[10]]

        assert sessions_between(sparse[0], sparse[2], sparse) == 2

    def test_a_day_outside_the_calendar_is_unmeasurable(self) -> None:
        """**0 を返さない。** 0 は「同じ日」であって「暦に無い」ではない。"""
        assert sessions_between(dt.date(1990, 1, 1), _CALENDAR[0], _CALENDAR) is None


class TestLendingIsReadAsOfTheDay:
    """**2026-09-08 に踏んだ形。** 最後に見えた姿で引くと、例外は出ない。"""

    @staticmethod
    def _index() -> LendingIndex:
        return LendingIndex(
            {"1301": [(dt.date(2020, 1, 1), False), (dt.date(2021, 1, 1), True)]},
            (dt.date(2020, 1, 1), dt.date(2022, 1, 1)),
        )

    def test_before_the_change_it_was_not_lending(self) -> None:
        assert self._index()("1301", dt.date(2020, 6, 1)) is False

    def test_after_the_change_it_was(self) -> None:
        assert self._index()("1301", dt.date(2021, 6, 1)) is True

    def test_before_the_rosters_begin_it_is_unknown(self) -> None:
        """**`False` と `None` を混ぜない。** 名簿の無い期間が黙って消える。"""
        assert self._index()("1301", dt.date(2010, 1, 1)) is None

    def test_a_symbol_with_no_roster_row_is_unknown(self) -> None:
        assert self._index()("9999", dt.date(2021, 6, 1)) is None

    def test_no_rosters_at_all_means_unknown_rather_than_false(self) -> None:
        assert LendingIndex({}, None)("1301", dt.date(2021, 6, 1)) is None


class TestReadingTheLendingText:
    @pytest.mark.parametrize("text", ["貸借銘柄", "貸借", "2"])
    def test_lending_is_recognised(self, text: str) -> None:
        assert _is_lending(text) is True

    @pytest.mark.parametrize("text", ["信用銘柄", "その他", "1", "3"])
    def test_not_lending_is_recognised(self, text: str) -> None:
        assert _is_lending(text) is False

    @pytest.mark.parametrize("text", [None, "", "なにか新しい区分"])
    def test_anything_else_is_unknown(self, text) -> None:
        """**知らない中身を「貸借でない」と読まない。** 増えたときに黙って消える。"""
        assert _is_lending(text) is None


class TestTheCensusFillsTheTable:
    @staticmethod
    def _alerts() -> list[MarginAlert]:
        found: list[MarginAlert] = []
        for index, symbol in enumerate(["1301", "1302", "1303"]):
            found.append(_alert(symbol, _CALENDAR[0], False))
            found.append(_alert(symbol, _CALENDAR[1], True))
            found.append(_alert(symbol, _CALENDAR[2 + index], False))
        return found

    def test_it_counts_the_events_and_the_days(self) -> None:
        found = census(self._alerts(), _CALENDAR)

        assert found.events == 3
        assert found.days_with_events == 1
        assert found.by_year == {2020: 3}

    def test_the_median_release_drives_the_window(self) -> None:
        found = census(self._alerts(), _CALENDAR)

        assert found.release_days_median == 2
        assert found.window == 2

    def test_unknown_lending_is_counted_rather_than_dropped_silently(self) -> None:
        found = census(self._alerts(), _CALENDAR, lending_on=lambda symbol, on: None)

        assert found.after_lending == 0
        assert found.lending_unknown == 3
        assert any("貸借区分が読めなかった" in line for line in found.warnings())

    def test_the_liquidity_floor_is_reported_when_it_bites(self) -> None:
        found = census(
            self._alerts(),
            _CALENDAR,
            liquid_on=lambda symbol, on: symbol == "1301",
        )

        assert found.after_liquidity == 1
        assert any("流動性の下限" in line for line in found.warnings())

    def test_the_window_is_measured_after_the_filters(self) -> None:
        """**絞る前で測ると、使わないイベントが窓を決める。**"""
        found = census(
            self._alerts(),
            _CALENDAR,
            liquid_on=lambda symbol, on: symbol == "1303",
        )

        assert found.release_days_median == 3

    def test_censoring_is_warned_about_rather_than_hidden(self) -> None:
        """**中央値は解けたものだけで出している。** 短い側に寄っている。"""
        alerts = self._alerts()
        alerts.append(_alert("1304", _CALENDAR[0], False))
        alerts.append(_alert("1304", _CALENDAR[1], True))

        found = census(alerts, _CALENDAR)

        assert found.censored == 1
        assert found.censored_share > CENSORED_LIMIT
        assert any("短い側に寄っている" in line for line in found.warnings())

    def test_clustering_is_warned_about(self) -> None:
        """**同じ日に固まると独立な観測が減る。**"""
        found = census(self._alerts(), _CALENDAR)

        assert found.busiest_share == pytest.approx(1.0)
        assert any("上位1割の日" in line for line in found.warnings())

    def test_nothing_at_all_says_so_rather_than_returning_zeroes_quietly(self) -> None:
        found = census([], _CALENDAR)

        assert found.events == 0
        assert "数えていない" in found.summary()
        assert any("1件も無い" in line for line in found.warnings())


# --- 組み立てを1本通す ---------------------------------------------------------
#
# **部品だけでは足りない。** `factor_panel` は部品を13個テストしていたのに
# `build_panel` を一度も呼んでおらず、存在しない引数が本番まで出て行った。
# `composite-gate` でも、部品26本が緑のまま `Gate.reason` が本番まで出た。

_HEADER = "PubDate,AppDate,Code,PubReason,TSEMrgnRegCls,ShrtOut,LongOut,SLRatio\n"


def _row(day: dt.date, symbol: str, restricted: bool, regulation: str = "") -> str:
    reason = "{'Restricted': '%s', 'DailyPublication': '1'}" % ("1" if restricted else "0")
    return f'{day:%Y-%m-%d},{day:%Y-%m-%d},{symbol}0,"{reason}",{regulation},100,200,2.0\n'


def _archive(tmp_path, rows_by_file: dict[str, str]):
    """原本を1本ずつ書いて、目録を作る。**保存は展開せずにしてある。**"""
    import gzip

    from stock_ai.data.jquants_archive import ArchivedFile, path_for, write_manifest

    entries = {}
    for name, body in rows_by_file.items():
        key = f"markets/margin-alert/historical/{name}"
        target = path_for(tmp_path, key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(gzip.compress(body.encode("utf-8")))
        entries[key] = ArchivedFile(
            key=key,
            size=target.stat().st_size,
            bytes_written=target.stat().st_size,
            sha256="0" * 64,
            last_modified="",
            fetched_on=dt.date(2026, 9, 15),
        )
    write_manifest(tmp_path, entries)
    return tmp_path


class TestTheCensusRunsEndToEnd:
    @staticmethod
    def _prices(symbols: list[str], index):
        import numpy as np
        import pandas as pd

        from stock_ai.data.schema import ADJ_CLOSE, CLOSE, HIGH, LOW, OPEN, VOLUME
        from stock_ai.database.engine import Database
        from stock_ai.database.repository import PriceRepository

        database = Database("sqlite:///:memory:")
        database.create_all()
        with database.session() as session:
            repo = PriceRepository(session)
            for seed, symbol in enumerate(["1306", *symbols]):
                close = 1_000.0 * np.exp(
                    np.cumsum(np.random.default_rng(seed).normal(0.0, 0.01, len(index)))
                )
                repo.upsert_prices(
                    symbol,
                    pd.DataFrame(
                        {
                            OPEN: close,
                            HIGH: close,
                            LOW: close,
                            CLOSE: close,
                            ADJ_CLOSE: close,
                            VOLUME: [1_000_000.0] * len(index),
                        },
                        index=index,
                    ),
                    market="JP",
                )
        return database

    def _run(self, tmp_path, monkeypatch, columns: str = "200"):
        import pandas as pd
        from typer.testing import CliRunner

        from stock_ai import cli

        # **端末の幅で折り返す。** 表題の途中で改行が入ると、綴りが割れて
        # 手元で緑・CI で赤になる（2026-09-15、PR #100。幅40で再現した）。
        # ここでは幅を固定して、描画ではなく**中身**を見る。
        monkeypatch.setenv("COLUMNS", columns)

        index = pd.bdate_range("2020-01-06", periods=60, name="date")
        days = [stamp.date() for stamp in index]
        body = _HEADER
        for symbol in ("1301", "1302"):
            body += _row(days[0], symbol, False)
            body += _row(days[1], symbol, True)
            body += _row(days[5], symbol, False)
        archive = _archive(tmp_path / "archive", {"2020/margin_202001.csv.gz": body})

        monkeypatch.setattr(cli, "Database", lambda *a, **k: self._prices(["1301", "1302"], index))
        return CliRunner().invoke(
            cli.app,
            [
                "margin-census",
                "--dir",
                str(archive),
                "--rosters",
                str(tmp_path / "rosters"),
            ],
        )

    def test_it_reads_the_originals_and_fills_the_table(self, tmp_path, monkeypatch) -> None:
        result = self._run(tmp_path, monkeypatch)

        assert result.exit_code == 0, result.output
        assert "件数センサス" in result.output
        assert "保有窓" in result.output
        assert "2020" in result.output

    def test_a_narrow_terminal_still_finishes(self, tmp_path, monkeypatch) -> None:
        """**手元の幅は CI の幅ではない。** 折り返しても落ちないこと。"""
        result = self._run(tmp_path, monkeypatch, columns="40")

        assert result.exit_code == 0, result.output

    def test_no_rosters_is_said_out_loud(self, tmp_path, monkeypatch) -> None:
        """**貸借区分で絞れていないことを、黙って通さない。**"""
        result = self._run(tmp_path, monkeypatch)

        assert "名簿が1枚も無い" in result.output

    def test_a_missing_archive_stops_rather_than_reporting_zero(self, tmp_path) -> None:
        from typer.testing import CliRunner

        from stock_ai.cli import app

        result = CliRunner().invoke(app, ["margin-census", "--dir", str(tmp_path / "nope")])

        assert result.exit_code == 1
        assert "§2 の件数センサス" not in result.output


class TestTheSplitIsByPeriodNotByCount:
    """**§0 の期数は OOS のイベント数である。** 全期間ではない。

    全期間で計算すると、IS で推定した効果を全期間の検出力と比べることになる
    ——**尺度の違う2つを組み合わせる、繰り返し踏んでいる形そのもの。**
    """

    @staticmethod
    def _alerts() -> list[MarginAlert]:
        """前半に1件、後半に3件。**件数で割れば 2 対 2 になる形。**"""
        found: list[MarginAlert] = []
        plan = [("1301", 0, 1), ("1302", 40, 41), ("1303", 42, 43), ("1304", 44, 45)]
        for symbol, off, on in plan:
            found.append(_alert(symbol, _CALENDAR[off], False))
            found.append(_alert(symbol, _CALENDAR[on], True))
            found.append(_alert(symbol, _CALENDAR[on + 2], False))
        return found

    def test_the_split_follows_the_calendar_not_the_events(self) -> None:
        found = census(self._alerts(), _CALENDAR)

        assert found.events_is == 1
        assert found.events_oos == 3

    def test_the_two_halves_add_up_to_what_survived_the_filters(self) -> None:
        """**足して合わないなら、どこかで落としている。**"""
        found = census(self._alerts(), _CALENDAR, liquid_on=lambda s, on: s != "1304")

        assert found.events_is + found.events_oos == found.after_liquidity

    def test_an_explicit_split_is_honoured(self) -> None:
        found = census(self._alerts(), _CALENDAR, split_on=_CALENDAR[43])

        assert found.events_is == 3
        assert found.events_oos == 1

    def test_a_thin_out_of_sample_is_warned_about(self) -> None:
        """**1,000 件という線は 2026-09-05 に書いてある。**"""
        found = census(self._alerts(), _CALENDAR)

        assert any("OOS のイベント" in line for line in found.warnings())

    def test_the_lending_filter_is_warned_about_when_it_halves_the_set(self) -> None:
        found = census(self._alerts(), _CALENDAR, lending_on=lambda s, on: s == "1302")

        assert any("貸借に絞って" in line for line in found.warnings())


class TestTheEventReturnsFollowTheTradingRule:
    """§4 が固定した入り方——**D の翌営業日の寄付きで入り、N 日後の終値で降りる。**

    **D の終値では入れない。** 公表は 16:30 頃で、その日の引けには間に合わない。
    ここを1日早く置くと、**まだ公表されていない日の値動きを使う。**
    """

    @staticmethod
    def _database(
        index,
        closes: dict[str, list[float]],
        opens: dict[str, list[float]] | None = None,
    ):
        import pandas as pd

        from stock_ai.data.schema import ADJ_CLOSE, CLOSE, HIGH, LOW, OPEN, VOLUME
        from stock_ai.database.engine import Database
        from stock_ai.database.repository import PriceRepository

        database = Database("sqlite:///:memory:")
        database.create_all()
        with database.session() as session:
            repo = PriceRepository(session)
            for symbol, close in closes.items():
                start = (opens or {}).get(symbol, close)
                repo.upsert_prices(
                    symbol,
                    pd.DataFrame(
                        {
                            OPEN: start,
                            HIGH: close,
                            LOW: close,
                            CLOSE: close,
                            ADJ_CLOSE: close,
                            VOLUME: [1_000_000.0] * len(close),
                        },
                        index=index,
                    ),
                    market="JP",
                )
        return database

    def test_a_stock_that_falls_against_a_flat_market_gives_a_negative_excess(self) -> None:
        import pandas as pd

        from stock_ai.backtest.margin_census import event_returns

        index = pd.bdate_range("2020-01-06", periods=30, name="date")
        flat = [100.0] * 30
        falling = [100.0 if position <= 1 else 90.0 for position in range(30)]
        database = self._database(index, {"1306": flat, "1301": falling})

        values = event_returns(database, [("1301", index[0].date())], holding=5)

        assert len(values) == 1
        assert values[0] == pytest.approx(-0.10)

    def test_entry_is_the_day_after_not_the_event_day(self) -> None:
        """**公表日の引けに動いても、それは取れない。**

        イベント日 D の終値だけを叩き落とし、D+1 以降は水平にする。D の終値で
        入れていればその下げが入り、翌日の寄付きで入っていれば入らない。
        """
        import pandas as pd

        from stock_ai.backtest.margin_census import event_returns

        index = pd.bdate_range("2020-01-06", periods=30, name="date")
        flat = [100.0] * 30
        # D=index[3]。その日の終値だけ 80、翌日以降は 100 に戻る。
        shaped = [80.0 if position == 3 else 100.0 for position in range(30)]
        database = self._database(index, {"1306": flat, "1301": shaped})

        values = event_returns(database, [("1301", index[3].date())], holding=5)

        assert values[0] == pytest.approx(0.0)

    def test_the_same_day_is_one_equal_weighted_observation(self) -> None:
        """**まとめないと、発動が重なった日だけ重みが増える。**"""
        import pandas as pd

        from stock_ai.backtest.margin_census import event_returns

        index = pd.bdate_range("2020-01-06", periods=30, name="date")
        flat = [100.0] * 30
        down = [100.0 if position <= 1 else 90.0 for position in range(30)]
        up = [100.0 if position <= 1 else 110.0 for position in range(30)]
        database = self._database(index, {"1306": flat, "1301": down, "1302": up})

        values = event_returns(
            database,
            [("1301", index[0].date()), ("1302", index[0].date())],
            holding=5,
        )

        assert len(values) == 1
        assert values[0] == pytest.approx(0.0)

    def test_an_event_after_the_cut_is_not_used(self) -> None:
        """**OOS には1日も触れない。**"""
        import pandas as pd

        from stock_ai.backtest.margin_census import event_returns

        index = pd.bdate_range("2020-01-06", periods=30, name="date")
        flat = [100.0] * 30
        database = self._database(index, {"1306": flat, "1301": flat})

        early = index[0].date()
        late = index[10].date()
        values = event_returns(database, [("1301", early), ("1301", late)], holding=5, until=early)

        assert len(values) == 1

    def test_an_event_too_close_to_the_end_is_dropped_rather_than_shortened(self) -> None:
        """**窓が足りないイベントを短い窓で測らない。** 混ぜると窓が2つになる。"""
        import pandas as pd

        from stock_ai.backtest.margin_census import event_returns

        index = pd.bdate_range("2020-01-06", periods=10, name="date")
        flat = [100.0] * 10
        database = self._database(index, {"1306": flat, "1301": flat})

        values = event_returns(database, [("1301", index[8].date())], holding=5)

        assert values == []

    def test_a_window_of_zero_is_refused(self) -> None:
        import pandas as pd

        from stock_ai.backtest.margin_census import event_returns

        index = pd.bdate_range("2020-01-06", periods=10, name="date")
        database = self._database(index, {"1306": [100.0] * 10})

        with pytest.raises(ValueError):
            event_returns(database, [], holding=0)
