"""Exception hierarchy for stock-ai.

A single base (:class:`StockAIError`) lets callers catch any application-level
error, while each subclass maps to the pipeline layer that raised it.
"""

from __future__ import annotations


class StockAIError(Exception):
    """Base class for all stock-ai errors."""


class ConfigError(StockAIError):
    """Configuration or settings are invalid or missing."""


class DataError(StockAIError):
    """Market-data acquisition or persistence failed."""


class RateLimitError(DataError):
    """The provider asked us to slow down (HTTP 429).

    Kept distinct from a plain :class:`DataError` because the right response is
    the opposite one. An ordinary failure belongs to a single symbol and the run
    should move on; a rate limit belongs to the *run*, and moving on just spends
    the rest of the universe collecting the same refusal. A bulk load that
    treats 429 as a per-symbol error finishes in seconds having fetched nothing.
    """

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        """Record how long the provider asked us to wait, if it said."""
        super().__init__(message)
        self.retry_after = retry_after


class NoDataError(DataError):
    """The provider answered, and had nothing for the range asked for.

    Distinct from a plain :class:`DataError` because it is usually not a
    failure at all. A daily refresh asks for "everything after the last stored
    bar", which before the market closes is an empty range by definition - and
    on a weekend or a holiday it stays empty all day. Reporting that as a
    failed job means the scheduled task reports failure nearly every morning,
    and an alarm that fires every day is one nobody reads.

    It is still a real problem when a symbol has *no* stored bars and the
    provider returns nothing for a wide backfill window: that is a ticker the
    provider does not know. Only the caller can tell those apart, which is why
    this carries no verdict of its own.
    """


class ScreeningError(StockAIError):
    """A screening condition or the screening pipeline failed."""


class BacktestError(StockAIError):
    """Backtest configuration or execution failed."""


class BrokerError(StockAIError):
    """Order execution or brokerage connectivity failed."""


class NotificationError(StockAIError):
    """A notification channel failed to deliver a message."""


class AIError(StockAIError):
    """An AI provider failed, was misconfigured, or refused a request."""


class OpsError(StockAIError):
    """The canonical trading repository was reached, and the request failed."""


class OpsUnavailableError(OpsError):
    """The canonical trading repository could not be reached at all.

    Kept distinct because the two mean opposite things on screen. A failed
    request is about the request; an unreachable repository means every number
    in the operations view is missing rather than wrong - and the fix lies
    outside stock-ai (start WSL, correct the path). Rendering that state as
    "no positions" would be a lie the reader has no way to detect.
    """
