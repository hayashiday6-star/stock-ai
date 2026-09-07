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

#: 出典: J-Quants 公式 `j-quants-doc-mcp` の `reference_data.json`
#: （コミット 4f9e404、2026-08-27 時点）。**手で写していない**——前に一覧を
#: 手で写して6本綴りを間違えている。
#:
#: **書類種別は45通りある。** 配布サンプルには IFRS・連結の4件しか入っておらず、
#: 最初は `IFRS` / `JP` / `US` の3つだけを見ていた。実際には `JMIS`・`Foreign`・
#: `REIT` があり、**16通りで会計基準が読めていなかった。** 期間も `OtherPeriod`
#: を落としていて、8通りで読めていなかった。どちらも例外は出ず、`None` が並ぶ
#: だけである。
DOCUMENT_TYPES: dict[str, str] = {
    "FYFinancialStatements_Consolidated_JP": "決算短信（連結・日本基準）",
    "FYFinancialStatements_Consolidated_US": "決算短信（連結・米国基準）",
    "FYFinancialStatements_NonConsolidated_JP": "決算短信（非連結・日本基準）",
    "1QFinancialStatements_Consolidated_JP": "第1四半期決算短信（連結・日本基準）",
    "1QFinancialStatements_Consolidated_US": "第1四半期決算短信（連結・米国基準）",
    "1QFinancialStatements_NonConsolidated_JP": "第1四半期決算短信（非連結・日本基準）",
    "2QFinancialStatements_Consolidated_JP": "第2四半期決算短信（連結・日本基準）",
    "2QFinancialStatements_Consolidated_US": "第2四半期決算短信（連結・米国基準）",
    "2QFinancialStatements_NonConsolidated_JP": "第2四半期決算短信（非連結・日本基準）",
    "3QFinancialStatements_Consolidated_JP": "第3四半期決算短信（連結・日本基準）",
    "3QFinancialStatements_Consolidated_US": "第3四半期決算短信（連結・米国基準）",
    "3QFinancialStatements_NonConsolidated_JP": "第3四半期決算短信（非連結・日本基準）",
    "OtherPeriodFinancialStatements_Consolidated_JP": "その他四半期決算短信（連結・日本基準）",
    "OtherPeriodFinancialStatements_Consolidated_US": "その他四半期決算短信（連結・米国基準）",
    "OtherPeriodFinancialStatements_NonConsolidated_JP": "その他四半期決算短信（非連結・日本基準）",
    "FYFinancialStatements_Consolidated_JMIS": "決算短信（連結・ＪＭＩＳ）",
    "1QFinancialStatements_Consolidated_JMIS": "第1四半期決算短信（連結・ＪＭＩＳ）",
    "2QFinancialStatements_Consolidated_JMIS": "第2四半期決算短信（連結・ＪＭＩＳ）",
    "3QFinancialStatements_Consolidated_JMIS": "第3四半期決算短信（連結・ＪＭＩＳ）",
    "OtherPeriodFinancialStatements_Consolidated_JMIS": "その他四半期決算短信（連結・ＪＭＩＳ）",
    "FYFinancialStatements_NonConsolidated_IFRS": "決算短信（非連結・ＩＦＲＳ）",
    "1QFinancialStatements_NonConsolidated_IFRS": "第1四半期決算短信（非連結・ＩＦＲＳ）",
    "2QFinancialStatements_NonConsolidated_IFRS": "第2四半期決算短信（非連結・ＩＦＲＳ）",
    "3QFinancialStatements_NonConsolidated_IFRS": "第3四半期決算短信（非連結・ＩＦＲＳ）",
    "OtherPeriodFinancialStatements_NonConsolidated_IFRS": (
        "その他四半期決算短信（非連結・ＩＦＲＳ）"
    ),
    "FYFinancialStatements_Consolidated_IFRS": "決算短信（連結・ＩＦＲＳ）",
    "1QFinancialStatements_Consolidated_IFRS": "第1四半期決算短信（連結・ＩＦＲＳ）",
    "2QFinancialStatements_Consolidated_IFRS": "第2四半期決算短信（連結・ＩＦＲＳ）",
    "3QFinancialStatements_Consolidated_IFRS": "第3四半期決算短信（連結・ＩＦＲＳ）",
    "OtherPeriodFinancialStatements_Consolidated_IFRS": "その他四半期決算短信（連結・ＩＦＲＳ）",
    "FYFinancialStatements_NonConsolidated_Foreign": "決算短信（非連結・外国株）",
    "1QFinancialStatements_NonConsolidated_Foreign": "第1四半期決算短信（非連結・外国株）",
    "2QFinancialStatements_NonConsolidated_Foreign": "第2四半期決算短信（非連結・外国株）",
    "3QFinancialStatements_NonConsolidated_Foreign": "第3四半期決算短信（非連結・外国株）",
    "OtherPeriodFinancialStatements_NonConsolidated_Foreign": (
        "その他四半期決算短信（非連結・外国株）"
    ),
    "FYFinancialStatements_Consolidated_Foreign": "決算短信（連結・外国株）",
    "1QFinancialStatements_Consolidated_Foreign": "第1四半期決算短信（連結・外国株）",
    "2QFinancialStatements_Consolidated_Foreign": "第2四半期決算短信（連結・外国株）",
    "3QFinancialStatements_Consolidated_Foreign": "第3四半期決算短信（連結・外国株）",
    "OtherPeriodFinancialStatements_Consolidated_Foreign": "その他四半期決算短信（連結・外国株）",
    "FYFinancialStatements_Consolidated_REIT": "決算短信（REIT）",
    "DividendForecastRevision": "配当予想の修正",
    "EarnForecastRevision": "業績予想の修正",
    "REITDividendForecastRevision": "分配予想の修正",
    "REITEarnForecastRevision": "利益予想の修正",
}

#: `DocType` の末尾に来る値。**会計基準とは限らない**（`REIT` が入る）ので、
#: 「基準」と呼びきらずに末尾の区分として扱う。
KNOWN_STANDARDS: tuple[str, ...] = ("Foreign", "IFRS", "JMIS", "JP", "REIT", "US")

#: `DocType` の先頭に来る期間。**長いものから照合する**——`OtherPeriod` を
#: 短いものより後に置くと、先に当たった側が勝ってしまう。
KNOWN_PERIODS: tuple[str, ...] = ("OtherPeriod", "FY", "3Q", "2Q", "1Q")


def is_known_doc_type(doc_type: str | None) -> bool:
    """公式の一覧に載っている書類種別か。

    **載っていなければ、読み取りは推測である。** 一覧は45通りで、増えることが
    ありうる。増えたときに黙って `None` を並べるのではなく、ここで分かる。
    """
    return bool(doc_type) and doc_type in DOCUMENT_TYPES


def describe_doc_type(doc_type: str | None) -> str | None:
    """書類種別の日本語の説明。"""
    return DOCUMENT_TYPES.get(doc_type or "")


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
        else:
            # **大文字に直して照合しない。** 末尾に来る値は `IFRS` のような
            # 頭字語と `Foreign` のような語が混ざっている。`"FOREIGN"` は
            # 一覧のどれとも一致せず、**例外を出さずに `None` になる。**
            match = next(
                (name for name in KNOWN_STANDARDS if name.lower() == lowered),
                None,
            )
            if match is not None:
                standard = match
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


#: 予想の修正を表す書類種別。出典は :data:`DOCUMENT_TYPES`（公式の45種）。
REVISION_TYPES: tuple[str, ...] = (
    "EarnForecastRevision",
    "DividendForecastRevision",
    "REITEarnForecastRevision",
    "REITDividendForecastRevision",
)

#: 決算短信の書類種別を見分ける語。
STATEMENT_MARKER = "FinancialStatements"


@dataclasses.dataclass
class RevisionCensus:
    """予想修正が、決算発表と**別の日に**出ているか。

    **説#5 を閉じた理由そのものを測る。** 記録にはこうある。

        予想修正は**独立した開示として取得できない**。イベント日が決算発表日と
        重なる。「決算とは独立」という前提が崩れる。

    公式の書類種別一覧には `EarnForecastRevision`（業績予想の修正）が**独立
    した種別として載っている。** 載っていることと、別の日に出ることは別で
    ある——**後者を数える。**
    """

    doc_types: dict[str, int] = dataclasses.field(default_factory=dict)
    revisions: int = 0
    on_statement_day: int = 0
    """同じ銘柄・同じ日に決算短信もあった修正。**独立ではない。**"""

    standalone: int = 0
    """決算短信の無い日に単独で出た修正。**これが独立イベントである。**"""

    symbols: set[str] = dataclasses.field(default_factory=set)

    def summary(self) -> str:
        """1行のまとめ。"""
        if not self.revisions:
            return "予想修正の開示が1件も無い。"
        share = self.standalone / self.revisions
        return (
            f"予想修正 {self.revisions:,} 件（{len(self.symbols):,} 銘柄）。"
            f"うち決算と同じ日 {self.on_statement_day:,}、"
            f"**単独 {self.standalone:,}（{share:.0%}）**"
        )


def revision_census(
    items: list[StatementDetail], into: RevisionCensus | None = None
) -> RevisionCensus:
    """予想修正が決算発表日と重なっているかを数える。

    **「取れるか」ではなく「別の日か」を見る。** 一覧に載っていても、いつも
    決算と同じ日に出るなら、独立イベントにはならない——それが #5 を閉じた
    理由である。

    Args:
        items: `fins/summary` の行（`DocType` を持つもの）。
        into: 足し込み先。複数のファイルにまたがって数えるときに渡す。
    """
    census = into or RevisionCensus()
    statement_days = {
        (item.symbol, item.disclosed_on) for item in items if STATEMENT_MARKER in item.doc_type
    }
    for item in items:
        census.doc_types[item.doc_type] = census.doc_types.get(item.doc_type, 0) + 1
        if item.doc_type not in REVISION_TYPES:
            continue
        census.revisions += 1
        census.symbols.add(item.symbol)
        if (item.symbol, item.disclosed_on) in statement_days:
            census.on_statement_day += 1
        else:
            census.standalone += 1
    return census
