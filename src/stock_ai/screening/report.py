"""Turn screening results into a table and export it as CSV, JSON, or Excel."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from sqlalchemy import select

from stock_ai.core.exceptions import DataError
from stock_ai.data.types import Fundamentals
from stock_ai.database.engine import Database
from stock_ai.database.models import Security
from stock_ai.database.repository import FundamentalsRepository

SUPPORTED_FORMATS: tuple[str, ...] = ("csv", "json", "xlsx")

_REPORT_FIELDS: list[str] = [
    "symbol",
    "as_of",
    "roe",
    "per",
    "pbr",
    "dividend_yield",
    "market_cap",
    "revenue",
    "net_income",
]


def collect_fundamentals(database: Database, symbols: list[str]) -> list[Fundamentals]:
    """Load the latest fundamentals for each symbol, skipping any without data."""
    with database.session() as session:
        repo = FundamentalsRepository(session)
        return [snap for sym in symbols if (snap := repo.get_latest(sym)) is not None]


def build_report(items: list[Fundamentals], names: dict[str, str] | None = None) -> pd.DataFrame:
    """Build a tabular report (one row per symbol) from fundamentals snapshots.

    Args:
        items: The snapshots to tabulate.
        names: Optional ``symbol -> company name``. A four-digit code is not a
            company to anyone reading the output, and the one judgement a screen
            cannot make - "is this list plausible?" - needs the name to be made
            at all.
    """
    columns = list(_REPORT_FIELDS)
    if names is not None:
        columns.insert(1, "name")

    rows = []
    for item in items:
        row = {field: getattr(item, field) for field in _REPORT_FIELDS}
        if names is not None:
            row["name"] = names.get(item.symbol) or ""
        rows.append(row)
    return pd.DataFrame(rows, columns=columns)


def company_names(database: Database, symbols: list[str]) -> dict[str, str]:
    """Return ``symbol -> stored company name`` for the symbols that have one."""
    with database.session() as session:
        rows = session.execute(
            select(Security.symbol, Security.name).where(Security.symbol.in_(symbols))
        ).all()
    return {symbol: name for symbol, name in rows if name}


def write_report(frame: pd.DataFrame, path: Path, fmt: str) -> None:
    """Write ``frame`` to ``path`` in the given format.

    Args:
        frame: The report table.
        path: Destination file path.
        fmt: One of :data:`SUPPORTED_FORMATS` (``csv``, ``json``, ``xlsx``).

    Raises:
        DataError: If ``fmt`` is not supported.
    """
    fmt = fmt.lower()
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "csv":
        frame.to_csv(path, index=False)
    elif fmt == "json":
        frame.to_json(path, orient="records", date_format="iso", indent=2)
    elif fmt == "xlsx":
        frame.to_excel(path, index=False)
    else:
        raise DataError(f"Unsupported format {fmt!r}; use one of {SUPPORTED_FORMATS}.")
