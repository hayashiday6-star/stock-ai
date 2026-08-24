"""穴を報告する道具そのもの。

パーサが埋められなかった項目について、有報の中に何があったかを並べる。ここが
間違うと、次に足す要素名を間違える――「候補が無い」と出れば、その有報には本当に
無いのだと判断してしまう。

固定値は日立 6501 の実際の有報から。
"""

from __future__ import annotations

import io
import pathlib
import sys
import zipfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "tools"))

from edinet_financials_check import _largest_consolidated, _width  # noqa: E402

from stock_ai.ir.edinet_financials import read_csv_zip  # noqa: E402

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "edinet_6501_summary.tsv"


@pytest.fixture(scope="module")
def rows() -> list[dict[str, str]]:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("XBRL_TO_CSV/jpcrp030000-asr-001.csv", FIXTURE.read_bytes())
    return read_csv_zip(buffer.getvalue())


def test_the_largest_consolidated_amount_is_revenue(rows: list[dict[str, str]]) -> None:
    """売上は損益計算書でほぼ必ず最大。項目名を知らなくても上位に出る。

    これが成り立つから、探している要素名を知らなくても一覧から見つけられる。
    """
    top = _largest_consolidated(rows, "期間")
    assert top[0][0] == "RevenueIFRSSummaryOfBusinessResults"
    assert top[0][1][0] == 10_881_150_000_000.0  # 三期前が5期で最大


def test_segment_rows_are_excluded() -> None:
    """セグメント別の行を混ぜると、上位が同じ項目の内訳で埋まる。

    見分けるのはコンテキストID。``CurrentYearDuration_…SegmentMember`` のように
    ``_`` が付く。日立の実データでは、売上収益がセグメントごとに6行あり、全社の
    1行と同じ要素名で並んでいた。

    フィクスチャは「主要な経営指標等」に絞ってあってセグメント行を持たないので、
    ここだけは実物と同じ形の行を組んで確かめる。
    """
    rows = [
        {
            "要素ID": "jpcrp_cor:RevenueIFRS",
            "項目名": "売上収益（IFRS）",
            "コンテキストID": "CurrentYearDuration",
            "相対年度": "当期",
            "期間・時点": "期間",
            "単位": "円",
            "値": "10586781000000",
        },
        {
            "要素ID": "jpcrp_cor:SegmentRevenueIFRS",
            "項目名": "売上収益（IFRS）",
            "コンテキストID": "CurrentYearDuration_DigitalSystemsReportableSegmentMember",
            "相対年度": "当期",
            "期間・時点": "期間",
            "単位": "円",
            "値": "99999999999999",
        },
    ]
    assert [name for name, _ in _largest_consolidated(rows, "期間")] == ["RevenueIFRS"]


def test_standalone_rows_are_excluded(rows: list[dict[str, str]]) -> None:
    """単体の行は連結の一覧に出さない。"""
    names = {name for name, _ in _largest_consolidated(rows, "期間", top=50)}
    assert "NetIncomeLossSummaryOfBusinessResults" not in names


def test_instant_and_duration_are_separated(rows: list[dict[str, str]]) -> None:
    """総資産は時点の項目。期間の一覧に混ざると売上より大きく出て先頭を奪う。"""
    durations = {name for name, _ in _largest_consolidated(rows, "期間", top=50)}
    instants = {name for name, _ in _largest_consolidated(rows, "時点", top=50)}
    assert "TotalAssetsIFRSSummaryOfBusinessResults" in instants
    assert "TotalAssetsIFRSSummaryOfBusinessResults" not in durations


def test_ratios_and_share_counts_are_excluded(rows: list[dict[str, str]]) -> None:
    """単位が円でないものは金額ではない。ROE や株式数を金額の順位に混ぜない。"""
    names = {name for name, _ in _largest_consolidated(rows, "期間", top=50)}
    assert "RateOfReturnOnEquityIFRSSummaryOfBusinessResults" not in names


@pytest.mark.parametrize(
    ("text", "expected"),
    [("年度", 4), ("ROE", 3), ("―", 2), ("－", 2), ("12,345", 6), ("", 0)],
)
def test_display_width_counts_japanese_as_two(text: str, expected: int) -> None:
    """コンソールの桁数は文字数ではない。表がずれると隣の列の値として読まれる。

    ``―``（U+2015）は Unicode 上 Ambiguous だが、日本語 Windows のコンソールでは
    全角で描かれる。値の無い列に使っているのがこれなので、1桁と数えると空欄の行
    だけがずれる。
    """
    assert _width(text) == expected
