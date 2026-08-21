"""Which market a ticker belongs to, decided from the ticker itself.

A price source serves one market. yfinance cannot price ``7203`` and J-Quants
cannot price ``AAPL``, so any command that takes a free-form symbol list has to
split it before choosing a provider - otherwise a single ``--source`` flag is
silently applied to symbols it cannot serve.

That has already happened once in this project: ``bulk-fetch --segment stored``
sent US tickers to J-Quants because it did not distinguish markets. The failure
mode is not an obvious one - the request goes out, the provider refuses, and the
symbol is recorded as "failed" among genuine failures.

The rule is deliberately narrow. A Japanese listing is four digits, optionally
suffixed ``.T`` or ``.JP``; everything else is treated as US. Anything cleverer
would start guessing, and a wrong guess here routes a symbol to a provider that
cannot answer for it.
"""

from __future__ import annotations

#: Suffixes that mark an otherwise-bare code as a Tokyo listing.
JP_SUFFIXES = (".T", ".JP")

JAPAN = "JP"
UNITED_STATES = "US"


def market_for_symbol(symbol: str) -> str:
    """Return :data:`JAPAN` for a Japanese ticker, :data:`UNITED_STATES` otherwise.

    Examples:
        >>> market_for_symbol("7203")
        'JP'
        >>> market_for_symbol("7203.T")
        'JP'
        >>> market_for_symbol("AAPL")
        'US'
    """
    head = symbol.strip().upper().split(".")[0]
    return JAPAN if len(head) == 4 and head.isdigit() else UNITED_STATES


def to_yahoo_symbol(symbol: str) -> str:
    """Return the form Yahoo Finance knows ``symbol`` by.

    A bare four-digit code is not a Tokyo listing to Yahoo - it is whatever
    exchange answers first. Saudi's Tadawul also numbers its listings in four
    digits, and ``3003`` there is City Cement: watching ヒューリック by its bare
    code delivered Middle East small-cap articles, correctly summarised, under
    a Japanese company's name. Nothing in that output looks like an error.

    The suffix is added only where the code is unambiguous, and an already
    qualified symbol is returned untouched.
    """
    text = symbol.strip().upper()
    if market_for_symbol(text) != JAPAN:
        return text
    head, _, suffix = text.partition(".")
    if not suffix:
        return f"{head}.T"
    return f"{head}.T" if f".{suffix}" in JP_SUFFIXES else text


def split_by_market(symbols: list[str]) -> dict[str, list[str]]:
    """Group ``symbols`` by market, preserving their given order.

    Returns:
        A dict keyed by market code. Markets with no symbols are absent, so a
        single-market list yields a single entry and callers can simply iterate.
    """
    grouped: dict[str, list[str]] = {}
    for symbol in symbols:
        grouped.setdefault(market_for_symbol(symbol), []).append(symbol)
    return grouped
