"""財務諸表（BS/PL/CF、`/fins/details`）を読む。

Premium 専用で、20年前まで遡れる。**いまの `fins/summary` には貸借対照表も
キャッシュフローも無い**ので、これが入ると EDINET の有報を1日1リクエストで
数百日ぶん走査する経路が要らなくなる。

## この CSV の形

1行が1つの開示で、数字は `FS` 列に**まとめて1つの辞書**として入っている。
列が数百に分かれているのではない。

```
DiscDate,DiscTime,Code,DiscNo,DocType,FS
2022-01-27,12:00:00,86970,20220126573026,
  3QFinancialStatements_Consolidated_IFRS,"{'Assets (IFRS)': '62076519000000', ...}"
```

**`FS` は Python の辞書の見た目**（単引用符）で、`json` では読めない。
`margin-alert` の `PubReason` と同じ形である。

## 鍵の名前が会計基準で変わる

```
'Assets (IFRS)'            IFRS の総資産
'Equity (IFRS)'            IFRS の資本
'Equity attributable to owners of parent (IFRS)'   親会社所有者帰属分
```

**日本基準の開示では鍵の名前が違う。** 配布サンプルには IFRS・連結の4件しか
入っていないので、**日本基準の鍵をここに書かない。** 見ていないものを書けば、
それは出典の無い数字である。

代わりに :func:`field_census` を置いてある。原本を落としたあとに実物の鍵を
数え上げて、**証拠から対応表を書く。** 数百件を目で見るより速く、しかも
間違えない。

## 取り違えると黙って通るところ

| 罠 | 素直に読むとどうなるか |
|---|---|
| `Equity` と `Equity attributable ...` | 前方一致で引くと、少数株主分の有無が入れ替わる |
| 連結と単体 | `DocType` にしか書いていない。数字だけ見ても分からない |
| 無い鍵 | `0` を返すと「資産ゼロの会社」が並ぶ |
| 四半期にキャッシュフローが無い | 四半期の CF 因子は、値が1つも埋まらない |

**引き当ては完全一致だけにしてある。** 前方一致や部分一致を許すと、上の1行目
が例外を出さずに入れ替わる。このプロジェクトが繰り返し踏んでいる型である。
"""

from __future__ import annotations

import ast
import dataclasses
import datetime as dt

from stock_ai.core.logging import get_logger
from stock_ai.data.jquants_bulk import records_from_csv
from stock_ai.data.jquants_margin import parse_date
from stock_ai.data.universe import four_digit_code

logger = get_logger(__name__)

#: `DocType` に現れる会計基準。**実物から取った値だけ**を載せる。
#: 配布サンプルにあるのは `IFRS` だけで、他は原本を見てから足す。
KNOWN_STANDARDS: tuple[str, ...] = ("IFRS", "JP", "US")

#: `DocType` の先頭に来る期間。
KNOWN_PERIODS: tuple[str, ...] = ("1Q", "2Q", "3Q", "FY")


@dataclasses.dataclass(frozen=True)
class StatementDetail:
    """1件の開示。"""

    symbol: str
    disclosed_on: dt.date
    disclosed_at: dt.time | None
    number: str
    """`DiscNo`。同じ日に複数の開示があるとき、これでしか区別できない。"""

    doc_type: str
    """`DocType` の生の値。**加工した派生値と一緒に必ず残す。**"""

    period: str | None
    consolidated: bool | None
    """`DocType` から読んだ連結・単体。**数字からは分からない。**"""

    standard: str | None
    values: dict[str, str]
    """`FS` の中身。**文字列のまま持つ。**"""

    def value(self, *names: str) -> str | None:
        """名前で1つ引く。**完全一致だけ。**

        前方一致を許すと ``Equity`` が
        ``Equity attributable to owners of parent`` を拾い、少数株主分を
        含む・含まないが例外なしで入れ替わる。

        複数渡したときは**先に書いたものが勝つ**。会計基準ごとの別名を
        並べるための順序である。
        """
        for name in names:
            found = self.values.get(name)
            if found is not None and found.strip() != "":
                return found
        return None

    def number_of(self, *names: str) -> float | None:
        """名前で1つ引いて数にする。**無い鍵は `None`。`0` にしない。**

        `0` を返すと「資産ゼロの会社」が並ぶ。合計も平均も通るし、例外も
        出ない。
        """
        text = self.value(*names)
        if text is None:
            return None
        try:
            return float(text.replace(",", ""))
        except ValueError:
            return None


def parse_doc_type(text: str | None) -> tuple[str | None, bool | None, str | None]:
    """`DocType` から ``(期間, 連結か, 会計基準)`` を読む。

    ``3QFinancialStatements_Consolidated_IFRS`` のような形。**読めなかった
    ところは `None` にする**——`False` を入れると「単体である」と読める。
    「単体だと分かっている」と「連結かどうか分からない」は別である。
    """
    if not text:
        return None, None, None
    parts = text.split("_")
    head = parts[0]
    period = next((item for item in KNOWN_PERIODS if head.startswith(item)), None)

    consolidated: bool | None = None
    standard: str | None = None
    for part in parts[1:]:
        lowered = part.lower()
        if lowered.startswith("nonconsolidated"):
            consolidated = False
        elif lowered.startswith("consolidated"):
            consolidated = True
        elif part.upper() in KNOWN_STANDARDS:
            standard = part.upper()
    return period, consolidated, standard


def parse_values(text: str | None) -> dict[str, str]:
    """`FS` 列の辞書を読む。**単引用符なので `json` では読めない。**

    値は文字列のまま返す。`'55967000000.0'` と `'62076519000000'` が混ざって
    いるので、ここで数にすると**表記の違いが型の違いになって**下流に出る。
    """
    if not text or not text.strip():
        return {}
    try:
        parsed = ast.literal_eval(text.strip())
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        logger.warning("FS を読めなかった: %.60s", text)
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(key): str(value) for key, value in parsed.items()}


def parse_time(text: str | None) -> dt.time | None:
    """`DiscTime` を読む。`12:00:00` と `12:00` の両方。"""
    if not text or not text.strip():
        return None
    for pattern in ("%H:%M:%S", "%H:%M"):
        try:
            return dt.datetime.strptime(text.strip(), pattern).time()
        except ValueError:
            continue
    return None


def parse_details(payload: bytes) -> list[StatementDetail]:
    """展開済みの CSV から :class:`StatementDetail` を作る。

    **開示日か銘柄コードを持たない行は落とす。** 日付の無い開示は並べようが
    ない。
    """
    items: list[StatementDetail] = []
    for row in records_from_csv(payload):
        symbol = four_digit_code((row.get("Code") or "").strip())
        disclosed = parse_date(row.get("DiscDate"))
        if symbol is None or disclosed is None:
            continue
        doc_type = (row.get("DocType") or "").strip()
        period, consolidated, standard = parse_doc_type(doc_type)
        items.append(
            StatementDetail(
                symbol=symbol,
                disclosed_on=disclosed,
                disclosed_at=parse_time(row.get("DiscTime")),
                number=(row.get("DiscNo") or "").strip(),
                doc_type=doc_type,
                period=period,
                consolidated=consolidated,
                standard=standard,
                values=parse_values(row.get("FS")),
            )
        )
    return items


def field_census(items: list[StatementDetail]) -> dict[tuple[str | None, bool | None], list]:
    """会計基準・連結の別ごとに、**実際に出てきた鍵**を数える。

    配布サンプルには IFRS・連結しか無いので、日本基準の鍵の名前は**分から
    ない。** 分からないものを対応表に書けば、それは出典の無い数字になる。

    原本を落としたあとにこれを回して、**証拠から対応表を書く。** 数百件を目で
    見るより速く、しかも間違えない。

    Returns:
        ``(会計基準, 連結か)`` ごとに ``(鍵, 出てきた件数)`` を件数の多い順で。
    """
    counts: dict[tuple[str | None, bool | None], dict[str, int]] = {}
    for item in items:
        bucket = counts.setdefault((item.standard, item.consolidated), {})
        for key in item.values:
            bucket[key] = bucket.get(key, 0) + 1
    return {
        group: sorted(bucket.items(), key=lambda pair: (-pair[1], pair[0]))
        for group, bucket in counts.items()
    }
