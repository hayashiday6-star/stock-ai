"""Values, and the several different ways a value can be absent.

This package answers questions about money, and some of those questions have no
answer available. Dark-pool share, block prints, borrow fees - no source this
project can reach reports them, and an analysis that quietly fills those with a
plausible figure is worse than one that leaves them out: it reads exactly like
the parts that are real.

So absence is a type here, not a blank. :class:`Missing` cannot be added,
compared, or formatted as a number - any arithmetic on it raises - and the one
renderer in :func:`render` prints its reason instead of a digit. A metric that
was never fetched therefore cannot reach a table looking like a measurement.

The three kinds are kept apart because they call for different responses:

``UNAVAILABLE``
    No source we can reach has it. Nothing to implement; the analysis has to
    stand without it.
``NOT_IMPLEMENTED``
    A source we *can* reach has it and this code does not read it yet. A
    promise, and an honest one only while it is labelled as such.
``INSUFFICIENT``
    The source answered and the answer was too short to compute from - a
    symbol with 12 days of history asked for a 20-day range.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Absence(StrEnum):
    """Why a number is not here."""

    UNAVAILABLE = "取得不可"
    NOT_IMPLEMENTED = "未実装"
    INSUFFICIENT = "データ不足"


@dataclass(frozen=True)
class Missing:
    """A value that is not available, carrying why.

    Deliberately not a number and deliberately not falsy-like-zero: code that
    tries to use one in a calculation fails loudly at the point of the mistake
    rather than silently producing a figure.
    """

    kind: Absence
    reason: str = ""

    def __str__(self) -> str:
        """Render as the marker itself, never as a value."""
        return str(self.kind)


def unavailable(reason: str) -> Missing:
    """No reachable source provides this."""
    return Missing(Absence.UNAVAILABLE, reason)


def not_implemented(reason: str) -> Missing:
    """A reachable source provides this; this code does not read it yet."""
    return Missing(Absence.NOT_IMPLEMENTED, reason)


def insufficient(reason: str) -> Missing:
    """The source answered with too little to compute from."""
    return Missing(Absence.INSUFFICIENT, reason)


#: A measurement, or a stated reason there is none. There is no third option:
#: ``None`` is not used for absence anywhere in this package, because ``None``
#: formats as "None" and reads as an oversight rather than a finding.
type Measure = float | int | Missing


def is_value(measure: Measure) -> bool:
    """Whether ``measure`` is a real number rather than a stated absence."""
    return not isinstance(measure, Missing)


def value_or(measure: Measure, fallback: float) -> float:
    """The number, or ``fallback`` when it is absent.

    For arithmetic that has a defined answer without the metric. Never use it
    to fill a *displayed* figure - that is the substitution this module exists
    to prevent.
    """
    return float(measure) if is_value(measure) else fallback


def render(measure: Measure, *, unit: str = "", digits: int = 2, signed: bool = False) -> str:
    """Format a measurement for display, or say why there isn't one."""
    if isinstance(measure, Missing):
        return str(measure)
    number = float(measure)
    if abs(number) >= 1e9:
        text = f"{number / 1e9:.2f}B"
    elif abs(number) >= 1e6:
        text = f"{number / 1e6:.2f}M"
    elif abs(number) >= 1000:
        text = f"{number:,.0f}"
    else:
        text = f"{number:.{digits}f}"
    if signed and number > 0:
        text = f"+{text}"
    return f"{text}{unit}"


def render_pct(measure: Measure, *, digits: int = 1, signed: bool = False) -> str:
    """Format a ratio as a percentage, or say why there isn't one."""
    if isinstance(measure, Missing):
        return str(measure)
    number = float(measure) * 100
    sign = "+" if signed and number > 0 else ""
    return f"{sign}{number:.{digits}f}%"
