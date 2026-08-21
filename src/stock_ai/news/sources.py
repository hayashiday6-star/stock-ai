"""News sources and the ``text_source`` adapter used by AI scoring.

A :class:`NewsSource` returns recent headlines/snippets for a symbol. The
:func:`make_text_source` adapter joins them into a single string, which is
exactly the ``text_source`` callable that
:class:`~stock_ai.portfolio.ai_factors.NewsSentimentFactor` expects - so the
news pipeline plugs into scoring without either side knowing about the other.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from stock_ai.core.logging import get_logger
from stock_ai.data.markets import to_yahoo_symbol

logger = get_logger(__name__)

TextSource = Callable[[str], str | None]


@dataclass(frozen=True)
class NewsItem:
    """A single news headline with an optional summary."""

    title: str
    summary: str = ""

    def as_text(self) -> str:
        """Return the item as one text block."""
        return f"{self.title}. {self.summary}".strip()


@runtime_checkable
class NewsSource(Protocol):
    """Returns recent news items for a symbol."""

    name: str

    def fetch(self, symbol: str, limit: int = 5) -> list[NewsItem]:
        """Return up to ``limit`` recent items for ``symbol`` (empty if none)."""
        ...


class StaticNewsSource:
    """Serve pre-supplied items - for offline development and tests."""

    name = "static"

    def __init__(self, items_by_symbol: dict[str, list[NewsItem]]) -> None:
        """Store the per-symbol item lists."""
        self.items_by_symbol = items_by_symbol

    def fetch(self, symbol: str, limit: int = 5) -> list[NewsItem]:
        """Return the stored items for ``symbol``."""
        return self.items_by_symbol.get(symbol, [])[:limit]


class YFinanceNewsSource:
    """Fetch recent headlines via yfinance (no API key required)."""

    name = "yfinance"

    def __init__(self, fetcher: Callable[[str], list[dict[str, Any]]] | None = None) -> None:
        """Create the source.

        Args:
            fetcher: Callable returning raw yfinance news dicts; injected in tests.
        """
        self._fetch = fetcher or self._default_fetch

    @staticmethod
    def _default_fetch(symbol: str) -> list[dict[str, Any]]:
        """Fetch raw news entries from yfinance (imported lazily)."""
        import yfinance as yf

        return yf.Ticker(symbol).news or []

    def fetch(self, symbol: str, limit: int = 5) -> list[NewsItem]:
        """Return up to ``limit`` recent headlines; failures yield an empty list.

        The symbol is put into Yahoo's own form first. A bare Japanese code is
        not a Tokyo listing there, and the wrong company's news arrives looking
        entirely correct - right ticker in the header, coherent summary, no
        error anywhere.
        """
        queried = to_yahoo_symbol(symbol)
        if queried != symbol:
            logger.debug("News for %s queried as %s", symbol, queried)
        try:
            raw = self._fetch(queried)
        except Exception as exc:  # news is best-effort - never break scoring
            logger.warning("News fetch failed for %s: %s", queried, exc)
            return []

        items: list[NewsItem] = []
        for entry in raw[:limit]:
            # yfinance nests fields under "content" in newer versions.
            content = entry.get("content", entry)
            title = content.get("title") or entry.get("title") or ""
            summary = content.get("summary") or entry.get("summary") or ""
            if title:
                items.append(NewsItem(title=title, summary=summary))
        return items


def make_text_source(source: NewsSource, limit: int = 5, max_chars: int = 4000) -> TextSource:
    """Adapt a :class:`NewsSource` into a ``text_source`` callable.

    Args:
        source: The news source to read from.
        limit: Maximum number of items to include.
        max_chars: Truncate the combined text to this length.

    Returns:
        A callable mapping a symbol to combined news text (``None`` if there is
        nothing to analyze).
    """

    def text_source(symbol: str) -> str | None:
        items = source.fetch(symbol, limit=limit)
        if not items:
            return None
        return "\n".join(item.as_text() for item in items)[:max_chars]

    return text_source
