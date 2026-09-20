"""Canonical OHLCV schema and normalization.

All price providers must return a DataFrame in this shape so downstream layers
(database, technical, backtest) can rely on a single, stable contract:

- index: a timezone-naive ``DatetimeIndex`` named ``"date"``, sorted ascending
- columns: :data:`OHLCV_COLUMNS` (``open, high, low, close, adj_close, volume``)
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Sequence

import numpy as np
import pandas as pd

from stock_ai.core.exceptions import DataError

DATE = "date"
OPEN = "open"
HIGH = "high"
LOW = "low"
CLOSE = "close"
ADJ_CLOSE = "adj_close"
VOLUME = "volume"

OHLCV_COLUMNS: list[str] = [OPEN, HIGH, LOW, CLOSE, ADJ_CLOSE, VOLUME]

# Maps provider (yfinance) column labels to the canonical names above.
_YF_RENAME: dict[str, str] = {
    "Open": OPEN,
    "High": HIGH,
    "Low": LOW,
    "Close": CLOSE,
    "Adj Close": ADJ_CLOSE,
    "Volume": VOLUME,
}


def normalize_ohlcv(raw: pd.DataFrame) -> pd.DataFrame:
    """Normalize a raw yfinance price frame into the canonical OHLCV schema.

    Handles single- and multi-index columns, timezone-aware indexes, unsorted
    or duplicated dates, and coerces ``volume`` to an integer.

    Args:
        raw: The frame returned by ``yfinance.download`` for one symbol.

    Returns:
        A canonical OHLCV DataFrame (see module docstring).

    Raises:
        DataError: If required columns are missing or no valid rows remain.
    """
    df = raw.copy()

    # yfinance may return a MultiIndex (field, ticker); keep the field level.
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df.rename(columns=_YF_RENAME)

    missing = [col for col in OHLCV_COLUMNS if col not in df.columns]
    if missing:
        raise DataError(f"Price frame is missing columns: {missing}")

    df = df[OHLCV_COLUMNS].copy()

    index = pd.to_datetime(df.index)
    if getattr(index, "tz", None) is not None:
        index = index.tz_localize(None)
    df.index = index
    df.index.name = DATE

    df = df[~df.index.duplicated(keep="last")].sort_index()
    df = df.dropna(subset=[OPEN, HIGH, LOW, CLOSE])
    if df.empty:
        raise DataError("Price frame contains no valid rows after cleaning.")

    df[VOLUME] = df[VOLUME].fillna(0).astype("int64")
    return df


def split_adjusted(prices: pd.DataFrame) -> pd.DataFrame:
    """Put ``open``/``high``/``low``/``close`` on the ``adj_close`` basis.

    Every strategy, indicator and the backtest engine read :data:`CLOSE` and
    :data:`OPEN` - the prices actually traded. Across a split those jump, and
    nothing downstream can tell a split from a crash. Hitachi (6501) is a plain
    example: over 2024-06-03 .. 2024-07-31 the raw close falls 79.8% while the
    adjusted close rises 0.9%. A 200-day average spanning that day is
    meaningless, and a reported drawdown becomes an artifact - the same symbol's
    25-year hold reported a maximum drawdown of -84.71%, which is exactly the
    raw series' figure, against -83.01% on the adjusted one. The reported low
    was 2025-04-07 measured from a pre-split 2024 peak five times its own scale.

    Adjusting once, here, is what keeps signals and returns in the same space.
    The factor is ``adj_close / close``, which is the split ratio (and, where a
    provider adjusts for dividends too, the total-return ratio). It is applied
    to the whole bar because a high or a low is on the same scale as its close.

    Rows the factor cannot be computed for - a zero or missing close - are left
    as they are rather than dropped: this function normalises, it does not judge
    what is usable.

    Returns:
        A new frame; the input is not modified. Frames without
        :data:`ADJ_CLOSE` come back unchanged.
    """
    if ADJ_CLOSE not in prices.columns or CLOSE not in prices.columns:
        return prices

    close = pd.to_numeric(prices[CLOSE], errors="coerce")
    adjusted = pd.to_numeric(prices[ADJ_CLOSE], errors="coerce")
    factor = (adjusted / close).where(close > 0).fillna(1.0)

    frame = prices.copy()
    for column in (OPEN, HIGH, LOW, CLOSE):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce") * factor
    return frame


#: 飛ばした行を何件まで持って返るか。**全部持つと、銘柄数ぶん積み上がる。**
#:
#: **件数は別に数えている**ので、ここが上限に当たっても数は正しい。
#: 中身は「1件取り出して額と終値を並べる」ために在る。
MAX_SKIPPED_KEPT = 200


@dataclasses.dataclass(frozen=True)
class SkippedDividend:
    """当てなかった権利落ち1件。**件数だけでは追えないので中身を持つ。**

    **「中身を見ること」と書いて、見る道具が無かった**のを2度やらない
    （`CLAUDE.md`）。`impossible` が 53 件と出たとき、**銘柄も日付も額も
    出ていなかったので、どちら側の読み違いか決められなかった**
    （2026-09-20、ユーザーが指摘）。
    """

    symbol: str
    ex_date: dt.date
    rate: float
    """公表された1株あたりの額（円）。"""

    base: float
    """割る相手にした**調整前の前日終値**。"""

    reason: str

    @property
    def ratio(self) -> float | None:
        """利回り。**分母が使えなければ ``None``。**"""
        return self.rate / self.base if self.base > 0 else None


@dataclasses.dataclass(frozen=True)
class DividendAdjustment:
    """配当を落とした結果の内訳。**当てた件数と、当てなかった理由を返す。**

    **黙って飛ばさないために在る。** 以前は倍率が 1 以上のとき ``continue``
    していて、**「抜きすぎる」ではなく「1件も抜かない」に化けていた**——
    どちらも件数が出ないので、出力からは区別できなかった。
    """

    applied: int = 0
    """実際に当てた権利落ちの数。"""

    unpublished: int = 0
    """公表が権利落ち日より後。**使えば先読みになる。**"""

    not_in_frame: int = 0
    """その権利落ち日の足がこの銘柄に無い。"""

    no_base: int = 0
    """前日の終値が無い（先頭の足）か、0 以下。"""

    not_a_drop: int = 0
    """額が 0 以下。**無配の公表にも ``ExDate`` は入る。**"""

    impossible: int = 0
    r"""配当が前日終値以上。**額か終値のどちらかが読み違いである。**

    **「正しい分母で割ればまず起きない」と書いていたが、起きた**——分母を
    直した後の実データで 53 件（急落側）と 17 件（保有窓側）が残った
    （2026-09-20、ユーザーが指摘）。**書いた前提は、成り立つことを確かめる
    か、前提が要らない形に変える。**

    **原因はまだ分かっていない。** だから :attr:`rows` に中身を持って返り、
    `checks\権利落ちの日は合っているか.bat` が額と前日終値を並べる。
    """

    rows: tuple[SkippedDividend, ...] = ()
    """当てなかった行そのもの。**`MAX_SKIPPED_KEPT` 件で打ち切る。**

    **件数とは一致しない**（上限があるので）。`KnifeEvents.ex_date_events`
    とはそこが違う——あちらは件数と中身を突き合わせて落とすが、こちらは
    **上限つきの標本**である。
    """

    @property
    def skipped(self) -> int:
        """当てなかった数。"""
        return (
            self.unpublished + self.not_in_frame + self.no_base + self.not_a_drop + self.impossible
        )

    def __add__(self, other: DividendAdjustment) -> DividendAdjustment:
        """銘柄ごとの内訳を足し合わせる。"""
        return DividendAdjustment(
            applied=self.applied + other.applied,
            unpublished=self.unpublished + other.unpublished,
            not_in_frame=self.not_in_frame + other.not_in_frame,
            no_base=self.no_base + other.no_base,
            not_a_drop=self.not_a_drop + other.not_a_drop,
            impossible=self.impossible + other.impossible,
            rows=(self.rows + other.rows)[:MAX_SKIPPED_KEPT],
        )

    def breakdown(self) -> list[tuple[str, int]]:
        """理由ごとの数。**合計だけにしない。**

        **列ごとに独立に数える**（`CLAUDE.md`）。「当てなかった 2,264 件」
        とだけ出していたので、**どれか1つが大きくてもその中に紛れた**
        （2026-09-20、ユーザーが指摘）。
        """
        return [
            ("当てた", self.applied),
            ("公表が権利落ちより後（先読みになる）", self.unpublished),
            ("その日の足が無い", self.not_in_frame),
            ("前日の終値が無い", self.no_base),
            ("額が 0 以下", self.not_a_drop),
            ("額が前日終値以上", self.impossible),
        ]

    def warnings(self) -> list[str]:
        """気付かなくても目に入るべきこと。**早期 return しない。**"""
        found: list[str] = []
        if self.impossible:
            found.append(
                f"**配当が前日終値以上の権利落ちが {self.impossible:,} 件あった。** "
                "額か終値のどちらかが読み違いである。**原因はまだ分かっていない** "
                r"——`checks\権利落ちの日は合っているか.bat` が中身を並べる。"
            )
        if self.no_base:
            found.append(f"前日の終値が無くて落とせなかった権利落ちが {self.no_base:,} 件。")
        return found


def dividend_adjusted(
    prices: pd.DataFrame,
    announced: Sequence[tuple[dt.date, dt.date, float]] | None,
    *,
    base: Sequence[float] | np.ndarray,
    symbol: str = "",
) -> tuple[pd.DataFrame, DividendAdjustment]:
    """Take the dividend drop out of ``open``/``high``/``low``/``close``.

    **配当を落としてから測る。** そうしないと、権利落ちの値下がりが値動きに
    見える。`#16`（落ちるナイフ）は「5営業日で −20%」で事象を選ぶので、
    **配当が線の向こうに押し出した分**まで事象になっていた——実データで
    98 件がそれで、**外すために置いた規則が本物の急落を 189 件巻き込んで
    いた**（2026-09-20、ユーザーが指摘）。

    **順序の問題である。** 「権利落ちが窓に在れば外す」は代理であって、
    事前登録 §3 が言う「機械的な値下がりを外す」そのものではない。
    **先に落としてから線を当てれば、外す規則は要らない。**

    そして**ショートは配当を払う側**なので、保有する窓でも同じことが要る。
    落とさずに測ると取り高が高く出る。**急落側だけ直すと、非対称が残る。**

    ここは :func:`split_adjusted` の**後**に当てる。倍率は
    ``1 − 配当 ÷ 権利落ち日の前日終値``で、**その日より前**の足に掛かる。

    ## 割る相手は、調整前の終値である（``base``）

    **額は円建てで、分割では変わらない。** 割る相手に分割調整後の終値を使うと、
    **分割より前の権利落ちが分割比のぶん余計に落ちる**——1:10 なら利回り
    1.0% が 10.0% になる（2026-09-20 に再現）。

    見つかったのは**監査が鳴ったから**である。
    :class:`~stock_ai.backtest.ex_date_audit.HoldingDividends` が「窓の中の
    配当 +0.007%/件 に対し、抜けたのは +0.036%/件」と出した。**「消えている
    はず」と書いていたときは、この食い違いが見えていなかった。**

    **倍率そのものは尺度によらない**ので、調整前の終値で出した比を調整後の
    足に掛けるのが正しい。``base`` を**必須**にしてあるのは、渡し忘れが
    黙って通る形を残さないためである。

    Args:
        prices: :func:`split_adjusted` を通した足。**日付の昇順**であること。
        announced: ``(公表日, 権利落ち日, 額)`` の並び
            （:func:`~stock_ai.data.jquants_dividend.ex_dividends_known_by`）。
            **その足より後に公表されたものは使わない**——使えば先読みになる。
        base: **調整前の終値。** ``prices`` と同じ長さ・同じ並びであること。
            分割調整を掛けていない足なら ``prices[CLOSE]`` そのものでよい。
        symbol: 飛ばした行に付ける名札。**数えるだけなら要らないが、
            中身を見るときに銘柄が分からないと追えない。**

    Returns:
        ``(新しい frame, 内訳)``。入力は変えない。配当が1件も当たらなければ
        frame はそのまま返る。**内訳は必ず返す**——数えずに済ませない。

    Raises:
        ValueError: ``base`` の長さが ``prices`` と違う。
    """
    if len(base) != len(prices):
        raise ValueError(
            f"base の長さ {len(base)} が prices の {len(prices)} と違う。"
            " **調整前の終値を、同じ並びで渡すこと。**"
        )
    if not announced or CLOSE not in prices.columns or prices.empty:
        return prices, DividendAdjustment()

    when_of = [stamp.date() for stamp in prices.index]
    index_of = {day: position for position, day in enumerate(when_of)}
    unadjusted = np.asarray(base, dtype=float)

    applied = unpublished = not_in_frame = no_base = not_a_drop = impossible = 0
    skipped: list[SkippedDividend] = []

    def _skip(ex_date: dt.date, rate: float, before: float, reason: str) -> None:
        """飛ばした行を残す。**上限まで。** 件数は呼ぶ側が別に数える。"""
        if len(skipped) < MAX_SKIPPED_KEPT:
            skipped.append(
                SkippedDividend(
                    symbol=symbol, ex_date=ex_date, rate=rate, base=before, reason=reason
                )
            )

    # **後ろから畳む。** 権利落ち日より前の足に、その日の倍率を掛けていく。
    factor = np.ones(len(prices), dtype=float)
    for published, ex_date, rate in announced:
        position = index_of.get(ex_date)
        if position is None:
            not_in_frame += 1
            _skip(ex_date, rate, 0.0, "その日の足が無い")
            continue
        # **その日までに公表されたものだけ。** 公表が権利落ち日より後なら、
        # 落ちる時点では分かっていない。
        if published > ex_date:
            unpublished += 1
            _skip(ex_date, rate, 0.0, "公表が権利落ちより後")
            continue
        if position == 0:
            no_base += 1
            _skip(ex_date, rate, 0.0, "先頭の足（前日が無い）")
            continue
        # **調整前の終値で割る。** 調整後で割ると、分割より前の権利落ちが
        # 分割比のぶん余計に落ちる。
        before = unadjusted[position - 1]
        if before <= 0:
            no_base += 1
            _skip(ex_date, rate, before, "前日の終値が 0 以下")
            continue
        if rate <= 0:
            not_a_drop += 1
            continue
        ratio = rate / before
        if ratio >= 1.0:
            impossible += 1
            _skip(ex_date, rate, before, "額が前日終値以上")
            continue
        factor[:position] *= 1.0 - ratio
        applied += 1

    counted = DividendAdjustment(
        applied=applied,
        unpublished=unpublished,
        not_in_frame=not_in_frame,
        no_base=no_base,
        not_a_drop=not_a_drop,
        impossible=impossible,
        rows=tuple(skipped),
    )

    if np.all(factor == 1.0):
        return prices, counted

    frame = prices.copy()
    for column in (OPEN, HIGH, LOW, CLOSE):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce") * factor
    return frame, counted
