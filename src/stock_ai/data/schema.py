"""Canonical OHLCV schema and normalization.

All price providers must return a DataFrame in this shape so downstream layers
(database, technical, backtest) can rely on a single, stable contract:

- index: a timezone-naive ``DatetimeIndex`` named ``"date"``, sorted ascending
- columns: :data:`OHLCV_COLUMNS` (``open, high, low, close, adj_close, volume``)
"""

from __future__ import annotations

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
