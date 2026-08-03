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
