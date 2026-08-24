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
import unicodedata

from dotenv import load_dotenv
from pydantic import SecretStr

from stock_ai.core.logging import configure_logging
from stock_ai.core.version import describe
from stock_ai.ir.edinet import EdinetDisclosureSource
from stock_ai.ir.edinet_financials import (
    ANNUAL_REPORT_TYPES,
    CONTEXT,
    ELEMENT,
    ELEMENTS,
    INSTANT_FIELDS,
    LABEL,
    PERIOD_KIND,
    RELATIVE_YEAR,
    UNIT,
    VALUE,
    AnnualFigures,
    element_name,
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


#: 全角として数える East Asian Width の区分。
#:
#: ``W``（Wide）と ``F``（Fullwidth）は当然として、``A``（Ambiguous）も入れる。
#: この表を読むのは日本語 Windows のコンソール（cp932）で、そこでは Ambiguous は
#: 全角で描かれる。値なしに使っている ``―``（U+2015）がまさに ``A`` で、1桁と
#: 数えると空欄の行だけ1桁ずつ左にずれる。
_WIDE = frozenset("WFA")


def _width(text: str) -> int:
    """コンソール上の桁数。日本語は1文字で2桁を占める。

    ``str.rjust`` は**文字数**で揃えるので、日本語の見出しを混ぜた表は必ずずれる。
    ずれた表は、隣の列の値をその列の値として読ませる。
    """
    return sum(2 if unicodedata.east_asian_width(c) in _WIDE else 1 for c in text)


def _cell(text: str, width: int) -> str:
    """右詰め。桁数は表示幅で数える。"""
    return " " * max(0, width - _width(text)) + text


def _amount(value: float | None, width: int = 14) -> str:
    """億円。値が無ければダッシュ――**幅は同じ**にする。

    ここを詰めずに返すと列が左へ寄り、隣の列の数字がその列の値に見える。
    実際に起きた: 三菱UFJで純利益が取れなかった回、自己資本の 179,882 が
    純利益の位置に出て、売上が空欄に見えた。
    """
    return _cell("―" if value is None else f"{value / OKU:,.0f}", width)


def _ratio(value: float | None, width: int = 8) -> str:
    return _cell("―" if value is None else f"{value:.1%}", width)


#: 空欄になった項目を埋めうる語。CSV 全体の項目名をこれで引く。
#: 項目名に含まれていたら候補から外す語。株価収益率が「収益」で引っ掛かるため。
GAP_EXCLUDE = ("率",)

GAP_HINTS: dict[str, tuple[str, ...]] = {
    "revenue": ("売上", "収益", "収入"),
    "net_income": ("当期純利益", "当期利益", "親会社"),
    "equity": ("純資産", "持分", "自己資本"),
    "total_assets": ("総資産", "資産合計"),
    "roe": ("自己資本利益率", "持分利益率"),
    "shares_outstanding": ("発行済株式",),
}


def _largest_consolidated(
    rows: list[dict[str, str]], kind: str, top: int = 12
) -> list[tuple[str, tuple[float, str, str]]]:
    """連結・円建ての金額を大きい順に。項目名の当てずっぽうに頼らない一覧。

    探している項目名を知っていないと引けない、という前提を外すためにある。売上は
    損益計算書でほぼ必ず最大の金額なので、上位に出れば要素名がその場で分かる。

    セグメント別の行は外す。コンテキストIDに ``_`` が付くのがそれで、混ぜると
    上位が同じ項目の内訳で埋まる。
    """
    seen: dict[str, tuple[float, str, str]] = {}
    for row in rows:
        context = row.get(CONTEXT, "")
        if "_" in context or not context or row.get(PERIOD_KIND) != kind:
            continue
        if row.get(UNIT) != "円":
            continue
        try:
            value = float((row.get(VALUE) or "").strip())
        except ValueError:
            continue
        name = element_name(row)
        if name not in seen or abs(value) > abs(seen[name][0]):
            seen[name] = (value, row.get(LABEL, ""), row.get(RELATIVE_YEAR, ""))
    return sorted(seen.items(), key=lambda kv: -abs(kv[1][0]))[:top]


def _report_gaps(rows: list[dict[str, str]], figures: list[AnnualFigures]) -> None:
    """埋まらなかった項目について、CSV 全体に何があったかを出す。

    空欄のまま「取れませんでした」とだけ言われても、要素名を足せばいいのか、
    その有報に本当に無いのかが分からない。``主要な経営指標等`` の外まで含めて
    候補を並べれば、次に何を足すかがその場で決まる。

    トヨタがこれを必要にした。IFRS 適用なのに ``RevenueIFRS…`` を持たず、
    ``NetSales…``（売上高）は提出会社単体。連結の売上が表から丸ごと欠けている。
    """
    missing = sorted({f for f in GAP_HINTS if all(getattr(e, f) is None for e in figures)})
    if not missing:
        return

    print("\n--- 埋まらなかった項目と、CSV 全体にあった候補 ---")
    for field in missing:
        hints = GAP_HINTS[field]
        found = {}
        for row in rows:
            label = row.get(LABEL, "")
            if not any(h in label for h in hints):
                continue
            if any(x in label for x in GAP_EXCLUDE):
                continue
            name = row.get(ELEMENT, "").split(":")[-1]
            if name not in found:
                basis = "連結" if is_consolidated(row) else "単体"
                found[name] = (basis, label, row.get(RELATIVE_YEAR, ""), row.get(VALUE, ""))
        print(f"\n  [{field}] 項目名で引いた候補 {len(found)} 件")
        for name, (basis, label, year, value) in sorted(found.items())[:20]:
            print(f"    {basis} {name:<56} {label[:26]}  [{year}={value[:22]}]")
        if len(found) > 20:
            print(f"    …ほか {len(found) - 20} 件")

        kind = "時点" if field in INSTANT_FIELDS else "期間"
        largest = _largest_consolidated(rows, kind)
        print(f"    -- 連結・{kind}・円 の金額が大きい順（項目名に頼らない一覧） --")
        for name, (value, label, year) in largest:
            print(f"    連結 {name:<56} {label[:26]}  [{year}={value / OKU:,.0f}億]")


def check(sec_code: str, days: int) -> int:
    """1銘柄ぶん、実物を取って読めたところと読めなかったところを出す。"""
    secret = (os.environ.get("EDINET_API_KEY") or "").strip()
    if not secret:
        sys.exit("環境変数 EDINET_API_KEY が空です。.env を確認してください。")
    # 貼られた出力だけで、どのコードが出したものか確定できるように。
    print(f"バージョン: {describe()}")
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

    _report_gaps(rows, parse_summary(rows))

    print("\n上の出力をそのまま貼ってください。")
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
