"""市場の残高・内訳（`/markets/...`）を読む。

`margin-alert`（日々公表信用残）は :mod:`stock_ai.data.jquants_margin` にある。
ここは残りの3つ。

| これ | 元 | 何が入っているか |
|---|---|---|
| :func:`parse_margin_interest` | `/markets/margin-interest` | 信用取引の**週末**残高 |
| :func:`parse_breakdown` | `/markets/breakdown` | 売買の内訳（新規・返済、金額・株数） |
| :func:`parse_short_positions` | `/markets/short-sale-report` | 空売り残高報告（**報告者ごと**） |

## 3つに共通する、黙って間違える形

**日付の意味が列ごとに違う。** どれも「いつ時点か」と「いつ知れたか」が別に
なっていて、前者をイベント日に置くと、まだ公表されていない日の値動きを使う。

- 週末残高の `Date` は**金曜時点**で、公表は第2営業日（通常は火曜）である
- 空売り残高の `CalcDate` は計算基準日で、`DiscDate` が公表日である

**週末残高がいちばん危ない。** ずれが2営業日あるうえ、`Date` という名前が
「その日のデータ」に見える。ここでは `as_of` と呼ぶ。

## 空売り残高は1銘柄1行ではない

**報告者ごとに1行である。** 1行目を取ると、あるヘッジファンド1社の残高が、
その銘柄の残高として出る。**例外は出ないし、値ももっともらしい。** 合計が
要るなら :func:`total_short_position` を通すこと。
"""

from __future__ import annotations

import dataclasses
import datetime as dt

from stock_ai.data.jquants_bulk import records_from_csv
from stock_ai.data.jquants_margin import parse_date, parse_number
from stock_ai.data.universe import four_digit_code

#: 週末残高が公表されるまでの日数（金曜時点 → 第2営業日）。出典は
#: `.claude/skills/jquants-cli-usage/references/data-update-schedule.md`。
#:
#: **これは下限である。** 祝日のある週は後ろ倒しになるので、実際の公表はこれ
#: より遅いことがある。イベント日に使うなら、`/markets/calendar`（取引カレン
#: ダー）と突き合わせること——**下限をそのまま使うと、祝日の週だけ未公表の
#: 値動きを使う。**
MARGIN_INTEREST_LAG_DAYS = 4

#: 貸借銘柄を表す `IssType`。出典は配布サンプルの実測値（`8697` が `2`）と
#: 立花の `sSinyouC`。**1（制度信用のみ）と混ぜない。**
ISS_TYPE_LENDING = "2"


@dataclasses.dataclass(frozen=True)
class MarginInterest:
    """信用取引の週末残高。**1銘柄・1週。**"""

    symbol: str
    as_of: dt.date
    """**金曜時点。** 公表はその第2営業日。`date` と呼ばないのは、
    「その日のデータ」に見えるからである。"""

    short_volume: float | None
    long_volume: float | None
    short_negotiable: float | None
    long_negotiable: float | None
    short_standardized: float | None
    long_standardized: float | None
    issue_type: str | None
    """`IssType`。**文字列のまま。** 区分の符号であって量ではない。"""

    @property
    def lending(self) -> bool:
        """貸借銘柄か。"""
        return self.issue_type == ISS_TYPE_LENDING


@dataclasses.dataclass(frozen=True)
class Breakdown:
    """売買内訳。**金額（円）と株数を別々に持つ。**

    同じ名前で単位が違う値を1つの入れ物に混ぜると、合計が通ってしまう。
    """

    symbol: str
    date: dt.date
    values_yen: dict[str, float]
    values_shares: dict[str, float]


@dataclasses.dataclass(frozen=True)
class ShortPosition:
    """空売り残高報告の**1行**。1銘柄ではなく**1報告者**である。"""

    symbol: str
    disclosed_on: dt.date
    calculated_on: dt.date | None
    """`CalcDate`。**`disclosed_on` より前。**"""

    reporter: str
    """報告者名。**同じ銘柄・同じ日に複数いる。**"""

    ratio_to_shares_outstanding: float | None
    shares: float | None


def earliest_publication(as_of: dt.date) -> dt.date:
    """週末残高が公表されうる最も早い日。**下限であって、確定ではない。**

    祝日のある週は後ろ倒しになる。**下限をそのままイベント日に使うと、祝日の
    週だけ未公表の値動きを使うことになる**——例外は出ないし、その週だけなので
    件数からも気付けない。取引カレンダーと突き合わせること。
    """
    return as_of + dt.timedelta(days=MARGIN_INTEREST_LAG_DAYS)


def parse_margin_interest(payload: bytes) -> list[MarginInterest]:
    """週末残高の CSV を読む。"""
    items: list[MarginInterest] = []
    for row in records_from_csv(payload):
        symbol = four_digit_code((row.get("Code") or "").strip())
        as_of = parse_date(row.get("Date"))
        if symbol is None or as_of is None:
            continue
        issue_type = (row.get("IssType") or "").strip()
        items.append(
            MarginInterest(
                symbol=symbol,
                as_of=as_of,
                short_volume=parse_number(row.get("ShrtVol")),
                long_volume=parse_number(row.get("LongVol")),
                short_negotiable=parse_number(row.get("ShrtNegVol")),
                long_negotiable=parse_number(row.get("LongNegVol")),
                short_standardized=parse_number(row.get("ShrtStdVol")),
                long_standardized=parse_number(row.get("LongStdVol")),
                issue_type=issue_type or None,
            )
        )
    return items


def parse_breakdown(payload: bytes) -> list[Breakdown]:
    """売買内訳の CSV を読む。

    列名は ``LongSellVa``（金額）と ``LongSellVo``（株数）のように、末尾の
    2文字だけが違う。**同じ辞書に入れない。** 単位の違う値が同じ名前で並ぶと、
    合計が通ってしまう。
    """
    items: list[Breakdown] = []
    for row in records_from_csv(payload):
        symbol = four_digit_code((row.get("Code") or "").strip())
        date = parse_date(row.get("Date"))
        if symbol is None or date is None:
            continue
        yen: dict[str, float] = {}
        shares: dict[str, float] = {}
        for column, text in row.items():
            if column in {"Date", "Code"} or column is None:
                continue
            value = parse_number(text)
            if value is None:
                continue
            if column.endswith("Va"):
                yen[column[:-2]] = value
            elif column.endswith("Vo"):
                shares[column[:-2]] = value
        items.append(Breakdown(symbol=symbol, date=date, values_yen=yen, values_shares=shares))
    return items


def parse_short_positions(payload: bytes) -> list[ShortPosition]:
    """空売り残高報告の CSV を読む。**1行が1報告者である。**"""
    items: list[ShortPosition] = []
    for row in records_from_csv(payload):
        symbol = four_digit_code((row.get("Code") or "").strip())
        disclosed = parse_date(row.get("DiscDate"))
        if symbol is None or disclosed is None:
            continue
        items.append(
            ShortPosition(
                symbol=symbol,
                disclosed_on=disclosed,
                calculated_on=parse_date(row.get("CalcDate")),
                reporter=(row.get("SSName") or "").strip(),
                ratio_to_shares_outstanding=parse_number(row.get("ShrtPosToSO")),
                shares=parse_number(row.get("ShrtPosShares")),
            )
        )
    return items


def total_short_position(
    positions: list[ShortPosition],
) -> dict[tuple[str, dt.date], float]:
    """報告者ぶんを足して、``(銘柄, 公表日)`` ごとの残高比率にする。

    **1行目を銘柄の残高として使わないための口である。** 報告者ごとに1行なので、
    先頭を取るとヘッジファンド1社の残高が銘柄の残高として出る。例外は出ない
    し、値ももっともらしい。

    同じ報告者が同じ日に2行出したときは**1回だけ数える。** 訂正が2行に見える
    ことがあり、足すと倍になる。
    """
    seen: dict[tuple[str, dt.date], dict[str, float]] = {}
    for item in positions:
        if item.ratio_to_shares_outstanding is None:
            continue
        key = (item.symbol, item.disclosed_on)
        seen.setdefault(key, {})[item.reporter] = item.ratio_to_shares_outstanding
    return {key: sum(byreporter.values()) for key, byreporter in seen.items()}
