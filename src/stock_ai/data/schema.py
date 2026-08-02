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
