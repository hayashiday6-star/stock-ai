"""Screening abstractions: the evaluation context and the condition interface.

Conditions are small, composable predicates. They combine with ``&`` (all),
``|`` (any), and ``~`` (negate), so callers build arbitrary rules without the
engine or existing conditions needing to change (open/closed principle).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import pandas as pd

from stock_ai.data.types import Fundamentals


@dataclass(frozen=True)
class ScreeningContext:
    """Everything a condition may inspect for one candidate symbol."""

    symbol: str
    fundamentals: Fundamentals | None = None
    prices: pd.DataFrame | None = None


class Condition(ABC):
    """A boolean predicate over a :class:`ScreeningContext`."""

    @abstractmethod
    def evaluate(self, context: ScreeningContext) -> bool:
        """Return ``True`` if ``context`` satisfies this condition."""

    @abstractmethod
    def describe(self) -> str:
        """Return a short human-readable description of this condition."""

    def __and__(self, other: Condition) -> Condition:
        """Combine with ``other`` so both must hold."""
        return All(self, other)

    def __or__(self, other: Condition) -> Condition:
        """Combine with ``other`` so at least one must hold."""
        return Any(self, other)

    def __invert__(self) -> Condition:
        """Negate this condition."""
        return Not(self)

    def __str__(self) -> str:
        """Delegate to :meth:`describe`."""
        return self.describe()


class All(Condition):
    """Passes only when every sub-condition passes (logical AND)."""

    def __init__(self, *conditions: Condition) -> None:
        """Store the sub-conditions to combine."""
        self.conditions = conditions

    def evaluate(self, context: ScreeningContext) -> bool:
        """Return ``True`` if all sub-conditions pass."""
        return all(c.evaluate(context) for c in self.conditions)

    def describe(self) -> str:
        """Describe as a parenthesised AND of the sub-conditions."""
        return "(" + " AND ".join(c.describe() for c in self.conditions) + ")"


class Any(Condition):
    """Passes when at least one sub-condition passes (logical OR)."""

    def __init__(self, *conditions: Condition) -> None:
        """Store the sub-conditions to combine."""
        self.conditions = conditions

    def evaluate(self, context: ScreeningContext) -> bool:
        """Return ``True`` if any sub-condition passes."""
        return any(c.evaluate(context) for c in self.conditions)

    def describe(self) -> str:
        """Describe as a parenthesised OR of the sub-conditions."""
        return "(" + " OR ".join(c.describe() for c in self.conditions) + ")"


class Not(Condition):
    """Passes when the wrapped condition fails (logical NOT)."""

    def __init__(self, condition: Condition) -> None:
        """Store the condition to negate."""
        self.condition = condition

    def evaluate(self, context: ScreeningContext) -> bool:
        """Return the negation of the wrapped condition."""
        return not self.condition.evaluate(context)

    def describe(self) -> str:
        """Describe as ``NOT (...)``."""
        return f"NOT {self.condition.describe()}"
