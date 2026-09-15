"""原本から読んだ行が、本当にデータベースに入ったかを数える（項目4）。

## なぜ、この形でしか閉じられないか

**データベースは、行ごとの出所を持っていない。** `price_bars` に「立花から
来た」「J-Quants から来た」の区別は無い。

5年ぶん（2021-09〜）のときは、それで困らなかった。窓がまるごと J-Quants の
覆う期間だったからである。**20年に伸ばしたら、立花の 2001年以降と重なった。**
実測で DB 16,271,965 行に対し、取り込みの報告は 15,462,008 行。差の
809,957 行が「入らなかった行」なのか「立花の行」なのか、**区別する手立てが
無い。**

## 廃止銘柄だけを見る

**立花のマスタは現存銘柄しか返さない。** だから廃止銘柄の株価は、DB にあれば
J-Quants から来たものに決まっている。そこだけを数えれば、両側が同じものを
数えたことになる。

廃止銘柄は名簿から決める。**DB との差では決めない**——DB は「取り込んだ分の
集合」であって上場一覧ではないので、「廃止」と「そもそも取っていない」を
混ぜる（一度混ぜた）。

## 数え方を両側で揃える

原本側も DB 側も、**同じ銘柄・同じ期間**で数える。片方だけ条件が違えば、
差がどちらから来たのか分からなくなる——このプロジェクトが繰り返している形で
ある。
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import func, select

from stock_ai.core.logging import get_logger
from stock_ai.data.jquants_bulk import records_from_csv
from stock_ai.data.jquants_margin import parse_date, parse_number
from stock_ai.data.jquants_prices import BARS_ENDPOINT
from stock_ai.data.jquants_read import endpoint_of, read_archived
from stock_ai.data.universe import four_digit_code
from stock_ai.database.models import PriceBar, Security

logger = get_logger(__name__)


def delisted_symbols(snapshots: dict[dt.date, set[str]]) -> set[str]:
    """一度でも名簿に出たが、**いちばん新しい名簿には居ない**銘柄。

    **立花が返せない銘柄の集合である。** ここに居る銘柄の株価が DB にあれば、
    それは J-Quants から来ている。

    名簿が1枚しか無ければ、比べる相手が無いので空を返す。**「0件」と
    「比べていない」を混ぜない**ように、呼ぶ側は名簿の枚数も見ること。
    """
    if len(snapshots) < 2:
        return set()
    newest = snapshots[max(snapshots)]
    seen: set[str] = set()
    for symbols in snapshots.values():
        seen |= symbols
    return seen - newest


@dataclasses.dataclass
class RowAudit:
    """原本と DB を、同じ銘柄・同じ期間で数えた結果。"""

    symbols: int
    """数えた銘柄の数（廃止銘柄）。"""

    archive_rows: int
    """原本にあった行。**終値の無い行は数えない**——取り込みも入れないため。"""

    database_rows: int
    first: dt.date | None
    last: dt.date | None
    files: int

    @property
    def difference(self) -> int:
        """DB − 原本。**0 なら、読んだ行は全部入っている。**"""
        return self.database_rows - self.archive_rows

    def summary(self) -> str:
        """1行のまとめ。**期間と銘柄数を必ず言う。**"""
        if not self.symbols:
            return "廃止銘柄が1件も無い。**比べていない。**"
        return (
            f"廃止銘柄 {self.symbols:,} 件、{self.first} 〜 {self.last}、原本 {self.files} 本。"
            f"原本 {self.archive_rows:,} 行 ／ DB {self.database_rows:,} 行"
            f"（差 {self.difference:+,}）。"
        )


def count_in_archive(
    directory: Path,
    symbols: set[str],
    window: tuple[dt.date, dt.date] | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> tuple[int, int, set[dt.date]]:
    """原本を読んで、``symbols`` の行を数える。**取りには行かない。**

    **終値の無い行は数えない。** 取り込み側が落としているので、数えると片側
    だけ多くなる。0 を終値として入れないのと同じ理由である。

    同じ銘柄日が2本のファイルに出ることがある（月次と日次が重なる）。
    **重なったぶんは1度だけ数える**——残すと二重に数える。

    Returns:
        （行数、読んだファイル数、出てきた日付）。
    """
    from stock_ai.data.jquants_archive import path_for, read_manifest

    keys = sorted(key for key in read_manifest(directory) if endpoint_of(key) == BARS_ENDPOINT)
    seen: set[tuple[str, dt.date]] = set()
    dates: set[dt.date] = set()
    for index, key in enumerate(keys, start=1):
        if progress is not None:
            progress(index, len(keys), key)
        for row in records_from_csv(read_archived(path_for(directory, key))):
            symbol = four_digit_code((row.get("Code") or "").strip())
            if symbol is None or symbol not in symbols:
                continue
            date = parse_date(row.get("Date"))
            if date is None:
                continue
            if window and not (window[0] <= date <= window[1]):
                continue
            close = parse_number(row.get("C"))
            if close is None or close == 0:
                continue
            seen.add((symbol, date))
            dates.add(date)
    return len(seen), len(keys), dates


def count_in_database(
    database,
    symbols: set[str],
    window: tuple[dt.date, dt.date] | None = None,
) -> int:
    """DB の ``price_bars`` から、``symbols`` の行を数える。

    **銘柄コードで引く。** `security_id` で引くと、同じコードが2つの行に
    分かれていたときに片方しか数えない。
    """
    if not symbols:
        return 0
    with database.session() as session:
        query = (
            select(func.count())
            .select_from(PriceBar)
            .join(Security, Security.id == PriceBar.security_id)
            .where(Security.symbol.in_(symbols))
        )
        if window:
            query = query.where(PriceBar.date >= window[0], PriceBar.date <= window[1])
        return int(session.execute(query).scalar_one() or 0)


def audit(
    database,
    directory: Path,
    snapshots: dict[dt.date, set[str]],
    window: tuple[dt.date, dt.date] | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> RowAudit:
    """廃止銘柄だけで、原本と DB を突き合わせる。

    Args:
        database: :class:`Database`。
        directory: 原本の置き場所。
        snapshots: 日付ごとの名簿（``membership`` が返す形）。
        window: 数える期間。渡さなければ原本の全期間。
        progress: 1本読むごとに呼ばれる。

    Returns:
        :class:`RowAudit`。
    """
    symbols = delisted_symbols(snapshots)
    if not symbols:
        return RowAudit(0, 0, 0, None, None, 0)

    rows, files, dates = count_in_archive(directory, symbols, window, progress)
    stored = count_in_database(database, symbols, window)
    return RowAudit(
        symbols=len(symbols),
        archive_rows=rows,
        database_rows=stored,
        first=min(dates) if dates else None,
        last=max(dates) if dates else None,
        files=files,
    )
