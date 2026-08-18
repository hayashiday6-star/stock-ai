"""Analysis use-cases built on top of any :class:`~stock_ai.ai.base.AIProvider`.

These functions own the prompts; the provider only turns a prompt into text, so
switching models never changes the analysis logic.
"""

from __future__ import annotations

from stock_ai.ai.base import AIProvider
from stock_ai.data.types import Importance

#: Every prompt here carries this. The monitor watches Japanese filings and the
#: reader is Japanese, but nothing in the prompt said so: the same IR excerpt
#: came back in Japanese one run and in English the next. A summary whose
#: language is a coin flip cannot go in a notification, and asking the model to
#: "use Japanese" would break the US names in the same watchlist - so the rule
#: is to follow the source, which is right for both.
_SAME_LANGUAGE = " Reply in the same language as the text you are given: Japanese in, Japanese out."

SUMMARY_SYSTEM = (
    "You are a financial analyst. Summarize factually and concisely, "
    "without speculation or investment advice." + _SAME_LANGUAGE
)
SENTIMENT_SYSTEM = (
    "You classify the sentiment of financial text as exactly one word: "
    "positive, neutral, or negative."
)

#: Output ceilings, per call. These are what make a pre-run cost estimate
#: bounded rather than open-ended: the model can never bill more output than
#: this, whatever it decides to say.
#:
#: The one-word ceilings were 8, which is what a one-word answer costs and
#: nothing more. Run against the live API, that returned a 200 with an empty
#: content list: the model spends its budget before the word arrives, and there
#: is no partial answer to salvage. Raising it to 64 keeps the estimate bounded
#: - 64 output tokens is $0.0016 at opus rates, against the $0.0002 the tight
#: ceiling "saved" - and buys enough headroom that a model which prefaces its
#: answer still gets to finish it.
#:
#: This mattered well beyond ``sentiment``, where it was found. The same
#: ceiling is on the importance rating that the nightly monitor depends on, and
#: there the failure would not have looked like a failure: every disclosure
#: would have come back unjudged and the run would have reported no alerts.
IMPORTANCE_MAX_TOKENS = 64
SENTIMENT_MAX_TOKENS = 64
SUMMARY_MAX_TOKENS = 1024

#: Summary length the watchlist monitor asks for. Kept here beside the prompt
#: it feeds so a cost estimate and the real run cannot drift apart.
DEFAULT_SUMMARY_WORDS = 80

# Deliberately *not* on the sentiment and importance prompts: both are parsed
# by looking for an English label in the reply, so a Japanese answer would be
# read as unclassifiable. Those two stay one English word regardless of input.

Sentiment = str  # one of "positive" | "neutral" | "negative" | "unknown"
_SENTIMENT_LABELS: tuple[str, ...] = ("positive", "negative", "neutral")


def summary_prompt(text: str, *, max_words: int = DEFAULT_SUMMARY_WORDS) -> str:
    """Build the summarization prompt.

    Separate from :func:`summarize` so the cost estimate can count the tokens
    of the *exact* prompt the run will send. An estimate built from a
    reconstructed prompt would price a request that never happens.
    """
    return f"Summarize the following in at most {max_words} words:\n\n{text}"


def importance_prompt(text: str) -> str:
    """Build the importance-rating prompt (see :func:`summary_prompt`)."""
    return f"Rate the importance (high/medium/low) of this disclosure:\n\n{text}"


def summarize(provider: AIProvider, text: str, *, max_words: int = 120) -> str:
    """Summarize ``text`` (an IR document, news article, ...) in <= ``max_words``."""
    return provider.complete(
        summary_prompt(text, max_words=max_words),
        system=SUMMARY_SYSTEM,
        max_tokens=SUMMARY_MAX_TOKENS,
    ).strip()


def analyze_sentiment(provider: AIProvider, text: str) -> Sentiment:
    """Return the sentiment label for ``text`` (``unknown`` if unclassifiable)."""
    prompt = f"Classify the sentiment (positive/neutral/negative) of:\n\n{text}"
    raw = provider.complete(
        prompt, system=SENTIMENT_SYSTEM, max_tokens=SENTIMENT_MAX_TOKENS
    ).lower()
    for label in _SENTIMENT_LABELS:
        if label in raw:
            return label
    return "unknown"


IMPORTANCE_SYSTEM = (
    "You rate how much a disclosure about a listed company should change an "
    "investor's view. Reply with exactly one word.\n"
    "high   - materially changes the investment case: guidance revised, "
    "results far off expectations, M&A, large financing or dilution, "
    "dividend or buyback change, regulatory or legal action, executive "
    "departure, a pipeline or product decision.\n"
    "medium - genuine company news with limited immediate impact: routine "
    "results in line, small contracts, personnel below board level.\n"
    "low    - administrative or promotional: notices of meeting dates, "
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
    raw = provider.complete(
        importance_prompt(text), system=IMPORTANCE_SYSTEM, max_tokens=IMPORTANCE_MAX_TOKENS
    ).lower()
    for label in _IMPORTANCE_LABELS:
        if label in raw:
            return Importance(label)
    return Importance.UNKNOWN
