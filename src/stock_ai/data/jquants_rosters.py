"""保存した `/equities/master` から、営業日ごとの名簿を取り出す。

**一括ファイル1本の中に、その月の全営業日ぶんが入っている。** 2026-08 の
1本が 88,870 行で、これは 4,441 銘柄 × 20営業日である（2026-09-07 に実測）。

いまディスクにある名簿は、JSON API を**30日刻み**で叩いて集めた66枚である。
**同じ5年ぶんが、一括ファイルには約1,220枚（全営業日）入っている。**

| | 枚数 | 何が分かるか |
|---|---|---|
| 30日刻み（いまの66枚） | 66 | 廃止が**どの月**に起きたか |
| 全営業日（一括から） | 約1,220 | 廃止が**どの日**に起きたか |

20年ぶんなら約5,000枚になる。しかも**API を1回も叩かずに取り出せる**——
原本さえ保存してあれば、契約が終わったあとでも取り出し直せる。

## 混ぜない

**`data/universe_snapshots/` には書かない。**

あちらは JSON 経路が投信・ETF・5桁コードを除いたあとのもので、こちらは
一括ファイルの生である。同じ絞り込みを通してから書くが、**通し忘れや規則の
食い違いが起きたとき、境目をまたいで差を取ると「消えてもいない銘柄が消えた」
ことになる。**

立花と J-Quants の名簿を別フォルダに置いているのと同じ理由である——別の
場所に置けば、混ぜるときは必ず意図的になる。

## 絞り込みは1つしか持たない

`normalize_listings` をそのまま通す。JSON 経路が使っているのと**同じ関数**
である。ここで自前の規則を書くと、2つ持つことになり、片方だけ直したときに
気付けない。
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Callable
from pathlib import Path

from stock_ai.config.constants import DATA_DIR
from stock_ai.core.logging import get_logger
from stock_ai.data.delisted import stored_dates, write_snapshot
from stock_ai.data.jquants_bulk import records_from_csv
from stock_ai.data.jquants_margin import parse_date
from stock_ai.data.jquants_read import endpoint_of, read_archived
from stock_ai.data.types import SecurityProfile
from stock_ai.data.universe import Segment, normalize_listings

logger = get_logger(__name__)

#: 営業日ごとの名簿の置き場所。**`universe_snapshots/` と混ぜない。**
DAILY_SNAPSHOT_DIR: Path = DATA_DIR / "universe_daily"

#: 取り出し元のエンドポイント。
MASTER_ENDPOINT = "/equities/master"


@dataclasses.dataclass
class ExtractReport:
    """1回の取り出しで何が起きたか。"""

    files: int = 0
    written: list[dt.date] = dataclasses.field(default_factory=list)
    skipped: list[dt.date] = dataclasses.field(default_factory=list)
    """すでにファイルがあった日付。"""

    rows: int = 0
    undated: int = 0
    """日付を読めなかった行。**0 でないなら、列名が変わった疑いがある。**"""

    empty: list[dt.date] = dataclasses.field(default_factory=list)
    """絞り込みのあと1銘柄も残らなかった日付。**書かない。**"""

    failed: dict[str, str] = dataclasses.field(default_factory=dict)

    def summary(self) -> str:
        """1行のまとめ。"""
        return (
            f"{self.files} 本から {len(self.written)} 日ぶんを書き出し、"
            f"{len(self.skipped)} 日は既存、{self.rows:,} 行を読んだ"
            + (f"、日付を読めない行 {self.undated:,}" if self.undated else "")
            + (f"、{len(self.failed)} 本が読めず" if self.failed else "")
        )


def rosters_from_payload(payload: bytes) -> tuple[dict[dt.date, list[SecurityProfile]], int, int]:
    """展開済みの一括名簿を、**日付ごとに分ける。**

    **1本を1枚の名簿として扱わない。** 月ぶんをまとめて1枚にすると、その月の
    どこかで上場した銘柄が、月初から居たことになる。このプロジェクトには
    「和集合だと、まだ上場していない銘柄を過去の分位に入れてしまう」と既に
    書いてある——**同じ間違いが、月の中でも起きる。**

    Returns:
        ``(日付ごとの名簿, 読んだ行数, 日付を読めなかった行数)``。
    """
    rows = records_from_csv(payload)
    by_date: dict[dt.date, list[dict[str, str]]] = {}
    undated = 0
    for row in rows:
        date = parse_date(row.get("Date"))
        if date is None:
            undated += 1
            continue
        by_date.setdefault(date, []).append(row)

    # **絞り込みは JSON 経路と同じ関数を通す。** 自前で書くと2つ持つことになる。
    return (
        {date: normalize_listings(items, Segment.ALL) for date, items in by_date.items()},
        len(rows),
        undated,
    )


def extract(
    archive_dir: Path,
    out_dir: Path = DAILY_SNAPSHOT_DIR,
    refetch: bool = False,
    progress: Callable[[int, int, str], None] | None = None,
) -> ExtractReport:
    """保存済みの `/equities/master` から、営業日ごとの名簿を書き出す。

    **API を1回も叩かない。** 原本さえあれば、解約後にも実行できる。

    途中で止めても安全に再開できる。すでにファイルがある日付は作り直さない。

    Args:
        archive_dir: 原本の置き場所。
        out_dir: 書き出し先。**`universe_snapshots/` を指さないこと。**
        refetch: すでにある日付も書き直す。
        progress: 1本ごとに ``(番号, 総数, key)`` で呼ばれる。

    Returns:
        :class:`ExtractReport`。
    """
    from stock_ai.data.jquants_archive import path_for, read_manifest

    keys = [
        key for key in sorted(read_manifest(archive_dir)) if endpoint_of(key) == MASTER_ENDPOINT
    ]
    report = ExtractReport()
    if not keys:
        logger.warning("`%s` の原本が1本も無い。", MASTER_ENDPOINT)
        return report

    existing = set() if refetch else set(stored_dates(out_dir))
    total = len(keys)
    for index, key in enumerate(keys, start=1):
        if progress is not None:
            progress(index, total, key)
        try:
            rosters, rows, undated = rosters_from_payload(read_archived(path_for(archive_dir, key)))
        except Exception as exc:  # noqa: BLE001 - どこで読めないかが記録に値する
            report.failed[key] = f"{type(exc).__name__}: {exc}"
            logger.warning("原本を読めなかった: %s: %s", key, exc)
            continue

        report.files += 1
        report.rows += rows
        report.undated += undated
        for date, profiles in sorted(rosters.items()):
            if date in existing:
                report.skipped.append(date)
                continue
            if not profiles:
                # **空の名簿を書かない。** 書くと、その日に全銘柄が上場廃止
                # したように見える。例外は出ない。
                report.empty.append(date)
                continue
            write_snapshot(out_dir, date, profiles)
            report.written.append(date)
            existing.add(date)

    logger.info("営業日ごとの名簿: %s", report.summary())
    return report


@dataclasses.dataclass
class CompareReport:
    """2つの名簿を突き合わせた結果。

    **同じ日付が両方にあるなら、中身も同じはずである。** 違うなら、絞り込みか
    日付の意味のどちらかが食い違っている。
    """

    common: list[dt.date] = dataclasses.field(default_factory=list)
    same: list[dt.date] = dataclasses.field(default_factory=list)
    differing: dict[dt.date, tuple[int, int]] = dataclasses.field(default_factory=dict)
    """日付ごとの ``(片方だけにある数, もう片方だけにある数)``。"""

    examples: dict[dt.date, tuple[list[str], list[str]]] = dataclasses.field(default_factory=dict)
    lending_differs: dict[dt.date, int] = dataclasses.field(default_factory=dict)
    """銘柄は同じで貸借区分が違った件数。**これも黙って通る種類である。**"""

    def summary(self) -> str:
        """1行のまとめ。"""
        if not self.common:
            return "重なる日付が無い。突き合わせられない。"
        return (
            f"重なる日付 {len(self.common)} 日のうち、"
            f"{len(self.same)} 日が完全一致、{len(self.differing)} 日が食い違い"
            + (
                f"、貸借区分だけ違う日が {len(self.lending_differs)} 日"
                if self.lending_differs
                else ""
            )
        )


def compare(first: Path, second: Path, limit: int = 5) -> CompareReport:
    """2つの名簿フォルダを、**重なる日付だけ**突き合わせる。

    30日刻みで集めた名簿と、一括から取り出した名簿は**別の経路で作った同じ
    ものである。** 同じ日付で中身が違うなら、どちらかが間違っている。

    **これをやらずに新しい経路へ乗り換えない。** 片方だけを見ているかぎり、
    絞り込みの食い違いは「銘柄数がちょっと違う」としか見えず、それは毎日
    変わる値なので区別が付かない。

    Args:
        first: 一方の名簿フォルダ。
        second: もう一方。
        limit: 食い違った銘柄を何件まで控えるか。

    Returns:
        :class:`CompareReport`。
    """
    from stock_ai.data.delisted import read_snapshot, snapshot_path

    report = CompareReport()
    shared = sorted(set(stored_dates(first)) & set(stored_dates(second)))
    report.common = shared
    for date in shared:
        left = {p.symbol: p for p in read_snapshot(snapshot_path(first, date))}
        right = {p.symbol: p for p in read_snapshot(snapshot_path(second, date))}
        only_left = sorted(set(left) - set(right))
        only_right = sorted(set(right) - set(left))
        if only_left or only_right:
            report.differing[date] = (len(only_left), len(only_right))
            if len(report.examples) < limit:
                report.examples[date] = (only_left[:limit], only_right[:limit])
            continue
        # 銘柄が同じでも、貸借区分が違うことはありうる。**そこも見る。**
        differs = sum(
            1 for symbol in left if (left[symbol].lending or "") != (right[symbol].lending or "")
        )
        if differs:
            report.lending_differs[date] = differs
        report.same.append(date)
    return report


CALENDAR_ENDPOINT = "/markets/calendar"


def trading_days_from_archive(archive_dir: Path) -> set[dt.date] | None:
    """保存済みの取引カレンダーから、立会のある日を読む。

    **半日立会（`HolDiv=2`）を落とさない。** 落とすと、その日を挟んだ
    「N営業日後」が1日ずれる。年に数日なので件数からは気付けない。

    Returns:
        立会日の集合。カレンダーの原本が無ければ ``None``——**空集合と
        区別する。** 空集合を返すと「1日も立会が無い」と読めてしまう。
    """
    from stock_ai.data.jquants_archive import path_for, read_manifest
    from stock_ai.data.jquants_markets import parse_calendar, trading_days

    keys = [
        key for key in sorted(read_manifest(archive_dir)) if endpoint_of(key) == CALENDAR_ENDPOINT
    ]
    if not keys:
        return None
    found: set[dt.date] = set()
    for key in keys:
        try:
            found.update(trading_days(parse_calendar(read_archived(path_for(archive_dir, key)))))
        except Exception as exc:  # noqa: BLE001 - 読めない理由が記録に値する
            logger.warning("取引カレンダーを読めなかった: %s: %s", key, exc)
    return found


def explain_missing(
    dates: list[dt.date], trading: set[dt.date] | None
) -> tuple[list[dt.date], list[dt.date]]:
    """重ならなかった日付を、取引カレンダーで説明できるか見る。

    30日刻みの日付は、休日にも当たる。一括の名簿には**立会日しか無い**ので、
    休日ぶんは重ならない。それは欠けではない。

    **「たぶん休日だろう」で済ませない。** 立会日なのに名簿が無い日が混じって
    いたら、それは本当の欠けである。両者は件数では区別が付かない。

    Returns:
        ``(休日だった, 立会日なのに無かった)``。
    """
    if trading is None:
        return [], list(dates)
    return (
        [date for date in dates if date not in trading],
        [date for date in dates if date in trading],
    )
