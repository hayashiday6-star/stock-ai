"""Currency handling, so JP and US figures can be compared on one scale.

Ratio metrics (ROE, margin, P/E, yield) are already unitless and need nothing
here. Absolute figures — market cap above all — are quoted in the listing
market's currency, so a ¥100bn company and a $100bn company would otherwise
rank as equals. :class:`FxConverter` normalizes those onto a single base.

The rate lookup is injectable, so conversion is unit-testable without network
and a caller can pin an explicit rate for reproducible reports.
"""

from __future__ import annotations

from collections.abc import Callable

from stock_ai.core.exceptions import DataError
from stock_ai.core.logging import get_logger

logger = get_logger(__name__)

#: Listing market code -> ISO currency of that market's quotes.
CURRENCY_BY_MARKET: dict[str, str] = {"JP": "JPY", "US": "USD"}

DEFAULT_BASE = "USD"

# A fetcher takes (currency, base) and returns how many units of ``base`` one
# unit of ``currency`` buys, e.g. ("JPY", "USD") -> 0.0064.
RateFetcher = Callable[[str, str], float]


def currency_for_market(market: str) -> str:
    """Return the quote currency for a market code, defaulting to the base."""
    return CURRENCY_BY_MARKET.get(market.upper(), DEFAULT_BASE)


def _default_rate_fetcher(currency: str, base: str) -> float:
    """Look up a spot rate via yfinance's ``<PAIR>=X`` quotes (imported lazily)."""
    import yfinance as yf

    ticker = f"{currency}{base}=X"
    history = yf.Ticker(ticker).history(period="5d")
    if history.empty:
        raise DataError(f"No FX quote for {ticker}.")
    return float(history["Close"].iloc[-1])


class FxConverter:
    """Convert amounts into a single base currency.

    Rates are resolved lazily and cached for the lifetime of the converter, so
    ranking a few hundred symbols costs one lookup per currency, not per row.
    """

    def __init__(
        self,
        base: str = DEFAULT_BASE,
        rates: dict[str, float] | None = None,
        fetcher: RateFetcher | None = None,
    ) -> None:
        """Create the converter.

        Args:
            base: Currency every amount is converted into.
            rates: Pre-seeded ``currency -> rate`` map. Seeded entries are used
                as-is and never fetched; pass these to pin a report's FX.
            fetcher: Callable resolving a missing rate; injected in tests.
                Defaults to a yfinance spot lookup.
        """
        self.base = base.upper()
        self._rates: dict[str, float] = {self.base: 1.0}
        self._rates.update({k.upper(): v for k, v in (rates or {}).items()})
        self._fetch = fetcher or _default_rate_fetcher

    def rate(self, currency: str) -> float:
        """Return the ``currency -> base`` rate, fetching and caching on first use."""
        key = currency.upper()
        if key not in self._rates:
            self._rates[key] = self._fetch(key, self.base)
            logger.info("Resolved FX %s->%s = %g", key, self.base, self._rates[key])
        return self._rates[key]

    def convert(self, amount: float | None, currency: str) -> float | None:
        """Convert ``amount`` from ``currency`` into the base, passing ``None`` through."""
        if amount is None:
            return None
        return amount * self.rate(currency)

    def convert_from_market(self, amount: float | None, market: str) -> float | None:
        """Convert an amount quoted in ``market``'s currency into the base."""
        return self.convert(amount, currency_for_market(market))


def static_converter(base: str = DEFAULT_BASE, **rates: float) -> FxConverter:
    """Build a converter that only ever uses the rates given here.

    Any currency not supplied raises instead of silently reaching the network,
    which is what you want in tests and in reproducible batch reports.
    """

    def _refuse(currency: str, _base: str) -> float:
        raise DataError(f"No FX rate configured for {currency!r}.")

    return FxConverter(base=base, rates=rates, fetcher=_refuse)
