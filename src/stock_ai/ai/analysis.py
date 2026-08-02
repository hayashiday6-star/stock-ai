"""Analysis use-cases built on top of any :class:`~stock_ai.ai.base.AIProvider`.

These functions own the prompts; the provider only turns a prompt into text, so
switching models never changes the analysis logic.
"""

from __future__ import annotations

from stock_ai.ai.base import AIProvider

_SUMMARY_SYSTEM = (
    "You are a financial analyst. Summarize factually and concisely, "
    "without speculation or investment advice."
)
_SENTIMENT_SYSTEM = (
    "You classify the sentiment of financial text as exactly one word: "
    "positive, neutral, or negative."
)

Sentiment = str  # one of "positive" | "neutral" | "negative" | "unknown"
_SENTIMENT_LABELS: tuple[str, ...] = ("positive", "negative", "neutral")


def summarize(provider: AIProvider, text: str, *, max_words: int = 120) -> str:
    """Summarize ``text`` (an IR document, news article, ...) in <= ``max_words``."""
    prompt = f"Summarize the following in at most {max_words} words:\n\n{text}"
    return provider.complete(prompt, system=_SUMMARY_SYSTEM).strip()


def analyze_sentiment(provider: AIProvider, text: str) -> Sentiment:
    """Return the sentiment label for ``text`` (``unknown`` if unclassifiable)."""
    prompt = f"Classify the sentiment (positive/neutral/negative) of:\n\n{text}"
    raw = provider.complete(prompt, system=_SENTIMENT_SYSTEM, max_tokens=8).lower()
    for label in _SENTIMENT_LABELS:
        if label in raw:
            return label
    return "unknown"
