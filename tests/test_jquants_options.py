"""日経225オプションから、日ごとの ATM 予想変動率を作る。

ここで押さえるのは4つ。

1. **畳み方は1つだけ。** 限月の選び方を変えて良いほうを採ると、その時点で
   #10 と同じところに落ちる
2. **満期の週を外す。** 実データで、残存4日の限月を採ると原本の `BaseVol`
   と 2.1 ポイント食い違った
3. **別の切り口で同じ数字を出す。** `BaseVol` と突き合わせる。**一致は当たり
   前ではない**——限月の選び方を1日ずらすだけで崩れる
4. **`IV` は古い原本に入っていない。** 2008-05 では9列が全行で空である。
   **無いことは出力に出ない**ので、年ごとに数える
"""

from __future__ import annotations

import csv
import datetime as dt
import gzip
import io
import pathlib

import pytest

from stock_ai.data.jquants_archive import MANIFEST, MANIFEST_COLUMNS
from stock_ai.data.jquants_options import (
    AGREEMENT_BAND,
    MIN_TENOR_DAYS,
    daily_atm_iv,
)

SAMPLE = pathlib.Path("tests/fixtures/jquants_options_225_sample.csv")
"""**実物から作った fixture である。** 2026-01-05 の2限月ぶんと、2008-05。"""


def _archive(tmp_path, body: str):
    key = "derivatives/bars/daily/options/225/options_225_sample.csv.gz"
    target = tmp_path / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(gzip.compress(body.encode("utf-8")))
    (tmp_path / MANIFEST).write_text(
        ",".join(MANIFEST_COLUMNS) + "\n" + f"/{key},1,1,x,,2026-09-21\n",
        encoding="utf-8",
    )
    return tmp_path


def _rows() -> tuple[list[str], list[dict[str, str]]]:
    text = SAMPLE.read_text(encoding="utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    return list(reader.fieldnames or []), list(reader)


def _body(rows: list[dict[str, str]], names: list[str]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=names, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return out.getvalue()


class TestTheFoldIsFixedInAdvance:
    """**畳み方は、壁を測る前に1つに決めてある。**"""

    def test_it_takes_the_strike_nearest_the_underlying(self, tmp_path) -> None:
        """**行使価格は `UnderPx` にいちばん近いもの。**"""
        names, rows = _rows()
        found = daily_atm_iv(_archive(tmp_path, _body(rows, names)))

        assert dt.date(2026, 1, 5) in found.levels

    def test_it_skips_the_expiring_month(self, tmp_path) -> None:
        """**満期の週を外す。** fixture には残存4日の限月も入っている。

        2026-01-05 の限月は 2026-01-09（残存4日）と 2026-02-13（39日）。
        **前者を採ると `BaseVol` と食い違う。**
        """
        names, rows = _rows()
        directory = _archive(tmp_path, _body(rows, names))

        strict = daily_atm_iv(directory)
        loose = daily_atm_iv(directory, min_tenor=0)

        assert strict.levels[dt.date(2026, 1, 5)] != loose.levels[dt.date(2026, 1, 5)]

    def test_the_call_and_the_put_are_averaged(self, tmp_path) -> None:
        """**両側の平均。** 片方だけ採ると、スキューのぶんずれる。"""
        names, rows = _rows()
        directory = _archive(tmp_path, _body(rows, names))

        found = daily_atm_iv(directory)

        picked = [
            float(row["IV"])
            for row in rows
            if row["Date"] == "2026-01-05" and row["SQD"] == "2026-02-13"
        ]
        under = float(rows[0]["UnderPx"])
        strikes = {
            float(row["Strike"])
            for row in rows
            if row["Date"] == "2026-01-05" and row["SQD"] == "2026-02-13"
        }
        nearest = min(strikes, key=lambda value: (abs(value - under), value))
        both = [
            float(row["IV"])
            for row in rows
            if row["Date"] == "2026-01-05"
            and row["SQD"] == "2026-02-13"
            and float(row["Strike"]) == nearest
        ]
        assert len(both) == 2, "**fixture にコールとプットが揃っていない。**"  # noqa: PLR2004
        assert picked  # fixture が空でないこと
        assert found.levels[dt.date(2026, 1, 5)] == pytest.approx(sum(both) / 2)

    def test_the_minimum_tenor_is_a_week(self) -> None:
        """**定数を書き写さない。** 決めた値がそのまま出ていること。"""
        assert MIN_TENOR_DAYS == 7  # noqa: PLR2004 - 決めた値そのもの


class TestItAgreesWithTheOriginalsOwnLevel:
    """**別の切り口で同じ数字を出す。** 一致は当たり前ではない。"""

    def test_the_atm_matches_base_vol(self, tmp_path) -> None:
        """実データ（2026-01）では 19 日すべてで一致した。"""
        names, rows = _rows()

        found = daily_atm_iv(_archive(tmp_path, _body(rows, names)))

        assert found.checked == 1
        assert not found.disagreed
        assert found.levels[dt.date(2026, 1, 5)] == pytest.approx(
            float(rows[0]["BaseVol"]), abs=AGREEMENT_BAND / 10
        )

    def test_a_wrong_fold_is_caught(self, tmp_path) -> None:
        """**この検査が落ちる条件を、実際に1つ作る。**

        限月の選び方を満期の週に倒すと、`BaseVol` と食い違う。**落ちること
        を見ていない検査は、何も守っていない。**
        """
        names, rows = _rows()

        found = daily_atm_iv(_archive(tmp_path, _body(rows, names)), min_tenor=0)

        assert found.disagreed, "**畳み方を壊しても鳴らない。**"
        assert any("食い違う" in line for line in found.warnings())


class TestTheColumnsAreNotThereInTheOldOriginals:
    """**無いことは、出力に出ない。** 年ごとに数える。"""

    def test_the_old_month_makes_no_level(self, tmp_path) -> None:
        """2008-05 の原本では `IV` が全行で空である。"""
        names, rows = _rows()

        found = daily_atm_iv(_archive(tmp_path, _body(rows, names)))

        years = {year: (days, made) for year, days, made in found.by_year()}
        assert years[2008] == (1, 0), "**空の年から水準を作っている。**"
        assert years[2026][1] == 1

    def test_the_empty_rows_are_counted_and_said(self, tmp_path) -> None:
        """件数だけでなく、**警告に出る**こと。"""
        names, rows = _rows()

        found = daily_atm_iv(_archive(tmp_path, _body(rows, names)))

        assert found.rows > found.with_iv
        assert any("`IV` が空" in line for line in found.warnings())

    def test_an_archive_with_nothing_says_so(self, tmp_path) -> None:
        """**黙って空を返さない。**"""
        (tmp_path / MANIFEST).write_text(",".join(MANIFEST_COLUMNS) + "\n", encoding="utf-8")

        found = daily_atm_iv(tmp_path)

        assert found.rows == 0
        assert any("1行も読めなかった" in line for line in found.warnings())

    def test_a_day_with_only_the_expiring_month_is_named(self, tmp_path) -> None:
        """**残存が足りない日は、水準を作らずに数える。**"""
        names, rows = _rows()
        only_near = [
            row for row in rows if row["Date"] == "2026-01-05" and row["SQD"] == "2026-01-09"
        ]

        found = daily_atm_iv(_archive(tmp_path, _body(only_near, names)))

        assert not found.levels
        assert found.no_tenor == 1
        assert any("限月が" in line for line in found.warnings())
