"""有価証券報告書の「主要な経営指標等」から財務の時系列を読む。

J-Quants の有料プランを外すのに残る最後の穴。立花が返すのは比率のスナップ
ショットだけで、``factor-test`` が要る過去時点の財務は取れない。EDINET の
有報にはそれがあり、無料で、しかも**1つの有報に5年分**入っている。

``documents/{docID}?type=5`` は XBRL を CSV に変換したものを ZIP で返す。
UTF-16LE のタブ区切りで、``jpcrp`` で始まるファイルが本体（``jpaud`` は監査
報告書）。iXBRL 一式（3.7MB・86ファイル）を解析する必要はない。

実データ（日立 6501, 2026年6月提出）で確かめた罠が3つある。どれも数字は出る。

**1. 同じ表に連結と単体が同居する。見分けるのはコンテキストID。** 「連結・個別」
列はどちらも「その他」で役に立たない。効くのは ``コンテキストID`` の
``_NonConsolidatedMember`` という接尾辞で、これが付いている行が提出会社単体。
日立の当期純利益は連結 802,368 百万に対し単体 784,025 百万、純資産に至っては
6,568,369 対 3,949,169 と4割違う。

要素名でも見分けられるように**見える**。IFRS 適用会社では連結が
``...IFRSSummaryOfBusinessResults``、単体が ``...SummaryOfBusinessResults`` に
なっていて、日立ではコンテキストによる分類と完全に一致した。だがこれは IFRS
適用会社に限った話で、**日本基準の会社では連結も単体も同じ要素名を使う**。名前
だけで選ぶと、そこで単体を掴む。だからまずコンテキストで絞る。

連結財務諸表を作っていない会社（子会社が無い）では連結の行が1つも無い。その
ときだけ単体に落ちる。

**2. EPS だけが分割調整されている。** 発行済株式数と1株配当は当時のまま。
純利益 ÷ EPS で株数を逆算すると、分割前の年は報告値のちょうど5倍になる。
配当性向を「1株配当 ÷ EPS」で出すと、日立の前々期は 141.8%（実際は 28.8%）。
**1株当たりの値どうしを、年をまたいで割ってはいけない。**

**3. 相対年度は「当期」と「当期末」が別。** 期間の項目（売上・利益）と時点の
項目（純資産・総資産・株式数）でラベルが違う。片方だけ見ると3年分しか揃わない。

**4. 要素名が意味を保証しない。** 日立の
``EquityToAssetRatioIFRSSummaryOfBusinessResults`` は「自己資本比率」ではなく
「１株当たり親会社所有者帰属持分（IFRS）」＝BPS で、値は 897.78 円。同じ有報の
``EquityToAssetRatioSummaryOfBusinessResults`` のほうは本当に自己資本比率
（0.454）。EDINET は会計基準ごとに別の概念へ同じ名前を使い回している。名前で
選ぶときは項目名と値の桁を必ず確かめること。

**5. 相対年度しか無いので、決算年度は自分で解決する。** 表には「当期」「前期」
としか書いていない。実際の年度は ``jpdei`` の ``CurrentFiscalYearEndDateDEI``
から1年ずつ遡って割り当てる。銘柄コードも同じ ``jpdei`` にあるが5桁
（日立は ``65010``）で、末尾の株式種別を落とさないと watchlist と噛み合わない。

このモジュールは**絶対額と、報告された比率**だけを取る。1株当たりの値から
何かを導かない。
"""

from __future__ import annotations

import csv
import dataclasses
import datetime as dt
import io
import zipfile

from pydantic import SecretStr

from stock_ai.core.exceptions import DataError
from stock_ai.core.logging import get_logger
from stock_ai.data.types import FinancialReport, FiscalPeriod
from stock_ai.ir.edinet import CURRENT_PLACEMENT, EdinetDisclosureSource, key_placements

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

#: 書類そのものを取る口。``type`` で中身が変わり、``5`` だけが CSV 変換版。
_DOCUMENT_URL = "https://api.edinet-fsa.go.jp/api/v2/documents/{doc_id}"

#: XBRL を CSV に変換したもの。``1`` は iXBRL 一式（3.7MB・86ファイル）、``2`` は
#: PDF、``3`` は代替書面、``4`` は英文。``5`` が要るものだけを持っている。
CSV_TYPE = "5"

#: 有価証券報告書と、その訂正。訂正のほうが新しく、数字が直っている。
ANNUAL_REPORT_TYPES = ("120", "130")

#: 有報の素性が入っている要素。相対年度は ``提出日時点`` の1件だけ。
_FISCAL_YEAR_END = "CurrentFiscalYearEndDateDEI"
_PREVIOUS_YEAR_END = "PreviousFiscalYearEndDateDEI"
_SECURITY_CODE = "SecurityCodeDEI"
_ACCOUNTING_STANDARD = "AccountingStandardsDEI"
_FILER_NAME = "FilerNameInJapaneseDEI"

#: コンテキストIDに付く、提出会社単体を表す接尾辞。
#:
#: ``CurrentYearDuration_NonConsolidatedMember`` のように付く。「連結・個別」列は
#: 連結にも単体にも「その他」を入れるので、見分けられるのはここだけ。
NON_CONSOLIDATED = "NonConsolidatedMember"

#: EDINET が未記載に使う印。どれも 0 ではない。
_BLANKS = frozenset(("", "-", "－", "―"))

#: :class:`AnnualFigures` の項目名 -> 会計基準ごとの、連結の要素名。
#:
#: 並びがそのまま優先順位。IFRS を先に見る。IFRS 適用会社の CSV には日本基準名の
#: 要素も入っているが、それは提出会社単体の表であって連結ではない。
#:
#: 日本基準側の名前は日立（IFRS）1本から起こしたもので、日本基準の会社で同じ名前が
#: 使われている確証はまだない。``tools/edinet_financials_check.py`` が、実際の有報に
#: 出てくる要素とここを突き合わせて穴を出す。
ELEMENTS: dict[str, tuple[str, ...]] = {
    "revenue": (
        "RevenueIFRSSummaryOfBusinessResults",
        "NetSalesSummaryOfBusinessResults",
    ),
    "net_income": (
        "ProfitLossAttributableToOwnersOfParentIFRSSummaryOfBusinessResults",
        "NetIncomeLossSummaryOfBusinessResults",
    ),
    "equity": (
        "EquityAttributableToOwnersOfParentIFRSSummaryOfBusinessResults",
        "NetAssetsSummaryOfBusinessResults",
    ),
    "total_assets": (
        "TotalAssetsIFRSSummaryOfBusinessResults",
        "TotalAssetsSummaryOfBusinessResults",
    ),
    "roe": (
        "RateOfReturnOnEquityIFRSSummaryOfBusinessResults",
        "RateOfReturnOnEquitySummaryOfBusinessResults",
    ),
    "shares_outstanding": ("TotalNumberOfIssuedSharesSummaryOfBusinessResults",),
}

#: 時点の項目。相対年度が「当期末」側のラベルになる。残りは期間の項目。
INSTANT_FIELDS = frozenset({"equity", "total_assets", "shares_outstanding"})

#: 連結・単体の区別が意味を持たない項目。連結で絞る前の行から探す。
#:
#: 発行済株式総数は提出会社そのものの事実で、連結にしたところで別の数にはならない。
#: EDINET もそう扱っていて、この行には常に ``_NonConsolidatedMember`` が付く。連結の
#: 行だけに絞ると、単体の財務を弾くのと同じ理屈でこれも消え、株式数の列だけが空欄に
#: なる。例外は出ない。
ENTITY_FIELDS = frozenset({"shares_outstanding"})


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


@dataclasses.dataclass(frozen=True)
class FilingHeader:
    """有報そのものの素性。数字を年度と銘柄に結び付けるのに要る。"""

    symbol: str | None = None
    """4桁の銘柄コード。上場していない提出会社では ``None``。"""

    fiscal_year_end: dt.date | None = None
    """当期の決算日。相対年度を実際の年度に直す基準。"""

    accounting_standard: str | None = None
    """``IFRS`` / ``Japan GAAP`` / ``US GAAP``。どの要素名を掴んだかの裏取りに使う。"""

    filer_name: str | None = None


def _text(rows: list[dict[str, str]], name: str) -> str | None:
    """``jpdei`` の1項目を読む。未記載の印は値として返さない。"""
    for row in rows:
        if row.get(ELEMENT, "").endswith(name):
            value = (row.get(VALUE) or "").strip()
            if value not in _BLANKS:
                return value
    return None


def parse_header(rows: list[dict[str, str]]) -> FilingHeader:
    """``jpdei`` から、有報の素性を読む。"""
    raw_code = _text(rows, _SECURITY_CODE)
    # EDINET の銘柄コードは5桁で、末尾は株式の種別。日立は 65010。
    symbol = raw_code[:4] if raw_code and raw_code[:4].isdigit() else None

    end = _text(rows, _FISCAL_YEAR_END)
    try:
        fiscal_year_end = dt.date.fromisoformat(end) if end else None
    except ValueError:
        logger.warning("決算日として読めません: %r", end)
        fiscal_year_end = None

    return FilingHeader(
        symbol=symbol,
        fiscal_year_end=fiscal_year_end,
        accounting_standard=_text(rows, _ACCOUNTING_STANDARD),
        filer_name=_text(rows, _FILER_NAME),
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


def is_consolidated(row: dict[str, str]) -> bool:
    """その行が連結の数字か。単体なら ``False``。

    「連結・個別」列は両方に「その他」を入れるので使えない。コンテキストIDの
    ``_NonConsolidatedMember`` だけが実際に分かれている。
    """
    return NON_CONSOLIDATED not in row.get(CONTEXT, "")


def summary_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """「主要な経営指標等」の行だけにする。"""
    return [r for r in rows if "SummaryOfBusinessResults" in r.get(ELEMENT, "")]


def consolidated_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """連結の行だけにする。連結が1行も無ければ、渡された行をそのまま返す。

    子会社を持たない会社は連結財務諸表を作らないので、単体しか無い。そこで空を
    返すと、有報はあるのに何も読めないことになる。どちらを使ったかはログに残す。
    """
    consolidated = [r for r in rows if is_consolidated(r)]
    if consolidated:
        return consolidated
    logger.info("連結の行がありません。提出会社単体の数字を使います。")
    return rows


def _pick(rows: list[dict[str, str]], names: tuple[str, ...], year: str) -> float | None:
    """``names`` を優先順に探し、``year`` の値を返す。

    連結で絞った後の第二の防御線。IFRS 適用会社の表には日本基準名の要素も残る
    ことがあるので、順序で IFRS を先に見る。
    """
    for name in names:
        for row in rows:
            if not row.get(ELEMENT, "").endswith(name):
                continue
            if row.get(RELATIVE_YEAR) != year:
                continue
            raw = (row.get(VALUE) or "").strip()
            if raw in _BLANKS:
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
    summary = summary_rows(rows)
    group = consolidated_rows(summary)
    figures: list[AnnualFigures] = []
    for duration, instant in YEAR_LABELS:
        values = {
            field: _pick(
                summary if field in ENTITY_FIELDS else group,
                names,
                instant if field in INSTANT_FIELDS else duration,
            )
            for field, names in ELEMENTS.items()
        }
        entry = AnnualFigures(year=duration, **values)
        if not entry.is_empty():
            figures.append(entry)
    return figures


def to_reports(
    header: FilingHeader, figures: list[AnnualFigures], symbol: str | None = None
) -> list[FinancialReport]:
    """相対年度の並びを、決算年度の付いた :class:`FinancialReport` にする。

    年度は当期の決算日の**年**。J-Quants 側が ``FYEnd`` の年を使っており、揃えて
    おかないと同じ決算期が2行に分かれる（``(security, fiscal_year, period)`` が
    一意キー）。3月期なら 2026-03-31 は 2026 年度。

    そこから1期につき1年ずつ遡る。決算期を変更した会社ではずれるが、有報の表は
    相対年度しか持たないので、これ以上のことは1本の有報からは分からない。

    Args:
        header: ``parse_header`` の結果。決算日が無ければ何も返せない。
        figures: ``parse_summary`` の結果。古い順。
        symbol: 銘柄コードを外から与える。有報が上場コードを持たないときの逃げ道。

    Raises:
        DataError: 決算日か銘柄コードが解決できない。
    """
    code = symbol or header.symbol
    if not code:
        raise DataError("有報から銘柄コードを読めませんでした（未上場の提出会社の可能性）。")
    if header.fiscal_year_end is None:
        raise DataError(f"{code}: 有報から決算日を読めず、相対年度を年度に直せません。")

    latest = header.fiscal_year_end.year
    reports = []
    for offset, entry in enumerate(reversed(figures)):
        reports.append(
            FinancialReport(
                symbol=code,
                fiscal_year=latest - offset,
                period=FiscalPeriod.FY,
                revenue=entry.revenue,
                net_income=entry.net_income,
                equity=entry.equity,
                shares_outstanding=entry.shares_outstanding,
            )
        )
    reports.reverse()
    return reports


def parse_filing(body: bytes, symbol: str | None = None) -> list[FinancialReport]:
    """``type=5`` の応答から、5期ぶんの財務を取り出す。

    ここが入口。ZIP のバイト列を渡すと、そのまま
    :class:`~stock_ai.database.repository.FinancialStatementRepository` に入れられる
    並びが返る。
    """
    rows = read_csv_zip(body)
    figures = parse_summary(rows)
    if not figures:
        raise DataError("「主要な経営指標等」から1期ぶんも読めませんでした。")
    header = parse_header(rows)
    reports = to_reports(header, figures, symbol)
    logger.info(
        "%s (%s): %d 期ぶんを読みました（%d〜%d 年度）",
        reports[0].symbol,
        header.accounting_standard or "会計基準不明",
        len(reports),
        reports[0].fiscal_year,
        reports[-1].fiscal_year,
    )
    return reports


def fetch_document(doc_id: str, api_key: SecretStr | None = None, timeout: float = 60.0) -> bytes:
    """``type=5`` の ZIP をそのまま落とす。

    EDINET は断った要求にも **HTTP 200** を返し、本文を JSON のエラーにする。
    ここで ZIP かどうかを見ないと、``read_csv_zip`` が「ZIP ではありません」と
    言うだけになり、鍵が無いのか書類が無いのか分からなくなる。

    Raises:
        DataError: HTTP エラー、または ZIP 以外が返った。
    """
    import httpx

    params = {"type": CSV_TYPE}
    headers: dict[str, str] = {}
    if api_key is not None:
        extra_params, extra_headers = key_placements(api_key.get_secret_value())[CURRENT_PLACEMENT]
        params.update(extra_params)
        headers.update(extra_headers)

    url = _DOCUMENT_URL.format(doc_id=doc_id)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        response = client.get(url, params=params, headers=headers)
    if response.status_code >= 400:
        raise DataError(f"{doc_id}: EDINET が HTTP {response.status_code} を返しました。")

    body = response.content
    if body[:2] != b"PK":
        # 200 で返ってくるエラー本文。鍵が無い / 書類が無い / type が違う のどれか。
        detail = body[:200].decode("utf-8", errors="replace").strip()
        hint = "" if api_key is not None else " EDINET_API_KEY が未設定です。"
        raise DataError(f"{doc_id}: ZIP ではなく {detail!r} が返りました。{hint}")

    logger.info("%s: type=%s を %d バイト取得しました", doc_id, CSV_TYPE, len(body))
    return body


def fetch_annual_reports(
    symbol: str,
    api_key: SecretStr | None = None,
    lookback_days: int = 400,
    source: EdinetDisclosureSource | None = None,
) -> list[FinancialReport]:
    """``symbol`` の直近の有報から、5期ぶんの財務を取る。

    有報は年に1度しか出ないので、既定の窓は400日。1日1リクエストで、日ごとの
    応答は :class:`EdinetDisclosureSource` が抱え込むため、同じインスタンスを
    使い回せば銘柄が何本あっても走査の費用は変わらない。逆に1銘柄だけのために
    呼ぶと400リクエスト掛かる。

    Raises:
        DataError: 窓の中に有報が見つからない。
    """
    finder = source or EdinetDisclosureSource(api_key=api_key, lookback_days=lookback_days)
    doc_ids = finder.find_documents(symbol, ANNUAL_REPORT_TYPES, limit=1)
    if not doc_ids:
        raise DataError(
            f"{symbol}: 直近 {finder.lookback_days} 日に有価証券報告書がありません。"
            "窓を広げるか、決算から3ヶ月後あたりに実行してください。"
        )
    return parse_filing(fetch_document(doc_ids[0], api_key), symbol=symbol)
