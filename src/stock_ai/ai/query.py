"""Natural-language screening: "PER15以下でROE20%以上の半導体株".

The model never writes SQL and never sees the database. It fills in a small,
fixed JSON schema of screening criteria, and this module turns that object into
the same :class:`~stock_ai.screening.base.Condition` tree the CLI flags build.
Two properties fall out of that design:

- **Nothing the model emits can reach the database as code.** An unknown field
  is rejected here, so a hallucinated or hostile response degrades into a
  refused query rather than an executed one.
- **The result is inspectable.** The parsed criteria have a plain-text
  description, so the user can see what the question was understood to mean
  before trusting the list of tickers.

Numbers are normalized on the way in: a model asked for "ROE 20%" may answer
``20`` or ``0.2``, and a screen that silently reads the first as 2000% would
return nothing with no hint as to why.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from stock_ai.ai.base import AIProvider
from stock_ai.core.exceptions import AIError
from stock_ai.core.logging import get_logger
from stock_ai.data.sectors import Sector
from stock_ai.database.engine import Database
from stock_ai.database.repository import get_profile, list_securities
from stock_ai.screening.base import All, Condition
from stock_ai.screening.conditions import (
    MaxMarketCap,
    MaxPayoutRatio,
    MaxPBR,
    MaxPER,
    MinConsecutiveDividendIncreases,
    MinDividendGrowth,
    MinDividendYield,
    MinMarketCap,
    MinProfitGrowth,
    MinRevenueGrowth,
    MinROE,
)
from stock_ai.screening.engine import ScreeningEngine

logger = get_logger(__name__)

#: Fields the model may set, and the condition each one builds.
_NUMERIC_FIELDS: dict[str, type[Condition]] = {
    "min_roe": MinROE,
    "max_per": MaxPER,
    "max_pbr": MaxPBR,
    "min_dividend_yield": MinDividendYield,
    "min_market_cap": MinMarketCap,
    "max_market_cap": MaxMarketCap,
    "min_revenue_growth": MinRevenueGrowth,
    "min_profit_growth": MinProfitGrowth,
    "min_dividend_growth": MinDividendGrowth,
    "max_payout_ratio": MaxPayoutRatio,
}

#: Fields whose value is a ratio, so "20" must be read as 20%, not 2000%.
_RATIO_FIELDS = frozenset(
    {
        "min_roe",
        "min_dividend_yield",
        "min_revenue_growth",
        "min_profit_growth",
        "min_dividend_growth",
        "max_payout_ratio",
    }
)

#: Criteria the statement series is needed for.
_SERIES_FIELDS = frozenset(
    {
        "min_revenue_growth",
        "min_profit_growth",
        "min_dividend_growth",
        "max_payout_ratio",
        "min_dividend_streak",
    }
)

_ALLOWED_FIELDS = frozenset(_NUMERIC_FIELDS) | {"min_dividend_streak", "sectors", "markets"}

_SYSTEM = (
    "You translate a stock-screening request into JSON. Reply with a single "
    "JSON object and nothing else - no prose, no code fences.\n"
    "Allowed keys (omit any that the request does not mention):\n"
    "  min_roe, max_per, max_pbr, min_dividend_yield, min_market_cap,\n"
    "  max_market_cap, min_revenue_growth, min_profit_growth,\n"
    "  min_dividend_growth, max_payout_ratio  - numbers; express percentages\n"
    "    as fractions (20% -> 0.2)\n"
    "  min_dividend_streak - whole years the dividend was raised\n"
    "  sectors - list from: " + ", ".join(str(s) for s in Sector) + "\n"
    "  markets - list from: JP, US\n"
    "Market caps are in USD. Never invent a key that is not listed above."
)


@dataclass(frozen=True)
class ScreenQuery:
    """A parsed natural-language screen, ready to run."""

    condition: Condition | None
    sectors: list[Sector] = field(default_factory=list)
    markets: list[str] = field(default_factory=list)
    needs_statements: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    def describe(self) -> str:
        """Return a human-readable summary of what will be screened for.

        Shown before the results so the user can check the interpretation
        rather than trusting an opaque list of tickers.
        """
        parts: list[str] = []
        if self.condition is not None:
            parts.append(str(self.condition))
        if self.sectors:
            parts.append("sector in [" + ", ".join(str(s) for s in self.sectors) + "]")
        if self.markets:
            parts.append("market in [" + ", ".join(self.markets) + "]")
        return " AND ".join(parts) if parts else "(no criteria)"

    @property
    def is_empty(self) -> bool:
        """Whether the query would filter nothing at all."""
        return self.condition is None and not self.sectors and not self.markets


def _extract_json(text: str) -> dict[str, Any]:
    """Pull the JSON object out of a model reply.

    Models wrap JSON in prose or fences often enough that failing on the first
    stray character would make the feature unusable; the outermost braces are
    located instead.
    """
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match is None:
        raise AIError(f"Model reply contained no JSON object: {text[:200]!r}")
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise AIError(f"Model reply was not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise AIError("Model reply was not a JSON object.")
    return parsed


def _as_number(field_name: str, value: Any) -> float:
    """Coerce a criterion value to a number, rescaling stray percentages."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise AIError(f"Criterion {field_name!r} must be a number, got {value!r}.")
    try:
        number = float(value)
    except ValueError as exc:
        raise AIError(f"Criterion {field_name!r} must be a number, got {value!r}.") from exc

    # "ROE 20%" comes back as 20 about as often as 0.2. No real ROE threshold
    # is 2000%, so a ratio field above 1 is a percentage the model forgot to
    # convert. Growth can legitimately exceed 1 (100%), so cap the rescale.
    if field_name in _RATIO_FIELDS and 1.0 < number <= 100.0:
        logger.debug("Rescaling %s from %g to %g", field_name, number, number / 100.0)
        return number / 100.0
    return number


def _parse_sectors(value: Any) -> list[Sector]:
    """Parse the sector list, ignoring labels outside the canonical set."""
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    sectors: list[Sector] = []
    for item in values:
        try:
            sector = Sector(str(item).strip())
        except ValueError:
            logger.warning("Ignoring unknown sector %r from the model", item)
            continue
        if sector not in sectors:
            sectors.append(sector)
    return sectors


def _parse_markets(value: Any) -> list[str]:
    """Parse the market list, keeping only markets the system knows."""
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    known = {"JP", "US"}
    return [m for m in (str(v).strip().upper() for v in values) if m in known]


def build_query(payload: dict[str, Any]) -> ScreenQuery:
    """Turn a parsed criteria object into a runnable :class:`ScreenQuery`.

    Args:
        payload: The model's JSON object.

    Returns:
        The query, with a condition tree built from the recognised criteria.

    Raises:
        AIError: If the payload names a field outside the allowed schema, or
            gives a non-numeric value for a numeric one.
    """
    unknown = set(payload) - _ALLOWED_FIELDS
    if unknown:
        raise AIError(f"Model produced unsupported criteria: {sorted(unknown)}")

    conditions: list[Condition] = []
    for name, condition_type in _NUMERIC_FIELDS.items():
        if payload.get(name) is None:
            continue
        conditions.append(condition_type(_as_number(name, payload[name])))

    streak = payload.get("min_dividend_streak")
    if streak is not None:
        years = int(_as_number("min_dividend_streak", streak))
        conditions.append(MinConsecutiveDividendIncreases(years))

    condition: Condition | None = None
    if len(conditions) == 1:
        condition = conditions[0]
    elif conditions:
        condition = All(*conditions)

    return ScreenQuery(
        condition=condition,
        sectors=_parse_sectors(payload.get("sectors")),
        markets=_parse_markets(payload.get("markets")),
        needs_statements=bool(_SERIES_FIELDS & set(payload)),
        raw=payload,
    )


def parse_query(provider: AIProvider, question: str) -> ScreenQuery:
    """Translate a natural-language screening request into a :class:`ScreenQuery`.

    Args:
        provider: The AI provider used for the translation.
        question: The user's request, in any language.

    Returns:
        The parsed query.

    Raises:
        AIError: If the model's reply cannot be read as a valid criteria object.
    """
    reply = provider.complete(question, system=_SYSTEM, max_tokens=512)
    query = build_query(_extract_json(reply))
    logger.info("Parsed query %r as [%s]", question, query.describe())
    return query


def run_query(database: Database, query: ScreenQuery) -> list[str]:
    """Run a parsed query and return the matching symbols.

    Sector and market are applied here rather than as screening conditions:
    both live on the security row, not in the metrics a
    :class:`~stock_ai.screening.base.Condition` inspects, so filtering the
    universe up front is both simpler and cheaper than loading every candidate.
    """
    universe = _filter_universe(database, query)
    if not universe:
        return []
    if query.condition is None:
        return universe

    engine = ScreeningEngine(database, load_statements=query.needs_statements)
    return engine.screen(query.condition, symbols=universe)


def _filter_universe(database: Database, query: ScreenQuery) -> list[str]:
    """Return the symbols matching the query's sector and market restrictions."""
    wanted_sectors = {str(s) for s in query.sectors}
    wanted_markets = set(query.markets)

    with database.session() as session:
        symbols: list[str] = []
        for symbol, market in list_securities(session):
            if wanted_markets and market not in wanted_markets:
                continue
            if wanted_sectors:
                profile = get_profile(session, symbol)
                if profile is None or profile.sector not in wanted_sectors:
                    continue
            symbols.append(symbol)
        return symbols
