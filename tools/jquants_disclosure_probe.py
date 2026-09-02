"""J-Quants が開示について実際に何を返すかを、実物のレスポンスで確かめる。

PEAD は「決算がいつ市場に出たか」で成否が決まる。**引け後の開示なら D+1 の
寄りから、場中の開示ならその日のうちに価格が動く。** どちらか分からないまま
実装すると、先読みになるか、反応日を1日取り違えるかのどちらかになる。

いま手元のパーサ（``stock_ai.data.jquants_fundamentals``）は ``DiscDate`` /
``DisclosedDate`` しか読んでおらず、**時刻を持つ列があるかどうかを誰も
確かめていない**。DBにも時刻の列は無い。この道具は、その1点を推測ではなく
実データで確定させるために書いた。

もう1つ、期限がある。5年分の開示履歴を取り直せるのは有料プランがある間だけで、
解約予定は 2026-09-22 である。「Light に何が含まれるか」を実機で見ておかないと、
事前登録を書いた後に取り返しがつかなくなる。

もう1つ、この道具は**どこで開示が落ちているか**を1回の実行で切り分ける。
センサスの実測で、1銘柄あたりの開示が内側の年でも平均3.36件しかなかった。
四半期ごとに短信が出る以上4件のはずで、16%足りない。落ちている場所は3箇所の
どこかで、目視で2つの出力を見比べると取り違える。

    APIのレコード数 → パーサを通した件数 → DBの行数

を並べれば、どちらの区間で減ったかが一意に決まる。減っていなければ、
3.36件は J-Quants が返す件数そのものということになる。

**値は出さない。** 出すのは、レスポンスに現れたキーの名前と、時刻らしき値の
「形」（``15:00`` のような桁の並び）、および件数だけである。銘柄の中身を
見るための道具ではないので、これで十分であり、貼り付けても差し支えない出力になる。

使い方::

    uv run python tools/jquants_disclosure_probe.py --symbol 7203

APIキーは指紋しか表示しない。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter
from typing import Any

from dotenv import load_dotenv

from stock_ai.core.logging import configure_logging
from stock_ai.core.version import describe
from stock_ai.data.jquants_fundamentals import normalize_statements
from stock_ai.database.engine import Database
from stock_ai.database.repository import FinancialStatementRepository

#: 時刻に見える値。``15:00`` / ``15:00:00`` を拾う。
_TIME_SHAPE = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")

#: 日付に見える値。
_DATE_SHAPE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_SUMMARY_URL = "https://api.jquants.com/v2/fins/summary"
_ANNOUNCEMENT_URL = "https://api.jquants.com/v2/fins/announcement"


def _fingerprint(value: str) -> str:
    """秘密そのものではなく、値が変わったかどうかだけ分かる表示にする。"""
    import hashlib

    return hashlib.sha256(value.encode()).hexdigest()[:8]


def _shape_of(value: Any) -> str:
    """値そのものではなく、値の「形」を返す。"""
    if value is None:
        return "null"
    text = str(value)
    if not text:
        return "空文字"
    if _TIME_SHAPE.match(text):
        return f"時刻らしい（{text}）"
    if _DATE_SHAPE.match(text):
        return "日付らしい（YYYY-MM-DD）"
    if text.replace("-", "").replace(".", "").isdigit():
        return f"数値らしい（{len(text)}桁）"
    return f"文字列（{len(text)}文字）"


def _fetch(url: str, params: dict[str, str], api_key: str) -> list[dict[str, Any]]:
    """1エンドポイントを1回叩いて、レコードの配列を返す。"""
    import httpx

    with httpx.Client(timeout=30.0) as client:
        response = client.get(url, params=params, headers={"x-api-key": api_key})
    if response.status_code != 200:
        # 本文をそのまま出さない。プラン外エンドポイントの応答に
        # 契約情報が混ざることがある。
        print(f"  HTTP {response.status_code}（このプランでは使えない可能性）")
        return []
    payload = response.json()
    for value in payload.values():
        if isinstance(value, list):
            return value
    return []


def _report(title: str, records: list[dict[str, Any]]) -> None:
    """レコード群に現れたキーと、その値の形を並べる。"""
    print(f"\n=== {title} ===")
    if not records:
        print("  レコードなし。")
        return

    print(f"  レコード数: {len(records)}")
    keys: Counter[str] = Counter()
    for record in records:
        keys.update(record.keys())

    print(f"  キー数: {len(keys)}")
    newest = records[-1]
    for key in sorted(keys):
        presence = "" if keys[key] == len(records) else f"  [{keys[key]}/{len(records)}件のみ]"
        print(f"    {key}: {_shape_of(newest.get(key))}{presence}")

    timeish = [k for k in keys if _TIME_SHAPE.match(str(newest.get(k, "")))]
    print()
    if timeish:
        print(f"  ★ 時刻を持つ列: {', '.join(sorted(timeish))}")
        print("    → PEAD のエントリー日をこの列で決められる。")
    else:
        print("  ★ 時刻を持つ列が見当たらない。")
        print("    → 引け後か場中かを判別できない。エントリーを D+1 に固定する")
        print("      （場中開示なら1日遅れるが、先読みにはならない）か、")
        print("      別の情報源で時刻を補うかの二択になる。")


def _trace_losses(symbol: str, records: list[dict[str, Any]]) -> None:
    """API → パーサ → DB の3点で件数を並べ、減った区間を名指しする。

    センサスは1銘柄あたりの開示が内側の年でも3.36件しかないと言っている。
    四半期ごとに短信が出る以上4件のはずで、原因は「APIがそもそも返していない」
    「パーサが落としている」「DBに入っていない」のどれか。3つ並べれば、
    どれかは推測でなく確定する。
    """
    print("\n=== どこで開示が落ちているか ===")
    if not records:
        print("  APIが0件なので、切り分けるものがない。")
        return

    parsed = normalize_statements(symbol, records)
    print(f"  1. API が返したレコード      : {len(records)}")
    print(f"  2. パーサを通した件数        : {len(parsed)}")

    try:
        database = Database()
        database.create_all()
        with database.session() as session:
            stored = FinancialStatementRepository(session).get_reports(symbol, period=None)
    except Exception as exc:  # noqa: BLE001 - DBが無くても probe は成立する
        print(f"  3. DB の行数                 : 読めなかった（{type(exc).__name__}）")
        return
    print(f"  3. DB の行数                 : {len(stored)}")

    print()
    if len(parsed) < len(records):
        print(f"  ★ パーサで {len(records) - len(parsed)} 件落ちている。")
        print("    同じ（会計年度, 四半期）に割り当てられた開示が畳まれている。")
    if len(stored) < len(parsed):
        print(f"  ★ DB で {len(parsed) - len(stored)} 件少ない。取り直しが要る。")
    if len(stored) > len(parsed):
        print(f"  ★ DB のほうが {len(stored) - len(parsed)} 件多い。")
        print("    古い鍵で入った行が残っている可能性がある（会計年度の意味が変わった）。")
    if len(parsed) == len(records) == len(stored):
        print("  ★ どこでも落ちていない。")
        print("    1銘柄あたりの件数が4に満たないのは、API が返す件数そのもの。")

    # どの期が入っているかまで出す。落ちているのが特定の四半期に偏っていれば、
    # 「たまたま少ない」ではなく割り当ての問題だと分かる。
    periods = Counter(f"{r.fiscal_year}-{r.period}" for r in parsed)
    print(f"\n  パーサが割り当てた（会計年度-期）: {len(periods)} 種類")
    for key in sorted(periods):
        mark = "" if periods[key] == 1 else f"  <- {periods[key]}件が同じ鍵"
        print(f"    {key}{mark}")


def main() -> int:
    """開示エンドポイントの列構成を実データで確かめる。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="7203", help="調べる銘柄コード（既定: 7203）")
    args = parser.parse_args()

    load_dotenv()
    configure_logging("INFO")
    print(f"バージョン: {describe()}")

    api_key = os.environ.get("JQUANTS_API_KEY")
    if not api_key:
        print("JQUANTS_API_KEY が .env にない。APIキー設定.bat で設定してから実行する。")
        return 1
    print(f"JQUANTS_API_KEY: {len(api_key)} 文字, 指紋 {_fingerprint(api_key)}")
    print(f"銘柄: {args.symbol}")

    summary = _fetch(_SUMMARY_URL, {"code": args.symbol}, api_key)
    _report("fins/summary（決算。いまパーサが読んでいる先）", summary)
    _report(
        "fins/announcement（決算発表予定日）",
        _fetch(_ANNOUNCEMENT_URL, {}, api_key),
    )

    _trace_losses(args.symbol, summary)

    print("\n値は出していない。出したのはキー名と値の形と件数だけである。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
