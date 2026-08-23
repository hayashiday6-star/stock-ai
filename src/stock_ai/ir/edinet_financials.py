"""有価証券報告書の「主要な経営指標等」から財務の時系列を読む。

J-Quants の有料プランを外すのに残る最後の穴。立花が返すのは比率のスナップ
ショットだけで、``factor-test`` が要る過去時点の財務は取れない。EDINET の
有報にはそれがあり、無料で、しかも**1つの有報に5年分**入っている。

``documents/{docID}?type=5`` は XBRL を CSV に変換したものを ZIP で返す。
UTF-16LE のタブ区切りで、``jpcrp`` で始まるファイルが本体（``jpaud`` は監査
報告書）。iXBRL 一式（3.7MB・86ファイル）を解析する必要はない。

実データ（日立 6501, 2026年6月提出）で確かめた罠が3つある。どれも数字は出る。

**1. 同じ表に連結と単体が同居し、区分の列では見分けられない。** IFRS 適用会社
では、連結が ``...IFRSSummaryOfBusinessResults``、提出会社単体が
``...SummaryOfBusinessResults`` で、どちらも「連結・個別」列は「その他」になる。
日立の当期純利益は連結 802,368 百万に対し単体 784,025 百万、純資産に至っては
6,568,369 対 3,949,169 と4割違う。名前で選ばなければ、それらしい別会社の数字を
持つことになる。

**2. EPS だけが分割調整されている。** 発行済株式数と1株配当は当時のまま。
純利益 ÷ EPS で株数を逆算すると、分割前の年は報告値のちょうど5倍になる。
配当性向を「1株配当 ÷ EPS」で出すと、日立の前々期は 141.8%（実際は 28.8%）。
**1株当たりの値どうしを、年をまたいで割ってはいけない。**

**3. 相対年度は「当期」と「当期末」が別。** 期間の項目（売上・利益）と時点の
項目（純資産・総資産・株式数）でラベルが違う。片方だけ見ると3年分しか揃わない。

このモジュールは**絶対額と、報告された比率**だけを取る。1株当たりの値から
何かを導かない。
"""

from __future__ import annotations

import csv
import dataclasses
import io
import zipfile

from stock_ai.core.exceptions import DataError
from stock_ai.core.logging import get_logger

logger = get_logger(__name__)

#: CSV の列名。EDINET が付けるものをそのまま使う。
ELEMENT, LABEL, CONTEXT = "要素ID", "項目名", "コンテキストID"
RELATIVE_YEAR, BASIS, PERIOD_KIND = "相対年度", "連結・個別", "期間・時点"
UNIT_ID, UNIT, VALUE = "ユニットID", "単位", "値"

#: 期間の項目と時点の項目で、相対年度のラベルが違う。同じ決算期を指す組。
#:
#: 古い順。1つの有報に5期ぶん入っており、これが J-Quants の無料プラン（2年）を
#: 置き換えられる理由。
YEAR_LABELS: tuple[tuple[str, str], ...] = (
    ("四期前", "四期前時点"),
    ("三期前", "三期前時点"),
    ("前々期", "前々期末"),
    ("前期", "前期末"),
    ("当期", "当期末"),
)

#: 会計基準ごとの、連結の要素名。IFRS を先に見る。
#:
#: IFRS 適用会社の CSV には日本基準名の要素も入っているが、それは提出会社単体の
#: 表であって連結ではない。順序がそのまま優先順位になる。
_REVENUE = ("RevenueIFRSSummaryOfBusinessResults", "NetSalesSummaryOfBusinessResults")
_NET_INCOME = (
    "ProfitLossAttributableToOwnersOfParentIFRSSummaryOfBusinessResults",
    "NetIncomeLossSummaryOfBusinessResults",
)
_EQUITY = (
    "EquityAttributableToOwnersOfParentIFRSSummaryOfBusinessResults",
    "NetAssetsSummaryOfBusinessResults",
)
_TOTAL_ASSETS = (
    "TotalAssetsIFRSSummaryOfBusinessResults",
    "TotalAssetsSummaryOfBusinessResults",
)
_ROE = (
    "RateOfReturnOnEquityIFRSSummaryOfBusinessResults",
    "RateOfReturnOnEquitySummaryOfBusinessResults",
)
_SHARES = ("TotalNumberOfIssuedSharesSummaryOfBusinessResults",)


@dataclasses.dataclass(frozen=True)
class AnnualFigures:
    """1決算期ぶんの、絶対額と報告された比率。

    1株当たりの値は持たない。EPS は分割調整済みで発行済株式数と1株配当は当時の
    ままという食い違いがあり、両者を組み合わせた指標は年をまたぐと壊れる。必要な
    ものは絶対額から現在の株数で導く。
    """

    year: str
    """相対年度のラベル（``当期``、``前期`` など）。決算日は別に解決する。"""

    revenue: float | None = None
    net_income: float | None = None
    equity: float | None = None
    total_assets: float | None = None
    roe: float | None = None
    shares_outstanding: float | None = None

    def is_empty(self) -> bool:
        """何も読めなかったか。"""
        return all(
            getattr(self, f.name) is None for f in dataclasses.fields(self) if f.name != "year"
        )


def read_csv_zip(body: bytes) -> list[dict[str, str]]:
    """``type=5`` の ZIP から、本体 CSV の行を読む。

    ``jpaud`` で始まるのは監査報告書で、財務の数字は入っていない。``jpcrp`` が
    本体。UTF-16LE のタブ区切りで、全項目が引用符で囲まれている。

    Raises:
        DataError: ZIP でない、または本体 CSV が入っていない。
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(body))
    except zipfile.BadZipFile:
        raise DataError(
            f"EDINET の type=5 応答が ZIP ではありません（先頭 {body[:8]!r}）。"
        ) from None

    names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
    main = [n for n in names if "jpcrp" in n.lower()]
    if not main:
        raise DataError(f"本体の CSV (jpcrp…) が入っていません。収録: {names or '（CSV なし）'}")
    if len(main) > 1:
        logger.warning("本体候補が %d 件あります。先頭を使います: %s", len(main), main)

    text = archive.read(main[0]).decode("utf-16")
    return list(csv.DictReader(io.StringIO(text), delimiter="\t"))


def _pick(rows: list[dict[str, str]], names: tuple[str, ...], year: str) -> float | None:
    """``names`` を優先順に探し、``year`` の値を返す。

    順序が会計基準の優先順位そのもの。IFRS 適用会社の CSV には日本基準名の要素も
    入っているが、それは提出会社単体の表であって連結ではない。
    """
    for name in names:
        for row in rows:
            if not row.get(ELEMENT, "").endswith(name):
                continue
            if row.get(RELATIVE_YEAR) != year:
                continue
            raw = (row.get(VALUE) or "").strip()
            if raw in ("", "-", "－", "―"):
                continue
            try:
                return float(raw)
            except ValueError:
                logger.debug("数値として読めない値を飛ばしました: %s=%r", name, raw)
    return None


def parse_summary(rows: list[dict[str, str]]) -> list[AnnualFigures]:
    """「主要な経営指標等」を、古い順の決算期の並びにする。

    値が1つも取れなかった期は落とす。有報によっては5期揃わない（上場から日が
    浅い、会計基準を変えた、など）。
    """
    figures: list[AnnualFigures] = []
    for duration, instant in YEAR_LABELS:
        entry = AnnualFigures(
            year=duration,
            revenue=_pick(rows, _REVENUE, duration),
            net_income=_pick(rows, _NET_INCOME, duration),
            equity=_pick(rows, _EQUITY, instant),
            total_assets=_pick(rows, _TOTAL_ASSETS, instant),
            roe=_pick(rows, _ROE, duration),
            shares_outstanding=_pick(rows, _SHARES, instant),
        )
        if not entry.is_empty():
            figures.append(entry)
    return figures


def parse_filing(body: bytes) -> list[AnnualFigures]:
    """``type=5`` の応答から、5期ぶんの財務を取り出す。"""
    figures = parse_summary(read_csv_zip(body))
    if not figures:
        raise DataError("「主要な経営指標等」から1期ぶんも読めませんでした。")
    logger.info("有報から %d 期ぶんを読みました", len(figures))
    return figures
