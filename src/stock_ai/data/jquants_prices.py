"""保存した `/equities/bars/daily` から、株価を DB に入れる。

**銘柄ごとに叩かない。** `BulkIngester` は1銘柄1リクエストで、この経路は
2回止まっている——84銘柄で1回、3,700銘柄で1回、どちらも 429 である。
20年 × 約4,400銘柄は1週間に収まらない。

一括ファイルには**全銘柄の四本値が日付ごとに**入っている。名簿と同じ形で
ある。240本を保存すれば、あとはローカルで読むだけになる。

## 調整値はこちらで組み立てる。ファイルの `AdjC` には頼らない

**一括ファイルに `AdjC` の列は入っていない**（2026-09-07 実測。4,996,413行
すべてで読めなかった）。配布サンプルの `Stock Prices (OHLC).csv` には有る
ので、**サンプルだけ見て「有る」と思い込むと、静かに外れる。**

最初の実装は `AdjC` を読み、無ければ生値をそのまま入れていた。つまり
**全期間が無調整**になり、立花（調整済み）の上に重ねたところ、継ぎ目の
2021-09-01 で76銘柄中16銘柄が 20% 以上跳んだ——+100%、+102%、+301%。
**市場の動きではなく分割比そのものである。**

`AdjFactor` は行ごとに入っている（分割の権利落ち日に `0.5` など）。そこから
組み立てる。

    adj_close(d) = close(d) × Π{ factor(j) : j が d より後 }

**`j > d` であって `j >= d` ではない。** 権利落ち日の価格は既に新しい基準
なので、その日の係数は掛けない。取り違えると分割日1日だけがずれる。

分割は稀なので、`factor != 1` の行だけ集めれば全期間ぶんでも小さい。取り
込みが2周なのはこのためである——1周目で分割を集め、2周目で書き込む。

**`AdjC` が読めた行数も数える。** 0 なら列が無い。数えていなければ、
「一致した」と「比べていない」の区別が付かない——**実際にそこで診断を1回
間違えた。**

## 生値と調整値を取り違えない

このプロジェクトが繰り返し踏んでいるのが「分割前後で尺度の違う値を組み
合わせる」である。一括ファイルには**両方入っている**ので、推測せずに済む。

| 入れる先 | 元の列 | 何か |
|---|---|---|
| `open` `high` `low` `close` | `O` `H` `L` `C` | **実際に売買された値** |
| `adj_close` | `C` × 後の `AdjFactor` の積 | **こちらで組み立てる**（上を見よ） |
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
    "Vo": VOLUME,
}

#: 調整係数が1とみなせる幅。浮動小数の丸めで `1.0000000001` が来ても、
#: 分割として数えない。
FACTOR_TOLERANCE = 1e-9

#: 組み立て直した調整値が、ファイルの `AdjC` とどれだけ違えば「違う」と数えるか。
ADJ_MISMATCH_TOLERANCE = 0.001

#: 銘柄ごとの ``{分割の権利落ち日: 係数}``。
SplitTable = dict[str, dict[dt.date, float]]


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
    splits: int = 0
    """`AdjFactor` が1でない行の数。**分割の権利落ち日である。**"""

    adj_c_rows: int = 0
    """ファイルの `AdjC` が読めた行。

    **0 なら、その列がそもそも無い。** 2026-09-07 の実測がこれだった——
    配布サンプルには `AdjC` があるのに、一括ファイルには入っていない。

    **これを数えていなかったせいで、診断を1回間違えた。** 「`AdjC` は後の
    分割を知らない」と書いたが、実際は `AdjC` が無く、古い実装が生値を
    そのまま調整値に入れていた（＝無調整）だけだった。
    """

    adj_mismatch: int = 0
    """組み立て直した調整値が、ファイルの `AdjC` と違った行。

    `adj_c_rows` が 0 なら、これも必ず 0 になる。**2つを並べて見ること**
    ——片方だけでは「一致した」と「比べていない」の区別が付かない。
    """

    failed: dict[str, str] = dataclasses.field(default_factory=dict)

    split_table: SplitTable = dataclasses.field(default_factory=dict)
    """集めた分割。**書き込んだあとに、権利落ち日を確かめるために持っておく。**"""

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
            + (f"、分割 {self.splits:,}" if self.splits else "")
            + (
                f"、AdjC のある行 {self.adj_c_rows:,}（うち違う {self.adj_mismatch:,}）"
                if self.adj_c_rows
                else "、AdjC の列は無い"
            )
            + (f"、{len(self.failed)} 本が読めず" if self.failed else "")
        )


def split_factors_from_payload(payload: bytes, into: SplitTable) -> int:
    """`AdjFactor` が1でない行を拾って ``into`` に足す。**分割の権利落ち日。**

    分割は稀なので、これだけ集めれば全期間ぶんでも小さい。**全部の行を覚えて
    おく必要は無い。**

    Returns:
        拾った件数。
    """
    found = 0
    for row in records_from_csv(payload):
        factor = parse_number(row.get("AdjFactor"))
        if factor is None or abs(factor - 1.0) <= FACTOR_TOLERANCE:
            continue
        symbol = four_digit_code((row.get("Code") or "").strip())
        date = parse_date(row.get("Date"))
        if symbol is None or date is None:
            continue
        into.setdefault(symbol, {})[date] = factor
        found += 1
    return found


def cumulative_factor(factors: dict[dt.date, float] | None, on: dt.date) -> float:
    """``on`` の価格を最新の基準に揃えるための倍率。

    **``on`` より後の分割だけを掛ける。** 権利落ち日そのものの価格は既に新しい
    基準なので、その日の係数は掛けない——`j > d` であって `j >= d` ではない。
    ここを取り違えると、分割日1日だけが分割比ぶんずれる。
    """
    if not factors:
        return 1.0
    total = 1.0
    for date, factor in factors.items():
        if date > on:
            total *= factor
    return total


def frames_from_payload(
    payload: bytes, splits: SplitTable | None = None
) -> tuple[dict[str, pd.DataFrame], PriceIngestReport]:
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
        for column in (OPEN, HIGH, LOW):
            if values[column] is None:
                values[column] = close
        values[VOLUME] = values[VOLUME] or 0

        # 調整値は全期間の `AdjFactor` から組み立てる。**ファイルの `AdjC` に
        # 頼らない**——一括ファイルにその列が無いことが実測で分かっている。
        factor = cumulative_factor((splits or {}).get(symbol), date)
        values[ADJ_CLOSE] = close * factor
        if abs(factor - 1.0) > FACTOR_TOLERANCE:
            report.splits += 1

        # **`AdjC` があるなら、突き合わせる。** 無いなら、無いと数える。
        # 「一致した」と「比べていない」を、件数で区別できるようにする。
        provided = parse_number(row.get("AdjC"))
        if provided is not None and provided > 0:
            report.adj_c_rows += 1
            if abs(values[ADJ_CLOSE] / provided - 1.0) > ADJ_MISMATCH_TOLERANCE:
                report.adj_mismatch += 1
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

    # **1周目: 分割だけを集める。** 月ごとの原本はその月より後の分割を知らない
    # ので、調整値は全期間を見てからでないと組み立てられない。分割は稀なので、
    # `AdjFactor != 1` の行だけなら全期間ぶんでも小さい。
    splits: SplitTable = {}
    for index, key in enumerate(keys, start=1):
        if progress is not None:
            progress(index, total * 2, key)
        try:
            split_factors_from_payload(read_archived(path_for(archive_dir, key)), splits)
        except Exception as exc:  # noqa: BLE001 - どこで読めないかが記録に値する
            total_report.failed[key] = f"{type(exc).__name__}: {exc}"
            logger.warning("原本を読めなかった: %s: %s", key, exc)
    logger.info("分割のある銘柄: %d", len(splits))

    # 2周目: 書き込む。
    for index, key in enumerate(keys, start=1):
        if progress is not None:
            progress(total + index, total * 2, key)
        if key in total_report.failed:
            continue
        try:
            frames, report = frames_from_payload(read_archived(path_for(archive_dir, key)), splits)
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
        total_report.splits += report.splits
        total_report.adj_c_rows += report.adj_c_rows
        total_report.adj_mismatch += report.adj_mismatch
        total_report.symbols |= report.symbols
        for symbol, frame in frames.items():
            total_report.written += upsert(symbol, frame)

    total_report.split_table = splits
    logger.info("株価の取り込み: %s", total_report.summary())
    return total_report


def span_of(frames: dict[str, pd.DataFrame]) -> tuple[dt.date, dt.date] | None:
    """表の集まりが覆っている日付の範囲。"""
    dates = [index for frame in frames.values() for index in frame.index]
    if not dates:
        return None
    return (min(dates).date(), max(dates).date())


def join_returns(
    load: Callable[[str], pd.DataFrame],
    symbols: list[str],
    on: dt.date,
) -> list[tuple[str, float]]:
    """継ぎ目の日の、調整後の収益率を銘柄ごとに返す。

    **DB には出所の違う株価が入りうる。** 立花は 2001年から、一括の原本は
    2021-09 からで、重なる期間は一括が上書きする。継ぎ目より前は立花、後は
    J-Quants という系列になる。

    どちらも「最新の分割を基準にした調整後」を出しているはずだが、**はず**で
    ある。基準が違えば、継ぎ目の1日だけ分割比ぶんの収益率が立つ。

    **例外は出ない。** 収益率の表も、指標も、そのまま通る。そこだけ見れば
    「その日に大きく動いた銘柄が沢山あった」に見える。

    Args:
        load: 銘柄を受けて**調整後**の日足を返す呼び出し。
        symbols: 見る銘柄。全部見なくてよい——**基準が違えば、ほぼ全部が跳ぶ。**
        on: 継ぎ目の日（原本が覆い始める日）。

    Returns:
        ``(銘柄, 継ぎ目の日の収益率)``。前後どちらかが欠ける銘柄は入れない。
    """
    found: list[tuple[str, float]] = []
    for symbol in symbols:
        frame = load(symbol)
        if frame.empty or CLOSE not in frame:
            continue
        series = frame[CLOSE]
        before = series[series.index < pd.Timestamp(on)]
        after = series[series.index >= pd.Timestamp(on)]
        if before.empty or after.empty:
            continue
        previous = float(before.iloc[-1])
        if previous <= 0:
            continue
        found.append((symbol, float(after.iloc[0]) / previous - 1.0))
    return found


#: 「係数を掛け忘れた形」とみなす幅。
#:
#: 規約を `j >= d` で書くと、権利落ち日の収益率が **ちょうど `係数 - 1`**
#: になる（1:2 なら −50%）。そこからどれだけ離れていたら「別の理由」と
#: 見なすか。
UNAPPLIED_TOLERANCE = 0.02


def split_day_returns(
    load: Callable[[str], pd.DataFrame],
    splits: SplitTable,
) -> list[tuple[str, dt.date, float, float]]:
    """分割の権利落ち日の、調整後の収益率と係数を返す。

    **継ぎ目の検査は1日しか見ていない。** 期間の内側で起きた分割は、そこでは
    確かめられない。組み立てを `j >= d` で書いていたら、**継ぎ目は綺麗なまま、
    分割日だけが1日ずつずれる。** どちらも例外は出ない。

    Returns:
        ``(銘柄, 権利落ち日, その日の収益率, 係数)``。前日が無い分割は入れない。
    """
    found: list[tuple[str, dt.date, float, float]] = []
    for symbol, factors in splits.items():
        frame = load(symbol)
        if frame.empty or CLOSE not in frame:
            continue
        series = frame[CLOSE]
        for date, factor in sorted(factors.items()):
            stamp = pd.Timestamp(date)
            before = series[series.index < stamp]
            on = series[series.index == stamp]
            if before.empty or on.empty or float(before.iloc[-1]) <= 0:
                continue
            change = float(on.iloc[0]) / float(before.iloc[-1]) - 1.0
            found.append((symbol, date, change, factor))
    return found


def looks_unapplied(change: float, factor: float) -> bool:
    """その日の動きが、**係数を掛け忘れた形**に見えるか。

    **「大きく動いた」だけでは判定にならない。** 権利落ち日に本当に 25% 動く
    銘柄はある。分割以外の事由（併合・株式無償割当・合併）でも係数は立ち、
    その日の動きが係数どおりにならないことも普通にある。

    規約を間違えていれば、動きは**ちょうど `係数 - 1`** になる。しかも
    **1件ではなく全件がそうなる。** 見るべきはそこである。
    """
    return abs(change - (factor - 1.0)) < UNAPPLIED_TOLERANCE


@dataclasses.dataclass
class SymbolProbe:
    """1銘柄が、原本の中でどう見えているか。

    **「株価が無い」には、少なくとも3つの理由がありうる。**

    | 見え方 | 意味 |
    |---|---|
    | 四本値に行が無い | 一括の株価に載っていない銘柄である |
    | 行はあるが終値が無い | 上場しているが売買が成立していない |
    | 終値もある | こちらの取り込みが落としている——**不具合** |

    **件数だけでは、この3つが区別できない。**
    """

    symbol: str
    bar_rows: int = 0
    priced_rows: int = 0
    first: dt.date | None = None
    last: dt.date | None = None
    name: str = ""
    market: str = ""
    product: str = ""

    @property
    def verdict(self) -> str:
        """3つのどれか。"""
        if self.bar_rows == 0:
            return "四本値に行が無い"
        if self.priced_rows == 0:
            return "行はあるが終値が無い"
        return "終値もある（取り込みの不具合）"


def probe_symbols(archive_dir: Path, symbols: set[str]) -> dict[str, SymbolProbe]:
    """原本を読んで、その銘柄がどう見えているかを返す。**取得はしない。**

    株価が無い理由を、**当てずっぽうではなく原本から**決める。名簿には出て
    いるのに株価が1本も無い銘柄が16件あり、そのうち0件が出所の違いだった
    （2026-09-08）。残る説明は原本の中にしかない。
    """
    from stock_ai.data.jquants_archive import path_for, read_manifest

    found = {symbol: SymbolProbe(symbol=symbol) for symbol in symbols}
    manifest = sorted(read_manifest(archive_dir))

    for key in manifest:
        endpoint = endpoint_of(key)
        if endpoint not in (BARS_ENDPOINT, "/equities/master"):
            continue
        try:
            rows = records_from_csv(read_archived(path_for(archive_dir, key)))
        except Exception as exc:  # noqa: BLE001 - どこで読めないかが記録に値する
            logger.warning("原本を読めなかった: %s: %s", key, exc)
            continue

        for row in rows:
            symbol = four_digit_code((row.get("Code") or "").strip())
            if symbol is None or symbol not in found:
                continue
            probe = found[symbol]
            if endpoint == "/equities/master":
                # **最後に見えた姿を残す。** 廃止直前の市場区分が知りたい。
                probe.name = (row.get("CoName") or probe.name).strip()
                probe.market = (row.get("MktNm") or probe.market).strip()
                probe.product = (row.get("ProdCat") or probe.product).strip()
                continue
            probe.bar_rows += 1
            date = parse_date(row.get("Date"))
            if date is not None:
                probe.first = date if probe.first is None else min(probe.first, date)
                probe.last = date if probe.last is None else max(probe.last, date)
            close = parse_number(row.get("C"))
            if close is not None and close > 0:
                probe.priced_rows += 1
    return found


def frames_for(archive_dir: Path, symbols: set[str]) -> dict[str, pd.DataFrame]:
    """保存済みの四本値から、指定した銘柄ぶんだけを組み立てる。

    **原本を1周しか読まない。** 銘柄ごとに読み直すと、65本を銘柄の数だけ
    開くことになる。

    調整値は :func:`ingest` と同じ作り方をする——`AdjFactor` の積である。
    **ここだけ別の作り方にすると、突き合わせが「作り方の違い」を測ることに
    なる。**
    """
    from stock_ai.data.jquants_archive import path_for, read_manifest

    keys = [key for key in sorted(read_manifest(archive_dir)) if endpoint_of(key) == BARS_ENDPOINT]
    splits: SplitTable = {}
    for key in keys:
        try:
            split_factors_from_payload(read_archived(path_for(archive_dir, key)), splits)
        except Exception as exc:  # noqa: BLE001 - どこで読めないかが記録に値する
            logger.warning("原本を読めなかった: %s: %s", key, exc)

    collected: dict[str, list[pd.DataFrame]] = {}
    for key in keys:
        try:
            frames, _report = frames_from_payload(read_archived(path_for(archive_dir, key)), splits)
        except Exception:  # noqa: BLE001 - 上で警告済み
            continue
        for symbol in symbols & set(frames):
            collected.setdefault(symbol, []).append(frames[symbol])

    return {symbol: pd.concat(parts).sort_index() for symbol, parts in collected.items() if parts}
