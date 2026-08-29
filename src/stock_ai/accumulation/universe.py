"""The tradable US universe, assembled from the exchanges' own listing files.

There is no listing endpoint on the price provider this project uses, so the
universe has to come from somewhere else. NASDAQ Trader publishes the two
authoritative files - one for NASDAQ, one for everything else - and they carry
the flags needed to throw out what a stock screen must not contain.

The exclusions matter more than they look. A screen for "unusual volume near
the 52-week low" that leaves ETFs in returns leveraged funds every time, since
a 3x fund's daily range and volume swing by construction rather than because
anyone is accumulating anything. Warrants and units do the same thing for a
different reason: they are thin, so one ordinary block is a 5x volume day.
Neither is a finding, and both crowd out the real ones.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from stock_ai.core.exceptions import DataError

NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"

#: Exchange codes in ``otherlisted.txt`` this screen accepts. ``P`` (NYSE Arca)
#: and ``Z`` (Cboe BZX) are listing venues for funds almost exclusively, so
#: keeping them would reintroduce the ETFs the ETF flag just removed.
_WANTED_EXCHANGES = {"N": "NYSE", "A": "AMEX"}

#: Security-name patterns that mark something this screen should not hold.
#: Matched against the upper-cased name, so every pattern here is upper case.
_EXCLUDED_NAME_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bWARRANT", "warrant"),
    (r"\bRIGHTS?\b", "right"),
    (r"\bUNITS?\b", "unit"),
    (r"\bPREFERRED\b", "preferred"),
    (r"DEPOSITARY SHARE", "depositary share"),
    (r"AMERICAN DEPOSITARY", "ADR"),
    (r"\bAD[RS]\b", "ADR"),
    (r"\bACQUISITION CORP", "SPAC"),
    (r"\bACQUISITION COMPANY", "SPAC"),
    (r"\bNOTES? DUE\b", "note"),
    (r"\bTRUST PREFERRED", "trust preferred"),
    (r"%\s", "fixed-rate security"),
)

#: Characters that mark a symbol as a preferred series or a warrant line
#: rather than a common share. A dot is deliberately absent: NYSE writes share
#: *classes* with one (BRK.B, BF.B), and those are ordinary common stock.
_NON_COMMON_SYMBOL_MARKS = ("$", "^")


@dataclass(frozen=True)
class Listing:
    """One listed common stock."""

    symbol: str
    name: str
    exchange: str


def excluded_reason(symbol: str, name: str) -> str | None:
    """Why this listing does not belong in the screen, or ``None`` to keep it.

    Returns the reason rather than a bool so a run can report *what* it threw
    away. A universe that silently halves is indistinguishable from a broken
    parser, and the difference is one line of output.
    """
    upper = name.upper()
    for pattern, reason in _EXCLUDED_NAME_PATTERNS:
        if re.search(pattern, upper):
            return reason
    if any(mark in symbol for mark in _NON_COMMON_SYMBOL_MARKS):
        return "preferred or warrant symbol"
    # There is deliberately no five-letter-suffix rule here. "a NASDAQ symbol
    # ending in W/R/U/P is a warrant/right/unit/preferred" is the usual
    # shorthand and it is wrong often enough to matter: CSGP is CoStar Group,
    # not a preferred. The security *names* in these files spell the type out
    # ("- Warrant", "- Unit", "- Rights"), and the patterns above read that
    # instead of guessing from the ticker.
    return None


def to_yahoo_symbol(symbol: str) -> str:
    """Render a listing symbol the way the price provider spells it.

    The exchanges write a share class with a dot (``BRK.B``); Yahoo writes it
    with a hyphen (``BRK-B``). Asking for the dotted form returns nothing at
    all - not an error - so the whole class-B side of the universe would come
    back empty and look like a market with no data.
    """
    return symbol.replace(".", "-")


def _rows(text: str) -> list[dict[str, str]]:
    """Parse a pipe-delimited NASDAQ Trader file into records.

    The last line of these files is a "File Creation Time" footer rather than a
    record, and it parses as a row with the wrong field count. Dropping rows
    that do not match the header is what keeps it out.
    """
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise DataError("Listing file is empty.")
    header = lines[0].split("|")
    out: list[dict[str, str]] = []
    for line in lines[1:]:
        fields = line.split("|")
        if len(fields) != len(header):
            continue  # the footer, or a truncated line
        record = dict(zip(header, fields, strict=True))
        if record.get("Symbol", record.get("ACT Symbol", "")).startswith("File Creation Time"):
            continue
        out.append(record)
    return out


def parse_nasdaq_listed(text: str) -> list[Listing]:
    """NASDAQ-listed common stocks from ``nasdaqlisted.txt``."""
    listings: list[Listing] = []
    for row in _rows(text):
        if row.get("Test Issue") == "Y" or row.get("ETF") == "Y":
            continue
        symbol, name = row.get("Symbol", "").strip(), row.get("Security Name", "").strip()
        if not symbol or excluded_reason(symbol, name):
            continue
        listings.append(Listing(symbol, name, "NASDAQ"))
    return listings


def parse_other_listed(text: str) -> list[Listing]:
    """NYSE and NYSE American common stocks from ``otherlisted.txt``."""
    listings: list[Listing] = []
    for row in _rows(text):
        if row.get("Test Issue") == "Y" or row.get("ETF") == "Y":
            continue
        exchange = _WANTED_EXCHANGES.get(row.get("Exchange", ""))
        if exchange is None:
            continue
        symbol = (row.get("ACT Symbol") or "").strip()
        name = (row.get("Security Name") or "").strip()
        if not symbol or excluded_reason(symbol, name):
            continue
        listings.append(Listing(symbol, name, exchange))
    return listings


def _http_get(url: str) -> str:
    """Fetch a listing file as text."""
    response = httpx.get(url, timeout=60.0, follow_redirects=True)
    response.raise_for_status()
    return response.text


def load_universe(fetch: Callable[[str], str] = _http_get) -> list[Listing]:
    """Every NYSE / NASDAQ / AMEX common stock, funds and derivatives removed.

    Args:
        fetch: Reads a URL and returns its body. Injected so the parsing can be
            tested without the network, and so a caller can supply a cache.

    Raises:
        DataError: If neither file could be read or both parsed to nothing.
    """
    listings: list[Listing] = []
    errors: list[str] = []
    for url, parse in (
        (NASDAQ_LISTED_URL, parse_nasdaq_listed),
        (OTHER_LISTED_URL, parse_other_listed),
    ):
        try:
            listings.extend(parse(fetch(url)))
        except Exception as exc:  # one file failing should not lose the other
            errors.append(f"{url}: {exc}")
    if not listings:
        raise DataError("Could not build the universe. " + " ".join(errors))
    # A symbol can appear on both files during a venue transfer; the first
    # listing wins so the count matches the number of distinct tickers priced.
    seen: dict[str, Listing] = {}
    for listing in listings:
        seen.setdefault(listing.symbol, listing)
    return sorted(seen.values(), key=lambda item: item.symbol)
