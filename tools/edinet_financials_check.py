"""有報の「主要な経営指標等」を実物で読み、拾えた要素と拾えなかった要素を並べる。

パーサの要素名は日立（IFRS）1本から起こしたもので、日本基準の会社で同じ名前が
使われている保証がない。たとえば ``NetSalesSummaryOfBusinessResults`` は日立の
有報には**1行も出てこない**。IFRS 適用会社の CSV に入っている日本基準名の要素は
提出会社単体の表なので、連結の日本基準名は別物である可能性がある。

黙って ``None`` が並ぶのが一番困る。5期ぶんの表が空欄だらけで出てくるだけで、
例外は出ない。だからこの道具は**表に出てくる要素名を全部並べ**、パーサが見て
いる名前に印を付ける。印の付かない行が、埋めるべき穴。

使い方::

    uv run python tools/edinet_financials_check.py --sec-code 8306

APIキーは指紋しか表示しない。
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import os
import sys

from dotenv import load_dotenv
from pydantic import SecretStr

from stock_ai.core.logging import configure_logging
from stock_ai.ir.edinet import EdinetDisclosureSource
from stock_ai.ir.edinet_financials import (
    ANNUAL_REPORT_TYPES,
    ELEMENT,
    ELEMENTS,
    LABEL,
    RELATIVE_YEAR,
    VALUE,
    fetch_document,
    is_consolidated,
    parse_header,
    parse_summary,
    read_csv_zip,
    summary_rows,
)

OKU = 100_000_000.0


def _fingerprint(secret: str) -> str:
    """鍵そのものを出さずに、同じ鍵かどうかだけ言えるようにする。"""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:12]


def _amount(value: float | None) -> str:
    return "―" if value is None else f"{value / OKU:>12,.0f}"


def _ratio(value: float | None) -> str:
    return "―" if value is None else f"{value:>6.1%}"


def check(sec_code: str, days: int) -> int:
    """1銘柄ぶん、実物を取って読めたところと読めなかったところを出す。"""
    secret = (os.environ.get("EDINET_API_KEY") or "").strip()
    if not secret:
        sys.exit("環境変数 EDINET_API_KEY が空です。.env を確認してください。")
    print(f"APIキー 指紋: {_fingerprint(secret)}  (値そのものは表示しません)")

    api_key = SecretStr(secret)
    source = EdinetDisclosureSource(api_key=api_key, lookback_days=days)
    print(f"対象: 証券コード {sec_code} の有価証券報告書を直近 {days} 日から探します")

    doc_ids = source.find_documents(sec_code, ANNUAL_REPORT_TYPES, limit=1)
    if not doc_ids:
        print(f"\n直近 {days} 日に {sec_code} の有報はありませんでした。")
        print("--days を伸ばすか、別の銘柄を指定してください（有報は年1回です）。")
        return 1

    rows = read_csv_zip(fetch_document(doc_ids[0], api_key))
    header = parse_header(rows)

    print("\n--- 有報の素性 ---")
    for field in dataclasses.fields(header):
        print(f"  {field.name}: {getattr(header, field.name)!r}")

    print("\n--- 読めた5期ぶん（億円 / 比率 / 百万株） ---")
    columns = f"{'売上':>14}{'純利益':>14}{'自己資本':>14}{'総資産':>14}{'ROE':>8}{'株式数':>12}"
    print(f"  {'年度':<8}{columns}")
    for entry in parse_summary(rows):
        shares = (
            "―" if entry.shares_outstanding is None else f"{entry.shares_outstanding / 1e6:>10,.0f}"
        )
        print(
            f"  {entry.year:<8}{_amount(entry.revenue)}{_amount(entry.net_income)}"
            f"{_amount(entry.equity)}{_amount(entry.total_assets)}{_ratio(entry.roe)}{shares}"
        )

    known = {name for names in ELEMENTS.values() for name in names}
    print("\n--- パーサが探している要素が、この有報にあるか ---")
    present = {row.get(ELEMENT, "").split(":")[-1] for row in rows}
    for field, names in ELEMENTS.items():
        for name in names:
            mark = "○" if name in present else "×"
            print(f"  {mark} {field:<18} {name}")

    print("\n--- 表に出てくる要素の全部 ---")
    print("  ○ = パーサが見ている / 連結・単体はコンテキストIDから判定")
    print("  ※ 1株当たりの値は意図的に使っていません（分割調整の食い違い）。")
    summary = summary_rows(rows)
    consolidated = {r.get(ELEMENT, "").split(":")[-1] for r in summary if is_consolidated(r)}
    seen: dict[str, tuple[str, str]] = {}
    for row in summary:
        element = row.get(ELEMENT, "").split(":")[-1]
        if element in seen:
            continue
        seen[element] = (row.get(LABEL, ""), f"{row.get(RELATIVE_YEAR, '')}={row.get(VALUE, '')}")
    for element, (label, sample) in sorted(seen.items()):
        mark = "○" if element in known else " "
        basis = "連結" if element in consolidated else "単体"
        print(f"  {mark} {basis} {element:<62} {label}  [{sample[:40]}]")

    print(f"\n要素 {len(seen)} 種類（うち連結 {len(consolidated)}）。")
    print("上の出力をそのまま貼ってください。")
    print("有価証券報告書は公開情報なので、そのままで構いません。")
    return 0


def main() -> int:
    """コマンドラインから実行する。"""
    parser = argparse.ArgumentParser(description="有報の主要な経営指標等を実物で読む")
    parser.add_argument("--sec-code", default="8306", help="証券コード（既定: 8306 三菱UFJ）")
    parser.add_argument("--days", type=int, default=400, help="遡る日数。有報は年1回なので広めに")
    args = parser.parse_args()
    configure_logging("INFO")
    return check(args.sec_code, args.days)


if __name__ == "__main__":
    load_dotenv()
    raise SystemExit(main())
