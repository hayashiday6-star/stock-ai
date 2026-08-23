"""有報の「主要な経営指標等」を読む。

固定値は日立 6501 の実際の有報（docID ``S100YGBO``, 2026年6月提出）から取った。
``tests/fixtures/edinet_6501_summary.tsv`` は ``type=5`` の ZIP に入っていた本体
CSV から SummaryOfBusinessResults の行だけを抜いたもので、UTF-16LE・タブ区切り・
全項目引用符という EDINET の形式をそのまま保っている。

このファイルが守っているのは主に**選び間違い**で、どれも例外は出ない。IFRS 適用
会社の CSV には連結と単体が同居していて、名前でしか区別できない。単体を掴んでも
それらしい5期ぶんの表が出るので、テストが無ければ気づかない。
"""

from __future__ import annotations

import datetime as dt
import io
import logging
import pathlib
import zipfile
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import SecretStr

from stock_ai.core.exceptions import DataError
from stock_ai.data.types import FiscalPeriod
from stock_ai.ir.edinet import EdinetDisclosureSource
from stock_ai.ir.edinet_financials import (
    ELEMENTS,
    INSTANT_FIELDS,
    NON_CONSOLIDATED,
    AnnualFigures,
    FilingHeader,
    element_name,
    fetch_annual_reports,
    fetch_document,
    is_consolidated,
    parse_filing,
    parse_header,
    parse_summary,
    read_csv_zip,
    summary_rows,
    to_reports,
)

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "edinet_6501_summary.tsv"

#: EDINET が ZIP に入れてくるパス。実際の ``S100YGBO`` の ``type=5`` の中身と
#: 同じ3本で、本体は ``jpcrp`` の1本だけ。``jpaud`` 2本は監査報告書。
MAIN_CSV = "XBRL_TO_CSV/jpcrp030000-asr-001_E01737-000_2026-03-31_01_2026-06-22.csv"
AUDIT_CSV = "XBRL_TO_CSV/jpaud-aai-cc-001_E01737-000_2026-03-31_01_2026-06-22.csv"
AUDIT_CSV2 = "XBRL_TO_CSV/jpaud-aar-cn-001_E01737-000_2026-03-31_01_2026-06-22.csv"

#: 有報の素性。同じ ``S100YGBO`` から。相対年度は「提出日時点」の1件だけ。
DEI = (
    ("SecurityCodeDEI", "提出日時点", "65010"),
    ("CurrentFiscalYearEndDateDEI", "提出日時点", "2026-03-31"),
    ("AccountingStandardsDEI", "提出日時点", "IFRS"),
    ("FilerNameInJapaneseDEI", "提出日時点", "株式会社日立製作所"),
)

#: 億円。実データの照合を読みやすくするためだけの定数。
OKU = 100_000_000.0


@pytest.fixture(scope="module")
def fixture_bytes() -> bytes:
    """実際の有報から抜いた CSV を、そのままのバイト列で。"""
    return FIXTURE.read_bytes()


@pytest.fixture(scope="module")
def rows(fixture_bytes: bytes) -> list[dict[str, str]]:
    """CSV の行。ZIP を経由して、実運用と同じ経路で読む。"""
    return read_csv_zip(make_zip(fixture_bytes))


@pytest.fixture(scope="module")
def years(rows: list[dict[str, str]]) -> dict[str, AnnualFigures]:
    """相対年度で引ける5期ぶん。"""
    return {f.year: f for f in parse_summary(rows)}


def make_zip(*members: bytes, names: tuple[str, ...] = (MAIN_CSV,)) -> bytes:
    """``type=5`` が返すのと同じ形の ZIP を組む。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, body in zip(names, members, strict=True):
            archive.writestr(name, body)
    return buffer.getvalue()


def utf16(text: str) -> bytes:
    """EDINET と同じ UTF-16LE + BOM にする。"""
    return text.encode("utf-16")


HEADER = "\t".join(
    f'"{c}"'
    for c in (
        "要素ID",
        "項目名",
        "コンテキストID",
        "相対年度",
        "連結・個別",
        "期間・時点",
        "ユニットID",
        "単位",
        "値",
    )
)


def csv_text(*entries: tuple[str, ...]) -> str:
    """(要素ID, 相対年度, 値[, コンテキストID]) から、最小の CSV を作る。

    ``jpdei`` の項目は接頭辞が違う。要素名で振り分けて、実物と同じ形にする。
    「連結・個別」列は実物と同じく常に「その他」を入れる――そこでは何も分からない、
    というのがこのモジュールの前提だから。
    """
    lines = [HEADER]
    for element, year, value, *rest in entries:
        prefix = "jpdei_cor" if element.endswith("DEI") else "jpcrp_cor"
        context = rest[0] if rest else ""
        cells = (f"{prefix}:{element}", "", context, year, "その他", "", "JPY", "円", value)
        lines.append("\t".join(f'"{c}"' for c in cells))
    return "\r\n".join(lines)


# --- ZIP の取り出し -------------------------------------------------------


def test_read_csv_zip_reads_the_body(fixture_bytes: bytes) -> None:
    """本体 CSV の全行が読める。"""
    assert len(read_csv_zip(make_zip(fixture_bytes))) == 187


def test_read_csv_zip_ignores_the_audit_reports(fixture_bytes: bytes) -> None:
    """``jpaud`` は監査報告書。実際の ZIP でも本体より先に並んでいる。

    順番で選ぶと監査報告書を読み、財務が1つも取れない。名前で選ぶ。
    """
    assert AUDIT_CSV < MAIN_CSV
    audit = utf16(csv_text(("NetSalesSummaryOfBusinessResults", "当期", "1")))
    body = make_zip(audit, audit, fixture_bytes, names=(AUDIT_CSV, AUDIT_CSV2, MAIN_CSV))
    assert len(read_csv_zip(body)) == 187


def test_read_csv_zip_warns_when_the_body_is_ambiguous(
    fixture_bytes: bytes, caplog: pytest.LogCaptureFixture
) -> None:
    """本体が2本ある有報は見たことがない。黙って片方を選ぶより言う。"""
    other = MAIN_CSV.replace("asr-001", "asr-002")
    with caplog.at_level(logging.WARNING):
        assert (
            len(read_csv_zip(make_zip(fixture_bytes, fixture_bytes, names=(MAIN_CSV, other))))
            == 187
        )
    assert "本体候補が 2 件" in caplog.text


def test_read_csv_zip_rejects_a_non_zip() -> None:
    """``type`` を間違えると JSON や PDF が返る。ZIP として開けないと言う。"""
    with pytest.raises(DataError, match="ZIP ではありません"):
        read_csv_zip(b'{"metadata":{"status":"404"}}')


def test_read_csv_zip_rejects_an_archive_without_the_body() -> None:
    """監査報告書しか入っていない ZIP は、財務としては空。"""
    body = make_zip(utf16(HEADER), names=(AUDIT_CSV,))
    with pytest.raises(DataError, match="jpcrp"):
        read_csv_zip(body)


# --- 要素表と dataclass が食い違わないこと -------------------------------


def test_every_element_maps_onto_a_field() -> None:
    """``ELEMENTS`` の鍵は ``AnnualFigures`` の項目名。綴り違いは ``TypeError`` になる。

    ``parse_summary`` は ``AnnualFigures(**values)`` で組むので、片方だけ足すと
    起動時に落ちる。テストがあるのは、落ちる場所が読んだ有報の中身に依存しない
    ことをはっきりさせるため。
    """
    fields = set(AnnualFigures.__dataclass_fields__) - {"year"}
    assert set(ELEMENTS) == fields


def test_instant_fields_are_a_subset_of_the_elements() -> None:
    """時点の項目は、必ず表にある項目でなければならない。

    ここが外れると、対応する項目は「当期末」ではなく「当期」で探されて必ず空に
    なる。5期ぶんの表がその列だけ空欄で出てくるだけで、例外は出ない。
    """
    instants = set(INSTANT_FIELDS)
    assert instants <= set(ELEMENTS)
    assert instants == {"equity", "total_assets", "shares_outstanding"}


# --- 罠1: 連結と単体が同居する -------------------------------------------


def test_net_income_takes_the_consolidated_figure(years: dict[str, AnnualFigures]) -> None:
    """当期純利益は連結 8,023億。単体の 7,840億ではない。"""
    assert years["当期"].net_income == 802_368_000_000.0


def test_equity_takes_the_consolidated_figure(years: dict[str, AnnualFigures]) -> None:
    """自己資本は連結 65,683億。単体の 39,491億とは4割違う。"""
    assert years["当期"].equity == 6_568_369_000_000.0
    assert years["当期"].equity / 3_949_169_000_000.0 == pytest.approx(1.66, abs=0.01)


def test_total_assets_takes_the_consolidated_figure(years: dict[str, AnnualFigures]) -> None:
    """総資産は連結 150,412億。単体の 69,326億は2倍以上違う。

    この要素は IFRS 名と日本基準名が**両方**この CSV に入っている。優先順位が
    効いていることを、実データだけで確かめられる数少ない項目。
    """
    assert years["当期"].total_assets == 15_041_246_000_000.0


def test_roe_takes_the_consolidated_figure(years: dict[str, AnnualFigures]) -> None:
    """ROE も連結 12.9%。単体は 20.8% で、良い会社に見えてしまう。"""
    assert years["当期"].roe == pytest.approx(0.129)


def test_falls_back_to_japanese_gaap_when_there_is_no_ifrs() -> None:
    """日本基準の会社には IFRS 名の要素が無い。そのときだけ日本基準名を使う。"""
    body = make_zip(
        utf16(
            csv_text(
                ("NetSalesSummaryOfBusinessResults", "当期", "5000000000"),
                ("NetIncomeLossSummaryOfBusinessResults", "当期", "300000000"),
                ("NetAssetsSummaryOfBusinessResults", "当期末", "2000000000"),
            )
        )
    )
    (current,) = parse_summary(read_csv_zip(body))
    assert (current.revenue, current.net_income, current.equity) == (
        5_000_000_000.0,
        300_000_000.0,
        2_000_000_000.0,
    )


def test_ifrs_wins_even_when_the_japanese_gaap_row_comes_first() -> None:
    """優先順位は行の並びではなく要素名で決まる。"""
    body = make_zip(
        utf16(
            csv_text(
                ("NetIncomeLossSummaryOfBusinessResults", "当期", "784025000000"),
                (
                    "ProfitLossAttributableToOwnersOfParentIFRSSummaryOfBusinessResults",
                    "当期",
                    "802368000000",
                ),
            )
        )
    )
    (current,) = parse_summary(read_csv_zip(body))
    assert current.net_income == 802_368_000_000.0


def test_consolidated_and_standalone_are_told_apart_by_context(
    rows: list[dict[str, str]],
) -> None:
    """実データで、どの行が単体かはコンテキストIDだけが言っている。

    「連結・個別」列は連結にも単体にも「その他」を入れる。日立の有報で単体側に
    回るのは、非IFRS の財務項目と――発行済株式数・資本金のような、そもそも連結の
    概念が無い項目。
    """
    assert {r["連結・個別"] for r in summary_rows(rows)} == {"その他"}
    standalone = {r["要素ID"].split(":")[-1] for r in summary_rows(rows) if not is_consolidated(r)}
    assert "NetIncomeLossSummaryOfBusinessResults" in standalone
    assert "ProfitLossAttributableToOwnersOfParentIFRSSummaryOfBusinessResults" not in standalone


def test_the_japanese_gaap_case_needs_the_context_not_the_name() -> None:
    """日本基準の会社は、連結も単体も**同じ要素名**を使う。

    IFRS 適用会社では連結に ``...IFRS...`` が付くので名前で分かれて見えるが、
    それは IFRS 適用会社に限った話。日本基準では名前が一致し、違うのは
    コンテキストIDの ``_NonConsolidatedMember`` だけになる。名前の優先順位しか
    見ていないと、ここで単体を掴む――行の並び次第で、例外は出ない。
    """
    body = make_zip(
        utf16(
            csv_text(
                (
                    "NetIncomeLossSummaryOfBusinessResults",
                    "当期",
                    "300",
                    "CurrentYearDuration_" + NON_CONSOLIDATED,
                ),
                ("NetIncomeLossSummaryOfBusinessResults", "当期", "500", "CurrentYearDuration"),
            )
        )
    )
    (current,) = parse_summary(read_csv_zip(body))
    assert current.net_income == 500.0


def test_a_filer_without_subsidiaries_falls_back_to_standalone() -> None:
    """子会社が無ければ連結財務諸表そのものが無い。空を返すより単体を使う。"""
    body = make_zip(
        utf16(
            csv_text(
                (
                    "NetIncomeLossSummaryOfBusinessResults",
                    "当期",
                    "300",
                    "CurrentYearDuration_" + NON_CONSOLIDATED,
                ),
            )
        )
    )
    (current,) = parse_summary(read_csv_zip(body))
    assert current.net_income == 300.0


def test_the_share_count_survives_the_consolidated_filter(
    rows: list[dict[str, str]], years: dict[str, AnnualFigures]
) -> None:
    """発行済株式総数は常に単体タグが付く。連結で絞ると消える。

    連結の株式数という概念は無いので、EDINET は提出会社の事実として持っている。
    財務項目と同じ扱いで弾くと、株式数の列だけが空欄になる――例外は出ない。
    """
    shares = [
        r
        for r in summary_rows(rows)
        if r["要素ID"].endswith("TotalNumberOfIssuedSharesSummaryOfBusinessResults")
    ]
    assert shares and all(not is_consolidated(r) for r in shares)
    assert years["当期"].shares_outstanding == 4_535_560_000.0


# --- 罠2: 1株当たりの値を年をまたいで使えない ----------------------------


def test_share_count_is_not_split_restated(years: dict[str, AnnualFigures]) -> None:
    """発行済株式数は当時のまま。EPS は分割調整済み。噛み合わない。

    前々期（2024年の1:5分割の前）で純利益 ÷ EPS を取ると 46.5億株になるが、
    報告されている発行済株式数は 9.27億株。ちょうど5倍ずれる。株数を EPS から
    逆算してはいけない、という一点をこの数字が示している。
    """
    before = years["前々期"]
    assert before.shares_outstanding == 927_167_000.0
    implied = before.net_income / 126.91  # 前々期の基本的1株当たり当期利益（IFRS）
    assert implied / before.shares_outstanding == pytest.approx(5.0, abs=0.02)


def test_no_per_share_fields_are_exposed() -> None:
    """1株当たりの値は持たない。持てば、いずれ年をまたいで割られる。

    実データで言うと、前々期の1株配当 180円 ÷ EPS 126.91円 は配当性向 141.8%。
    有報が報告している値は 28.8%。分割前の株数で払った配当を、分割後の尺度の
    EPS で割った結果で、どちらの数字も単体では正しい。
    """
    implied_payout = 180 / 126.91  # 前々期の1株配当 ÷ 基本的1株当たり当期利益（IFRS）
    assert implied_payout == pytest.approx(1.418, abs=0.001)
    fields = set(AnnualFigures.__dataclass_fields__)
    assert not {f for f in fields if "per_share" in f or f in {"eps", "dps", "bps"}}
    assert fields == {
        "year",
        "revenue",
        "net_income",
        "equity",
        "total_assets",
        "roe",
        "shares_outstanding",
    }


# --- 罠3: 期間の項目と時点の項目でラベルが違う ---------------------------


def test_duration_and_instant_labels_land_in_the_same_year(
    years: dict[str, AnnualFigures],
) -> None:
    """``当期`` の売上と ``当期末`` の総資産が、同じ1期に入る。"""
    current = years["当期"]
    assert current.revenue == 10_586_781_000_000.0  # 当期
    assert current.total_assets == 15_041_246_000_000.0  # 当期末
    assert current.shares_outstanding == 4_535_560_000.0  # 当期末


def test_instant_values_are_not_found_under_the_duration_label() -> None:
    """``当期`` のラベルで時点の項目を探しても何も出ない。組を間違えると空になる。"""
    body = make_zip(
        utf16(
            csv_text(
                ("EquityAttributableToOwnersOfParentIFRSSummaryOfBusinessResults", "当期", "1")
            )
        )
    )
    assert parse_summary(read_csv_zip(body)) == []


# --- 5期ぶんの並び -------------------------------------------------------


def test_five_years_come_back_oldest_first(rows: list[dict[str, str]]) -> None:
    """1つの有報に5期。無料で過去の財務が取れる理由がこれ。"""
    figures = parse_summary(rows)
    assert [f.year for f in figures] == ["四期前", "三期前", "前々期", "前期", "当期"]


def test_revenue_matches_the_filing_across_all_five_years(rows: list[dict[str, str]]) -> None:
    """売上収益（億円）。実際の有報の表と一致する。"""
    actual = [round(f.revenue / OKU) for f in parse_summary(rows)]
    assert actual == [102_646, 108_812, 97_287, 97_834, 105_868]


def test_equity_matches_the_filing_across_all_five_years(rows: list[dict[str, str]]) -> None:
    """自己資本（億円）。単体を掴んでいれば、ここが 26,437 から始まる。"""
    actual = [round(f.equity / OKU) for f in parse_summary(rows)]
    assert actual == [43_418, 49_429, 57_037, 58_471, 65_684]


def test_share_count_shows_the_split(rows: list[dict[str, str]]) -> None:
    """前期に9.27億株から45.8億株へ跳ぶ。2024年の1:5分割がそのまま出る。"""
    actual = [f.shares_outstanding for f in parse_summary(rows)]
    assert actual[2] == 927_167_000.0
    assert actual[3] == 4_580_341_000.0


def test_years_without_data_are_dropped() -> None:
    """上場が浅い、会計基準を変えた、などで5期揃わない有報がある。"""
    body = make_zip(
        utf16(
            csv_text(
                ("RevenueIFRSSummaryOfBusinessResults", "前期", "1000"),
                ("RevenueIFRSSummaryOfBusinessResults", "当期", "1100"),
            )
        )
    )
    assert [f.year for f in parse_summary(read_csv_zip(body))] == ["前期", "当期"]


# --- 値の掃除 -------------------------------------------------------------


@pytest.mark.parametrize("blank", ["", "-", "－", "―", "  "])
def test_placeholder_values_are_not_numbers(blank: str) -> None:
    """未記載はダッシュで来る。0 として取り込むと、下流では業績悪化に見える。"""
    body = make_zip(
        utf16(
            csv_text(
                ("RevenueIFRSSummaryOfBusinessResults", "当期", blank),
                ("TotalAssetsIFRSSummaryOfBusinessResults", "当期末", "500"),
            )
        )
    )
    (current,) = parse_summary(read_csv_zip(body))
    assert current.revenue is None
    assert current.total_assets == 500.0


def test_a_blank_ifrs_value_falls_through_to_japanese_gaap() -> None:
    """要素があっても値が空なら、次の候補を見る。"""
    body = make_zip(
        utf16(
            csv_text(
                ("RevenueIFRSSummaryOfBusinessResults", "当期", "－"),
                ("NetSalesSummaryOfBusinessResults", "当期", "700"),
            )
        )
    )
    (current,) = parse_summary(read_csv_zip(body))
    assert current.revenue == 700.0


def test_unparseable_values_are_skipped_not_raised() -> None:
    """1項目の書式崩れで有報1本を落とさない。"""
    body = make_zip(
        utf16(
            csv_text(
                ("RevenueIFRSSummaryOfBusinessResults", "当期", "※注記参照"),
                ("ProfitLossAttributableToOwnersOfParentIFRSSummaryOfBusinessResults", "当期", "8"),
            )
        )
    )
    (current,) = parse_summary(read_csv_zip(body))
    assert current.revenue is None
    assert current.net_income == 8.0


# --- 罠4: 相対年度しか書いていない -------------------------------------


def test_header_reads_the_filing_identity(rows: list[dict[str, str]]) -> None:
    """銘柄・決算日・会計基準は ``jpdei`` にある。表そのものには無い。"""
    header = parse_header(rows)
    assert header.symbol == "6501"
    assert header.fiscal_year_end == dt.date(2026, 3, 31)
    assert header.accounting_standard == "IFRS"
    assert header.filer_name == "株式会社日立製作所"


def test_security_code_drops_the_share_class_digit(rows: list[dict[str, str]]) -> None:
    """EDINET の銘柄コードは5桁。末尾を残すと watchlist の 6501 と噛み合わない。"""
    raw = [r for r in rows if r["要素ID"].endswith("SecurityCodeDEI")]
    assert [r["値"] for r in raw] == ["65010"]
    assert parse_header(rows).symbol == "6501"


def test_header_ignores_the_not_applicable_marker() -> None:
    """``jpdei`` の未該当はダッシュで埋まる。文字列として拾ってはいけない。"""
    body = make_zip(utf16(csv_text(("SecurityCodeDEI", "提出日時点", "－"))))
    assert parse_header(read_csv_zip(body)) == FilingHeader()


def test_header_survives_an_unreadable_date() -> None:
    """決算日が読めなくても、他の項目は返す。判断は呼ぶ側に残す。"""
    body = make_zip(
        utf16(
            csv_text(
                ("CurrentFiscalYearEndDateDEI", "提出日時点", "令和8年3月31日"),
                ("SecurityCodeDEI", "提出日時点", "65010"),
            )
        )
    )
    header = parse_header(read_csv_zip(body))
    assert header.fiscal_year_end is None
    assert header.symbol == "6501"


# --- 相対年度から決算年度へ ----------------------------------------------


def test_fiscal_years_count_back_from_the_filing(rows: list[dict[str, str]]) -> None:
    """当期が 2026年度で、そこから1年ずつ遡る。

    年度は決算日の**年**。J-Quants 側が ``FYEnd`` の年を使っているので、揃えて
    おかないと同じ決算期が別の年度に入り、一意キーが効かなくなる。
    """
    reports = to_reports(parse_header(rows), parse_summary(rows))
    assert [r.fiscal_year for r in reports] == [2022, 2023, 2024, 2025, 2026]
    assert all(r.symbol == "6501" for r in reports)
    assert all(r.period is FiscalPeriod.FY for r in reports)


def test_reports_carry_the_consolidated_numbers(rows: list[dict[str, str]]) -> None:
    """年度を付けても連結のまま。単体に落ちない。"""
    latest = to_reports(parse_header(rows), parse_summary(rows))[-1]
    assert (latest.fiscal_year, latest.net_income) == (2026, 802_368_000_000.0)
    assert latest.equity == 6_568_369_000_000.0


def test_reports_leave_per_share_fields_unset(rows: list[dict[str, str]]) -> None:
    """``FinancialReport`` には eps も bps もあるが、有報からは埋めない。

    EPS は分割調整済み、1株配当と株数は当時のまま。同じ行に並べた時点で、
    どれかの組み合わせが年をまたいで割られる。埋めないのが唯一の防ぎ方。
    """
    for report in to_reports(parse_header(rows), parse_summary(rows)):
        assert report.eps is None
        assert report.bps is None
        assert report.dividend_per_share is None
        assert report.payout_ratio is None


def test_an_explicit_symbol_overrides_the_filing(rows: list[dict[str, str]]) -> None:
    """上場コードを持たない提出会社のための逃げ道。"""
    reports = to_reports(parse_header(rows), parse_summary(rows), symbol="6502")
    assert {r.symbol for r in reports} == {"6502"}


def test_to_reports_refuses_without_a_symbol(rows: list[dict[str, str]]) -> None:
    """コードが無ければ、どの会社の数字か決められない。0 埋めより例外。"""
    with pytest.raises(DataError, match="銘柄コード"):
        to_reports(FilingHeader(fiscal_year_end=dt.date(2026, 3, 31)), parse_summary(rows))


def test_to_reports_refuses_without_a_fiscal_year_end(rows: list[dict[str, str]]) -> None:
    """決算日が無ければ相対年度を年度に直せない。当て推量で並べない。"""
    with pytest.raises(DataError, match="決算日"):
        to_reports(FilingHeader(symbol="6501"), parse_summary(rows))


def test_a_december_filer_lands_on_the_calendar_year() -> None:
    """12月期は決算日の年がそのまま年度。3月期と同じ規則で扱える。"""
    header = FilingHeader(symbol="4755", fiscal_year_end=dt.date(2025, 12, 31))
    figures = [AnnualFigures(year="前期", revenue=1.0), AnnualFigures(year="当期", revenue=2.0)]
    assert [r.fiscal_year for r in to_reports(header, figures)] == [2024, 2025]


# --- 入口 -----------------------------------------------------------------


def test_parse_filing_reads_the_real_archive(fixture_bytes: bytes) -> None:
    """ZIP のバイト列から、そのまま保存できる5期ぶんまで、ひと息で。"""
    reports = parse_filing(make_zip(fixture_bytes))
    assert [r.fiscal_year for r in reports] == [2022, 2023, 2024, 2025, 2026]
    assert reports[-1].net_income == 802_368_000_000.0
    assert reports[-1].symbol == "6501"


def test_parse_filing_refuses_an_empty_summary() -> None:
    """表が空なら、空の5期を返すより言う。"""
    body = make_zip(utf16(csv_text(("SomethingElseEntirely", "当期", "1"), *DEI)))
    with pytest.raises(DataError, match="1期ぶんも読めませんでした"):
        parse_filing(body)


def test_is_empty_only_looks_at_the_numbers() -> None:
    """``year`` は常に入っている。空判定の材料にしない。"""
    assert AnnualFigures(year="当期").is_empty()
    assert not AnnualFigures(year="当期", roe=0.0).is_empty()


# --- 取ってくる -----------------------------------------------------------


class _Response:
    """``httpx.Response`` のうち、この経路が読む分だけ。"""

    def __init__(self, content: bytes, status_code: int = 200) -> None:
        self.content = content
        self.status_code = status_code


class _Client:
    """1回の GET を記録する偽 ``httpx.Client``。"""

    last: dict[str, Any] = {}
    response = _Response(b"PK\x03\x04rest")

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def __enter__(self) -> _Client:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def get(self, url: str, params: dict[str, str], headers: dict[str, str]) -> _Response:
        _Client.last = {"url": url, "params": params, "headers": headers}
        return _Client.response


@pytest.fixture
def fake_http(monkeypatch: pytest.MonkeyPatch) -> type[_Client]:
    """``httpx.Client`` を差し替える。実際の EDINET は叩かない。"""
    import httpx

    _Client.last = {}
    _Client.response = _Response(b"PK\x03\x04rest")
    monkeypatch.setattr(httpx, "Client", _Client)
    return _Client


def test_fetch_document_asks_for_the_csv_conversion(fake_http: type[_Client]) -> None:
    """``type=5`` だけが CSV。``1`` を取ると iXBRL 一式 3.7MB を解析する羽目になる。"""
    assert fetch_document("S100YGBO") == b"PK\x03\x04rest"
    assert fake_http.last["url"].endswith("/documents/S100YGBO")
    assert fake_http.last["params"]["type"] == "5"


def test_fetch_document_sends_the_key_where_the_gateway_reads_it(
    fake_http: type[_Client],
) -> None:
    """書類の口も一覧と同じ認証。ヘッダだけでは 401 になることが分かっている。"""
    fetch_document("S100YGBO", SecretStr("k"))
    assert fake_http.last["params"]["Subscription-Key"] == "k"
    assert fake_http.last["headers"]["Ocp-Apim-Subscription-Key"] == "k"


def test_fetch_document_sends_nothing_without_a_key(fake_http: type[_Client]) -> None:
    """鍵が無いときに空の値を送らない。"""
    fetch_document("S100YGBO")
    assert "Subscription-Key" not in fake_http.last["params"]
    assert fake_http.last["headers"] == {}


def test_fetch_document_reports_an_http_error(fake_http: type[_Client]) -> None:
    fake_http.response = _Response(b"", status_code=404)
    with pytest.raises(DataError, match="HTTP 404"):
        fetch_document("S100YGBO")


def test_fetch_document_catches_the_200_that_is_an_error(fake_http: type[_Client]) -> None:
    """EDINET は断った要求にも 200 を返し、本文だけをエラーにする。

    ここで見ないと、下流が「ZIP ではありません」と言うだけになり、鍵が無いのか
    書類が無いのか分からない。
    """
    fake_http.response = _Response(b'{"StatusCode":401,"message":"Access denied"}')
    with pytest.raises(DataError, match="Access denied"):
        fetch_document("S100YGBO", SecretStr("k"))


def test_a_missing_key_is_named_in_the_error(fake_http: type[_Client]) -> None:
    """鍵未設定は原因が分かっている数少ないケース。そう言う。"""
    fake_http.response = _Response(b'{"StatusCode":401}')
    with pytest.raises(DataError, match="EDINET_API_KEY"):
        fetch_document("S100YGBO")


def test_the_key_is_not_in_the_error_text(fake_http: type[_Client]) -> None:
    """例外文はログにもコンソールにも出る。鍵を混ぜない。"""
    fake_http.response = _Response(b'{"StatusCode":401,"message":"Access denied"}')
    with pytest.raises(DataError) as raised:
        fetch_document("S100YGBO", SecretStr("s3cret"))
    assert "s3cret" not in str(raised.value)


# --- 有報を見つけて読むまで ----------------------------------------------


#: 日立が実際に有報を出した日。
FILED_ON = dt.date(2026, 6, 22)


def day_records(
    calendar: dict[dt.date, list[dict[str, Any]]],
) -> Callable[[dt.date], list[dict[str, Any]]]:
    """日付ごとの書類一覧を返すフェッチャ。載っていない日は空。"""

    def fetch(day: dt.date) -> list[dict[str, Any]]:
        return list(calendar.get(day, ()))

    return fetch


def annual_report(doc_id: str = "S100YGBO", doc_type: str = "120") -> dict[str, Any]:
    return {
        "docID": doc_id,
        "secCode": "65010",
        "docTypeCode": doc_type,
        "filerName": "株式会社日立製作所",
        "submitDateTime": "2026-06-22 09:00",
    }


def source_over(
    *records: dict[str, Any], calendar: dict[dt.date, list[dict[str, Any]]] | None = None
) -> EdinetDisclosureSource:
    """2026-08-23 から400日遡る、実際と同じ窓の探索器。"""
    return EdinetDisclosureSource(
        lookback_days=400,
        fetcher=day_records(calendar if calendar is not None else {FILED_ON: list(records)}),
        clock=lambda: dt.date(2026, 8, 23),
    )


def test_find_documents_returns_the_doc_id() -> None:
    """``docID`` は書類一覧にしか無い。ダウンロードの口はこれしか受け取らない。"""
    assert source_over(annual_report()).find_documents("6501", ("120",)) == ["S100YGBO"]


def test_find_documents_matches_the_five_digit_code() -> None:
    """一覧側のコードも5桁。4桁の watchlist 記号と突き合わせる。"""
    assert source_over(annual_report()).find_documents("6501.T", ("120",)) == ["S100YGBO"]


def test_find_documents_ignores_other_filing_types() -> None:
    """臨時報告書には「主要な経営指標等」が無い。"""
    assert source_over(annual_report(doc_type="180")).find_documents("6501", ("120",)) == []


def test_find_documents_skips_a_withdrawn_filing() -> None:
    """取り下げられた書類は読めない。``fetch`` が外すものはここでも外す。"""
    withdrawn = annual_report() | {"withdrawalStatus": "1"}
    assert source_over(withdrawn).find_documents("6501", ("120",)) == []


def test_find_documents_skips_a_us_symbol() -> None:
    """EDINET に無い記号のために400日ぶんの走査をしない。"""
    assert source_over(annual_report()).find_documents("AAPL", ("120",)) == []


def test_fetch_annual_reports_goes_from_symbol_to_five_years(
    fake_http: type[_Client], fixture_bytes: bytes
) -> None:
    """銘柄コードだけ渡せば、保存できる5期ぶんが返る。"""
    fake_http.response = _Response(make_zip(fixture_bytes))
    reports = fetch_annual_reports("6501", source=source_over(annual_report()))
    assert [r.fiscal_year for r in reports] == [2022, 2023, 2024, 2025, 2026]
    assert fake_http.last["url"].endswith("/documents/S100YGBO")


def test_a_later_correction_wins(fake_http: type[_Client], fixture_bytes: bytes) -> None:
    """訂正有報には直した数字が入っている。後から出るので、新しい日から探せば勝つ。

    元の有報も候補型に入れておかないと、訂正の出ていない年は何も見つからない。
    順序を決めているのは型ではなく提出日。
    """
    fake_http.response = _Response(make_zip(fixture_bytes))
    source = source_over(
        calendar={
            FILED_ON: [annual_report()],
            dt.date(2026, 7, 15): [annual_report("S100ZZZZ", doc_type="130")],
        }
    )
    fetch_annual_reports("6501", source=source)
    assert fake_http.last["url"].endswith("/documents/S100ZZZZ")


def test_the_original_is_used_when_there_is_no_correction(
    fake_http: type[_Client], fixture_bytes: bytes
) -> None:
    """訂正が出ている年のほうが珍しい。"""
    fake_http.response = _Response(make_zip(fixture_bytes))
    fetch_annual_reports("6501", source=source_over(annual_report()))
    assert fake_http.last["url"].endswith("/documents/S100YGBO")


def test_fetch_annual_reports_says_when_the_window_is_too_narrow() -> None:
    """有報は年に1度。既定の7日窓で呼ぶと必ず空になる。理由を言う。"""
    source = EdinetDisclosureSource(
        lookback_days=7,
        fetcher=day_records({FILED_ON: [annual_report()]}),
        clock=lambda: dt.date(2026, 8, 23),
    )
    with pytest.raises(DataError, match="直近 7 日"):
        fetch_annual_reports("6501", source=source)


# --- 日本基準の会社（8306 三菱UFJ） ---------------------------------------
#
# ここから下の固定値は、8306 に対して tools/edinet_financials_check.py を実際に
# 走らせた出力（docID S100YJQO, 2026年3月期）から取った。フィクスチャは有報全体
# ではなく、その出力に現れた23要素の四期前ぶんを、実物と同じ形式で組み直したもの。
# 要素名・連結単体の別・項目名・値はすべて実データ。

JGAAP_FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "edinet_8306_summary.tsv"


@pytest.fixture(scope="module")
def jgaap_rows() -> list[dict[str, str]]:
    return read_csv_zip(make_zip(JGAAP_FIXTURE.read_bytes()))


@pytest.fixture(scope="module")
def jgaap(jgaap_rows: list[dict[str, str]]) -> AnnualFigures:
    (only,) = parse_summary(jgaap_rows)
    return only


def test_a_japanese_gaap_filer_is_recognised(jgaap_rows: list[dict[str, str]]) -> None:
    """IFRS 名の要素が1つも無い有報。会計基準は jpdei が言う。"""
    header = parse_header(jgaap_rows)
    assert header.accounting_standard == "Japan GAAP"
    assert header.symbol == "8306"
    assert not any("IFRS" in element_name(r) for r in jgaap_rows)


def test_the_bank_reports_revenue_as_ordinary_income(jgaap: AnnualFigures) -> None:
    """銀行に売上高は無い。最上段は経常収益。

    一般事業会社は売上高、証券・不動産などは営業収益、銀行・保険は経常収益。
    どれも無い会社だと revenue が空になり、成長率の画面から丸ごと消える。
    """
    assert jgaap.revenue == 6_075_887_000_000.0


def test_ordinary_income_is_not_confused_with_ordinary_profit(
    jgaap_rows: list[dict[str, str]], jgaap: AnnualFigures
) -> None:
    """経常収益と経常利益は、要素名が1語違うだけで同じ表に並んでいる。

    部分一致で探すと取り違える。60,758億 と 15,376億 で4倍違う。
    """
    names = {element_name(r) for r in jgaap_rows}
    assert {
        "OrdinaryIncomeSummaryOfBusinessResults",
        "OrdinaryIncomeLossSummaryOfBusinessResults",
    } <= names
    assert jgaap.revenue == 6_075_887_000_000.0  # 経常収益
    assert jgaap.revenue != 1_537_649_000_000.0  # 経常利益


def test_japanese_gaap_consolidated_net_income_has_its_own_element(jgaap: AnnualFigures) -> None:
    """日本基準の連結は ProfitLossAttributableToOwnersOfParent…。

    NetIncomeLoss… は提出会社単体の欄で、11,308億 対 5,718億 と2倍違う。日立
    （IFRS）では単体側にしか無かった要素なので、日本基準の有報を1本読むまで
    この名前は分からなかった。
    """
    assert jgaap.net_income == 1_130_840_000_000.0
    assert jgaap.net_income != 571_859_000_000.0


def test_the_reported_roe_agrees_with_the_consolidated_net_income(jgaap: AnnualFigures) -> None:
    """どちらの純利益を取ったかは、有報が報告している ROE で裏が取れる。

    連結 11,308億 ÷ 自己資本 179,882億 = 6.3%。報告値は 6.7%（期中平均を使うので
    少し高い）。単体 5,718億 なら 3.2% で、報告値の半分にしかならない。
    """
    assert jgaap.roe == pytest.approx(0.0668)
    assert jgaap.net_income / jgaap.equity == pytest.approx(0.063, abs=0.005)
    assert 571_859_000_000.0 / jgaap.equity == pytest.approx(0.032, abs=0.005)


def test_the_same_element_names_carry_both_bases(jgaap_rows: list[dict[str, str]]) -> None:
    """日本基準では連結と単体が同じ要素名を使う。分かれるのはコンテキストだけ。

    日立（IFRS）では連結に IFRS が付くので名前で分かれて見えたが、それは IFRS
    適用会社に限った話だった、という予想がそのまま出ている。
    """
    rows = summary_rows(jgaap_rows)
    assert not any("IFRS" in element_name(r) for r in rows)
    bases = {element_name(r): is_consolidated(r) for r in rows}
    assert bases["ProfitLossAttributableToOwnersOfParentSummaryOfBusinessResults"] is True
    assert bases["NetIncomeLossSummaryOfBusinessResults"] is False


def test_the_remaining_figures_come_through(jgaap: AnnualFigures) -> None:
    """自己資本・総資産・株式数。連結で絞った後も残る。"""
    assert jgaap.equity == 17_988_245_000_000.0
    assert jgaap.total_assets == 373_731_910_000_000.0
    assert jgaap.shares_outstanding == 13_281_995_120.0


# --- 年度はラベルが決める（並び順ではない） ------------------------------


def test_the_fiscal_year_comes_from_the_label_not_the_position(
    jgaap_rows: list[dict[str, str]],
) -> None:
    """1期しか無い有報でも、四期前は四期前の年に着く。

    並び順から数えていた頃は、これが当期（2026年度）になっていた。値が1つも
    取れない期は落としてあるので、途中に穴が空くとその前が全部1年ずれる。
    例外は出ない。
    """
    (report,) = to_reports(parse_header(jgaap_rows), parse_summary(jgaap_rows))
    assert report.fiscal_year == 2022


def test_a_gap_in_the_middle_does_not_shift_the_older_years() -> None:
    """前期だけ空でも、前々期は前々期の年に留まる。"""
    header = FilingHeader(symbol="9999", fiscal_year_end=dt.date(2026, 3, 31))
    figures = [
        AnnualFigures(year="前々期", revenue=1.0),
        AnnualFigures(year="当期", revenue=3.0),
    ]
    assert [r.fiscal_year for r in to_reports(header, figures)] == [2024, 2026]


def test_an_unknown_relative_year_is_dropped_with_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """知らないラベルに年を当てるくらいなら落とす。"""
    header = FilingHeader(symbol="9999", fiscal_year_end=dt.date(2026, 3, 31))
    with caplog.at_level(logging.WARNING):
        reports = to_reports(header, [AnnualFigures(year="五期前", revenue=1.0)])
    assert reports == []
    assert "五期前" in caplog.text
