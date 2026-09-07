"""保存した `/equities/bars/daily` から、株価を DB に入れる。

**銘柄ごとに叩かない。** `BulkIngester` は1銘柄1リクエストで、この経路は
2回止まっている——84銘柄で1回、3,700銘柄で1回、どちらも 429 である。
20年 × 約4,400銘柄は1週間に収まらない。

一括ファイルには**全銘柄の四本値が日付ごとに**入っている。名簿と同じ形で
ある。240本を保存すれば、あとはローカルで読むだけになる。

## 生値と調整値を取り違えない

このプロジェクトが繰り返し踏んでいるのが「分割前後で尺度の違う値を組み
合わせる」である。一括ファイルには**両方入っている**ので、推測せずに済む。

| 入れる先 | 元の列 | 何か |
|---|---|---|
| `open` `high` `low` `close` | `O` `H` `L` `C` | **実際に売買された値** |
| `adj_close` | `AdjC` | `close` に対応する調整後 |
| `volume` | `Vo` | 調整前の出来高 |

**`adj_close` は、同じ行の `close` に対応していなければならない。**
`split_adjusted` は `adj_close / close` を分割比として全ての足に掛ける。
ここで `AdjO` を `open` に入れると、比が意味を失い、**例外は出ないまま
すべての足がずれる。**

## 名簿とは絞り込みが違う

**投信・ETF を落とさない。** 名簿は「どの会社が上場していたか」なので投信を
除くが、株価は指数の代わりにも使う——このプロジェクトは `1306` を
ベンチマークにしている。**落とすと、比較対象が消える。**

4桁に直せないコード（優先株・種類株）だけを落とす。
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Callable
from pathlib import Path

import pandas as pd

from stock_ai.core.logging import get_logger
from stock_ai.data.jquants_bulk import records_from_csv
from stock_ai.data.jquants_margin import parse_date, parse_number
from stock_ai.data.jquants_read import endpoint_of, read_archived
from stock_ai.data.schema import ADJ_CLOSE, CLOSE, DATE, HIGH, LOW, OPEN, VOLUME
from stock_ai.data.universe import four_digit_code

logger = get_logger(__name__)

#: 取り出し元のエンドポイント。
BARS_ENDPOINT = "/equities/bars/daily"

#: 一括ファイルの列 → こちらの列。**生値と調整値を混ぜない。**
COLUMN_MAP: dict[str, str] = {
    "O": OPEN,
    "H": HIGH,
    "L": LOW,
    "C": CLOSE,
    "AdjC": ADJ_CLOSE,
    "Vo": VOLUME,
}


@dataclasses.dataclass
class PriceIngestReport:
    """1回の取り込みで何が起きたか。"""

    files: int = 0
    rows: int = 0
    written: int = 0
    symbols: set[str] = dataclasses.field(default_factory=set)
    skipped_no_close: int = 0
    """終値の無い行。**0 にしない。** 売買が無かった日と、値が欠けた日は別。"""

    no_close_but_traded: int = 0
    """終値が無いのに出来高がある行。**これは説明が付かない。**

    売買が無ければ終値も出来高も無い。出来高だけあるなら、こちらの読み方が
    間違っているか、向こうの列の意味が変わったかである。**0 でなければ、
    落とした行を「取引が無かった日」で説明できない。**
    """

    skipped_code: int = 0
    """4桁に直せないコード（優先株・種類株）。"""

    undated: int = 0
    failed: dict[str, str] = dataclasses.field(default_factory=dict)

    def summary(self) -> str:
        """1行のまとめ。"""
        return (
            f"{self.files} 本から {self.written:,} 行を書き込み、"
            f"{len(self.symbols):,} 銘柄、{self.rows:,} 行を読んだ"
            + (f"、終値なし {self.skipped_no_close:,}" if self.skipped_no_close else "")
            + (
                f"（うち出来高あり **{self.no_close_but_traded:,}**）"
                if self.no_close_but_traded
                else ""
            )
            + (f"、日付なし {self.undated:,}" if self.undated else "")
            + (f"、{len(self.failed)} 本が読めず" if self.failed else "")
        )


def frames_from_payload(payload: bytes) -> tuple[dict[str, pd.DataFrame], PriceIngestReport]:
    """展開済みの一括四本値を、**銘柄ごとの表**にする。

    `upsert_prices` が銘柄ごとに受け取る形に合わせる。日付を索引に持つ。

    **終値の無い行は落とす。** `0` を入れると、売買の無かった日が「値がゼロに
    なった日」として並ぶ。指標も収益率も通ってしまう。
    """
    report = PriceIngestReport()
    rows = records_from_csv(payload)
    report.rows = len(rows)

    collected: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        symbol = four_digit_code((row.get("Code") or "").strip())
        if symbol is None:
            report.skipped_code += 1
            continue
        date = parse_date(row.get("Date"))
        if date is None:
            report.undated += 1
            continue
        close = parse_number(row.get("C"))
        if close is None or close == 0:
            report.skipped_no_close += 1
            # **落とした理由を確かめられるようにする。** 売買が無ければ終値も
            # 出来高も無い。出来高だけあるなら「取引が無かった日」では説明が
            # 付かず、読み方か列の意味のどちらかが違う。
            if (parse_number(row.get("Vo")) or 0) > 0:
                report.no_close_but_traded += 1
            continue

        values: dict[str, object] = {DATE: pd.Timestamp(date)}
        for source, target in COLUMN_MAP.items():
            values[target] = parse_number(row.get(source))
        # 調整後が無いときは、生値をそのまま置く。**`0` にしない**——
        # `split_adjusted` は `adj_close / close` を掛けるので、0 は全ての足を
        # 0 にする。1倍（＝調整なし）のほうが、間違いとして軽い。
        if values[ADJ_CLOSE] is None:
            values[ADJ_CLOSE] = close
        for column in (OPEN, HIGH, LOW):
            if values[column] is None:
                values[column] = close
        values[VOLUME] = values[VOLUME] or 0
        collected.setdefault(symbol, []).append(values)

    frames: dict[str, pd.DataFrame] = {}
    for symbol, items in collected.items():
        frame = pd.DataFrame(items).set_index(DATE).sort_index()
        frame.index.name = DATE
        frames[symbol] = frame
    report.symbols = set(frames)
    return frames, report


def ingest(
    archive_dir: Path,
    upsert: Callable[[str, pd.DataFrame], int],
    limit: int | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> PriceIngestReport:
    """保存済みの一括四本値を、`upsert` に渡していく。

    **API を1回も叩かない。** 原本さえあれば、解約後にも実行できる。

    書き込みは何度やっても同じ結果になる（`upsert`）ので、途中で止めても
    もう一度実行すればよい。**DB は作り直せる**——原本と違って、ここでの
    失敗は安い。

    Args:
        archive_dir: 原本の置き場所。
        upsert: ``(銘柄, 表)`` を受けて書き込んだ行数を返す呼び出し。
        limit: 読む原本の本数の上限。**まず少数で試すため。**
        progress: 1本ごとに ``(番号, 総数, key)`` で呼ばれる。

    Returns:
        :class:`PriceIngestReport`。
    """
    from stock_ai.data.jquants_archive import path_for, read_manifest

    keys = [key for key in sorted(read_manifest(archive_dir)) if endpoint_of(key) == BARS_ENDPOINT]
    if limit is not None:
        keys = keys[:limit]

    total_report = PriceIngestReport()
    if not keys:
        logger.warning("`%s` の原本が1本も無い。", BARS_ENDPOINT)
        return total_report

    total = len(keys)
    for index, key in enumerate(keys, start=1):
        if progress is not None:
            progress(index, total, key)
        try:
            frames, report = frames_from_payload(read_archived(path_for(archive_dir, key)))
        except Exception as exc:  # noqa: BLE001 - どこで読めないかが記録に値する
            total_report.failed[key] = f"{type(exc).__name__}: {exc}"
            logger.warning("原本を読めなかった: %s: %s", key, exc)
            continue

        total_report.files += 1
        total_report.rows += report.rows
        total_report.skipped_no_close += report.skipped_no_close
        total_report.no_close_but_traded += report.no_close_but_traded
        total_report.skipped_code += report.skipped_code
        total_report.undated += report.undated
        total_report.symbols |= report.symbols
        for symbol, frame in frames.items():
            total_report.written += upsert(symbol, frame)

    logger.info("株価の取り込み: %s", total_report.summary())
    return total_report


def span_of(frames: dict[str, pd.DataFrame]) -> tuple[dt.date, dt.date] | None:
    """表の集まりが覆っている日付の範囲。"""
    dates = [index for frame in frames.values() for index in frame.index]
    if not dates:
        return None
    return (min(dates).date(), max(dates).date())
