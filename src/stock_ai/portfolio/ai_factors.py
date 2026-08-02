"""AI-driven scoring factors (kept separate so the AI dependency is opt-in).

A :class:`NewsSentimentFactor` turns news/IR text about a symbol into a 0..1
sub-score. Text is supplied by an injected ``text_source`` (a future news/IR
pipeline, or a stub), so the factor stays testable and the scorer can weight it
alongside the quantitative factors.
"""

from __future__ import annotations

from collections.abc import Callable

from stock_ai.ai.analysis import analyze_sentiment
from stock_ai.ai.base import AIProvider
from stock_ai.portfolio.factors import Factor
from stock_ai.portfolio.scoring import WeightedFactor, default_weighted_factors
from stock_ai.screening.base import ScreeningContext

TextSource = Callable[[str], str | None]

_SENTIMENT_SCORE: dict[str, float] = {
    "positive": 1.0,
    "neutral": 0.5,
    "negative": 0.0,
}


class NewsSentimentFactor(Factor):
    """Score a symbol from the AI sentiment of its news/IR text."""

    def __init__(self, provider: AIProvider, text_source: TextSource) -> None:
        """Store the AI provider and the per-symbol text source."""
        self.provider = provider
        self.text_source = text_source

    @property
    def name(self) -> str:
        """Return ``news_sentiment``."""
        return "news_sentiment"

    def score(self, context: ScreeningContext) -> float | None:
        """Return the sentiment-derived sub-score, or ``None`` if no text."""
        text = self.text_source(context.symbol)
        if not text:
            return None
        label = analyze_sentiment(self.provider, text)
        return _SENTIMENT_SCORE.get(label)  # None for "unknown"


def weighted_factors_with_sentiment(
    provider: AIProvider, text_source: TextSource, weight: float = 0.20
) -> list[WeightedFactor]:
    """Return the default factor set plus a weighted news-sentiment factor."""
    factors = default_weighted_factors()
    factors.append((NewsSentimentFactor(provider, text_source), weight))
    return factors
