"""TOPIX の指数そのものを読む（`/indices/bars/daily/topix`）。

## なぜ要るのか

ベンチマークに **1306（TOPIX 連動 ETF）** を使っている。指数そのものが手元に
無かったからで、**それ以上の理由は無い。**

Premium で `/indices/bars/daily/topix` が開いた。**2008-05 〜 2026-09 の
18年ぶん、88 KB である。**

## ETF と指数は同じものではない

| | |
|---|---|
| TOPIX | 指数。信託報酬も、売買のずれも無い |
| 1306 | それを追う ETF。**信託報酬が毎日引かれ、追跡のずれが乗る** |

18年ぶん積み上がると、これは無視できる大きさではなくなる。**ただし「無視
できない」は見込みであって、測った値ではない。** 引き算は
:func:`tracking_gap` がやる。

**どちらも配当を含まない。** TOPIX は配当なしの指数で、1306 の株価は配当を
払い出したぶん下がる。**だから引き算が成り立つ**——片方だけ配当込みなら、
差は信託報酬ではなく配当利回りを測ることになる。

## 置き換えると、それは2回目の判定になる

**#7 のベンチマークを 1306 から TOPIX に替えて回し直すのは、同じ説の2回目の
判定である。** ここで作るのは測る道具であって、判定のやり直しではない。

新しい説（#5・#8）はまだ判定を使っていないので、**そちらは最初から TOPIX を
使える。**

## 列

配布サンプル（`TOPIX Prices (OHLC).csv`）の列は4つだけ。

```
Date,O,H,L,C
```

**出来高が無い。** 指数なので当然だが、四本値と同じ読み口に流し込むときに
`VOLUME` を 0 で埋めるか落とすかを決める必要がある。**ここでは列を作らない**
——0 を入れると「売買が無かった日」と区別が付かなくなる。
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from pathlib import Path

import pandas as pd

from stock_ai.core.logging import get_logger
from stock_ai.data.jquants_bulk import records_from_csv
from stock_ai.data.jquants_margin import parse_date, parse_number
from stock_ai.data.jquants_read import endpoint_of, read_archived
from stock_ai.data.schema import CLOSE, DATE, HIGH, LOW, OPEN

logger = get_logger(__name__)

#: 取り出し元のエンドポイント。
TOPIX_ENDPOINT = "/indices/bars/daily/topix"

#: 一括ファイルの列 → こちらの列。**四本値と同じ綴りである。**
COLUMN_MAP: dict[str, str] = {"O": OPEN, "H": HIGH, "L": LOW, "C": CLOSE}


def parse_topix(payload: bytes) -> pd.DataFrame:
    """展開済みの TOPIX 四本値を、日付を索引にした表にする。

    **終値の無い行は落とす。** 指数に「売買が無かった日」は無いので、終値が
    無い行は読み違いか欠けのどちらかである。件数は :func:`census` が出す。
    """
    rows: list[dict[str, object]] = []
    for row in records_from_csv(payload):
        date = parse_date(row.get("Date"))
        if date is None:
            continue
        close = parse_number(row.get("C"))
        if close is None or close == 0:
            continue
        values: dict[str, object] = {DATE: pd.Timestamp(date)}
        for source, target in COLUMN_MAP.items():
            values[target] = parse_number(row.get(source))
        for column in (OPEN, HIGH, LOW):
            if values[column] is None:
                values[column] = close
        rows.append(values)

    if not rows:
        return pd.DataFrame(columns=[OPEN, HIGH, LOW, CLOSE], index=pd.DatetimeIndex([], name=DATE))
    frame = pd.DataFrame(rows).set_index(DATE).sort_index()
    frame.index.name = DATE
    return frame


def from_archive(archive_dir: Path) -> pd.DataFrame:
    """保存済みの原本から TOPIX を組み立てる。**API を1回も叩かない。**

    同じ日が複数の原本に出たら、**後から読んだほうで上書きしない**——月ごとの
    原本と `live` の原本が重なる期間があり、どちらを採るかで値が変わりうる。
    重複は落として、最初に読んだものを残す。
    """
    from stock_ai.data.jquants_archive import path_for, read_manifest

    keys = [key for key in sorted(read_manifest(archive_dir)) if endpoint_of(key) == TOPIX_ENDPOINT]
    if not keys:
        logger.warning("`%s` の原本が1本も無い。", TOPIX_ENDPOINT)
        return parse_topix(b"")

    pieces: list[pd.DataFrame] = []
    for key in keys:
        try:
            pieces.append(parse_topix(read_archived(path_for(archive_dir, key))))
        except Exception as exc:  # noqa: BLE001 - どこで読めないかが記録に値する
            logger.warning("TOPIX の原本を読めなかった: %s: %s", key, exc)
    if not pieces:
        return parse_topix(b"")

    frame = pd.concat(pieces).sort_index()
    return frame[~frame.index.duplicated(keep="first")]


@dataclasses.dataclass
class Census:
    """組み立てた TOPIX が、どんな形をしているか。"""

    rows: int = 0
    first: dt.date | None = None
    last: dt.date | None = None
    duplicates: int = 0

    missing: list[dt.date] = dataclasses.field(default_factory=list)
    """カレンダーが立会と言っているのに、指数の無い日。"""

    checked: bool = False
    """カレンダーと突き合わせたか。**`False` は「穴が無い」ではなく「見て
    いない」である。** 区別しないと、カレンダーを渡し忘れた実行が「穴なし」に
    見える。
    """

    extra: list[dt.date] = dataclasses.field(default_factory=list)
    """指数はあるのに、カレンダーが立会と言っていない日。**カレンダーのほうが
    疑わしい向きである。**
    """

    def summary(self) -> str:
        """1行のまとめ。"""
        if not self.rows:
            return "TOPIX の原本が無い。"
        tail = (
            f"、立会なのに指数の無い日 {len(self.missing)}"
            if self.checked
            else "、カレンダーが無いので穴は見ていない"
        )
        return (
            f"{self.first} 〜 {self.last} / {self.rows:,} 日"
            + (f"、重複 {self.duplicates}" if self.duplicates else "")
            + tail
            + (f"、立会でないのに指数のある日 {len(self.extra)}" if self.extra else "")
        )


def census(
    frame: pd.DataFrame,
    raw_rows: int | None = None,
    trading: set[dt.date] | None = None,
) -> Census:
    """組み立てた表を数える。**穴はカレンダーと突き合わせて決める。**

    最初は「暦で5日を超えて空いたら穴」と数えていた。**2026-09-15 の実測で
    21件出て、21件とも年末年始・ゴールデンウィーク・シルバーウィークだった。**

    「連休は5日まで」は当て推量で、日本の休場を調べずに書いたものである。
    **しかも取引カレンダーは手元にあった。** 持っている答えを使わずに、経験則で
    代用していた。

    カレンダーを渡さなければ**穴を数えない。** 0 と報告すると、渡し忘れた実行が
    「穴なし」に見える——**「見ていない」と「無い」は別である。**

    Args:
        frame: :func:`from_archive` が返す表。
        raw_rows: 畳む前の行数。重複の数を出すのに使う。
        trading: 立会日の集合。省略すると穴を数えない。
    """
    report = Census(rows=len(frame))
    if frame.empty:
        return report
    dates = [value.date() for value in frame.index]
    report.first, report.last = dates[0], dates[-1]
    if raw_rows is not None:
        report.duplicates = max(raw_rows - len(frame), 0)
    if trading is None:
        return report

    report.checked = True
    have = set(dates)
    # **重なる期間だけ見る。** カレンダーは指数より長い期間を覆っているので、
    # 全期間で取ると「指数の無い立会日」が何千日も出る。それは欠けではなく、
    # 指数がそこまで遡っていないだけである。
    window = {day for day in trading if report.first <= day <= report.last}
    report.missing = sorted(window - have)
    report.extra = sorted(day for day in have if day not in trading)
    return report


@dataclasses.dataclass
class TrackingGap:
    """ETF が指数からどれだけ離れたか。"""

    days: int = 0
    first: dt.date | None = None
    last: dt.date | None = None
    index_return: float = 0.0
    etf_return: float = 0.0

    @property
    def total(self) -> float:
        """期間全体の差（ETF − 指数）。"""
        return self.etf_return - self.index_return

    @property
    def annual(self) -> float:
        """年あたりに直した差。**18年ぶんを1年に均す。**"""
        years = self.days / 245.0
        if years <= 0:
            return 0.0
        return (1.0 + self.total) ** (1.0 / years) - 1.0 if self.total > -1.0 else 0.0

    def summary(self) -> str:
        """1行のまとめ。"""
        if not self.days:
            return "重なる日が無い。比べられない。"
        return (
            f"{self.first} 〜 {self.last} の {self.days:,} 日で、"
            f"指数 {self.index_return:+.1%} / ETF {self.etf_return:+.1%}、"
            f"差 {self.total:+.1%}（年あたり {self.annual:+.2%}）"
        )


def tracking_gap(index: pd.DataFrame, etf: pd.Series) -> TrackingGap:
    """指数と ETF を、**重なる日だけ**で比べる。

    **どちらも配当を含まない前提である。** TOPIX は配当なしの指数で、ETF の
    株価は配当を払い出したぶん下がる。片方だけ配当込みなら、この差は信託報酬
    ではなく配当利回りを測ることになる。

    Args:
        index: :func:`from_archive` が返す表。
        etf: ETF の調整後終値。日付を索引に持つ。

    Returns:
        :class:`TrackingGap`。
    """
    report = TrackingGap()
    if index.empty or etf.empty:
        return report
    shared = index.index.intersection(etf.index)
    if len(shared) < 2:
        return report

    left = index.loc[shared, CLOSE].astype(float)
    right = etf.loc[shared].astype(float)
    if left.iloc[0] <= 0 or right.iloc[0] <= 0:
        return report

    report.days = len(shared)
    report.first = shared[0].date()
    report.last = shared[-1].date()
    report.index_return = float(left.iloc[-1] / left.iloc[0] - 1.0)
    report.etf_return = float(right.iloc[-1] / right.iloc[0] - 1.0)
    return report
