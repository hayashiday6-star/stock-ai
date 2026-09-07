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
