"""配当金情報（`/fins/dividend`）を読む。

Premium 専用で、20年前まで遡れる。

## 1銘柄1行ではない

**同じ公表日に、期の違う行が並ぶ。** 配布サンプルでは 2022-04-26 に3行あり、
`IFTerm` が `2022-09` / `2023-03` / `2022-03` である。

```
PubDate=2022-04-26  RefNo=...B00020  IFTerm=2022-09
PubDate=2022-04-26  RefNo=...B00021  IFTerm=2023-03
PubDate=2022-04-26  RefNo=...B00019  IFTerm=2022-03
```

**足すと年間配当が3倍になる。** 例外は出ないし、桁も変わらないので、
利回りが「やけに高い会社」として並ぶだけである。期ごとに1本にするには
:func:`latest_by_term` を通すこと。

## 訂正がある

同じ期について後から別の行が出る。**新しいほうだけを使う。** 公表日時が
同じときは `RefNo` の大きいほうを後とみなす——同じ日の中の順序を、他に
決める材料が無いためである。

## 符号は符号のまま持つ

`StatCode` / `IFCode` / `FRCode` / `CommSpecCode` の意味は、配布サンプル
からは分からない。**分からないものに名前を付けない。** 文字列のまま持って
おき、原本を落としたあとに実物の分布を見てから決める。

## 分割をまたぐ配当は直せない

1株あたりの配当は、分割の前後で尺度が変わる。**分割日をまたぐ区間の配当を
足すと、尺度の違う値を足すことになる。** これは倍率で直せる種類の間違いでは
ない（`docs/HYPOTHESES.md` に記録がある）ので、ここでは**名前を付けて置く**
だけにしてある——:func:`straddles_a_split` を見ること。
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Iterable

from stock_ai.data.jquants_bulk import records_from_csv
from stock_ai.data.jquants_details import parse_time
from stock_ai.data.jquants_margin import parse_date, parse_number
from stock_ai.data.universe import four_digit_code


@dataclasses.dataclass(frozen=True)
class Dividend:
    """配当の1行。**1銘柄1行ではなく、1銘柄1期1回の公表である。**"""

    symbol: str
    published_on: dt.date
    published_at: dt.time | None
    reference: str
    """`RefNo`。同じ日の複数行を区別できる唯一の列。"""

    term: str
    """`IFTerm`。**これが違えば別の配当である。足さない。**"""

    rate: float | None
    """`DivRate`。1株あたり。**分割の前後で尺度が変わる。**"""

    ordinary_rate: float | None
    special_rate: float | None
    ex_date: dt.date | None
    record_date: dt.date | None
    pay_date: dt.date | None
    status_code: str
    """`StatCode`。**意味は分からないので符号のまま持つ。**"""


def parse_dividends(payload: bytes) -> list[Dividend]:
    """配当の CSV を読む。**公表日か銘柄コードの無い行は落とす。**"""
    items: list[Dividend] = []
    for row in records_from_csv(payload):
        symbol = four_digit_code((row.get("Code") or "").strip())
        published = parse_date(row.get("PubDate"))
        if symbol is None or published is None:
            continue
        items.append(
            Dividend(
                symbol=symbol,
                published_on=published,
                published_at=parse_time(row.get("PubTime")),
                reference=(row.get("RefNo") or "").strip(),
                term=(row.get("IFTerm") or "").strip(),
                rate=parse_number(row.get("DivRate")),
                ordinary_rate=parse_number(row.get("CommDivRate")),
                special_rate=parse_number(row.get("SpecDivRate")),
                ex_date=parse_date(row.get("ExDate")),
                record_date=parse_date(row.get("RecDate")),
                pay_date=parse_date(row.get("PayDate")),
                status_code=(row.get("StatCode") or "").strip(),
            )
        )
    return items


def latest_by_term(dividends: Iterable[Dividend]) -> dict[tuple[str, str], Dividend]:
    """``(銘柄, 期)`` ごとに**最後の公表だけ**を残す。

    そのまま足すと、同じ公表日に並んだ期の違う行が全部入り、**年間配当が
    3倍になる。** 桁も変わらないので、利回りの高い会社として並ぶだけである。

    公表日時が同じときは `RefNo` の大きいほうを後とみなす。同じ日の中の順序を
    決める材料が他に無いためで、**推測であることを名前に残しておく**より、
    ここに書いておくほうが読める。
    """
    best: dict[tuple[str, str], Dividend] = {}
    for item in dividends:
        key = (item.symbol, item.term)
        current = best.get(key)
        if current is None or _order(item) > _order(current):
            best[key] = item
    return best


def _order(item: Dividend) -> tuple[dt.date, dt.time, str]:
    return (item.published_on, item.published_at or dt.time.min, item.reference)


def straddles_a_split(dividend: Dividend, split_days: Iterable[dt.date]) -> bool:
    """権利確定日と権利落ち日のあいだに分割日が入っているか。

    **入っていたら、1株あたりの配当は尺度が混ざっている。** 倍率を掛けて直せる
    種類の間違いではない——どちらの尺度の値なのかが決まらないためである。

    ここでは**直さずに名前を付ける。** 黙って使うと、分割した会社の配当が
    半分または倍で並ぶ。
    """
    start = dividend.ex_date
    end = dividend.record_date
    if start is None or end is None:
        return False
    low, high = (start, end) if start <= end else (end, start)
    return any(low <= day <= high for day in split_days)
