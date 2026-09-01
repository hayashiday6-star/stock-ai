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

**値は出さない。** 出すのは、レスポンスに現れたキーの名前と、時刻らしき値の
「形」（``15:00`` のような桁の並び）だけである。銘柄の中身を見るための道具では
ないので、これで十分であり、貼り付けても差し支えない出力になる。

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

    _report(
        "fins/summary（決算。いまパーサが読んでいる先）",
        _fetch(_SUMMARY_URL, {"code": args.symbol}, api_key),
    )
    _report(
        "fins/announcement（決算発表予定日）",
        _fetch(_ANNOUNCEMENT_URL, {}, api_key),
    )

    print("\n値は出していない。出したのはキー名と値の形だけである。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
