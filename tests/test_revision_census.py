"""#5 の件数センサス（`stock_ai.backtest.revision_census`）。

**リターンを1つも計算しない。** ここで固定するのは、破っても例外が出ない点。

- 読めなかったものを **0 に落とさない**。読めていないことが「修正が無かった」に
  化ける
- **年度をまたいで比べない**。翌期の予想と比べても「修正」ではない
- **自分自身と比べない**。先に覚えてから比べると、変化が常に 0 になる
- **決算と同じ日は外す**。#2・#3 と同じ日付集合を使わないため
- 名前を**当てずっぽうで書かない**。1件も読めなかったときに、載っていた鍵を出す
"""

from __future__ import annotations

import datetime as dt

import pytest

from stock_ai.backtest.revision_census import (
    FORECAST_KEYS,
    REVISION_TYPE,
    UPWARD_MIN,
    census,
    find_upward,
)
from stock_ai.data.jquants_details import StatementDetail

_FY = "2025-03-31"


def _row(
    day: dt.date,
    symbol: str = "1301",
    doc_type: str = REVISION_TYPE,
    forecast: float | None = 100.0,
    fiscal: str | None = _FY,
    number: str = "1",
) -> StatementDetail:
    values: dict[str, str] = {}
    if forecast is not None:
        values[FORECAST_KEYS[0]] = str(forecast)
    if fiscal is not None:
        values["CurFYEn"] = fiscal
    return StatementDetail(
        symbol=symbol,
        disclosed_on=day,
        disclosed_at=None,
        number=number,
        doc_type=doc_type,
        period=None,
        consolidated=None,
        standard=None,
        values=values,
    )


_DAY = dt.date(2024, 5, 1)


def _later(offset: int) -> dt.date:
    return _DAY + dt.timedelta(days=offset)


class TestFindingTheUpwardRevisions:
    def test_a_ten_percent_raise_is_an_event(self) -> None:
        events, seen = find_upward(
            [
                _row(_DAY, doc_type="FYFinancialStatements_Consolidated_JP", forecast=100.0),
                _row(_later(10), forecast=110.0),
            ]
        )

        assert len(events) == 1
        assert events[0].change == pytest.approx(0.10)
        assert seen.upward == 1

    def test_a_small_raise_is_counted_but_not_an_event(self) -> None:
        """**±5% 未満は数えて外す。** 落とすと、母数が分からなくなる。"""
        events, seen = find_upward(
            [
                _row(_DAY, doc_type="FYFinancialStatements_Consolidated_JP", forecast=100.0),
                _row(_later(10), forecast=102.0),
            ]
        )

        assert events == []
        assert seen.too_small == 1

    def test_a_cut_is_counted_on_its_own(self) -> None:
        events, seen = find_upward(
            [
                _row(_DAY, doc_type="FYFinancialStatements_Consolidated_JP", forecast=100.0),
                _row(_later(10), forecast=80.0),
            ]
        )

        assert events == []
        assert seen.downward == 1

    def test_a_loss_forecast_turning_up_does_not_flip_the_sign(self) -> None:
        """**負の予想を分母にすると符号が反転する。** 絶対値で割る。

        −100 から −50 は**改善**である。素直に割ると (−50 − −100) / −100 = −0.5
        で、下方修正に数えられる。
        """
        events, _ = find_upward(
            [
                _row(_DAY, doc_type="FYFinancialStatements_Consolidated_JP", forecast=-100.0),
                _row(_later(10), forecast=-50.0),
            ]
        )

        assert len(events) == 1
        assert events[0].change == pytest.approx(0.5)

    def test_the_first_disclosure_is_not_an_event(self) -> None:
        """**原本の先頭は「そこで変わった」ではなく「そこから見え始めた」。**"""
        events, seen = find_upward([_row(_DAY, forecast=110.0)])

        assert events == []
        assert seen.no_previous == 1

    def test_a_different_fiscal_year_is_not_a_revision(self) -> None:
        """**年度をまたいだ比較は「修正」ではなく別の期の話である。**"""
        events, seen = find_upward(
            [
                _row(
                    _DAY,
                    doc_type="FYFinancialStatements_Consolidated_JP",
                    forecast=100.0,
                    fiscal="2024-03-31",
                ),
                _row(_later(10), forecast=200.0, fiscal="2025-03-31"),
            ]
        )

        assert events == []
        assert seen.no_previous == 1

    def test_a_revision_is_not_compared_with_itself(self) -> None:
        """**先に覚えてから比べると、変化が常に 0 になる。** 例外は出ない。"""
        events, seen = find_upward([_row(_DAY, forecast=100.0), _row(_later(10), forecast=150.0)])

        assert len(events) == 1
        assert seen.no_previous == 1

    def test_an_unreadable_forecast_is_counted_not_zeroed(self) -> None:
        """**0 に落とすと、読めていないことが「修正が無かった」に化ける。**"""
        events, seen = find_upward(
            [
                _row(_DAY, doc_type="FYFinancialStatements_Consolidated_JP", forecast=100.0),
                _row(_later(10), forecast=None),
            ]
        )

        assert events == []
        assert seen.no_forecast == 1

    def test_a_missing_fiscal_year_is_counted_on_its_own(self) -> None:
        events, seen = find_upward([_row(_DAY, fiscal=None)])

        assert events == []
        assert seen.no_fiscal_year == 1

    def test_the_keys_actually_present_are_recorded(self) -> None:
        """**「無い」のか「名前が違う」のかを分けられるようにする。**"""
        row = _row(_DAY, forecast=None)
        row.values["SomethingElse"] = "1"

        _events, seen = find_upward([row])

        assert "SomethingElse" in seen.keys_seen
        assert any("名前が違う" in line for line in seen.warnings())

    def test_a_statement_on_the_same_day_is_flagged(self) -> None:
        events, _ = find_upward(
            [
                _row(_DAY, doc_type="FYFinancialStatements_Consolidated_JP", forecast=100.0),
                _row(_later(10), forecast=120.0),
                _row(
                    _later(10),
                    doc_type="FYFinancialStatements_Consolidated_JP",
                    forecast=120.0,
                    number="2",
                ),
            ]
        )

        assert len(events) == 1
        assert events[0].on_statement_day


class TestTheCensusFillsTheTable:
    @staticmethod
    def _rows() -> list[StatementDetail]:
        found = [
            _row(_DAY, symbol=symbol, doc_type="FYFinancialStatements_Consolidated_JP")
            for symbol in ("1301", "1302", "1303")
        ]
        found.append(_row(_later(10), symbol="1301", forecast=120.0))
        found.append(_row(_later(200), symbol="1302", forecast=130.0))
        # 決算と同じ日に出た修正。**外す側。**
        found.append(_row(_later(300), symbol="1303", forecast=140.0))
        found.append(
            _row(
                _later(300),
                symbol="1303",
                doc_type="FYFinancialStatements_Consolidated_JP",
                forecast=140.0,
                number="2",
            )
        )
        return found

    def test_the_statement_day_revisions_are_taken_out(self) -> None:
        """**#2・#3 と同じ日付集合を使わない。** 再開の前提そのもの。"""
        found = census(self._rows())

        assert found.events == 3
        assert found.on_statement_day == 1
        assert found.standalone == 2

    def test_the_split_is_by_period_not_by_count(self) -> None:
        found = census(self._rows())

        assert found.events_is + found.events_oos == found.after_liquidity

    def test_the_liquidity_floor_is_reported_when_it_bites(self) -> None:
        found = census(self._rows(), liquid_on=lambda symbol, on: symbol == "1301")

        assert found.after_liquidity == 1
        assert any("流動性の下限" in line for line in found.warnings())

    def test_a_thin_out_of_sample_trips_the_stopping_rule(self) -> None:
        """**§10 の中止条件。** OOS が 1,000 件を割ったら設計を見直す。"""
        found = census(self._rows())

        assert any("§10 の中止条件" in line for line in found.warnings())

    def test_nothing_at_all_says_so_rather_than_returning_zeroes_quietly(self) -> None:
        found = census([])

        assert found.events == 0
        assert "数えていない" in found.summary()
        assert any("1件も無い" in line for line in found.warnings())

    def test_the_minimum_change_is_borrowed_not_invented(self) -> None:
        """**+5% はこの登録のために決めた数字ではない。**"""
        from stock_ai.backtest.forecast_revision import DEFAULT_MIN_CHANGE

        assert UPWARD_MIN == DEFAULT_MIN_CHANGE


# --- 組み立てを1本通す ---------------------------------------------------------
#
# **部品だけでは足りない。** `composite-gate` では部品26本が緑のまま
# `Gate.reason` という存在しない属性が本番まで出た。

_HEADER = "DiscDate,DiscTime,Code,DiscNo,DocType,FS\n"


def _csv(rows: list[tuple[dt.date, str, str, float, str]]) -> str:
    body = _HEADER
    for day, symbol, doc_type, forecast, number in rows:
        fs = "{'" + FORECAST_KEYS[0] + f"': '{forecast}', 'CurFYEn': '{_FY}'" + "}"
        body += f'{day:%Y-%m-%d},15:30,{symbol}0,{number},{doc_type},"{fs}"\n'
    return body


def _archive(tmp_path, body: str):
    import gzip

    from stock_ai.data.jquants_archive import ArchivedFile, path_for, write_manifest

    key = "fins/summary/historical/2024/fins_summary_202405.csv.gz"
    target = path_for(tmp_path, key)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(gzip.compress(body.encode("utf-8")))
    write_manifest(
        tmp_path,
        {
            key: ArchivedFile(
                key=key,
                size=target.stat().st_size,
                bytes_written=target.stat().st_size,
                sha256="0" * 64,
                last_modified="",
                fetched_on=dt.date(2026, 9, 15),
            )
        },
    )
    return tmp_path


class TestTheCensusRunsEndToEnd:
    @staticmethod
    def _database(symbols: list[str]):
        import numpy as np
        import pandas as pd

        from stock_ai.data.schema import ADJ_CLOSE, CLOSE, HIGH, LOW, OPEN, VOLUME
        from stock_ai.database.engine import Database
        from stock_ai.database.repository import PriceRepository

        index = pd.bdate_range("2024-01-01", periods=400, name="date")
        database = Database("sqlite:///:memory:")
        database.create_all()
        with database.session() as session:
            repo = PriceRepository(session)
            for seed, symbol in enumerate(symbols):
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
        from typer.testing import CliRunner

        from stock_ai import cli

        statement = "FYFinancialStatements_Consolidated_JP"
        rows = [
            (_DAY, "1301", statement, 100.0, "1"),
            (_later(10), "1301", REVISION_TYPE, 120.0, "1"),
            (_DAY, "1302", statement, 100.0, "1"),
            (_later(40), "1302", REVISION_TYPE, 130.0, "1"),
        ]
        archive = _archive(tmp_path / "archive", _csv(rows))
        monkeypatch.setenv("COLUMNS", columns)
        monkeypatch.setattr(cli, "Database", lambda *a, **k: self._database(["1301", "1302"]))
        return CliRunner().invoke(cli.app, ["revision-census", "--dir", str(archive)])

    def test_it_reads_the_originals_and_fills_the_table(self, tmp_path, monkeypatch) -> None:
        result = self._run(tmp_path, monkeypatch)

        assert result.exit_code == 0, result.output
        assert "件数センサス" in result.output
        assert "上方修正" in result.output

    def test_a_narrow_terminal_still_finishes(self, tmp_path, monkeypatch) -> None:
        """**手元の幅は CI の幅ではない。** 折り返しても落ちないこと。"""
        result = self._run(tmp_path, monkeypatch, columns="40")

        assert result.exit_code == 0, result.output

    def test_a_missing_archive_stops_rather_than_reporting_zero(self, tmp_path) -> None:
        from typer.testing import CliRunner

        from stock_ai.cli import app

        result = CliRunner().invoke(app, ["revision-census", "--dir", str(tmp_path / "nope")])

        assert result.exit_code == 1
        assert "件数センサス" not in result.output
