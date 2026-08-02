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


class ScreeningError(StockAIError):
    """A screening condition or the screening pipeline failed."""


class BacktestError(StockAIError):
    """Backtest configuration or execution failed."""


class BrokerError(StockAIError):
    """Order execution or brokerage connectivity failed."""


class NotificationError(StockAIError):
    """A notification channel failed to deliver a message."""
