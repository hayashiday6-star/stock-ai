"""Tests for the data layer: OHLCV normalization and the yfinance provider.

No network access — a fake downloader is injected into the provider.
"""

from __future__ import annotations

import sys
from datetime import date

import pandas as pd
import pytest

from stock_ai.core.exceptions import DataError
from stock_ai.data.schema import ADJ_CLOSE, DATE, OHLCV_COLUMNS, VOLUME, normalize_ohlcv
from stock_ai.data.yfinance_provider import YFinancePriceProvider

START = date(2024, 1, 1)
END = date(2024, 1, 5)


def _raw_yf_frame() -> pd.DataFrame:
    """Build a raw yfinance-style frame: unsorted dates, 'Adj Close', tz-aware."""
    idx = pd.to_datetime(["2024-01-03", "2024-01-02"]).tz_localize("America/New_York")
    return pd.DataFrame(
        {
            "Open": [102.0, 100.0],
            "High": [103.0, 101.0],
            "Low": [101.0, 99.0],
            "Close": [102.5, 100.5],
            "Adj Close": [102.5, 100.5],
            "Volume": [2_000, 1_000],
        },
        index=idx,
    )


def _provider_returning(frame: pd.DataFrame) -> YFinancePriceProvider:
    return YFinancePriceProvider(downloader=lambda _s, _st, _e: frame)


def test_normalize_produces_canonical_schema() -> None:
    result = normalize_ohlcv(_raw_yf_frame())
    assert list(result.columns) == OHLCV_COLUMNS
    assert result.index.name == DATE
    assert result.index.tz is None
    assert result.index.is_monotonic_increasing
    assert result[VOLUME].dtype == "int64"


def test_normalize_handles_multiindex_columns() -> None:
    raw = _raw_yf_frame()
    raw.columns = pd.MultiIndex.from_product([raw.columns, ["AAPL"]])
    result = normalize_ohlcv(raw)
    assert list(result.columns) == OHLCV_COLUMNS
    assert ADJ_CLOSE in result.columns


def test_normalize_missing_columns_raises() -> None:
    bad = pd.DataFrame({"Open": [1.0]}, index=pd.to_datetime(["2024-01-02"]))
    with pytest.raises(DataError):
        normalize_ohlcv(bad)


def test_fetch_prices_returns_sorted_bars() -> None:
    provider = _provider_returning(_raw_yf_frame())
    result = provider.fetch_prices("AAPL", START, END)
    assert len(result) == 2
    assert result.index[0] < result.index[1]


def test_fetch_prices_empty_raises() -> None:
    provider = _provider_returning(pd.DataFrame())
    with pytest.raises(DataError):
        provider.fetch_prices("AAPL", START, END)


def test_fetch_prices_rejects_reversed_range() -> None:
    provider = _provider_returning(_raw_yf_frame())
    with pytest.raises(DataError):
        provider.fetch_prices("AAPL", END, START)


# --- 429 must be distinguishable from every other failure -------------------


def test_a_429_becomes_a_rate_limit_error_with_the_requested_wait() -> None:
    """The bulk ingester's pacing depends on telling 429 from a bad symbol."""
    from stock_ai.core.exceptions import RateLimitError
    from stock_ai.data.http import raise_for_status

    class _Response:
        status_code = 429
        headers = {"Retry-After": "30"}

    with pytest.raises(RateLimitError) as excinfo:
        raise_for_status(_Response(), "statements for 7203")
    assert excinfo.value.retry_after == 30.0


def test_a_429_without_a_retry_after_still_carries_a_wait() -> None:
    """A backoff of None would be read as "retry immediately"."""
    from stock_ai.core.exceptions import RateLimitError
    from stock_ai.data.http import DEFAULT_RETRY_AFTER, raise_for_status

    class _Response:
        status_code = 429
        headers: dict[str, str] = {}

    with pytest.raises(RateLimitError) as excinfo:
        raise_for_status(_Response(), "x")
    assert excinfo.value.retry_after == DEFAULT_RETRY_AFTER


def test_an_http_date_retry_after_falls_back_rather_than_crashing() -> None:
    from stock_ai.core.exceptions import RateLimitError
    from stock_ai.data.http import DEFAULT_RETRY_AFTER, raise_for_status

    class _Response:
        status_code = 429
        headers = {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}

    with pytest.raises(RateLimitError) as excinfo:
        raise_for_status(_Response(), "x")
    assert excinfo.value.retry_after == DEFAULT_RETRY_AFTER


def test_other_errors_stay_ordinary_data_errors() -> None:
    """A 404 is one symbol's problem; treating it as a rate limit would stall."""
    from stock_ai.core.exceptions import DataError, RateLimitError
    from stock_ai.data.http import raise_for_status

    class _Response:
        status_code = 404
        headers: dict[str, str] = {}

    with pytest.raises(DataError) as excinfo:
        raise_for_status(_Response(), "x")
    assert not isinstance(excinfo.value, RateLimitError)


def test_a_successful_response_raises_nothing() -> None:
    from stock_ai.data.http import raise_for_status

    class _Response:
        status_code = 200
        headers: dict[str, str] = {}

    raise_for_status(_Response(), "x")


# --- error bodies -----------------------------------------------------------


class _Failing:
    """A minimal response object carrying a status and a body."""

    def __init__(self, status_code: int, text: str = "", headers: dict | None = None) -> None:
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}


def test_an_error_carries_the_providers_own_explanation() -> None:
    """A status code is a category; the reason is only ever in the body.

    Observed live: J-Quants answered a 13-year price range with ``400 Bad
    Request`` and the message said only "HTTP 400". Whether the window was too
    wide, the start predated the plan, or a parameter was wrong is exactly what
    the body would have said, and it was being thrown away - the same discard
    that turned a 403 into two rounds of guessing at "plan limits".
    """
    from stock_ai.core.exceptions import DataError
    from stock_ai.data.http import raise_for_status

    response = _Failing(400, '{"message":"The specified period is too long."}')
    with pytest.raises(DataError) as excinfo:
        raise_for_status(response, "prices for 7203")

    assert "400" in str(excinfo.value)
    assert "period is too long" in str(excinfo.value)


def test_an_error_body_never_leaks_a_key() -> None:
    """Error bodies quote the request back, and some providers key the URL."""
    from stock_ai.core.exceptions import DataError
    from stock_ai.core.logging import register_secret
    from stock_ai.data.http import raise_for_status

    register_secret("super-secret-key-value")
    response = _Failing(400, "Bad request for Subscription-Key=super-secret-key-value")

    with pytest.raises(DataError) as excinfo:
        raise_for_status(response, "prices for 7203")

    assert "super-secret-key-value" not in str(excinfo.value)
    assert "<redacted>" in str(excinfo.value)


def test_a_bodyless_error_still_reports_its_status() -> None:
    from stock_ai.core.exceptions import DataError
    from stock_ai.data.http import raise_for_status

    with pytest.raises(DataError, match="HTTP 500"):
        raise_for_status(_Failing(500, ""), "prices for 7203")


def test_a_long_error_body_is_truncated() -> None:
    from stock_ai.core.exceptions import DataError
    from stock_ai.data.http import raise_for_status

    with pytest.raises(DataError) as excinfo:
        raise_for_status(_Failing(400, "x" * 5000), "prices for 7203")

    assert len(str(excinfo.value)) < 500
    assert "..." in str(excinfo.value)


def test_a_rate_limit_is_still_typed_separately() -> None:
    """The 429 path must keep its own type, body or no body."""
    from stock_ai.core.exceptions import RateLimitError
    from stock_ai.data.http import raise_for_status

    with pytest.raises(RateLimitError) as excinfo:
        raise_for_status(_Failing(429, "slow down", {"Retry-After": "12"}), "prices for 7203")

    assert excinfo.value.retry_after == 12.0


# --- subscription window ----------------------------------------------------


def test_the_covered_range_is_read_back_out_of_the_refusal() -> None:
    """Observed live: the 400 body states exactly what the plan would serve."""
    import datetime as dt

    from stock_ai.data.jquants_provider import subscription_window

    message = (
        'HTTP 400 while fetching prices for 7203. Provider said: {"message": "Your '
        "subscription covers the following dates: 2021-08-16 ~ . If you want more "
        'data, please check other plans:https://jpx-jquants.com/#dataset"}'
    )
    window = subscription_window(message)

    assert window is not None
    start, end = window
    assert start == dt.date(2021, 8, 16)
    assert end is None  # open right-hand side means "up to today"


def test_a_closed_covered_range_is_read_too() -> None:
    import datetime as dt

    from stock_ai.data.jquants_provider import subscription_window

    window = subscription_window("subscription covers the following dates: 2021-08-16 ~ 2026-01-31")
    assert window == (dt.date(2021, 8, 16), dt.date(2026, 1, 31))


def test_an_unrelated_error_names_no_window() -> None:
    from stock_ai.data.jquants_provider import subscription_window

    assert subscription_window("HTTP 500 while fetching prices for 7203.") is None


def test_an_over_wide_request_is_narrowed_to_the_covered_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failing the whole symbol throws away the years the plan does cover.

    Live, a 5,000-day backfill asked for 2012-12-07 and the plan started
    2021-08-16. The request 400'd, the symbol was recorded as failed, and the
    ten extra months that *were* available never arrived.
    """
    import datetime as dt

    import httpx

    from stock_ai.data.jquants_provider import _default_fetcher

    asked: list[tuple[str, str]] = []
    body = (
        '{"message": "Your subscription covers the following dates: 2021-08-16 ~ . '
        'If you want more data, please check other plans."}'
    )

    class _Response:
        def __init__(self, status_code: int, payload: dict, text: str = "") -> None:
            self.status_code = status_code
            self._payload = payload
            self.text = text
            self.headers: dict[str, str] = {}

        def json(self) -> dict:
            return self._payload

    class _Client:
        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def __init__(self, **kwargs: object) -> None:
            pass

        def get(self, url: str, headers: dict, params: dict) -> _Response:
            asked.append((params["from"], params["to"]))
            if params["from"] < "2021-08-16":
                return _Response(400, {}, body)
            return _Response(200, {"data": [{"Date": "2021-08-16", "C": 100.0}]})

    monkeypatch.setattr(httpx, "Client", _Client)
    records = _default_fetcher(None)("7203", dt.date(2012, 12, 7), dt.date(2026, 8, 16))

    assert len(asked) == 2
    assert asked[0][0] == "2012-12-07"  # the request as asked
    assert asked[1][0] == "2021-08-16"  # narrowed to what the plan covers
    assert records  # and the covered years actually arrive


def test_a_narrowing_that_would_change_nothing_re_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retrying the identical range would loop without ever making progress."""
    import datetime as dt

    import httpx

    from stock_ai.core.exceptions import DataError
    from stock_ai.data.jquants_provider import _default_fetcher

    calls = {"n": 0}

    class _Response:
        status_code = 400
        text = "subscription covers the following dates: 2021-08-16 ~ "
        headers: dict[str, str] = {}

    class _Client:
        def __init__(self, **kwargs: object) -> None:
            pass

        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def get(self, url: str, headers: dict, params: dict) -> _Response:
            calls["n"] += 1
            return _Response()

    monkeypatch.setattr(httpx, "Client", _Client)
    with pytest.raises(DataError):
        _default_fetcher(None)("7203", dt.date(2021, 8, 16), dt.date(2026, 8, 16))

    assert calls["n"] == 1  # no pointless second attempt


def test_the_plan_window_is_learned_once_not_per_symbol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejected request per symbol doubles the cost of the heaviest run.

    The window belongs to the subscription, not the symbol. Re-discovering it
    1,564 times spends the rate-limit budget on refusals - on the first real
    backfill the run aborted with only 38 of 1,564 symbols extended.
    """
    import datetime as dt

    import httpx

    from stock_ai.data.jquants_provider import _default_fetcher

    asked: list[tuple[str, str]] = []
    body = '{"message": "Your subscription covers the following dates: 2021-08-16 ~ ."}'

    class _Response:
        def __init__(self, status_code: int, payload: dict, text: str = "") -> None:
            self.status_code = status_code
            self._payload = payload
            self.text = text
            self.headers: dict[str, str] = {}

        def json(self) -> dict:
            return self._payload

    class _Client:
        def __init__(self, **kwargs: object) -> None:
            pass

        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def get(self, url: str, headers: dict, params: dict) -> _Response:
            asked.append((params["code"], params["from"]))
            if params["from"] < "2021-08-16":
                return _Response(400, {}, body)
            return _Response(200, {"data": [{"Date": "2021-08-16", "C": 100.0}]})

    monkeypatch.setattr(httpx, "Client", _Client)
    fetcher = _default_fetcher(None)
    start, end = dt.date(2012, 12, 7), dt.date(2026, 8, 16)
    for symbol in ("7203", "6758", "9984"):
        assert fetcher(symbol, start, end)

    rejected = [code for code, frm in asked if frm < "2021-08-16"]
    assert rejected == ["7203"]  # only the first symbol pays the discovery cost
    assert len(asked) == 4  # 1 refusal + 3 successful fetches, not 6


def test_a_window_that_does_not_overlap_costs_no_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once the plan is known, an impossible range must not be sent at all."""
    import datetime as dt

    import httpx

    from stock_ai.data.jquants_provider import _default_fetcher

    calls: list[str] = []
    body = '{"message": "Your subscription covers the following dates: 2021-08-16 ~ ."}'

    class _Response:
        def __init__(self, status_code: int, payload: dict, text: str = "") -> None:
            self.status_code = status_code
            self._payload = payload
            self.text = text
            self.headers: dict[str, str] = {}

        def json(self) -> dict:
            return self._payload

    class _Client:
        def __init__(self, **kwargs: object) -> None:
            pass

        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def get(self, url: str, headers: dict, params: dict) -> _Response:
            calls.append(params["from"])
            if params["from"] < "2021-08-16":
                return _Response(400, {}, body)
            return _Response(200, {"data": [{"Date": "2021-08-16", "C": 100.0}]})

    monkeypatch.setattr(httpx, "Client", _Client)
    fetcher = _default_fetcher(None)
    fetcher("7203", dt.date(2012, 1, 1), dt.date(2026, 8, 16))  # learns the window
    before = len(calls)

    # Entirely before the plan starts: nothing to ask for.
    assert fetcher("6758", dt.date(2010, 1, 1), dt.date(2011, 1, 1)) == []
    assert len(calls) == before


def test_a_network_failure_is_not_reported_as_an_unknown_ticker() -> None:
    """yfinance returns an empty frame for a refused connection.

    Downstream that becomes "the provider does not know this symbol", so a blip
    during a 500-name load would report 500 unknown tickers - a conclusion
    about the data drawn from a fact about the network. Observed live: a proxy
    403 surfaced as "No price data returned for 'AMZN'".
    """
    import logging

    from stock_ai.data import yfinance_provider

    class _FakeYF:
        @staticmethod
        def download(symbol: str, **kwargs: object) -> pd.DataFrame:
            logging.getLogger("yfinance").error(
                "1 Failed download: ['%s']: ConnectionError('proxy refused')", symbol
            )
            return pd.DataFrame()

    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(sys.modules, "yfinance", _FakeYF())
        with pytest.raises(DataError) as excinfo:
            yfinance_provider._default_download("AMZN", date(2024, 1, 1), date(2024, 1, 5))

    message = str(excinfo.value)
    assert "ConnectionError" in message
    assert "No price data" not in message


def test_a_genuinely_empty_result_stays_a_no_data_answer() -> None:
    """An empty range with no complaint logged is still just "nothing yet"."""
    from stock_ai.data import yfinance_provider

    class _QuietYF:
        @staticmethod
        def download(symbol: str, **kwargs: object) -> pd.DataFrame:
            return pd.DataFrame()

    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(sys.modules, "yfinance", _QuietYF())
        frame = yfinance_provider._default_download("AAPL", date(2024, 1, 1), date(2024, 1, 5))

    assert frame.empty  # the caller turns this into NoDataError, not an error
