"""投資部門別売買状況から、週ごとの需給を作る。

ここで押さえるのは4つ。

1. **1観測は1週。** 原本が週に1行なので、こちらで決めることではない
2. **規模で割る。** 額そのものを使うと、20年で市場の大きさが変わる
3. **区分の名前は途中で変わる。** 在った区分を全部数えて返す——
   **無いことは、出力に出ない**
4. **同じ週が2回出たら足さない。** 後から公表されたほうを1つだけ採る
"""

from __future__ import annotations

import csv
import datetime as dt
import gzip
import io
import pathlib

import pytest

from stock_ai.data.jquants_archive import MANIFEST, MANIFEST_COLUMNS
from stock_ai.data.jquants_investor import SECTION, weekly_flows

SAMPLE = pathlib.Path("tests/fixtures/jquants_investor_types_sample.csv")
"""**実物である。** 2008-01 の配布ぶん、4週 × 4区分。"""


def _archive(tmp_path, body: str):
    key = "equities/investor-types/investor_types_sample.csv.gz"
    target = tmp_path / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(gzip.compress(body.encode("utf-8")))
    (tmp_path / MANIFEST).write_text(
        ",".join(MANIFEST_COLUMNS) + "\n" + f"/{key},1,1,x,,2026-09-21\n",
        encoding="utf-8",
    )
    return tmp_path


def _rows() -> tuple[list[str], list[dict[str, str]]]:
    reader = csv.DictReader(io.StringIO(SAMPLE.read_text(encoding="utf-8-sig")))
    return list(reader.fieldnames or []), list(reader)


def _body(rows: list[dict[str, str]], names: list[str]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=names, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return out.getvalue()


class TestOneObservationIsOneWeek:
    def test_it_reads_the_weeks_of_the_chosen_section(self, tmp_path) -> None:
        names, rows = _rows()

        found = weekly_flows(_archive(tmp_path, _body(rows, names)))

        assert len(found.weeks) == 4  # noqa: PLR2004 - fixture は4週
        assert [week.end for week in found.weeks] == sorted(week.end for week in found.weeks)

    def test_the_other_sections_are_not_mixed_in(self, tmp_path) -> None:
        """**4区分あるのに4週。** 混ぜたら16になる。"""
        names, rows = _rows()

        found = weekly_flows(_archive(tmp_path, _body(rows, names)))

        assert found.rows == 16  # noqa: PLR2004 - 4週 × 4区分
        assert len(found.sections) == 4  # noqa: PLR2004
        assert found.sections[SECTION] == 4  # noqa: PLR2004

    def test_the_share_is_the_balance_over_the_turnover(self, tmp_path) -> None:
        """**規模で割る。** 額そのものは20年で桁が変わる。"""
        names, rows = _rows()
        first = next(row for row in rows if row["Section"] == SECTION)

        found = weekly_flows(_archive(tmp_path, _body(rows, names)))

        assert found.weeks[0].foreign_share == pytest.approx(
            float(first["FrgnBal"]) / float(first["TotTot"])
        )
        assert found.weeks[0].individual_share == pytest.approx(
            float(first["IndBal"]) / float(first["TotTot"])
        )

    def test_the_publication_date_comes_back(self, tmp_path) -> None:
        """**先読みを外すのは呼ぶ側である。** 公表日を落とすと外せない。"""
        names, rows = _rows()

        found = weekly_flows(_archive(tmp_path, _body(rows, names)))

        assert found.weeks[0].published_on == dt.date(2008, 1, 16)
        assert found.weeks[0].published_on > found.weeks[0].end


class TestTheSectionsAreCountedWhateverTheyAreCalled:
    """**分からないものに名前を付けない。** 在った区分を全部数える。"""

    def test_a_missing_section_is_said_out_loud(self, tmp_path) -> None:
        """2022-04 の市場再編で名前が変わる。**黙って空を返さない。**"""
        names, rows = _rows()

        found = weekly_flows(_archive(tmp_path, _body(rows, names)), section="Prime")

        assert not found.weeks
        assert any("1つも無い" in line for line in found.warnings())
        # **在ったほうの名前を出す。** 出さないと、次に何を採ればよいか分からない。
        assert any(SECTION in line for line in found.warnings())

    def test_an_archive_with_nothing_says_so(self, tmp_path) -> None:
        (tmp_path / MANIFEST).write_text(",".join(MANIFEST_COLUMNS) + "\n", encoding="utf-8")

        found = weekly_flows(tmp_path)

        assert found.rows == 0
        assert any("1行も読めなかった" in line for line in found.warnings())


class TestTheSameWeekIsNotAdded:
    """**足すと2倍になる。** `latest_by_term` で踏んだのと同じ形である。"""

    def test_the_later_publication_wins(self, tmp_path) -> None:
        names, rows = _rows()
        first = next(row for row in rows if row["Section"] == SECTION)
        revised = dict(first)
        revised["PubDate"] = "2008-02-01"
        revised["FrgnBal"] = str(float(first["FrgnBal"]) * 2)

        found = weekly_flows(_archive(tmp_path, _body([*rows, revised], names)))

        assert len(found.weeks) == 4  # noqa: PLR2004 - 増えないこと
        assert found.duplicated == 1
        assert found.weeks[0].foreign_share == pytest.approx(
            float(revised["FrgnBal"]) / float(first["TotTot"])
        )

    def test_an_earlier_publication_does_not_win(self, tmp_path) -> None:
        """**両向きに置く。** 常に上書きする形でも緑にならないように。"""
        names, rows = _rows()
        first = next(row for row in rows if row["Section"] == SECTION)
        stale = dict(first)
        stale["PubDate"] = "2008-01-10"
        stale["FrgnBal"] = str(float(first["FrgnBal"]) * 2)

        found = weekly_flows(_archive(tmp_path, _body([*rows, stale], names)))

        assert found.weeks[0].foreign_share == pytest.approx(
            float(first["FrgnBal"]) / float(first["TotTot"])
        )


class TestTheWeeksAreCountedByYear:
    """**無いことは、出力に出ない。** どこから在るかを数える。"""

    def test_it_counts_by_year(self, tmp_path) -> None:
        names, rows = _rows()

        found = weekly_flows(_archive(tmp_path, _body(rows, names)))

        assert found.by_year() == [(2008, 4)]
        assert found.first == dt.date(2008, 1, 4)
        assert found.last == dt.date(2008, 1, 25)

    def test_a_week_without_turnover_is_counted_apart(self, tmp_path) -> None:
        """**割れなかった週を、読めた週に混ぜない。**"""
        names, rows = _rows()
        broken = [
            dict(row, TotTot="0") if row["Section"] == SECTION else row
            for row in rows  # 4週すべて
        ]

        found = weekly_flows(_archive(tmp_path, _body(broken, names)))

        assert not found.weeks
        assert found.no_turnover == 4  # noqa: PLR2004
        assert any("`TotTot` が 0" in line for line in found.warnings())
