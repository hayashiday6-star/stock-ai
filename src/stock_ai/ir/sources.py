"""Disclosure sources: where a watched company's news and filings come from.

A :class:`DisclosureSource` returns recent items for a symbol. The monitor
depends only on this protocol, so adding a feed never touches the monitoring,
summarization, or notification code.

**Coverage warning.** The only live source implemented here wraps yfinance's
news, which is thin for US large caps and essentially empty for Japanese small
caps - exactly the names a watchlist is most useful for. Proper JP coverage
needs a TDnet or EDINET adapter, which is not written: rather than ship an
unverified HTTP integration, the seam is left explicit. Implement ``fetch`` on
this protocol and pass it in; nothing else has to change.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from stock_ai.core.logging import get_logger
from stock_ai.data.types import Disclosure
from stock_ai.news.sources import NewsSource

logger = get_logger(__name__)


@runtime_checkable
class DisclosureSource(Protocol):
    """Returns recent disclosures for a symbol."""

    name: str

    def fetch(self, symbol: str, limit: int = 10) -> list[Disclosure]:
        """Return up to ``limit`` recent items for ``symbol`` (empty if none)."""
        ...


class StaticDisclosureSource:
    """Serve pre-supplied disclosures - for offline development and tests."""

    name = "static"

    def __init__(self, items_by_symbol: dict[str, list[Disclosure]]) -> None:
        """Store the per-symbol item lists."""
        self.items_by_symbol = items_by_symbol

    def fetch(self, symbol: str, limit: int = 10) -> list[Disclosure]:
        """Return the stored items for ``symbol``."""
        return self.items_by_symbol.get(symbol, [])[:limit]


class NewsDisclosureSource:
    """Adapt an existing :class:`~stock_ai.news.sources.NewsSource`.

    Lets the watchlist reuse the news plumbing already in the project. It
    inherits that source's coverage, including its gaps.
    """

    def __init__(self, source: NewsSource) -> None:
        """Wrap ``source``, taking its name."""
        self.source = source
        self.name = getattr(source, "name", "news")

    def fetch(self, symbol: str, limit: int = 10) -> list[Disclosure]:
        """Return the wrapped source's items as disclosures."""
        return [
            Disclosure(
                symbol=symbol,
                title=item.title,
                body=item.summary,
                source=self.name,
            )
            for item in self.source.fetch(symbol, limit=limit)
        ]


class CompositeDisclosureSource:
    """Query several sources and merge their items, newest first.

    Duplicates across feeds collapse on :attr:`~stock_ai.data.types.Disclosure.uid`,
    so a filing carried by both a news wire and an IR feed alerts once.
    """

    name = "composite"

    def __init__(self, *sources: DisclosureSource) -> None:
        """Store the sources to query, in priority order."""
        self.sources = sources

    def fetch(self, symbol: str, limit: int = 10) -> list[Disclosure]:
        """Return merged, de-duplicated items from every source."""
        merged: dict[str, Disclosure] = {}
        for source in self.sources:
            try:
                items = source.fetch(symbol, limit=limit)
            except Exception as exc:  # one dead feed must not blind the rest
                logger.warning("Disclosure source %s failed for %s: %s", source.name, symbol, exc)
                continue
            for item in items:
                merged.setdefault(item.uid, item)

        ordered = sorted(
            merged.values(),
            key=lambda d: d.published_on or dt.date.min,
            reverse=True,
        )
        return ordered[:limit]


def from_callable(
    name: str, fetcher: Callable[[str, int], list[dict[str, Any]]]
) -> DisclosureSource:
    """Build a source from a callable returning raw dicts.

    The escape hatch for a feed that is not worth a class: supply something
    returning ``{"title": ..., "body": ..., "published_on": ..., "url": ...}``
    and it becomes a usable source. Items without a title are dropped, since a
    disclosure with nothing to read cannot be summarized or judged.
    """

    class _Adapter:
        def __init__(self) -> None:
            self.name = name

        def fetch(self, symbol: str, limit: int = 10) -> list[Disclosure]:
            items: list[Disclosure] = []
            for raw in fetcher(symbol, limit):
                title = str(raw.get("title") or "").strip()
                if not title:
                    continue
                items.append(
                    Disclosure(
                        symbol=symbol,
                        title=title,
                        body=str(raw.get("body") or raw.get("summary") or ""),
                        published_on=_as_date(raw.get("published_on") or raw.get("date")),
                        url=raw.get("url"),
                        source=name,
                    )
                )
            return items[:limit]

    return _Adapter()


def _as_date(value: Any) -> dt.date | None:
    """Parse a date that may arrive as a date, datetime, or ISO-ish string."""
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if not value:
        return None
    try:
        return dt.date.fromisoformat(str(value).replace("/", "-")[:10])
    except ValueError:
        return None
