"""配当金情報の読み取り。

固定データは配布サンプル `sample_data_v2` の `Cash Dividend Data.csv`
**そのまま**である。
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from pathlib import Path

from stock_ai.data.jquants_dividend import (
    Dividend,
    latest_by_term,
    parse_dividends,
    straddles_a_split,
)

SAMPLE = Path(__file__).parent / "fixtures" / "jquants_dividend_sample.csv"


def dataclasses_replace(item: Dividend, **changes: object) -> Dividend:
    return dataclasses.replace(item, **changes)  # type: ignore[arg-type]


def _sample() -> list[Dividend]:
    return parse_dividends(SAMPLE.read_bytes())


def test_the_distributed_sample_parses() -> None:
    items = _sample()

    assert len(items) == 6
    assert {item.symbol for item in items} == {"8697"}
    assert items[0].published_on == dt.date(2022, 3, 22)
    assert items[0].published_at == dt.time(12, 37)


def test_one_publication_day_carries_several_terms() -> None:
    """**足すと年間配当が3倍になる。**

    2022-04-26 の3行は `2022-09` / `2023-03` / `2022-03` で、別の配当である。
    桁も変わらないので、利回りの高い会社として並ぶだけになる。
    """
    same_day = [item for item in _sample() if item.published_on == dt.date(2022, 4, 26)]

    assert len(same_day) == 3
    assert len({item.term for item in same_day}) == 3


def test_the_reference_number_is_what_separates_rows_on_one_day() -> None:
    """同じ公表日・同じ時刻の行を区別できる唯一の列である。"""
    same_day = [item for item in _sample() if item.published_on == dt.date(2022, 4, 26)]

    assert len({item.reference for item in same_day}) == 3


def test_keeping_one_row_per_term_collapses_the_double_count() -> None:
    by_term = latest_by_term(_sample())

    assert len(by_term) == len({item.term for item in _sample()})


def test_a_correction_replaces_the_earlier_row_for_the_same_term() -> None:
    """**新しいほうだけを使う。** 両方使うと同じ期を2回数える。"""
    early = Dividend(
        symbol="8697",
        published_on=dt.date(2022, 4, 26),
        published_at=dt.time(12, 38),
        reference="A",
        term="2023-03",
        rate=46.0,
        ordinary_rate=None,
        special_rate=None,
        ex_date=None,
        record_date=None,
        pay_date=None,
        status_code="1",
    )
    late = dataclasses_replace(early, published_on=dt.date(2022, 5, 10), reference="B", rate=50.0)

    assert latest_by_term([early, late])[("8697", "2023-03")].rate == 50.0
    assert latest_by_term([late, early])[("8697", "2023-03")].rate == 50.0


def test_a_same_day_correction_falls_back_to_the_reference_number() -> None:
    """同じ日の中の順序を決める材料が他に無い。**推測であることを試験に残す。**"""
    first = Dividend(
        symbol="8697",
        published_on=dt.date(2022, 4, 26),
        published_at=dt.time(12, 38),
        reference="202204261B00019",
        term="2022-03",
        rate=46.0,
        ordinary_rate=None,
        special_rate=None,
        ex_date=None,
        record_date=None,
        pay_date=None,
        status_code="1",
    )
    second = dataclasses_replace(first, reference="202204261B00021", rate=47.0)

    assert latest_by_term([first, second])[("8697", "2022-03")].rate == 47.0


def test_the_status_codes_stay_codes() -> None:
    """`StatCode` の意味はサンプルからは分からない。**名前を付けない。**"""
    assert {item.status_code for item in _sample()} == {"1", "2"}


def test_a_dividend_that_straddles_a_split_is_named_not_fixed() -> None:
    """**倍率で直せる種類の間違いではない。**

    どちらの尺度の値なのかが決まらない。黙って使うと、分割した会社の配当が
    半分または倍で並ぶ。
    """
    item = Dividend(
        symbol="8697",
        published_on=dt.date(2022, 3, 22),
        published_at=None,
        reference="A",
        term="2022-03",
        rate=46.0,
        ordinary_rate=None,
        special_rate=None,
        ex_date=dt.date(2022, 3, 30),
        record_date=dt.date(2022, 3, 31),
        pay_date=None,
        status_code="2",
    )

    assert straddles_a_split(item, [dt.date(2022, 3, 31)])
    assert not straddles_a_split(item, [dt.date(2022, 4, 15)])


def test_a_dividend_without_dates_is_not_reported_as_straddling() -> None:
    """**分からないことを「問題なし」にしない**——ここでは日付が無いだけで、
    分割をまたいでいないと言い切ってはいない。呼ぶ側が日付の有無を見る。
    """
    item = Dividend(
        symbol="8697",
        published_on=dt.date(2022, 3, 22),
        published_at=None,
        reference="A",
        term="2022-03",
        rate=46.0,
        ordinary_rate=None,
        special_rate=None,
        ex_date=None,
        record_date=None,
        pay_date=None,
        status_code="2",
    )

    assert not straddles_a_split(item, [dt.date(2022, 3, 31)])
    assert item.ex_date is None  # 呼ぶ側はこれを見る
