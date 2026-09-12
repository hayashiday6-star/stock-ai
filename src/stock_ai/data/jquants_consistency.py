"""名簿と四本値が、**互いに**整合しているかを見る。

同じ原本から、2本の経路が出ている。

| 経路 | 元 | 通した絞り込み |
|---|---|---|
| 名簿（`data/universe_daily/`） | `/equities/master` | `normalize_listings` |
| 四本値（DB の日足） | `/equities/bars/daily` | `four_digit_code` だけ |

**2本は同じ絞り込みを通っていない。** だから食い違うのが普通で、件数を見ても
何も分からない。分かるのは**理由が言えるかどうか**である。

## 向きを分けて数える

- **名簿にいて、四本値の行が無い。** これは警告である。名簿に載る会社は、
  その日に上場している。行が無い理由は思い付かない。
- **名簿にいて、行はあるが終値が無い。** 出来高が0なら、売買が無かった日
  である。**出来高があるのに終値が無いなら、読み方が違う。**
- **四本値に終値があって、名簿にいない。** ETF・REIT・TOKYO PRO Market が
  ここに来る。**落ちた理由を1件ずつ言えるなら、食い違いではない。**

## 「だいたい説明が付く」で終わらせない

名簿の差 109 件を「たぶん市場が違うのだろう」で済ませかけて、実際には
**当時の市場ではなく最新の市場**を見ていた（2026-09-08）。件数が近いことは
説明ではない。**1件ずつ理由を付けて、理由の言えないものを 0 にする。**

そのために :func:`~stock_ai.data.universe.rejection_reason` を使う。名簿を
作るときに通したのと**同じ関数**である。ここで自前に判定を書くと、絞り込みを
2つ持つことになり、片方だけ直したときに気付けない。
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from stock_ai.core.logging import get_logger
from stock_ai.data.jquants_bulk import records_from_csv
from stock_ai.data.jquants_margin import parse_date, parse_number
from stock_ai.data.jquants_prices import BARS_ENDPOINT
from stock_ai.data.jquants_read import endpoint_of, read_archived
from stock_ai.data.jquants_rosters import DAILY_SNAPSHOT_DIR, MASTER_ENDPOINT
from stock_ai.data.universe import four_digit_code, rejection_reason

logger = get_logger(__name__)

#: 四本値に終値があるのに、その日の名簿の原本に行が1つも無い。
#: **これは絞り込みでは説明が付かない。**
NO_ROSTER_ROW = "名簿の原本に行が無い"

#: 名簿の原本には残るはずの行があるのに、保存された名簿に銘柄がいない。
#: **取り出しが落としている。**
ROSTER_DISAGREES = "原本では残るはずなのに、保存された名簿に無い"

#: 名簿にいるのに、四本値の行が1つも無い。
NO_BAR_ROW = "四本値の行が無い"


@dataclasses.dataclass
class DayReport:
    """1営業日ぶんの突き合わせ。"""

    date: dt.date
    roster: int = 0
    """その日の名簿にいた銘柄数。"""

    matched: int = 0
    """名簿にいて、終値もあった銘柄数。"""

    quiet: int = 0
    """名簿にいて、行はあるが終値が無く、出来高も0。**売買が無かった日である。**"""

    no_bar_row: list[str] = dataclasses.field(default_factory=list)
    """名簿にいて、四本値の行すら無い銘柄。**警告。**"""

    traded_no_close: list[str] = dataclasses.field(default_factory=list)
    """出来高があるのに終値が無い銘柄。**警告。**"""

    price_only: dict[str, str] = dataclasses.field(default_factory=dict)
    """終値があって名簿にいない銘柄 → **落ちた理由。**"""


@dataclasses.dataclass
class ConsistencyReport:
    """全期間の突き合わせ。"""

    files: int = 0
    days: int = 0
    roster_symbol_days: int = 0
    matched: int = 0
    quiet: int = 0

    missing_roster: list[dt.date] = dataclasses.field(default_factory=list)
    """四本値はあるのに、保存された名簿が無い日。**比べていない日である。**"""

    missing_master: list[dt.date] = dataclasses.field(default_factory=list)
    """名簿の原本が覆っていない日。**理由を引く相手がいない日である。**"""

    no_bar_row: Counter[str] = dataclasses.field(default_factory=Counter)
    """銘柄 → 四本値の行が無かった日数。**警告。**"""

    traded_no_close: Counter[str] = dataclasses.field(default_factory=Counter)
    """銘柄 → 出来高があるのに終値が無かった日数。**警告。**"""

    reasons: Counter[str] = dataclasses.field(default_factory=Counter)
    """落ちた理由 → 銘柄日数。**内訳がここに出る。**"""

    unexplained: Counter[str] = dataclasses.field(default_factory=Counter)
    """理由を言えなかった銘柄 → 日数。**ここが 0 でないなら、食い違いである。**"""

    failed: dict[str, str] = dataclasses.field(default_factory=dict)

    @property
    def price_only(self) -> int:
        """終値があって名簿にいなかった銘柄日数。"""
        return sum(self.reasons.values())

    def summary(self) -> str:
        """1行のまとめ。"""
        return (
            f"{self.days} 日 / 名簿 {self.roster_symbol_days:,} 銘柄日のうち "
            f"{self.matched:,} に終値、{self.quiet:,} は売買なし、"
            f"行が無い {sum(self.no_bar_row.values()):,}。"
            f"名簿に無い終値 {self.price_only:,} 銘柄日、"
            f"うち理由を言えないもの {sum(self.unexplained.values()):,}"
        )


def bars_by_date(payload: bytes) -> dict[dt.date, dict[str, tuple[bool, float]]]:
    """四本値の原本1本を、``{日付: {銘柄: (終値があるか, 出来高)}}`` にする。

    **終値の有無と行の有無を分ける。** 終値が無い行を落として渡すと、
    「売買が無かった日」と「行そのものが無い日」が同じ顔になる。
    """
    by_date: dict[dt.date, dict[str, tuple[bool, float]]] = {}
    for row in records_from_csv(payload):
        symbol = four_digit_code((row.get("Code") or "").strip())
        if symbol is None:
            continue
        date = parse_date(row.get("Date"))
        if date is None:
            continue
        close = parse_number(row.get("C"))
        volume = parse_number(row.get("Vo")) or 0.0
        by_date.setdefault(date, {})[symbol] = (close is not None and close != 0, volume)
    return by_date


def reasons_by_date(payload: bytes) -> dict[dt.date, dict[str, str]]:
    """名簿の原本1本を、``{日付: {銘柄: 落ちた理由}}`` にする。

    残る行は空文字にする。**「残るはず」と「行が無い」を区別するため**で、
    落ちた行だけを持つと、両者が同じ「鍵が無い」になってしまう。
    """
    by_date: dict[dt.date, dict[str, str]] = {}
    for row in records_from_csv(payload):
        date = parse_date(row.get("Date"))
        if date is None:
            continue
        symbol = four_digit_code((row.get("Code") or "").strip())
        if symbol is None:
            # 4桁にならない行は、四本値の側にも出てこない。引き合わせる相手が
            # いないので、索引に入れても使われない。
            continue
        by_date.setdefault(date, {})[symbol] = rejection_reason(row) or ""
    return by_date


def _master_index(archive_dir: Path) -> dict[str, frozenset[dt.date]]:
    """名簿の原本ごとに、何日ぶんが入っているか。

    どの原本を開けばよいかを決めるためだけに使う。**行は持たない**ので、
    1本ぶんより大きくならない。
    """
    from stock_ai.data.jquants_archive import path_for, read_manifest

    index: dict[str, frozenset[dt.date]] = {}
    for key in sorted(read_manifest(archive_dir)):
        if endpoint_of(key) != MASTER_ENDPOINT:
            continue
        try:
            dates = {
                date
                for row in records_from_csv(read_archived(path_for(archive_dir, key)))
                if (date := parse_date(row.get("Date"))) is not None
            }
        except Exception as exc:  # noqa: BLE001 - どこで読めないかが記録に値する
            logger.warning("名簿の原本を読めなかった: %s: %s", key, exc)
            continue
        index[key] = frozenset(dates)
    return index


def compare_day(
    date: dt.date,
    roster: set[str],
    bars: dict[str, tuple[bool, float]],
    reasons: dict[str, str],
) -> DayReport:
    """1営業日を突き合わせる。

    Args:
        date: その日。
        roster: 保存された名簿にいる銘柄。
        bars: ``{銘柄: (終値があるか, 出来高)}``。
        reasons: その日の名簿の原本から引いた ``{銘柄: 落ちた理由}``。
            残る行は空文字。
    """
    report = DayReport(date=date, roster=len(roster))
    for symbol in sorted(roster):
        row = bars.get(symbol)
        if row is None:
            report.no_bar_row.append(symbol)
            continue
        has_close, volume = row
        if has_close:
            report.matched += 1
        elif volume > 0:
            # **出来高があって終値が無い。** 売買が無かったでは説明が付かない。
            report.traded_no_close.append(symbol)
        else:
            report.quiet += 1

    for symbol, (has_close, _volume) in bars.items():
        if not has_close or symbol in roster:
            continue
        # **終値が付いた銘柄だけを見る。** 値の付かない行が名簿に無いのは、
        # 落ちた理由を確かめるまでもない。
        reason = reasons.get(symbol)
        if reason is None:
            report.price_only[symbol] = NO_ROSTER_ROW
        elif reason == "":
            report.price_only[symbol] = ROSTER_DISAGREES
        else:
            report.price_only[symbol] = reason
    return report


def check(
    archive_dir: Path,
    roster_dir: Path = DAILY_SNAPSHOT_DIR,
    limit: int | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> ConsistencyReport:
    """保存済みの原本と名簿を、営業日ごとに突き合わせる。

    **API を1回も叩かない。** 解約後にも実行できる。

    Args:
        archive_dir: 原本の置き場所。
        roster_dir: 営業日ごとの名簿。
        limit: 四本値の原本を何本まで読むか。試すとき用。
        progress: 1本ごとに ``(番号, 総数, key)`` で呼ばれる。

    Returns:
        :class:`ConsistencyReport`。
    """
    from stock_ai.data.delisted import read_snapshot, snapshot_path
    from stock_ai.data.jquants_archive import path_for, read_manifest

    report = ConsistencyReport()
    keys = [key for key in sorted(read_manifest(archive_dir)) if endpoint_of(key) == BARS_ENDPOINT]
    if not keys:
        logger.warning("`%s` の原本が1本も無い。", BARS_ENDPOINT)
        return report
    if limit is not None:
        keys = keys[:limit]

    index = _master_index(archive_dir)
    cached_key: str | None = None
    cached: dict[dt.date, dict[str, str]] = {}

    total = len(keys)
    for number, key in enumerate(keys, start=1):
        if progress is not None:
            progress(number, total, key)
        try:
            bars = bars_by_date(read_archived(path_for(archive_dir, key)))
        except Exception as exc:  # noqa: BLE001 - どこで読めないかが記録に値する
            report.failed[key] = f"{type(exc).__name__}: {exc}"
            logger.warning("四本値の原本を読めなかった: %s: %s", key, exc)
            continue
        report.files += 1

        for date in sorted(bars):
            path = snapshot_path(roster_dir, date)
            if not path.is_file():
                # **名簿が無い日を「一致した」に数えない。** 比べていない。
                report.missing_roster.append(date)
                continue

            if cached_key is None or date not in cached:
                wanted = next((name for name, dates in index.items() if date in dates), None)
                if wanted is None:
                    report.missing_master.append(date)
                    continue
                if wanted != cached_key:
                    try:
                        cached = reasons_by_date(read_archived(path_for(archive_dir, wanted)))
                    except Exception as exc:  # noqa: BLE001
                        report.failed[wanted] = f"{type(exc).__name__}: {exc}"
                        logger.warning("名簿の原本を読めなかった: %s: %s", wanted, exc)
                        cached, cached_key = {}, None
                        continue
                    cached_key = wanted

            roster = {profile.symbol for profile in read_snapshot(path)}
            day = compare_day(date, roster, bars[date], cached.get(date, {}))

            report.days += 1
            report.roster_symbol_days += day.roster
            report.matched += day.matched
            report.quiet += day.quiet
            for symbol in day.no_bar_row:
                report.no_bar_row[symbol] += 1
            for symbol in day.traded_no_close:
                report.traded_no_close[symbol] += 1
            for symbol, reason in day.price_only.items():
                report.reasons[reason] += 1
                if reason in (NO_ROSTER_ROW, ROSTER_DISAGREES):
                    report.unexplained[symbol] += 1

    logger.info("名簿と四本値: %s", report.summary())
    return report
