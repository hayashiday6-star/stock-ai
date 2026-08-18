"""Price a batch of disclosure judgements before any of them is paid for.

Shared by the CLI's ``ai-cost`` and the dashboard, deliberately. The estimate's
only claim is that it counts the tokens of *the prompts the run will actually
send*; two copies of that logic would drift, and a second figure that disagrees
with the first is worse than no figure at all - the reader has no way to tell
which one to believe.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol

from stock_ai.ai.analysis import (
    DEFAULT_SUMMARY_WORDS,
    IMPORTANCE_MAX_TOKENS,
    IMPORTANCE_SYSTEM,
    SUMMARY_MAX_TOKENS,
    SUMMARY_SYSTEM,
    importance_prompt,
    summary_prompt,
)
from stock_ai.ai.pricing import RunEstimate


class TokenCounter(Protocol):
    """A provider that can count a request's input tokens without sending it."""

    @property
    def model(self) -> str:
        """The model ID the count and the run both use."""
        ...

    def count_tokens(self, prompt: str, *, system: str | None = None) -> int:
        """Return the exact input-token count for a request."""
        ...


def estimate_disclosure_run(
    provider: TokenCounter,
    texts: Sequence[str],
    *,
    on_progress: Callable[[int, int], None] | None = None,
) -> RunEstimate:
    """Count the exact input tokens of every prompt a monitor run would send.

    Args:
        provider: Anything with ``model`` and ``count_tokens`` - the counting
            endpoint does no generation and is not billed.
        texts: One entry per disclosure, already rendered the way the run
            renders it (``Disclosure.as_text()``).
        on_progress: Called as ``(done, total)`` so a caller can draw a bar.
            Counting is one network round trip per prompt, so a run over a
            busy filing day is slow enough to need one.

    Returns:
        A :class:`~stock_ai.ai.pricing.RunEstimate` over the same work the run
        would do.
    """
    rating_input = summary_input = 0
    total = len(texts)
    for index, text in enumerate(texts, start=1):
        rating_input += provider.count_tokens(importance_prompt(text), system=IMPORTANCE_SYSTEM)
        summary_input += provider.count_tokens(
            summary_prompt(text, max_words=DEFAULT_SUMMARY_WORDS), system=SUMMARY_SYSTEM
        )
        if on_progress is not None:
            on_progress(index, total)

    return RunEstimate(
        model=provider.model,
        items=total,
        rating_input_tokens=rating_input,
        summary_input_tokens=summary_input,
        rating_output_cap=IMPORTANCE_MAX_TOKENS,
        summary_output_cap=SUMMARY_MAX_TOKENS,
    )
