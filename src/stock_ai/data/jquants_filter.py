"""名簿の絞り込みが、**20年に伸ばしても持つか。**

いま絞り込みを決めているのは2つの列である。

| 列 | 使い道 | 無かったら |
|---|---|---|
| `Mkt` | 買えない市場を落とす | 市場名で見る。それも無ければ**残す** |
| `S33` | 投信・ETF・REIT を落とす | **無条件に「会社」とみなす** |

`S33` が無い行を残すのは意図した設計である。**符号の名前が変わったときに
universe が空になるより、ETF が1つ紛れるほうが安い。** ただしそれは、`S33`
がほぼ全部の行に在ることを前提にしている。

## 20年に伸びると、前提のほうが変わる

いま手元にあるのは5年ぶん（2021-09〜）である。**そこで `S33` が揃っている
ことは、2006年にも揃っていることを意味しない。** 符号の体系は変わるし、古い
行では空かもしれない。

そうなると起きるのは2つで、**向きが逆である。**

| 形 | 何が起きる |
|---|---|
| `S33` が空 | ETF・REIT が「会社」として universe に入る |
| `S33` が**表に無い符号** | 普通の会社が「その他」＝投信とみなされて落ちる |

後者のほうが重い。符号の体系が変われば、**落ちるのは1社ではなく全部になり
うる。** どちらも例外は出ない。

## だから、いま数えておく

契約が終わってからでは、20年ぶんの名簿は取れない。**取ったその日に、この
数え方で見られる状態にしておく。**

いま測れるのは5年ぶんだが、それが**基準線**になる。2006年の数字が基準線と
同じなら前提は持っており、違えば違うと分かる。**基準線が無ければ、20年ぶんの
数字を見ても「多いのか少ないのか」が言えない。**

## `ProdCat` が二の矢になるか

名簿には `ProdCat`（商品区分）という別の列がある。**`S33` とは独立に「これは
何か」を言っている可能性がある。** もしそうなら、`S33` が空のときの受け皿に
できる。

**使えるかどうかは、決め打ちせずに数える。** いま落としている投信・ETF の
`ProdCat` と、残している会社の `ProdCat` が**重なっていなければ**使える。
重なっていれば使えない。そこを分けずに「たぶん使える」と書かない。
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from stock_ai.core.logging import get_logger
from stock_ai.data.jquants_bulk import records_from_csv
from stock_ai.data.jquants_margin import parse_date
from stock_ai.data.jquants_read import endpoint_of, read_archived
from stock_ai.data.jquants_rosters import MASTER_ENDPOINT
from stock_ai.data.sectors import known_tse33
from stock_ai.data.universe import four_digit_code, rejection_reason

logger = get_logger(__name__)

#: `S33`（33業種）を探す列名。名簿の版によって綴りが違う。
SECTOR_FIELDS = ("S33", "Sec33Cd", "Sector33Code")

#: 商品区分を探す列名。
PRODUCT_FIELDS = ("ProdCat", "ProductCategory", "ProdCatNm")


def _first(row: dict[str, str], fields: tuple[str, ...]) -> str:
    """``fields`` のうち、最初に中身のあるもの。無ければ空文字。"""
    for field in fields:
        value = (row.get(field) or "").strip()
        if value:
            return value
    return ""


@dataclasses.dataclass
class YearSlice:
    """ある年の名簿に何が入っていたか。

    件数は**銘柄日**である（同じ銘柄が営業日の数だけ出る）。年をまたいで
    割合を比べるための数で、**銘柄数ではない。** 名指しが要るものは別に
    銘柄コードで持つ。
    """

    rows: int = 0
    kept: int = 0
    no_sector: int = 0
    """`S33` が空だった行。**無条件に「会社」とみなされている。**"""

    unknown_sector: int = 0
    """`S33` はあるが、こちらの表に無い符号。**「その他」と同じ扱いで落ちる。**"""

    reasons: Counter[str] = dataclasses.field(default_factory=Counter)

    @property
    def no_sector_share(self) -> float:
        """`S33` の無い行の割合。**基準線として使う値である。**"""
        return self.no_sector / self.rows if self.rows else 0.0


@dataclasses.dataclass
class FilterCensus:
    """絞り込みの通り具合を、年ごとに数えたもの。"""

    files: int = 0
    by_year: dict[int, YearSlice] = dataclasses.field(default_factory=dict)

    no_sector_symbols: set[str] = dataclasses.field(default_factory=set)
    """`S33` が空だった銘柄。**名指しできるようにする。**"""

    unknown_sector_codes: Counter[str] = dataclasses.field(default_factory=Counter)
    """表に無かった `S33` の符号 → 銘柄日。"""

    unknown_sector_names: dict[str, str] = dataclasses.field(default_factory=dict)
    """表に無かった符号 → `S33Nm` の一例。**何なのかを目で見るため。**"""

    product_kept: Counter[str] = dataclasses.field(default_factory=Counter)
    """残した行の `ProdCat`。"""

    product_dropped: dict[str, Counter[str]] = dataclasses.field(default_factory=dict)
    """落とした理由 → その行の `ProdCat`。"""

    failed: dict[str, str] = dataclasses.field(default_factory=dict)

    @property
    def rows(self) -> int:
        """全部の銘柄日。"""
        return sum(slice_.rows for slice_ in self.by_year.values())

    @property
    def span(self) -> tuple[int, int] | None:
        """最初と最後の年。"""
        return (min(self.by_year), max(self.by_year)) if self.by_year else None

    def summary(self) -> str:
        """1行のまとめ。"""
        if not self.by_year:
            return "名簿の原本が無い。"
        first, last = min(self.by_year), max(self.by_year)
        no_sector = sum(slice_.no_sector for slice_ in self.by_year.values())
        return (
            f"{first}〜{last} / {self.rows:,} 銘柄日、"
            f"`S33` が空 {no_sector:,}（{no_sector / self.rows:.2%}、"
            f"{len(self.no_sector_symbols)} 銘柄）、"
            f"表に無い符号 {sum(self.unknown_sector_codes.values()):,}"
        )


def product_separates(census: FilterCensus, reason: str) -> tuple[bool, set[str]]:
    """`ProdCat` が、その理由で落ちた行と残した行を**分けきれるか。**

    **「たぶん使える」と書かないための関数である。** 重なる値が1つでもあれば
    受け皿にはできない——重なった値の行は、どちらとも言えないからである。

    Returns:
        ``(分けきれるか, 重なっている値)``。
    """
    dropped = set(census.product_dropped.get(reason, Counter()))
    kept = set(census.product_kept)
    if not dropped or not kept:
        # **片方が空なら「分けられた」ではない。** 比べていない。
        return False, set()
    overlap = dropped & kept
    return not overlap, overlap


def census_payload(payload: bytes, into: FilterCensus) -> None:
    """名簿の原本1本を数え、``into`` に足す。"""
    for row in records_from_csv(payload):
        date = parse_date(row.get("Date"))
        if date is None:
            continue
        slice_ = into.by_year.setdefault(date.year, YearSlice())
        slice_.rows += 1

        sector = _first(row, SECTOR_FIELDS)
        code = four_digit_code((row.get("Code") or "").strip())
        if not sector:
            slice_.no_sector += 1
            if code is not None:
                into.no_sector_symbols.add(code)
        elif not known_tse33(sector):
            slice_.unknown_sector += 1
            into.unknown_sector_codes[sector] += 1
            into.unknown_sector_names.setdefault(sector, _first(row, ("S33Nm", "Sec33Name")))

        product = _first(row, PRODUCT_FIELDS)
        reason = rejection_reason(row)
        if reason is None:
            slice_.kept += 1
            into.product_kept[product] += 1
        else:
            slice_.reasons[reason] += 1
            into.product_dropped.setdefault(reason, Counter())[product] += 1


def census(
    archive_dir: Path,
    progress: Callable[[int, int, str], None] | None = None,
) -> FilterCensus:
    """保存済みの名簿を1周読んで、絞り込みの通り具合を年ごとに数える。

    **API を1回も叩かない。**

    Args:
        archive_dir: 原本の置き場所。
        progress: 1本ごとに ``(番号, 総数, key)`` で呼ばれる。

    Returns:
        :class:`FilterCensus`。
    """
    from stock_ai.data.jquants_archive import path_for, read_manifest

    report = FilterCensus()
    keys = [
        key for key in sorted(read_manifest(archive_dir)) if endpoint_of(key) == MASTER_ENDPOINT
    ]
    if not keys:
        logger.warning("`%s` の原本が1本も無い。", MASTER_ENDPOINT)
        return report

    total = len(keys)
    for number, key in enumerate(keys, start=1):
        if progress is not None:
            progress(number, total, key)
        try:
            census_payload(read_archived(path_for(archive_dir, key)), report)
        except Exception as exc:  # noqa: BLE001 - どこで読めないかが記録に値する
            report.failed[key] = f"{type(exc).__name__}: {exc}"
            logger.warning("名簿の原本を読めなかった: %s: %s", key, exc)
            continue
        report.files += 1

    logger.info("絞り込みの通り具合: %s", report.summary())
    return report


def baseline(census: FilterCensus) -> str:
    """基準線を1行で。**20年ぶんを取ったとき、これと比べる。**

    日付を書き添える。**いつ測った基準線かが分からなくなると、比べたときに
    「変わった」のか「別のものを見ている」のかが言えない。**
    """
    span = census.span
    if span is None:
        return "基準線なし。"
    no_sector = sum(slice_.no_sector for slice_ in census.by_year.values())
    return (
        f"基準線（{dt.date.today().isoformat()} 時点、{span[0]}〜{span[1]}）: "
        f"`S33` が空 {no_sector / census.rows:.4%}、"
        f"表に無い符号 {sum(census.unknown_sector_codes.values()) / census.rows:.4%}"
    )
