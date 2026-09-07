"""保存した原本を、読み口に通す。

**「落とせた」と「読めた」は別である。** このプロジェクトは2日で2回それを
踏んでいる（名簿の取り直しは3回、財務の一括は試行のあと本番）。原本を 1 GB
保存したあとで読み口が繋がっていないと分かるのが、いちばん高い。

`factor_panel` のときと同じ形でもある——部品を13個テストしていたのに
`build_panel` 自体を一度も呼んでおらず、存在しない引数が本番まで出て行った。
**ここは組み立てを1本通す口である。**

## 何をするか

1. 目録にある原本を1本ずつ開く（`.gz` なら展開する）
2. `key` から**どのエンドポイントのものか**を決める
3. 対応する読み口に通して、行数を数える

落とすことはしない。**解約後にも実行できる。**
"""

from __future__ import annotations

import dataclasses
import gzip
from collections.abc import Callable
from pathlib import Path

from stock_ai.core.logging import get_logger
from stock_ai.data.jquants_archive import DEFAULT_ARCHIVE_DIR, path_for, read_manifest
from stock_ai.data.jquants_details import parse_details
from stock_ai.data.jquants_dividend import parse_dividends
from stock_ai.data.jquants_earnings import parse_earnings_dates
from stock_ai.data.jquants_margin import parse_alerts
from stock_ai.data.jquants_markets import (
    parse_breakdown,
    parse_calendar,
    parse_margin_interest,
    parse_short_positions,
)

logger = get_logger(__name__)

#: エンドポイントごとの読み口。
#:
#: **ここに無いエンドポイントは「読めない」と数える。** 黙って飛ばすと、
#: 保存できているのに一度も読まれていないものが、出力から消える。
PARSERS: dict[str, Callable[[bytes], list]] = {
    "/fins/details": parse_details,
    "/fins/dividend": parse_dividends,
    "/fins/earnings-date": parse_earnings_dates,
    "/markets/margin-alert": parse_alerts,
    "/markets/margin-interest": parse_margin_interest,
    "/markets/breakdown": parse_breakdown,
    "/markets/short-sale-report": parse_short_positions,
    "/markets/calendar": parse_calendar,
}


@dataclasses.dataclass
class ReadReport:
    """1エンドポイントぶんの結果。"""

    endpoint: str
    files: int = 0
    rows: int = 0
    empty: list[str] = dataclasses.field(default_factory=list)
    """開けたが0行だったもの。**例外は出ない。**"""

    failed: dict[str, str] = dataclasses.field(default_factory=dict)


def endpoint_of(key: str, endpoints: list[str] | None = None) -> str | None:
    """`key` がどのエンドポイントのものかを決める。

    `key` は ``fins/summary/historical/2021/fins_summary_202109.csv.gz`` の
    ような形で、エンドポイントは ``/fins/summary``。

    **区切りの数で決めない。** ``/derivatives/bars/daily/futures`` は4つ、
    ``/fins/summary`` は2つで、数えると片方が必ず外れる。既知の名前のうち
    **いちばん長く前方一致するもの**を採る。
    """
    from stock_ai.data.jquants_bulk import ARCHIVE_ENDPOINTS

    candidates = endpoints if endpoints is not None else list(ARCHIVE_ENDPOINTS)
    normalized = "/" + key.lstrip("/")
    matched = [name for name in candidates if normalized.startswith(name.rstrip("/") + "/")]
    return max(matched, key=len) if matched else None


def read_archived(path: Path) -> bytes:
    """原本を1本読む。`.gz` なら展開する。

    **保存は展開せずにしてある**ので、読むときに戻す。ここで戻せることが、
    保存した意味そのものである。
    """
    payload = path.read_bytes()
    if path.suffix == ".gz":
        return gzip.decompress(payload)
    return payload


def census(directory: Path = DEFAULT_ARCHIVE_DIR) -> dict[str, ReadReport]:
    """保存済みの原本を全部、読み口に通して数える。**落とすことはしない。**

    **1本の失敗で止めない。** どこが読めないのかを一覧にするほうが、期限の
    ある作業には合う。

    Returns:
        エンドポイントごとの :class:`ReadReport`。読み口の無いエンドポイントも
        件数0で出す——**出力から消すと、保存できているのに一度も読まれていない
        ことが分からなくなる。**
    """
    manifest = read_manifest(directory)
    reports: dict[str, ReadReport] = {}
    for key in sorted(manifest):
        endpoint = endpoint_of(key) or "(不明)"
        report = reports.setdefault(endpoint, ReadReport(endpoint=endpoint))
        report.files += 1

        parser = PARSERS.get(endpoint)
        if parser is None:
            report.failed[key] = "読み口が無い"
            continue
        try:
            rows = parser(read_archived(path_for(directory, key)))
        except Exception as exc:  # noqa: BLE001 - どこで読めないかが記録に値する
            report.failed[key] = f"{type(exc).__name__}: {exc}"
            logger.warning("原本を読めなかった: %s: %s", key, exc)
            continue
        report.rows += len(rows)
        if not rows:
            # **0行は例外を出さない。** 読めたことと、中身があることは別である。
            report.empty.append(key)
    return reports


@dataclasses.dataclass(frozen=True)
class Shape:
    """原本1本の「形」。**中身ではなく、読める形かどうかを見る。**"""

    key: str
    columns: tuple[str, ...]
    rows: int
    encoding: str
    first_date: str
    last_date: str


#: 日付が入っていそうな列名。先に書いたものから探す。
DATE_COLUMNS = ("Date", "DiscDate", "PubDate", "SchDate", "AppDate", "CalcDate")


def shape_of(directory: Path, key: str) -> Shape | None:
    """原本1本の列名・行数・文字コード・日付の範囲を読む。

    **保存したものが何なのかを、落としてから確かめる口である。** 配布サンプル
    は1〜6行しか無く、月次の全銘柄ファイルとは形が違いうる。サンプルで通った
    ことは、実物で通ったことにならない。
    """
    from stock_ai.data.jquants_bulk import decode_csv, records_from_csv

    path = path_for(directory, key)
    if not path.is_file():
        return None
    payload = read_archived(path)
    _text, encoding = decode_csv(payload)
    rows = records_from_csv(payload)
    if not rows:
        return Shape(key, (), 0, encoding, "", "")

    columns = tuple(rows[0])
    column = next((name for name in DATE_COLUMNS if name in columns), None)
    dates = sorted({(row.get(column) or "").strip() for row in rows}) if column else []
    dates = [value for value in dates if value]
    return Shape(
        key=key,
        columns=columns,
        rows=len(rows),
        encoding=encoding,
        first_date=dates[0] if dates else "",
        last_date=dates[-1] if dates else "",
    )


def one_per_endpoint(directory: Path = DEFAULT_ARCHIVE_DIR) -> dict[str, str]:
    """エンドポイントごとに、いちばん新しい `key` を1つ選ぶ。

    **全部を開かない。** 385本を開くと数分かかるうえ、貼ったときに長くなる。
    形を見るだけなら1本で足りる。
    """
    latest: dict[str, str] = {}
    for key in sorted(read_manifest(directory)):
        endpoint = endpoint_of(key) or "(不明)"
        latest[endpoint] = key  # 昇順なので最後が残る
    return latest
