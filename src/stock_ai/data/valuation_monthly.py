"""月末の PBR だけを抜いて、1つのファイルに落とす。

## なぜ中間ファイルなのか

`pbr` は原本（`data/jquants_bulk`）にしか無い。1,588万銘柄日を毎回読むと数分
かかり、**回すたびに待つことになる。**

データベースに表を足す手もあるが、そうすると**「取り込みが本当に入ったか」を
確かめる作業がもう一度要る**（項目4でやったやつである）。判定に使う時間を、
そこに取られたくない。

**必要なのは月末の1点だけである。** 200ヶ月 × 3,500銘柄で 70万行、数 MB に
収まる。**原本から作り直せる**ので、食い違ったら捨てて作り直せばよい。

## 月末とは、その銘柄のその月の最後の観測である

**暦の月末ではない。** 月の途中で上場廃止になった銘柄は、その日が最後の観測に
なる。暦の月末を探すと**その銘柄が丸ごと消える**——生存バイアスを入れない、
というこのプロジェクトの前提に反する。

## 空の `pbr` は落とす

**0 で埋めない。** PBR が空の銘柄と PBR が 0 の銘柄は別のもので、0 を入れると
後者として並ぶ。**落とした件数は返す**ので、どれだけ落ちたかは見える。
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Callable
from pathlib import Path

import pandas as pd

from stock_ai.config.constants import DATA_DIR
from stock_ai.core.logging import get_logger
from stock_ai.data.schema import DATE

logger = get_logger(__name__)

#: 落とし先。**生成物である。** 手で直さない。
DEFAULT_PATH: Path = DATA_DIR / "valuation_monthly.csv.gz"

#: 残す列。**必要なものだけ。** 増やすとファイルが太る。
COLUMNS = ("date", "symbol", "pbr", "per", "bps", "market_cap")


@dataclasses.dataclass
class MonthlyReport:
    """作ったときに何が起きたか。"""

    rows: int
    symbols: int
    months: int
    first: dt.date | None
    last: dt.date | None
    dropped_no_pbr: int
    """`pbr` が空で落とした銘柄日。**0 で埋めていない。**"""

    def summary(self) -> str:
        """1行のまとめ。"""
        if not self.rows:
            return "月末の行が1つも作れなかった。**原本が読めていない。**"
        return (
            f"{self.rows:,} 行（{self.symbols:,} 銘柄 × {self.months} ヶ月）、"
            f"{self.first} 〜 {self.last}。"
            f"`pbr` が空で落としたのは {self.dropped_no_pbr:,} 銘柄日。"
        )


def month_ends(frame: pd.DataFrame) -> pd.DataFrame:
    """銘柄ごと・月ごとに、**最後の観測だけ**を残す。

    **暦の月末を探さない。** 月の途中で上場廃止になった銘柄は、その日が最後の
    観測である。暦の月末で引くと、その銘柄がその月から丸ごと消える。

    Args:
        frame: :func:`~stock_ai.data.jquants_valuation.parse_valuation` の形。

    Returns:
        同じ列のまま、1銘柄1ヶ月1行にしたもの。
    """
    if frame.empty:
        return frame
    ordered = frame.sort_values([DATE])
    month = pd.to_datetime(ordered[DATE]).dt.to_period("M")
    return ordered[~ordered.assign(_m=month).duplicated(["symbol", "_m"], keep="last")]


def build(
    archive: Path,
    into: Path = DEFAULT_PATH,
    progress: Callable[[int, int, str], None] | None = None,
) -> MonthlyReport:
    """原本から月末の行だけを抜いて書く。**取りには行かない。**

    Args:
        archive: 原本の置き場所。
        into: 落とし先。
        progress: 1本読むごとに呼ばれる。

    Returns:
        :class:`MonthlyReport`。
    """
    from stock_ai.data.jquants_valuation import from_archive

    frame = from_archive(archive, progress=progress)
    if frame.empty:
        return MonthlyReport(0, 0, 0, None, None, 0)

    before = len(frame)
    kept = frame[frame["pbr"].notna()]
    dropped = before - len(kept)

    monthly = month_ends(kept)
    if monthly.empty:
        return MonthlyReport(0, 0, 0, None, None, dropped)

    out = monthly[list(COLUMNS)].sort_values(["date", "symbol"]).reset_index(drop=True)
    into.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(into, index=False, compression="gzip")

    months = pd.to_datetime(out[DATE]).dt.to_period("M").nunique()
    return MonthlyReport(
        rows=len(out),
        symbols=int(out["symbol"].nunique()),
        months=int(months),
        first=min(out[DATE]),
        last=max(out[DATE]),
        dropped_no_pbr=dropped,
    )


def read(path: Path = DEFAULT_PATH) -> pd.DataFrame:
    """作ったファイルを読む。無ければ空。

    **日付は日付として読む。** 文字列のままだと、月で切るたびに変換が要り、
    どこか1箇所で忘れる。
    """
    if not path.is_file():
        return pd.DataFrame(columns=list(COLUMNS))
    frame = pd.read_csv(path, compression="gzip")
    frame[DATE] = pd.to_datetime(frame[DATE]).dt.date
    frame["symbol"] = frame["symbol"].astype(str).str.zfill(4)
    return frame
