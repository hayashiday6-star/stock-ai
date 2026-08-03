"""Analysis use-cases built on top of any :class:`~stock_ai.ai.base.AIProvider`.

These functions own the prompts; the provider only turns a prompt into text, so
switching models never changes the analysis logic.
"""

from __future__ import annotations

from stock_ai.ai.base import AIProvider
from stock_ai.data.types import Importance

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


_IMPORTANCE_SYSTEM = (
    "You rate how much a disclosure about a listed company should change an "
    "investor's view. Reply with exactly one word.\n"
    "high   — materially changes the investment case: guidance revised, "
    "results far off expectations, M&A, large financing or dilution, "
    "dividend or buyback change, regulatory or legal action, executive "
    "departure, a pipeline or product decision.\n"
    "medium — genuine company news with limited immediate impact: routine "
    "results in line, small contracts, personnel below board level.\n"
    "low    — administrative or promotional: notices of meeting dates, "
    "logo changes, conference appearances, reprints of old news."
)
_IMPORTANCE_LABELS: tuple[str, ...] = ("high", "medium", "low")


def classify_importance(provider: AIProvider, text: str) -> Importance:
    """Rate how much attention a disclosure deserves.

    Returns :attr:`~stock_ai.data.types.Importance.UNKNOWN` when the model's
    answer cannot be read. Unknown deliberately outranks ``low`` downstream: an
    item that could not be judged is worth a human glance, whereas silently
    treating it as routine is how the one filing that mattered gets missed.
    """
    prompt = f"Rate the importance (high/medium/low) of this disclosure:\n\n{text}"
    raw = provider.complete(prompt, system=_IMPORTANCE_SYSTEM, max_tokens=8).lower()
    for label in _IMPORTANCE_LABELS:
        if label in raw:
            return Importance(label)
    return Importance.UNKNOWN
