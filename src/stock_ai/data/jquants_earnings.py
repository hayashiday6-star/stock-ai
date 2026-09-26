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
import math
from collections.abc import Callable, Iterable
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

#: IS の本当の始まりを決める、公表から予定日までの日数の分位点。
#:
#: **出典は無い。決めの値である**（2026-09-26、壁を測る前に決めた）。原本が
#: 2014-09-01 から始まるので、それより前に公表された予定は載っていない。
#: 中央値（52 日）では残り半分を取りこぼすので、95% 点を足して月初に切り上げる。
LEAD_QUANTILE = 0.95


def previous_weekday(day: dt.date) -> dt.date:
    """``day`` の前の平日。**祝日は見ていない**——数えるだけの道具の近似である。

    壁を測る本番では、価格に在る実際の営業日を使うこと。平日だけで数えると、
    祝日の前後で「前営業日に公表された予定」を通してしまう。
    """
    day -= dt.timedelta(days=1)
    while day.weekday() >= 5:  # noqa: PLR2004 - 土日
        day -= dt.timedelta(days=1)
    return day


def known_schedules(
    rows: Iterable[EarningsDate], previous_day: Callable[[dt.date], dt.date | None]
) -> list[EarningsDate]:
    """**窓の始まりの日より前に公表された中で最新の予定**だけを返す（候補17 の事象）。

    予定日 S の窓は、S の前営業日 W の寄付きから始まる。**原本に公表時刻が無い**
    ので、W に公表された予定は W の寄付きでは見えていないかもしれない——だから
    **W より前に公表されたもの**だけを使う。

    **「動く前の予定日で数える」と最初に決めて、取り消した**（2026-09-26、
    ユーザーの指摘）。出し直しが W より前に公表されていれば、その日に知れて
    いたのは**動いた後の**予定である。#16 の `known_at_ex_date` と同じ規則
    ——先読みになるのは**その日より後の公表**だけで、公表順で後のものではない。

    同じ期（銘柄・`FQName`・`FYE`、公表日が :data:`REVISION_DAYS` 以内）の行を
    まとめて判断する。

    Args:
        rows: 予定の行。
        previous_day: 前営業日を返す関数。**既定を置かない**——数えるだけの道具は
            平日の近似（:func:`previous_weekday`）、壁を測る本番は**価格に在る
            実際の営業日**を渡す。既定があると、渡し忘れたとき祝日の前後で黙って
            近似になる。営業日が分からなければ ``None`` を返してよい（その予定は
            使わない）。

    Returns:
        事象にする予定の行。
    """
    by_term: dict[tuple[str, str, str], list[EarningsDate]] = {}
    for row in rows:
        if row.scheduled_on is None:
            continue
        by_term.setdefault((row.symbol, row.quarter, row.fiscal_year_end), []).append(row)
    found: list[EarningsDate] = []
    for group in by_term.values():
        group.sort(key=lambda row: row.published_on)
        for row in group:
            window = previous_day(row.scheduled_on)  # type: ignore[arg-type]
            if window is None:
                continue
            known = [
                other
                for other in group
                if other.published_on < window
                and abs((other.published_on - row.published_on).days) <= REVISION_DAYS
            ]
            if known and known[-1] is row:
                found.append(row)
    return found


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

    moved_in_time: int = 0
    """動いた組のうち、出し直しが**元の予定日の窓の始まりより前に**公表されたもの。

    その日に知れていたのは**動いた後の**予定である。
    """

    moved_late: int = 0
    """動いた組のうち、出し直しが元の予定日の窓の始まり**以降に**公表されたもの。

    その時点で避けられたのは**動く前の**予定日だけである。
    """

    lead_high_days: float | None = None
    """公表から予定日までの日数の :data:`LEAD_QUANTILE` 点（予定日が後の行）。"""

    is_start: dt.date | None = None
    """**IS の本当の始まり。** 原本の始まり ＋ :attr:`lead_high_days`、月初に切り上げ。"""

    is_end: dt.date | None = None
    is_days: int = 0
    """IS（:attr:`is_start` 〜 :attr:`is_end`）の**別々の予定日の数**。**観測の数である**
    ——1観測は予定日ごとのバスケットなので、件数では数えない（`docs/POSTMORTEMS.md`
    「独立な観測を、件数で数えない」）。:func:`known_schedules` の規則で数える。
    """

    def __post_init__(self) -> None:
        """**内訳が足して合うこと**（重ならない行で数える）。"""
        parts = self.ahead + self.same_day + self.behind + self.no_schedule
        if parts != self.distinct:
            raise ValueError(f"内訳 {parts} が、重ならない行 {self.distinct} と合わない。")
        split = self.moved_in_time + self.moved_late
        if split != self.moved:
            raise ValueError(f"動いた組の内訳 {split} が {self.moved} と合わない。")

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

    def usable(self) -> str:
        """**使えると決めた数**を1行で。壁の下見の「数えたもの」の欄に出す。

        最初は :meth:`summary`（原本の本数と総行数）を出していた。**札は「材料は
        在る」と言い、欄は材料の量しか見せていなかった**（2026-09-26、ユーザーの
        指摘）。しかも「重ならない行」だけを見ると「出し直しが無い」と読める
        ——出し直しは別の行として在る。
        """
        if not self.distinct:
            return self.summary()
        share = self.ahead / self.distinct
        lead = (
            ""
            if self.lead_days_median is None
            else f"、公表から予定日まで中央値 {self.lead_days_median:.0f} 日"
        )
        start = "" if self.published is None else f"、`PubDate` {self.published[0]:%Y-%m}〜"
        sample = (
            ""
            if self.is_start is None
            else f"、IS の予定日 {self.is_days:,}（{self.is_start:%Y-%m}〜{self.is_end:%Y-%m}）"
        )
        return (
            f"予定日が公表日より後 {self.ahead:,}（{share:.1%}）{lead}、"
            f"予定日が動いた組 {self.moved:,}（窓より前に出し直し {self.moved_in_time:,}）"
            f"{start}{sample}"
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
                f"{self.moved:,}**（予定日は同じ {self.repeated:,}）。そのうち出し直しが"
                f"**元の予定日の窓の始まりより前に公表 {self.moved_in_time:,}**"
                f"（動いた後の日で数える）・以降に公表 {self.moved_late:,}"
                "（動く前の日で数える）。**前営業日は平日で近似した（祝日は見ていない）。**"
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


def schedule_census(directory: Path, is_end: dt.date) -> ScheduleCensus:
    """保存した `/fins/earnings-date` の原本を数える。**取りには行かない。**

    Args:
        directory: 原本の置き場所。
        is_end: IS の終わり（`wall.IS_END`）。**既定を置かない。**

    Returns:
        数えたもの。
    """
    import statistics

    keys, periods, rows, unreadable, distinct = _read_originals(directory)

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
    moved = repeated = moved_in_time = moved_late = 0
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
                if earlier.scheduled_on is not None and later.published_on < previous_weekday(
                    earlier.scheduled_on
                ):
                    moved_in_time += 1
                else:
                    moved_late += 1
            else:
                repeated += 1
    lead_high = (
        statistics.quantiles(leads, n=100, method="inclusive")[round(LEAD_QUANTILE * 100) - 1]
        if len(leads) >= 2  # noqa: PLR2004 - 分位点には2つ要る
        else None
    )
    is_start = None
    is_days = 0
    if periods and lead_high is not None:
        began = dt.datetime.strptime(periods[0], "%Y%m%d").date()
        reach = began + dt.timedelta(days=math.ceil(lead_high))
        is_start = (
            reach
            if reach.day == 1
            else (reach.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
        )
        is_days = len(
            {
                row.scheduled_on
                for row in known_schedules(distinct.values(), previous_weekday)
                if row.scheduled_on is not None and is_start <= row.scheduled_on <= is_end
            }
        )
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
        moved_in_time=moved_in_time,
        moved_late=moved_late,
        lead_high_days=lead_high,
        is_start=is_start,
        is_end=is_end if is_start is not None else None,
        is_days=is_days,
    )


def _read_originals(
    directory: Path,
) -> tuple[
    list[str],
    list[str],
    int,
    int,
    dict[tuple[str, dt.date, dt.date | None, str, str], EarningsDate],
]:
    """原本を読む。**歩き方をここ1箇所に置く**——数える道具と壁の両方がここを呼ぶ。

    Returns:
        ``(鍵, 鍵の日付, 読めた行, 開けなかった本数, 重ならない行)``。
    """
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
    return keys, periods, rows, unreadable, distinct


def read_schedules(directory: Path) -> list[EarningsDate]:
    """保存した原本の、**ファイルをまたいで重ならない行**。**取りには行かない。**"""
    return list(_read_originals(directory)[4].values())
