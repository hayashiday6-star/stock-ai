"""What an AI call costs, in dollars, before and after it is made.

The monitor bills per disclosure, so the question "what will tonight's run
cost" has a real answer - and it is answerable *before* spending anything,
because both halves are knowable in advance:

- **Input tokens are exact.** ``messages.count_tokens`` returns the true count
  for a request without generating anything, and is not billed.
- **Output tokens are bounded.** Every call this project makes passes an
  explicit ``max_tokens``, so the worst case is a number, not a guess.

What cannot be known in advance is *how many* summaries a run will need: a
disclosure is only summarized if its importance clears the watch entry's
threshold, which is the model's verdict. So an estimate is a range, and this
module reports it as one rather than picking a figure that reads as certainty.

.. warning::
   The prices below are a **cached copy** (Anthropic first-party API rates, as
   of 2026-06-24) and will drift. They are here so a run can be costed offline;
   the invoice is the authority. Check
   https://www.anthropic.com/pricing before relying on a figure that matters.
   Bedrock and Vertex are billed by those platforms at their own rates, which
   are not represented here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Dollars per million tokens, as ``(input, output)``. Anthropic first-party
#: API rates; see the module warning about drift.
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-fable-5": (10.00, 50.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

_PER_MTOK = 1_000_000


def cost_of(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """Return the dollar cost of one call, or ``None`` for an unpriced model.

    ``None`` rather than a guess: a made-up price on a screen reads exactly
    like a real one, and this figure exists to be trusted.
    """
    price = PRICES_PER_MTOK.get(model)
    if price is None:
        return None
    input_rate, output_rate = price
    return (input_tokens * input_rate + output_tokens * output_rate) / _PER_MTOK


@dataclass(frozen=True)
class Usage:
    """Tokens actually consumed by one call, as the API reported them."""

    input_tokens: int
    output_tokens: int
    model: str

    @property
    def cost(self) -> float | None:
        """Dollars billed for this call, or ``None`` if the model is unpriced."""
        return cost_of(self.model, self.input_tokens, self.output_tokens)


@dataclass(frozen=True)
class RunEstimate:
    """The cost of a monitor run, as a range rather than a single number.

    ``low`` assumes no disclosure clears its importance threshold, so only the
    rating calls happen. ``high`` assumes every one does and is also summarized.
    The truth is somewhere between and depends on what was actually filed.
    """

    model: str
    items: int
    #: Exact, from ``count_tokens``. Summing rating and summary prompts.
    rating_input_tokens: int
    summary_input_tokens: int
    #: The ``max_tokens`` ceiling on each call, not a prediction.
    rating_output_cap: int
    summary_output_cap: int

    @property
    def low(self) -> float | None:
        """Cost if nothing clears the threshold: rating calls only."""
        return cost_of(self.model, self.rating_input_tokens, self.rating_output_cap * self.items)

    @property
    def high(self) -> float | None:
        """Cost if every item is rated *and* summarized."""
        return cost_of(
            self.model,
            self.rating_input_tokens + self.summary_input_tokens,
            (self.rating_output_cap + self.summary_output_cap) * self.items,
        )

    @property
    def priced(self) -> bool:
        """Whether this model has a known price at all."""
        return self.model in PRICES_PER_MTOK


@dataclass
class UsageLedger:
    """Running total of what one command has actually spent.

    ``Usage`` records a single call, which is enough for ``summarize`` and not
    nearly enough for ``monitor``: a run that rates twelve disclosures and
    summarizes four makes sixteen calls, and the last one's tokens say nothing
    about the bill. An estimate that is never checked against the invoice is
    just a claim, so the total is accumulated here and printed when the command
    ends - the same shape of number as the estimate, so the two can be compared
    directly.
    """

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    #: Dollars for the calls that could be priced. See :attr:`priced`.
    cost: float = 0.0
    #: Calls whose model has no cached price; their tokens still count.
    unpriced_calls: int = 0
    #: Models used, in first-seen order. Normally one, but a command is free
    #: to mix them and the total would be meaningless without saying so.
    models: list[str] = field(default_factory=list)

    def record(self, usage: Usage) -> None:
        """Add one call to the total."""
        self.calls += 1
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        if usage.model not in self.models:
            self.models.append(usage.model)
        call_cost = usage.cost
        if call_cost is None:
            self.unpriced_calls += 1
        else:
            self.cost += call_cost

    @property
    def priced(self) -> bool:
        """Whether :attr:`cost` covers every call made."""
        return self.unpriced_calls == 0
