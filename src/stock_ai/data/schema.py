"""Canonical OHLCV schema and normalization.

All price providers must return a DataFrame in this shape so downstream layers
(database, technical, backtest) can rely on a single, stable contract:

- index: a timezone-naive ``DatetimeIndex`` named ``"date"``, sorted ascending
- columns: :data:`OHLCV_COLUMNS` (``open, high, low, close, adj_close, volume``)
"""

from __future__ import annotations

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


def dividend_adjusted(
    prices: pd.DataFrame,
    announced: Sequence[tuple[dt.date, dt.date, float]] | None,
) -> pd.DataFrame:
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

    Args:
        prices: :func:`split_adjusted` を通した足。**日付の昇順**であること。
        announced: ``(公表日, 権利落ち日, 額)`` の並び
            （:func:`~stock_ai.data.jquants_dividend.ex_dividends_known_by`）。
            **その足より後に公表されたものは使わない**——使えば先読みになる。

    Returns:
        新しい frame。入力は変えない。配当が1件も当たらなければそのまま。
    """
    if not announced or CLOSE not in prices.columns or prices.empty:
        return prices

    when_of = [stamp.date() for stamp in prices.index]
    index_of = {day: position for position, day in enumerate(when_of)}
    close = pd.to_numeric(prices[CLOSE], errors="coerce").to_numpy(dtype=float)

    # **後ろから畳む。** 権利落ち日より前の足に、その日の倍率を掛けていく。
    factor = np.ones(len(close), dtype=float)
    for published, ex_date, rate in announced:
        position = index_of.get(ex_date)
        # **その日までに公表されたものだけ。** 公表が権利落ち日より後なら、
        # 落ちる時点では分かっていない。
        if position is None or position == 0 or published > ex_date:
            continue
        before = close[position - 1]
        if before <= 0 or rate <= 0:
            continue
        ratio = rate / before
        if ratio <= 0 or ratio >= 1.0:
            continue
        factor[:position] *= 1.0 - ratio

    if np.all(factor == 1.0):
        return prices

    frame = prices.copy()
    for column in (OPEN, HIGH, LOW, CLOSE):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce") * factor
    return frame
