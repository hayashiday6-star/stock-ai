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

import io
import logging
import pathlib
import zipfile

import pytest

from stock_ai.core.exceptions import DataError
from stock_ai.ir.edinet_financials import (
    AnnualFigures,
    parse_filing,
    parse_summary,
    read_csv_zip,
)

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "edinet_6501_summary.tsv"

#: EDINET が ZIP に入れてくるパス。実際の ``S100YGBO`` の ``type=5`` の中身と
#: 同じ3本で、本体は ``jpcrp`` の1本だけ。``jpaud`` 2本は監査報告書。
MAIN_CSV = "XBRL_TO_CSV/jpcrp030000-asr-001_E01737-000_2026-03-31_01_2026-06-22.csv"
AUDIT_CSV = "XBRL_TO_CSV/jpaud-aai-cc-001_E01737-000_2026-03-31_01_2026-06-22.csv"
AUDIT_CSV2 = "XBRL_TO_CSV/jpaud-aar-cn-001_E01737-000_2026-03-31_01_2026-06-22.csv"

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


def csv_text(*entries: tuple[str, str, str]) -> str:
    """(要素ID, 相対年度, 値) から、最小の CSV を作る。"""
    lines = [HEADER]
    for element, year, value in entries:
        cells = (f"jpcrp_cor:{element}", "", "", year, "その他", "", "JPY", "円", value)
        lines.append("\t".join(f'"{c}"' for c in cells))
    return "\n".join(lines)


# --- ZIP の取り出し -------------------------------------------------------


def test_read_csv_zip_reads_the_body(fixture_bytes: bytes) -> None:
    """本体 CSV の全行が読める。"""
    assert len(read_csv_zip(make_zip(fixture_bytes))) == 160


def test_read_csv_zip_ignores_the_audit_reports(fixture_bytes: bytes) -> None:
    """``jpaud`` は監査報告書。実際の ZIP でも本体より先に並んでいる。

    順番で選ぶと監査報告書を読み、財務が1つも取れない。名前で選ぶ。
    """
    assert AUDIT_CSV < MAIN_CSV
    audit = utf16(csv_text(("NetSalesSummaryOfBusinessResults", "当期", "1")))
    body = make_zip(audit, audit, fixture_bytes, names=(AUDIT_CSV, AUDIT_CSV2, MAIN_CSV))
    assert len(read_csv_zip(body)) == 160


def test_read_csv_zip_warns_when_the_body_is_ambiguous(
    fixture_bytes: bytes, caplog: pytest.LogCaptureFixture
) -> None:
    """本体が2本ある有報は見たことがない。黙って片方を選ぶより言う。"""
    other = MAIN_CSV.replace("asr-001", "asr-002")
    with caplog.at_level(logging.WARNING):
        assert (
            len(read_csv_zip(make_zip(fixture_bytes, fixture_bytes, names=(MAIN_CSV, other))))
            == 160
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


# --- 入口 -----------------------------------------------------------------


def test_parse_filing_reads_the_real_archive(fixture_bytes: bytes) -> None:
    """ZIP のバイト列から5期ぶんまで、ひと息で。"""
    figures = parse_filing(make_zip(fixture_bytes))
    assert len(figures) == 5
    assert figures[-1].net_income == 802_368_000_000.0


def test_parse_filing_refuses_an_empty_summary() -> None:
    """表が空なら、空の5期を返すより言う。"""
    body = make_zip(utf16(csv_text(("SomethingElseEntirely", "当期", "1"))))
    with pytest.raises(DataError, match="1期ぶんも読めませんでした"):
        parse_filing(body)


def test_is_empty_only_looks_at_the_numbers() -> None:
    """``year`` は常に入っている。空判定の材料にしない。"""
    assert AnnualFigures(year="当期").is_empty()
    assert not AnnualFigures(year="当期", roe=0.0).is_empty()
