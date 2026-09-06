"""日々公表信用取引残高（`/markets/margin-alert`）を読む。

**候補9 の材料そのものである。** 増担保・日々公表・監理といった規制の別が、
日付と銘柄ごとに並んでいる。JPX も日証金も過去分を配っていない（どちらも
その日の断面だけ）ので、**遡って手に入る経路はここしか見つかっていない。**

Standard で10年、Premium で20年。取れるのは契約している間だけである。

## 読むときに間違えやすいところ

**このプロジェクトで繰り返し起きる不具合は「例外で落ちる間違い」ではなく、
「もっともらしいが違う値が黙って出る」種類のものである。** この CSV には
その罠が4つある。

| 罠 | 素直に読むとどうなるか |
|---|---|
| 欠測が `-` や `*` | `float` に直せず、`0` を入れると「増減なし」になる |
| `PubReason` が Python の辞書の見た目 | `json` では読めない。単引用符である |
| `TSEMrgnRegCls` が `001` | 数値にすると `1` になり、`010` と区別が付かなくなる |
| `PubDate` と `AppDate` が別 | 1営業日ずれる。**早く置くと未公表の日を使う** |

**`-` を `0` にしないこと。** 「増減がゼロ」と「前の値が無いので増減が出せ
ない」は違う。前者は情報だが、後者は欠測である。

## 日付が2つあること

- `AppDate` — その残高が**いつ時点**のものか
- `PubDate` — それが**いつ公表された**か

公表は 16:30 頃なので、**知ってから売買できるのは `PubDate` の翌営業日で
ある。** `AppDate` をイベント日に置くと、まだ公表されていない日の値動きを
使うことになる。ここでは両方を持ち、名前で区別する。
"""

from __future__ import annotations

import ast
import dataclasses
import datetime as dt

from stock_ai.core.logging import get_logger
from stock_ai.data.jquants_bulk import records_from_csv
from stock_ai.data.universe import four_digit_code

logger = get_logger(__name__)

#: 欠測を表す記号。**`0` に直さない。**
#:
#: `-` は「前の値が無いので増減が出せない」、`*` は「比率を出せない」。
#: どちらも `0` とは違う意味である。
MISSING = frozenset({"", "-", "*", "－", "ー"})

#: `PubReason` に入る旗の名前。出典は配布サンプル `sample_data_v2` の
#: `Daily Margin Interest.csv` に実際に入っていた値。
REASONS: tuple[str, ...] = (
    "Restricted",
    "DailyPublication",
    "Monitoring",
    "RestrictedByJSF",
    "PrecautionByJSF",
    "UnclearOrSecOnAlert",
)


@dataclasses.dataclass(frozen=True)
class MarginAlert:
    """1銘柄・1日ぶんの日々公表信用残。"""

    symbol: str
    published: dt.date
    """公表日（`PubDate`）。**売買できるのはこの翌営業日から。**"""

    as_of: dt.date | None
    """残高の時点（`AppDate`）。`published` より前である。"""

    reasons: frozenset[str]
    """立っている旗だけ。立っていないものは入れない。"""

    regulation: str | None
    """東証の信用規制区分（`TSEMrgnRegCls`）。**文字列のまま**持つ。"""

    short_outstanding: float | None
    long_outstanding: float | None
    short_change: float | None
    long_change: float | None
    ratio: float | None
    """貸借倍率（`SLRatio`）。"""

    @property
    def restricted(self) -> bool:
        """増担保（日本語の「増担保規制」）が掛かっているか。"""
        return "Restricted" in self.reasons


def parse_reasons(text: str | None) -> frozenset[str]:
    """`PubReason` の中身から、**立っている旗だけ**を取り出す。

    中身は ``{'Restricted': '0', 'DailyPublication': '1', ...}`` という
    **Python の辞書の見た目**をしている。単引用符なので `json` では読めない
    ——`json.loads` は例外を出すので気付けるが、正規表現で拾おうとすると
    黙って全部落ちる。

    値が ``'1'`` のものだけを返す。**`'0'` を「有る」に数えない**のは当然に
    見えるが、辞書をそのまま `bool` に通すと `'0'` は真になる。
    """
    if not text or text.strip() in MISSING:
        return frozenset()
    try:
        parsed = ast.literal_eval(text.strip())
    except (ValueError, SyntaxError):
        logger.warning("PubReason を読めなかった: %.60s", text)
        return frozenset()
    if not isinstance(parsed, dict):
        return frozenset()
    return frozenset(str(key) for key, value in parsed.items() if str(value).strip() == "1")


def parse_number(text: str | None) -> float | None:
    """数の列を読む。**欠測は `None`。`0` にしない。**

    `-`（前の値が無い）と `0`（増減なし）を同じにすると、規制が掛かった日の
    残高変化が「変化なし」として並ぶ。**例外は出ないし、表も埋まる。**
    """
    if text is None:
        return None
    stripped = text.strip().replace(",", "")
    if stripped in MISSING:
        return None
    try:
        return float(stripped)
    except ValueError:
        return None


def parse_date(text: str | None) -> dt.date | None:
    """`YYYY-MM-DD` と `YYYYMMDD` の両方を読む。"""
    if not text:
        return None
    stripped = text.strip()
    for pattern in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"):
        try:
            return dt.datetime.strptime(stripped, pattern).date()
        except ValueError:
            continue
    return None


def parse_alerts(payload: bytes) -> list[MarginAlert]:
    """展開済みの CSV から :class:`MarginAlert` を作る。

    **公表日を持たない行は落とす。** 日付の無いイベントは並べようがなく、
    黙って `date.min` を入れると、その行が期間の先頭に固まって現れる。

    5桁コードのうち普通株でないもの（優先株・種類株）は落とす。
    `four_digit_code` の判断をそのまま借りる——**符号の規則を2つ持たない。**
    """
    alerts: list[MarginAlert] = []
    for row in records_from_csv(payload):
        symbol = four_digit_code((row.get("Code") or "").strip())
        published = parse_date(row.get("PubDate"))
        if symbol is None or published is None:
            continue
        regulation = (row.get("TSEMrgnRegCls") or "").strip()
        alerts.append(
            MarginAlert(
                symbol=symbol,
                published=published,
                as_of=parse_date(row.get("AppDate")),
                reasons=parse_reasons(row.get("PubReason")),
                regulation=regulation if regulation not in MISSING else None,
                short_outstanding=parse_number(row.get("ShrtOut")),
                long_outstanding=parse_number(row.get("LongOut")),
                short_change=parse_number(row.get("ShrtOutChg")),
                long_change=parse_number(row.get("LongOutChg")),
                ratio=parse_number(row.get("SLRatio")),
            )
        )
    return alerts


def onsets(alerts: list[MarginAlert], reason: str = "Restricted") -> list[tuple[str, dt.date]]:
    """旗が**立っていない日から立った日へ変わった**ところを拾う。

    候補9 のイベント日はここである。「立っている日」を全部数えると、1回の
    規制が何十日ぶんも重複して入り、独立でない観測を独立として数えることに
    なる。

    **その銘柄の最初の観測は、立っていてもイベントにしない。** データの先頭
    は「そこで立った」ではなく「そこから見え始めた」であり、これを数えると
    **取り込み開始日に人為的な山ができる。** 例外は出ない。件数が増えて、
    見栄えはむしろ良くなる。

    Args:
        alerts: 期間ぶん。順序は問わない（ここで並べ替える）。
        reason: 見る旗。既定は増担保（`Restricted`）。

    Returns:
        ``(銘柄, 公表日)`` を公表日の昇順で。**売買できるのはその翌営業日から。**
    """
    by_symbol: dict[str, list[MarginAlert]] = {}
    for item in alerts:
        by_symbol.setdefault(item.symbol, []).append(item)

    found: list[tuple[str, dt.date]] = []
    for symbol, rows in by_symbol.items():
        previous: bool | None = None
        for item in sorted(rows, key=lambda row: row.published):
            now = reason in item.reasons
            if previous is False and now:
                found.append((symbol, item.published))
            previous = now
    return sorted(found, key=lambda pair: (pair[1], pair[0]))
