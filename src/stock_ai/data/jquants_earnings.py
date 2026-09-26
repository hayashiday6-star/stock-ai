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
from pathlib import Path

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


#: 決算発表予定日の原本の置き場所で使う、エンドポイントの名前。
ENDPOINT = "/fins/earnings-date"

#: 「同じ期の予定が出し直された」とみなす、公表日どうしの間隔（日）。
#:
#: **出典は無い。決めの値である**（2026-09-26）。四半期は約 90 日ごとだが、
#: 期（`FQName`・`FYE`）が同じ行どうしだけを比べるので、次の期と混ざらない。
#: `FYE` には年が入っていない（上の表）ので、**間隔で同じ年の期に絞る。**
REVISION_DAYS = 120


@dataclasses.dataclass(frozen=True)
class ScheduleCensus:
    """保存した `/fins/earnings-date` の原本が、**予定の履歴になっているか**（候補17）。

    **「API が直近しか返さない」ことと、「手元に歴史が無い」ことは別である。**
    `jquants_plan.NO_HISTORY` は前者を言い、この module の冒頭は「`PubDate` が
    入っていて 2014年まで遡る」と言う——**2箇所が食い違っていた**（2026-09-26）。
    数えれば決まる。

    **効果は何も計算しない。** 見るのは、行が「その日に何が予定されていたか」
    として使えるかだけである。
    """

    files: int
    unreadable: int
    """開けなかった原本。**黙って飛ばさない。**"""

    first_file: str
    """いちばん古いファイルの ``YYYYMMDD``（`jquants_archive.key_period`）。"""

    last_file: str
    rows: int
    """読めた行（銘柄と `PubDate` が在るもの）。**ファイルをまたいで重なりうる。**"""

    distinct: int
    """ファイルをまたいで重ならない行（銘柄・`PubDate`・`SchDate`・期）。"""

    symbols: int
    published: tuple[dt.date, dt.date] | None
    scheduled: tuple[dt.date, dt.date] | None
    ahead: int
    """`SchDate` が `PubDate` より**後**。**予定として前もって分かっていた**行。"""

    same_day: int
    behind: int
    """`SchDate` が `PubDate` より前。**予定ではない。**"""

    no_schedule: int
    lead_days_median: float | None
    """``ahead`` の行で、公表から予定日までの日数の中央値。"""

    by_year: tuple[tuple[int, int], ...]
    """``(PubDate の年, 重ならない行)``。**抜けている年が在れば、そこは履歴が無い。**"""

    moved: int = 0
    """**同じ期の予定が、公表日を変えて出し直され、予定日も変わった**組。

    原本は「公表された予定を1回ずつ」持つ形なので（ファイルをまたいだ重なりが
    0）、**予定が動いたことは、公表日の遅い2本目の行として見える**はずである。
    """

    repeated: int = 0
    """同じ期の予定が出し直されたが、**予定日は同じ**だった組。"""

    def __post_init__(self) -> None:
        """**内訳が足して合うこと**（重ならない行で数える）。"""
        parts = self.ahead + self.same_day + self.behind + self.no_schedule
        if parts != self.distinct:
            raise ValueError(f"内訳 {parts} が、重ならない行 {self.distinct} と合わない。")

    def summary(self) -> str:
        """1行のまとめ。"""
        if not self.files:
            return f"`{ENDPOINT}` の原本が1本も無い。"
        span = (
            ""
            if self.published is None
            else f"（`PubDate` {self.published[0]} 〜 {self.published[1]}）"
        )
        return (
            f"`{ENDPOINT}` の原本 {self.files:,} 本（{self.first_file} 〜 {self.last_file}）、"
            f"{self.rows:,} 行、**重ならない行 {self.distinct:,}**、{self.symbols:,} 銘柄{span}。"
        )

    def warnings(self) -> list[str]:
        """気付かなくても目に入るべきこと。**早期 return しない。**"""
        found: list[str] = []
        if not self.files:
            found.append(f"**`{ENDPOINT}` の原本が1本も無い。** 材料が無い。")
        if self.unreadable:
            found.append(f"**{self.unreadable:,} 本は開けなかった。** 数えていない。")
        elif not self.distinct:
            found.append("**原本は在るが、1行も読めなかった。** 列名が違う。")
        if self.distinct:
            share = self.ahead / self.distinct
            found.append(
                f"**予定日が公表日より後の行 {self.ahead:,}（{share:.1%}）。** "
                "ここが「その日に何が予定されていたか」として使える行である。"
            )
        if self.behind:
            found.append(
                f"**{self.behind:,} 行は予定日が公表日より前だった。** 予定ではない——使うなら外す。"
            )
        if self.distinct:
            found.append(
                f"**同じ期の予定が {REVISION_DAYS} 日以内に出し直され、予定日が動いた組 "
                f"{self.moved:,}**（予定日は同じ {self.repeated:,}）。動いた銘柄は、"
                "**動く前の予定日で**避けることになる。"
            )
        years = [year for year, _count in self.by_year]
        if years:
            missing = sorted(set(range(years[0], years[-1] + 1)) - set(years))
            if missing:
                found.append(
                    "**`PubDate` の年が抜けている**: "
                    + "、".join(str(year) for year in missing)
                    + "。**その年は履歴が無い。**"
                )
        return found


def schedule_census(directory: Path) -> ScheduleCensus:
    """保存した `/fins/earnings-date` の原本を数える。**取りには行かない。**

    Args:
        directory: 原本の置き場所。

    Returns:
        数えたもの。
    """
    import statistics

    from stock_ai.data.jquants_archive import key_period, path_for, read_manifest
    from stock_ai.data.jquants_read import endpoint_of, read_archived

    keys = sorted(key for key in read_manifest(directory) if endpoint_of(key) == ENDPOINT)
    periods = sorted(period for key in keys if (period := key_period(key)))
    rows = unreadable = 0
    distinct: dict[tuple[str, dt.date, dt.date | None, str, str], EarningsDate] = {}
    for key in keys:
        try:
            items = parse_earnings_dates(read_archived(path_for(directory, key)))
        except Exception:  # noqa: BLE001 - 1本読めないことで全体を止めない
            unreadable += 1
            continue
        rows += len(items)
        for item in items:
            distinct[
                (
                    item.symbol,
                    item.published_on,
                    item.scheduled_on,
                    item.quarter,
                    item.fiscal_year_end,
                )
            ] = item

    ahead = same_day = behind = no_schedule = 0
    leads: list[int] = []
    years: dict[int, int] = {}
    for item in distinct.values():
        years[item.published_on.year] = years.get(item.published_on.year, 0) + 1
        if item.scheduled_on is None:
            no_schedule += 1
        elif item.scheduled_on > item.published_on:
            ahead += 1
            leads.append((item.scheduled_on - item.published_on).days)
        elif item.scheduled_on == item.published_on:
            same_day += 1
        else:
            behind += 1
    moved = repeated = 0
    # **`periods`（ファイルの日付）と別の名前にする。** 同じ名前で上書きして、
    # いちばん古いファイルの欄が `KeyError` で落ちた（2026-09-26）。
    by_term: dict[tuple[str, str, str], list[EarningsDate]] = {}
    for item in distinct.values():
        by_term.setdefault((item.symbol, item.quarter, item.fiscal_year_end), []).append(item)
    for rows_of_period in by_term.values():
        rows_of_period.sort(key=lambda row: row.published_on)
        for earlier, later in zip(rows_of_period, rows_of_period[1:], strict=False):
            if (later.published_on - earlier.published_on).days > REVISION_DAYS:
                continue
            if later.scheduled_on != earlier.scheduled_on:
                moved += 1
            else:
                repeated += 1
    published = [item.published_on for item in distinct.values()]
    scheduled = [item.scheduled_on for item in distinct.values() if item.scheduled_on]
    return ScheduleCensus(
        files=len(keys),
        unreadable=unreadable,
        first_file=periods[0] if periods else "",
        last_file=periods[-1] if periods else "",
        rows=rows,
        distinct=len(distinct),
        symbols=len({item.symbol for item in distinct.values()}),
        published=(min(published), max(published)) if published else None,
        scheduled=(min(scheduled), max(scheduled)) if scheduled else None,
        ahead=ahead,
        same_day=same_day,
        behind=behind,
        no_schedule=no_schedule,
        lead_days_median=statistics.median(leads) if leads else None,
        by_year=tuple(sorted(years.items())),
        moved=moved,
        repeated=repeated,
    )
