"""決算発表予定日の読み取り。

**この経路は最初こちらの一覧から抜けていた。** J-Quants 公式クライアントの
`BulkEndpoint` と突き合わせて見つかった（2026-09-06）。一括対応の20本のうち、
これ1本だけが漏れていた。

固定データは配布サンプルの `Earnings Announcement Dates.csv` そのまま（cp932）。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from stock_ai.data.jquants_earnings import EarningsDate, by_symbol, parse_earnings_dates

SAMPLE = Path(__file__).parent / "fixtures" / "jquants_earnings_date_sample.csv"


def _sample() -> list[EarningsDate]:
    return parse_earnings_dates(SAMPLE.read_bytes())


def test_the_distributed_sample_parses() -> None:
    items = _sample()

    assert len(items) == 48
    assert items[0].symbol == "8697"
    assert items[0].published_on == dt.date(2014, 9, 26)
    assert items[0].scheduled_on == dt.date(2014, 10, 31)
    assert items[0].quarter == "2Q"


def test_this_endpoint_has_history_and_is_not_latest_only() -> None:
    """**プラン表の「直近のみ」は別のデータのことである。**

    `eq earnings-calendar` は列が違う（`Date,Code,CoName,FY,SectorNm,FQ,
    Section`）。こちらは2014年まで遡っている。
    """
    items = _sample()

    assert min(item.published_on for item in items).year == 2014
    assert max(item.published_on for item in items).year >= 2026


def test_the_schedule_is_known_before_the_announcement() -> None:
    """**発表の前に、いつ発表されるかが分かる。** 結果の要らない窓である。"""
    for item in _sample():
        assert item.scheduled_on is not None
        assert item.published_on < item.scheduled_on


def test_the_fiscal_year_end_carries_no_year() -> None:
    """**ここで実際に間違えた。**

    最初は ``(銘柄, FYE, 四半期)`` でまとめる関数を書いた。48行に通すと4件に
    潰れた。`FYE` は `0331` のように月日しか入っておらず、12年ぶんの同じ
    四半期が全部1つになる。例外は出ず、「重複を除いた」ように見える。
    """
    items = _sample()

    assert {item.fiscal_year_end for item in items} == {"0331"}
    collapsed = {(item.symbol, item.fiscal_year_end, item.quarter) for item in items}
    assert len(collapsed) == 4  # 48行が4件になる
    assert len(items) == 48


def test_the_fiscal_year_end_stays_a_string() -> None:
    """`0331` を数にすると `331` になる。"""
    assert _sample()[0].fiscal_year_end == "0331"


def test_grouping_only_sorts_and_does_not_decide_the_period() -> None:
    """**決められないものを決めたことにしない。**"""
    grouped = by_symbol(_sample())

    assert list(grouped) == ["8697"]
    assert len(grouped["8697"]) == 48
    published = [item.published_on for item in grouped["8697"]]
    assert published == sorted(published)


def test_a_row_without_a_publication_date_is_dropped() -> None:
    payload = b"PubDate,SchDate,FQName,FYE,Code,CoName\n,2014-10-31,2Q,0331,86970,x\n"

    assert parse_earnings_dates(payload) == []
