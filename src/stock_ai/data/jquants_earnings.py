"""決算発表**予定日**（`/fins/earnings-date`）を読む。

**この経路は最初こちらの一覧から抜けていた。** J-Quants 公式クライアントの
`BulkEndpoint` と突き合わせて見つかった（2026-09-06）。一括対応の20本のうち、
これ1本だけが漏れていた。

## 「直近のみ」ではない

プラン表の「決算発表予定日 … 直近のみ」は `eq earnings-calendar` のほうで、
**列の違う別のデータである**（`Earnings Calendar.csv` は `Date,Code,CoName,
FY,SectorNm,FQ,Section`）。こちらの配布サンプルは **2014年まで遡っている。**

## 何に使えるか

`PubDate`（予定日が公表された日）と `SchDate`（発表予定日）の2つが入っている。

**発表の前に、いつ発表されるかが分かる。** 決算ドリフト（PEAD）は結果を見て
から入るが、こちらは結果の要らない窓である。予定日は変わることがあるので、
**同じ期について後から出た行が前の行を打ち消す**——最後の1本だけを使う。

## 日付を取り違える形

| 罠 | 素直に読むとどうなるか |
|---|---|
| `SchDate` をイベント日に置く | 正しい。**ただし予定であって、実績ではない** |
| `PubDate` をイベント日に置く | 予定が公表された日。発表そのものではない |
| 予定の変更を足す | 同じ決算が2回あることになる |
| `FYE` で期をまとめる | **年が入っていない。** 12年ぶんが1件に潰れる |
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Iterable

from stock_ai.data.jquants_bulk import records_from_csv
from stock_ai.data.jquants_margin import parse_date
from stock_ai.data.universe import four_digit_code


@dataclasses.dataclass(frozen=True)
class EarningsDate:
    """決算発表予定日の1行。"""

    symbol: str
    published_on: dt.date
    """`PubDate`。**予定が公表された日。** 発表そのものではない。"""

    scheduled_on: dt.date | None
    """`SchDate`。**予定であって、実績ではない。**"""

    quarter: str
    """`FQName`（`2Q` など）。"""

    fiscal_year_end: str
    """`FYE`（`0331` など）。**文字列のまま。** 月日の符号であって数ではない。"""

    name: str


def parse_earnings_dates(payload: bytes) -> list[EarningsDate]:
    """予定日の CSV を読む。**公表日か銘柄コードの無い行は落とす。**"""
    items: list[EarningsDate] = []
    for row in records_from_csv(payload):
        symbol = four_digit_code((row.get("Code") or "").strip())
        published = parse_date(row.get("PubDate"))
        if symbol is None or published is None:
            continue
        items.append(
            EarningsDate(
                symbol=symbol,
                published_on=published,
                scheduled_on=parse_date(row.get("SchDate")),
                quarter=(row.get("FQName") or "").strip(),
                fiscal_year_end=(row.get("FYE") or "").strip(),
                name=(row.get("CoName") or "").strip(),
            )
        )
    return items


def by_symbol(items: Iterable[EarningsDate]) -> dict[str, list[EarningsDate]]:
    """銘柄ごとに、公表日の昇順で並べる。

    **予定の変更を1本にまとめる鍵は、この CSV の列だけでは決まらない。**

    最初は ``(銘柄, FYE, 四半期)`` でまとめる関数を書いた。配布サンプル48行に
    通すと**4件に潰れた。** `FYE` は `0331` のように**月日しか入っておらず、
    年が無い**ので、12年ぶんの同じ四半期が全部1つになる。

    例外は出ない。件数が減るだけで、しかも「重複を除いた」ように見える。

    まとめるなら、呼ぶ側が期を決める規則を持つこと。ここでは並べるだけにして
    ある——**決められないものを決めたことにしない。**
    """
    grouped: dict[str, list[EarningsDate]] = {}
    for item in items:
        grouped.setdefault(item.symbol, []).append(item)
    for rows in grouped.values():
        rows.sort(key=lambda row: (row.published_on, row.scheduled_on or dt.date.min))
    return grouped
