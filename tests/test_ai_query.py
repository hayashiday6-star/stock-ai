"""Tests for natural-language screening: parsing, guards, and execution."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator

import numpy as np
import pandas as pd
import pytest

from stock_ai.ai.query import ScreenQuery, build_query, parse_query, run_query
from stock_ai.core.exceptions import AIError
from stock_ai.data.sectors import Sector
from stock_ai.data.types import FinancialReport, Fundamentals, SecurityProfile
from stock_ai.database.engine import Database
from stock_ai.database.repository import (
    FinancialStatementRepository,
    FundamentalsRepository,
    PriceRepository,
    upsert_profile,
)


class _StubProvider:
    """An AI provider that returns a fixed reply."""

    name = "stub"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    def complete(self, prompt: str, *, system: str | None = None, max_tokens: int = 1024) -> str:
        self.prompts.append(prompt)
        return self.reply


@pytest.fixture
def database() -> Iterator[Database]:
    db = Database("sqlite:///:memory:")
    db.create_all()
    yield db
    db.dispose()


def _seed(
    db: Database,
    symbol: str,
    market: str,
    sector: str,
    per: float,
    roe: float,
    statements: list[tuple[int, float, float]] | None = None,
) -> None:
    index = pd.date_range("2024-01-01", periods=30, freq="B", name="date")
    close = np.full(30, 100.0)
    frame = pd.DataFrame(
        {
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "adj_close": close,
            "volume": 1_000,
        },
        index=index,
    )
    with db.session() as session:
        PriceRepository(session).upsert_prices(symbol, frame, market=market)
        upsert_profile(session, SecurityProfile(symbol=symbol, market=market, sector=sector))
        FundamentalsRepository(session).upsert_fundamentals(
            Fundamentals(
                symbol=symbol, as_of=dt.date(2024, 6, 30), per=per, roe=roe, market_cap=1e10
            ),
            market=market,
        )
        if statements:
            FinancialStatementRepository(session).upsert_reports(
                symbol,
                [
                    FinancialReport(
                        symbol=symbol, fiscal_year=y, revenue=rev, net_income=ni, eps=ni
                    )
                    for y, rev, ni in statements
                ],
                market=market,
            )


# --- parsing ----------------------------------------------------------------


def test_criteria_become_a_condition_tree() -> None:
    query = build_query({"max_per": 15, "min_roe": 0.2, "sectors": ["Technology"]})
    assert query.sectors == [Sector.TECHNOLOGY]
    assert "PER <= 15.0" in query.describe()
    assert "ROE >= 0.2" in query.describe()


def test_a_percentage_the_model_forgot_to_convert_is_rescaled() -> None:
    """ "ROE 20%" comes back as 20 as often as 0.2; 2000% would match nothing."""
    assert build_query({"min_roe": 20}).describe() == "ROE >= 0.2"
    assert build_query({"min_roe": 0.2}).describe() == "ROE >= 0.2"


def test_non_ratio_fields_are_never_rescaled() -> None:
    assert "PER <= 15.0" in build_query({"max_per": 15}).describe()
    assert "MarketCap >= 1000000000.0" in build_query({"min_market_cap": 1e9}).describe()


def test_an_unsupported_field_is_refused_rather_than_ignored() -> None:
    """A hallucinated or hostile key must stop the query, not slip through."""
    with pytest.raises(AIError, match="unsupported criteria"):
        build_query({"exec": "rm -rf /", "max_per": 15})


def test_a_non_numeric_criterion_is_refused() -> None:
    with pytest.raises(AIError, match="must be a number"):
        build_query({"max_per": "cheap"})
    with pytest.raises(AIError, match="must be a number"):
        build_query({"max_per": True})


def test_unknown_sectors_are_dropped_but_known_ones_kept() -> None:
    query = build_query({"sectors": ["Technology", "Crypto Mining", "Financials"]})
    assert query.sectors == [Sector.TECHNOLOGY, Sector.FINANCIALS]


def test_unknown_markets_are_dropped() -> None:
    assert build_query({"markets": ["JP", "MARS", "us"]}).markets == ["JP", "US"]


def test_statement_backed_criteria_are_flagged() -> None:
    assert build_query({"max_per": 15}).needs_statements is False
    assert build_query({"min_revenue_growth": 0.1}).needs_statements is True
    assert build_query({"min_dividend_streak": 3}).needs_statements is True


def test_an_empty_payload_produces_an_empty_query() -> None:
    query = build_query({})
    assert query.is_empty
    assert query.describe() == "(no criteria)"


# --- reply handling ---------------------------------------------------------


def test_json_is_extracted_from_a_chatty_reply() -> None:
    provider = _StubProvider('Sure!\n```json\n{"max_per": 15}\n```\nHope that helps.')
    assert "PER <= 15.0" in parse_query(provider, "cheap stocks").describe()


def test_a_reply_with_no_json_is_an_error() -> None:
    with pytest.raises(AIError, match="no JSON object"):
        parse_query(_StubProvider("I cannot help with that."), "x")


def test_malformed_json_is_an_error() -> None:
    with pytest.raises(AIError, match="not valid JSON"):
        parse_query(_StubProvider('{"max_per": }'), "x")


def test_a_json_array_is_rejected() -> None:
    with pytest.raises(AIError):
        parse_query(_StubProvider("[1, 2, 3]"), "x")


def test_the_question_reaches_the_model_verbatim() -> None:
    provider = _StubProvider('{"max_per": 15}')
    parse_query(provider, "PER15以下の株")
    assert provider.prompts == ["PER15以下の株"]


# --- execution --------------------------------------------------------------


def test_sector_and_metric_filters_combine(database: Database) -> None:
    """The worked example: cheap, profitable, and in one sector."""
    _seed(database, "NVDA", "US", "Technology", per=12.0, roe=0.35)
    _seed(database, "INTC", "US", "Technology", per=40.0, roe=0.05)
    _seed(database, "JPM", "US", "Financials", per=11.0, roe=0.25)

    query = build_query({"max_per": 15, "min_roe": 0.2, "sectors": ["Technology"]})
    assert run_query(database, query) == ["NVDA"]


def test_market_restriction_narrows_the_universe(database: Database) -> None:
    _seed(database, "NVDA", "US", "Technology", per=12.0, roe=0.35)
    _seed(database, "6857.T", "JP", "Technology", per=13.0, roe=0.22)

    both = build_query({"max_per": 15, "min_roe": 0.2, "sectors": ["Technology"]})
    assert sorted(run_query(database, both)) == ["6857.T", "NVDA"]

    jp_only = build_query({"max_per": 15, "min_roe": 0.2, "markets": ["JP"]})
    assert run_query(database, jp_only) == ["6857.T"]


def test_growth_criteria_load_the_statement_series(database: Database) -> None:
    _seed(database, "NVDA", "US", "Technology", 12.0, 0.35, [(2023, 100, 10), (2024, 150, 18)])
    _seed(database, "INTC", "US", "Technology", 40.0, 0.05, [(2023, 100, 10), (2024, 101, 10)])

    query = build_query({"min_revenue_growth": 20})  # 20% after rescaling
    assert query.needs_statements
    assert run_query(database, query) == ["NVDA"]


def test_a_sector_only_query_needs_no_condition(database: Database) -> None:
    _seed(database, "NVDA", "US", "Technology", per=12.0, roe=0.35)
    _seed(database, "JPM", "US", "Financials", per=11.0, roe=0.25)

    assert run_query(database, build_query({"sectors": ["Financials"]})) == ["JPM"]


def test_a_symbol_without_a_profile_never_matches_a_sector_query(database: Database) -> None:
    """An unclassified name cannot be shown to be in the sector asked for."""
    _seed(database, "NVDA", "US", "Technology", per=12.0, roe=0.35)
    with database.session() as session:
        FundamentalsRepository(session).upsert_fundamentals(
            Fundamentals(symbol="MYSTERY", as_of=dt.date(2024, 6, 30), per=5.0, roe=0.9)
        )

    assert run_query(database, build_query({"sectors": ["Technology"]})) == ["NVDA"]


def test_an_empty_universe_returns_nothing(database: Database) -> None:
    assert run_query(database, build_query({"max_per": 15})) == []


def test_an_empty_query_is_reported_as_such() -> None:
    assert ScreenQuery(condition=None).is_empty
    assert not ScreenQuery(condition=None, markets=["JP"]).is_empty


def test_the_dummy_provider_cannot_answer_a_structured_query() -> None:
    """It echoes its prompt, so the failure should name the cause, not the JSON."""
    from stock_ai.ai.dummy import DummyAIProvider

    with pytest.raises(AIError, match="no JSON object"):
        parse_query(DummyAIProvider(), "PER15以下の株")


# --- cost estimation --------------------------------------------------------


def test_a_known_model_prices_a_call() -> None:
    from stock_ai.ai.pricing import cost_of

    # Opus 5: $5/MTok in, $25/MTok out.
    assert cost_of("claude-opus-5", 1_000_000, 0) == pytest.approx(5.00)
    assert cost_of("claude-opus-5", 0, 1_000_000) == pytest.approx(25.00)
    assert cost_of("claude-opus-5", 2_000, 500) == pytest.approx(0.0225)


def test_an_unpriced_model_returns_none_rather_than_a_guess() -> None:
    """A made-up price reads exactly like a real one on screen."""
    from stock_ai.ai.pricing import cost_of

    assert cost_of("some-future-model", 1000, 1000) is None


def test_the_estimate_is_a_range_because_summaries_are_conditional() -> None:
    """Only disclosures clearing their threshold get summarized, and that is
    the model's verdict - unknowable before the run."""
    from stock_ai.ai.pricing import RunEstimate

    estimate = RunEstimate(
        model="claude-opus-5",
        items=10,
        rating_input_tokens=5_000,
        summary_input_tokens=5_000,
        rating_output_cap=8,
        summary_output_cap=1024,
    )

    assert estimate.low is not None
    assert estimate.high is not None
    assert estimate.low < estimate.high
    # Floor: rating input + 10 x 8 output tokens.
    assert estimate.low == pytest.approx(cost_of_expected := (5_000 * 5 + 80 * 25) / 1_000_000)
    assert cost_of_expected > 0


def test_an_unpriced_estimate_reports_no_dollars() -> None:
    from stock_ai.ai.pricing import RunEstimate

    estimate = RunEstimate(
        model="unknown-model",
        items=1,
        rating_input_tokens=100,
        summary_input_tokens=100,
        rating_output_cap=8,
        summary_output_cap=1024,
    )

    assert not estimate.priced
    assert estimate.low is None and estimate.high is None


def test_usage_prices_itself() -> None:
    from stock_ai.ai.pricing import Usage

    usage = Usage(input_tokens=1_000, output_tokens=100, model="claude-opus-5")
    assert usage.cost == pytest.approx((1_000 * 5 + 100 * 25) / 1_000_000)


def test_the_estimate_counts_the_prompts_the_run_actually_sends() -> None:
    """An estimate built from a reconstructed prompt prices a request that
    never happens, so both paths must come from the same builders."""
    from stock_ai.ai.analysis import (
        IMPORTANCE_SYSTEM,
        SUMMARY_SYSTEM,
        importance_prompt,
        summary_prompt,
    )

    text = "決算短信: 通期予想を上方修正"
    assert text in importance_prompt(text)
    assert text in summary_prompt(text)
    assert "importance" in importance_prompt(text).lower()
    assert "summarize" in summary_prompt(text).lower()
    assert IMPORTANCE_SYSTEM and SUMMARY_SYSTEM


def test_usage_is_recorded_from_the_response() -> None:
    """Without this the run can say afterwards only that it spent something."""
    from stock_ai.ai.anthropic_provider import AnthropicProvider

    class _Block:
        type = "text"
        text = "high"

    class _Usage:
        input_tokens = 1_234
        output_tokens = 7

    class _Response:
        content = [_Block()]
        stop_reason = "end_turn"
        usage = _Usage()

    class _Messages:
        def create(self, **kwargs: object) -> _Response:
            return _Response()

    class _Client:
        messages = _Messages()

    provider = AnthropicProvider(client=_Client(), model="claude-opus-5")
    assert provider.last_usage is None

    provider.complete("rate this")

    assert provider.last_usage is not None
    assert provider.last_usage.input_tokens == 1_234
    assert provider.last_usage.output_tokens == 7
    assert provider.last_usage.cost == pytest.approx((1_234 * 5 + 7 * 25) / 1_000_000)


def test_a_missing_package_is_not_reported_as_a_key_problem() -> None:
    """Observed live: a correct 108-char key, and the advice said to check it.

    The SDK raises ModuleNotFoundError, which says nothing about credentials.
    Wrapping that in "check your API key" sends the reader to the one place
    the fault is not.
    """
    import builtins

    from stock_ai.ai.anthropic_provider import AnthropicProvider
    from stock_ai.core.exceptions import AIError

    real_import = builtins.__import__

    def _no_anthropic(name: str, *args: object, **kwargs: object) -> object:
        if name == "anthropic":
            raise ImportError("No module named 'anthropic'")
        return real_import(name, *args, **kwargs)

    provider = AnthropicProvider(model="claude-opus-5")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(builtins, "__import__", _no_anthropic)
        with pytest.raises(AIError) as excinfo:
            provider.count_tokens("hello")

    message = str(excinfo.value)
    assert "uv sync --extra ai" in message
    assert "not the problem" in message


def test_an_empty_env_value_is_not_reported_as_set() -> None:
    """``OPENAI_API_KEY=`` in .env parses to "", which is not None.

    Reported as "set (0 chars)" it reads as configured, which is the opposite
    of what it means.
    """
    from pydantic import SecretStr

    from stock_ai.cli import _secret_summary

    assert "not set" in _secret_summary(SecretStr(""))
    assert "not set" in _secret_summary(SecretStr("   "))
    assert "not set" in _secret_summary(None)
    assert "set" in _secret_summary(SecretStr("sk-real-key-value"))
    assert "not set" not in _secret_summary(SecretStr("sk-real-key-value"))


def test_the_ledger_totals_every_call_not_just_the_last() -> None:
    """A monitor run makes one call per disclosure; the last one is not the bill."""
    from stock_ai.ai.pricing import Usage, UsageLedger

    ledger = UsageLedger()
    ledger.record(Usage(input_tokens=1_000, output_tokens=10, model="claude-opus-5"))
    ledger.record(Usage(input_tokens=2_000, output_tokens=20, model="claude-opus-5"))

    assert ledger.calls == 2
    assert ledger.input_tokens == 3_000
    assert ledger.output_tokens == 30
    assert ledger.models == ["claude-opus-5"]
    assert ledger.priced
    assert ledger.cost == pytest.approx((3_000 * 5 + 30 * 25) / 1_000_000)


def test_an_unpriced_call_is_counted_but_leaves_the_total_incomplete() -> None:
    """Tokens are still real when the price is unknown; the dollars are not."""
    from stock_ai.ai.pricing import Usage, UsageLedger

    ledger = UsageLedger()
    ledger.record(Usage(input_tokens=100, output_tokens=5, model="claude-opus-5"))
    ledger.record(Usage(input_tokens=100, output_tokens=5, model="claude-not-launched-yet"))

    assert ledger.calls == 2
    assert ledger.input_tokens == 200
    assert ledger.unpriced_calls == 1
    assert not ledger.priced
    # The priced half is still summed - it is just not the whole story.
    assert ledger.cost == pytest.approx((100 * 5 + 5 * 25) / 1_000_000)


def test_the_provider_accumulates_across_calls() -> None:
    """``last_usage`` answers "that call"; a run needs "all of them"."""
    from stock_ai.ai.anthropic_provider import AnthropicProvider

    class _Block:
        type = "text"
        text = "high"

    class _Usage:
        input_tokens = 500
        output_tokens = 4

    class _Response:
        content = [_Block()]
        stop_reason = "end_turn"
        usage = _Usage()

    class _Messages:
        def create(self, **kwargs: object) -> _Response:
            return _Response()

    class _Client:
        messages = _Messages()

    provider = AnthropicProvider(client=_Client(), model="claude-opus-5")
    assert provider.usage.calls == 0

    provider.complete("rate this")
    provider.complete("rate that")
    provider.complete("and this")

    assert provider.usage.calls == 3
    assert provider.usage.input_tokens == 1_500
    assert provider.usage.output_tokens == 12
    assert provider.last_usage is not None
    assert provider.last_usage.input_tokens == 500  # unchanged meaning


def test_a_provider_with_no_ledger_prints_nothing_rather_than_zero() -> None:
    """The dummy provider is free; "spent $0.00" would imply it was billed."""
    from stock_ai.ai.dummy import DummyAIProvider
    from stock_ai.cli import _report_spend, console

    with console.capture() as captured:
        _report_spend(DummyAIProvider())
    assert captured.get() == ""


def test_the_spend_line_reports_the_run_total() -> None:
    from stock_ai.ai.anthropic_provider import AnthropicProvider
    from stock_ai.ai.pricing import Usage
    from stock_ai.cli import _report_spend, console

    provider = AnthropicProvider(model="claude-opus-5")
    provider.usage.record(Usage(input_tokens=10_000, output_tokens=1_000, model="claude-opus-5"))
    provider.usage.record(Usage(input_tokens=10_000, output_tokens=1_000, model="claude-opus-5"))

    with console.capture() as captured:
        _report_spend(provider)
    text = captured.get()

    assert "2 call(s)" in text
    assert "20,000 in" in text
    assert "2,000 out" in text
    assert "$0.1500" in text  # 20k * $5/M + 2k * $25/M


def test_the_configured_model_is_the_one_the_run_calls() -> None:
    """Otherwise ``ai-cost --model`` prices something the run cannot select."""
    from stock_ai.ai.anthropic_provider import DEFAULT_MODEL
    from stock_ai.ai.factory import get_ai_provider
    from stock_ai.ai.pricing import PRICES_PER_MTOK
    from stock_ai.config.settings import Settings

    # _env_file=None so a developer's own .env cannot decide the assertion.
    default = get_ai_provider("claude", Settings(_env_file=None))
    assert default.model == DEFAULT_MODEL

    chosen = get_ai_provider("claude", Settings(_env_file=None, ANTHROPIC_MODEL="claude-haiku-4-5"))
    assert chosen.model == "claude-haiku-4-5"
    # And the price it would be billed at follows the choice, not the default.
    assert PRICES_PER_MTOK[chosen.model] == (1.00, 5.00)


def test_an_empty_answer_names_the_ceiling_that_caused_it() -> None:
    """Observed live: 'no text content' from a call whose max_tokens was 8.

    The stop reason was in the response the whole time. Without it the reader
    checks the key and the model name, neither of which was the fault.
    """
    from stock_ai.ai.anthropic_provider import AnthropicProvider
    from stock_ai.core.exceptions import AIError

    class _Usage:
        input_tokens = 200
        output_tokens = 8

    class _Response:
        content: list[object] = []
        stop_reason = "max_tokens"
        usage = _Usage()

    class _Messages:
        def create(self, **kwargs: object) -> _Response:
            return _Response()

    class _Client:
        messages = _Messages()

    provider = AnthropicProvider(client=_Client(), model="claude-opus-5")
    with pytest.raises(AIError) as excinfo:
        provider.complete("classify this", max_tokens=8)

    message = str(excinfo.value)
    assert "max_tokens=8" in message
    assert "not a key or" in message
    # The call was billed even though it returned nothing usable.
    assert provider.usage.calls == 1


def test_the_one_word_ceilings_have_headroom() -> None:
    """8 tokens returned an empty answer live; the monitor shares the ceiling.

    Pinning the value keeps a future "it only needs one word" tidy-up from
    silently reintroducing the failure - which on the importance rating would
    show as every disclosure unjudged and no alerts, not as an error.
    """
    from stock_ai.ai.analysis import IMPORTANCE_MAX_TOKENS, SENTIMENT_MAX_TOKENS

    assert IMPORTANCE_MAX_TOKENS >= 32
    assert SENTIMENT_MAX_TOKENS >= 32
