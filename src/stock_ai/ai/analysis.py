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
#: The one-word ceilings started at 8 - what a one-word answer costs and
#: nothing more. Live, that returned a 200 with an empty content list: the
#: budget is gone before the word arrives, and there is no partial answer to
#: salvage. 64 fixed ``sentiment`` (49 output tokens observed) and the very
#: next run failed the *importance* rating at 64 on a real EDINET filing.
#:
#: Two wrong guesses is enough. The measured answers are around 30-50 tokens,
#: so a ceiling of 512 costs nothing in practice - it is a bound, not a
#: reservation, and the bill follows the tokens actually produced. What it does
#: cost is a wider printed estimate, and that is the right trade: an estimate
#: that is pessimistic and holds beats one that is tight and lets a rating fail
#: silently. ``spent:`` reports the real figure afterwards, so the width of the
#: range is checked against reality on every run rather than believed.
#:
#: Why one word needs hundreds of tokens of headroom is not something this
#: project can see from the outside; the response arrives empty with
#: ``stop_reason="max_tokens"`` and no partial text to inspect. The ceiling is
#: therefore set from measurement plus a wide margin, not from a model of what
#: ought to be enough - that model has now been wrong twice.
#:
#: This matters most where it was found second. The importance rating is what
#: the nightly monitor runs on, and there the failure does not look like a
#: failure: the disclosure comes back unjudged and the run reports no alerts,
#: which reads exactly like a quiet day.
#: Put in the model's mouth so the reply continues a sentence that can only
#: end in a label. No stop sequence accompanies it: a newline is the natural
#: place to cut a one-word answer, and the API rejects any stop sequence made
#: only of whitespace - a rejection that fails the whole request, so the cure
#: was worse than the verbosity it treated.
#:
#: Asking in prose for "exactly one word" was not binding: the
#: ratings measured on live data ran ~110 output tokens each, and two of
#: nineteen produced no text at all - one after exhausting a 512-token ceiling.
#: That ceiling had already been raised from 8 to 64 to 512 chasing the same
#: symptom, which is the sign of a cause elsewhere: the request never
#: constrained the answer, so no ceiling was ever going to be high enough.
ONE_WORD_PREFILL = "The answer is:"
IMPORTANCE_MAX_TOKENS = 512
SENTIMENT_MAX_TOKENS = 512
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


def importance_prompt(text: str, *, about: str | None = None) -> str:
    """Build the importance-rating prompt (see :func:`summary_prompt`).

    ``about`` names the company the item is supposed to concern. Without it the
    model is asked whether the news matters at all, which is a different
    question: a feed indexed by ticker returns peers, customers and sector
    round-ups, and those are often genuinely important news - about somebody
    else. Rated on their own merits they come back high, correctly, and land
    in the watched company's alerts.
    """
    if about:
        return f"Rate the importance (high/medium/low) of this item **to {about}**:\n\n{text}"
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
        prompt,
        system=SENTIMENT_SYSTEM,
        max_tokens=SENTIMENT_MAX_TOKENS,
        prefill=ONE_WORD_PREFILL,
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
    "logo changes, conference appearances, reprints of old news.\n"
    "If a company is named, rate the item's bearing on that company only. "
    "Answer low when the item is about somebody else - a peer, a customer, a "
    "supplier, the wider sector, or a list the company merely appears in - "
    "however important that news is in its own right. Feeds indexed by ticker "
    "return such items constantly, and they are not news about the company."
)
_IMPORTANCE_LABELS: tuple[str, ...] = ("high", "medium", "low")


def classify_importance(provider: AIProvider, text: str, *, about: str | None = None) -> Importance:
    """Rate how much attention a disclosure deserves.

    Returns :attr:`~stock_ai.data.types.Importance.UNKNOWN` when the model's
    answer cannot be read. Unknown deliberately outranks ``low`` downstream: an
    item that could not be judged is worth a human glance, whereas silently
    treating it as routine is how the one filing that mattered gets missed.
    """
    raw = provider.complete(
        importance_prompt(text, about=about),
        system=IMPORTANCE_SYSTEM,
        max_tokens=IMPORTANCE_MAX_TOKENS,
        prefill=ONE_WORD_PREFILL,
    ).lower()
    for label in _IMPORTANCE_LABELS:
        if label in raw:
            return Importance(label)
    return Importance.UNKNOWN
