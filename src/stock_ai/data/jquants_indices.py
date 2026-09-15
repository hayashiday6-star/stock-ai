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
    gaps: list[tuple[dt.date, int]] = dataclasses.field(default_factory=list)
    """``(日付, 直前の営業日から空いた暦日数)``。**5日を超える穴だけ。**"""

    def summary(self) -> str:
        """1行のまとめ。"""
        if not self.rows:
            return "TOPIX の原本が無い。"
        return (
            f"{self.first} 〜 {self.last} / {self.rows:,} 日"
            + (f"、重複 {self.duplicates}" if self.duplicates else "")
            + (f"、5日を超える穴 {len(self.gaps)}" if self.gaps else "、穴なし")
        )


#: これを超えて空いたら穴として数える暦日数。
#:
#: 金曜から月曜で3日、連休をはさむと5日まで開く。**それを穴と呼ぶと、毎年の
#: 連休が全部並んで、本当の欠けが埋もれる。**
GAP_DAYS = 5


def census(frame: pd.DataFrame, raw_rows: int | None = None) -> Census:
    """組み立てた表を数える。**穴を数えるのは、欠けを件数から見つけるため。**"""
    report = Census(rows=len(frame))
    if frame.empty:
        return report
    dates = [value.date() for value in frame.index]
    report.first, report.last = dates[0], dates[-1]
    if raw_rows is not None:
        report.duplicates = max(raw_rows - len(frame), 0)
    for earlier, later in zip(dates, dates[1:], strict=False):
        span = (later - earlier).days
        if span > GAP_DAYS:
            report.gaps.append((later, span))
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
