"""Tests for the factor test: look-ahead control, bucketing, and noise rejection."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator

import numpy as np
import pandas as pd
import pytest

from stock_ai.backtest.factor_test import run_factor_test
from stock_ai.core.exceptions import BacktestError
from stock_ai.data.fx import static_converter
from stock_ai.data.types import FinancialReport, Fundamentals
from stock_ai.database.engine import Database
from stock_ai.database.repository import (
    FinancialStatementRepository,
    FundamentalsRepository,
    PriceRepository,
)
from stock_ai.portfolio.growth_factors import RevenueGrowthFactor, tenbagger_weighted_factors
from stock_ai.portfolio.scoring import WeightedScorer

_FORMATION = dt.date(2024, 6, 28)
_INDEX = pd.date_range("2022-01-03", periods=1200, freq="B", name="date")
_FORM_POS = int(np.searchsorted(_INDEX, pd.Timestamp(_FORMATION)))
_FX = static_converter("USD", USD=1.0, JPY=1.0 / 150.0)


@pytest.fixture
def database() -> Iterator[Database]:
    db = Database("sqlite:///:memory:")
    db.create_all()
    yield db
    db.dispose()


_HORIZON = 100


def _seed(
    db: Database,
    symbol: str,
    *,
    forward_return: float,
    revenues: list[tuple[int, dt.date, float]] | None = None,
    market_cap: float = 1e9,
    market: str = "US",
) -> None:
    """Store a name that is flat until formation, then delivers ``forward_return``.

    The path reaches its target exactly ``_HORIZON`` bars after formation and
    holds there, so a test asserting on the horizon return gets the number it
    asked for rather than a fraction of it.
    """
    after_formation = len(_INDEX) - _FORM_POS
    ramp = 100.0 * np.linspace(1.0, 1.0 + forward_return, _HORIZON + 1)
    close = np.concatenate(
        [
            np.full(_FORM_POS, 100.0),
            ramp[:after_formation],
            np.full(max(0, after_formation - len(ramp)), ramp[-1]),
        ]
    )
    frame = pd.DataFrame(
        {
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "adj_close": close,
            "volume": 1_000,
        },
        index=_INDEX,
    )
    with db.session() as session:
        PriceRepository(session).upsert_prices(symbol, frame, market=market)
        FundamentalsRepository(session).upsert_fundamentals(
            Fundamentals(symbol=symbol, as_of=dt.date(2024, 6, 30), market_cap=market_cap),
            market=market,
        )
        if revenues:
            FinancialStatementRepository(session).upsert_reports(
                symbol,
                [
                    FinancialReport(
                        symbol=symbol,
                        fiscal_year=year,
                        disclosed_on=disclosed,
                        revenue=revenue,
                        net_income=revenue * 0.08,
                        eps=revenue * 0.08,
                        dividend_per_share=0.0,
                    )
                    for year, disclosed, revenue in revenues
                ],
                market=market,
            )


def _growth_scorer() -> WeightedScorer:
    """A scorer with one factor, so bucket order follows revenue growth exactly."""
    return WeightedScorer([(RevenueGrowthFactor(target=1.0), 1.0)])


def _series(rate: float, last_rate: float | None = None) -> list[tuple[int, dt.date, float]]:
    """Three fiscal years compounding at ``rate``, the last optionally different."""
    revenue = 100.0
    rows: list[tuple[int, dt.date, float]] = []
    schedule = [
        (2021, dt.date(2022, 5, 10)),
        (2022, dt.date(2023, 5, 10)),
        (2023, dt.date(2024, 5, 10)),
    ]
    for index, (year, disclosed) in enumerate(schedule):
        rows.append((year, disclosed, revenue))
        step = last_rate if (last_rate is not None and index == len(schedule) - 2) else rate
        revenue *= 1.0 + step
    return rows


# --- look-ahead control -----------------------------------------------------


def test_a_statement_disclosed_after_formation_is_not_used(database: Database) -> None:
    """Scoring on figures published later is the classic backtest lie."""
    # Slow through 2022, then a jump only disclosed in May 2024.
    _seed(database, "LATE", forward_return=0.5, revenues=_series(0.02, last_rate=0.90))
    _seed(database, "STEADY", forward_return=0.1, revenues=_series(0.30))

    before = run_factor_test(
        database, _growth_scorer(), formation=dt.date(2024, 4, 1), horizon_days=_HORIZON, buckets=2
    )
    after = run_factor_test(
        database, _growth_scorer(), formation=dt.date(2024, 6, 28), horizon_days=_HORIZON, buckets=2
    )

    assert before.top is not None and after.top is not None
    assert before.top.symbols == ["STEADY"]  # the jump was not yet public
    assert after.top.symbols == ["LATE"]  # now it is


def test_a_statement_with_no_disclosure_date_is_never_visible(database: Database) -> None:
    """An undated filing cannot be shown to predate formation, so it is excluded."""
    with database.session() as session:
        FinancialStatementRepository(session).upsert_reports(
            "X",
            [FinancialReport(symbol="X", fiscal_year=2023, revenue=100.0)],
            market="US",
        )
    _seed(database, "X", forward_return=0.1)

    with pytest.raises(BacktestError):  # nothing computable, so nothing scored
        run_factor_test(database, _growth_scorer(), formation=_FORMATION, horizon_days=_HORIZON)


def test_prices_after_formation_do_not_reach_the_scorer(database: Database) -> None:
    """A momentum-style factor must not see the window it is being judged on."""
    scorer = WeightedScorer(tenbagger_weighted_factors(fx=_FX))
    _seed(database, "UP", forward_return=2.0, revenues=_series(0.30), market_cap=3e8)
    _seed(database, "DOWN", forward_return=-0.5, revenues=_series(0.30), market_cap=3e8)

    result = run_factor_test(
        database, scorer, formation=_FORMATION, horizon_days=_HORIZON, buckets=1
    )
    # Identical fundamentals and size, so the huge forward gap must not separate them.
    assert result.buckets[0].size == 2


# --- bucketing --------------------------------------------------------------


def test_buckets_order_by_score_and_measure_forward_returns(database: Database) -> None:
    for index, (rate, forward) in enumerate([(0.60, 0.40), (0.30, 0.20), (0.01, -0.10)]):
        _seed(database, f"S{index}", forward_return=forward, revenues=_series(rate))

    result = run_factor_test(
        database, _growth_scorer(), formation=_FORMATION, horizon_days=_HORIZON, buckets=3
    )

    assert [b.symbols for b in result.buckets] == [["S0"], ["S1"], ["S2"]]
    assert [b.label for b in result.buckets] == ["top", "mid1", "bottom"]
    assert result.buckets[0].mean_return == pytest.approx(0.40, rel=1e-3)


def test_the_universe_return_is_the_equal_weight_average(database: Database) -> None:
    _seed(database, "A", forward_return=0.30, revenues=_series(0.50))
    _seed(database, "B", forward_return=-0.10, revenues=_series(0.10))

    result = run_factor_test(
        database, _growth_scorer(), formation=_FORMATION, horizon_days=_HORIZON, buckets=2
    )
    assert result.universe_return == pytest.approx(0.10, rel=1e-3)
    assert result.excess_return == pytest.approx(0.20, rel=1e-3)


def test_uneven_universes_split_without_empty_buckets(database: Database) -> None:
    for index in range(7):
        _seed(database, f"S{index}", forward_return=0.1, revenues=_series(0.1 * index))

    result = run_factor_test(
        database, _growth_scorer(), formation=_FORMATION, horizon_days=_HORIZON, buckets=3
    )
    sizes = [b.size for b in result.buckets]
    assert sum(sizes) == 7
    assert max(sizes) - min(sizes) <= 1


def test_fewer_names_than_buckets_collapses_to_one(database: Database) -> None:
    """Single-name buckets would report one company's luck as a mean return."""
    _seed(database, "ONLY", forward_return=0.1, revenues=_series(0.3))
    result = run_factor_test(
        database, _growth_scorer(), formation=_FORMATION, horizon_days=_HORIZON, buckets=5
    )
    assert [b.label for b in result.buckets] == ["all"]


def test_hit_rate_counts_the_winners(database: Database) -> None:
    _seed(database, "W1", forward_return=0.2, revenues=_series(0.5))
    _seed(database, "W2", forward_return=0.1, revenues=_series(0.4))
    _seed(database, "L1", forward_return=-0.2, revenues=_series(0.3))
    _seed(database, "L2", forward_return=-0.3, revenues=_series(0.2))

    result = run_factor_test(
        database, _growth_scorer(), formation=_FORMATION, horizon_days=_HORIZON, buckets=1
    )
    assert result.buckets[0].hit_rate == pytest.approx(0.5)


# --- noise rejection --------------------------------------------------------


def test_a_real_signal_clears_two_sigma(database: Database) -> None:
    rng = np.random.default_rng(3)
    for index in range(40):
        growth = rng.uniform(0.0, 0.6)
        _seed(
            database,
            f"S{index:02d}",
            forward_return=(growth - 0.3) * 2.0 + rng.normal(0, 0.05),
            revenues=_series(growth),
        )

    result = run_factor_test(
        database, _growth_scorer(), formation=_FORMATION, horizon_days=_HORIZON, buckets=3
    )
    assert result.is_significant
    assert result.spread_t_stat is not None and result.spread_t_stat > 2.0


def test_pure_noise_does_not_clear_two_sigma(database: Database) -> None:
    """The guard that stops the tool from selling a random edge as a finding."""
    rng = np.random.default_rng(11)
    for index in range(40):
        _seed(
            database,
            f"S{index:02d}",
            forward_return=float(rng.normal(0, 0.25)),
            revenues=_series(float(rng.uniform(0.0, 0.6))),
        )

    result = run_factor_test(
        database, _growth_scorer(), formation=_FORMATION, horizon_days=_HORIZON, buckets=3
    )
    assert not result.is_significant


def test_the_spread_is_unmeasurable_on_a_tiny_universe(database: Database) -> None:
    _seed(database, "A", forward_return=0.5, revenues=_series(0.6))
    _seed(database, "B", forward_return=-0.2, revenues=_series(0.1))

    result = run_factor_test(
        database, _growth_scorer(), formation=_FORMATION, horizon_days=_HORIZON, buckets=2
    )
    assert result.spread_t_stat is None  # one name per bucket: no dispersion
    assert not result.is_significant


def test_monotonicity_is_reported(database: Database) -> None:
    for index, (rate, forward) in enumerate([(0.60, 0.40), (0.30, 0.20), (0.01, -0.10)]):
        _seed(database, f"S{index}", forward_return=forward, revenues=_series(rate))

    result = run_factor_test(
        database, _growth_scorer(), formation=_FORMATION, horizon_days=_HORIZON, buckets=3
    )
    assert result.is_monotonic


# --- guards -----------------------------------------------------------------


def test_names_without_a_forward_window_are_skipped(database: Database) -> None:
    _seed(database, "OK", forward_return=0.2, revenues=_series(0.4))
    result = run_factor_test(
        database, _growth_scorer(), formation=_FORMATION, horizon_days=_HORIZON, buckets=1
    )
    assert result.scored == 1

    # A horizon longer than the stored history leaves nothing measurable.
    with pytest.raises(BacktestError, match="fetch more history"):
        run_factor_test(database, _growth_scorer(), formation=_FORMATION, horizon_days=10_000)


def test_a_name_with_no_computable_score_is_skipped(database: Database) -> None:
    _seed(database, "SCORED", forward_return=0.2, revenues=_series(0.4))
    _seed(database, "NOSTATEMENTS", forward_return=0.9)  # no statements to score on

    result = run_factor_test(
        database, _growth_scorer(), formation=_FORMATION, horizon_days=_HORIZON, buckets=1
    )
    assert result.scored == 1
    assert "NOSTATEMENTS" in result.skipped


def test_invalid_arguments_are_refused(database: Database) -> None:
    with pytest.raises(BacktestError, match="horizon_days"):
        run_factor_test(database, _growth_scorer(), formation=_FORMATION, horizon_days=0)
    with pytest.raises(BacktestError, match="buckets"):
        run_factor_test(database, _growth_scorer(), formation=_FORMATION, buckets=0)


def test_the_universe_can_be_restricted(database: Database) -> None:
    _seed(database, "A", forward_return=0.3, revenues=_series(0.5))
    _seed(database, "B", forward_return=0.1, revenues=_series(0.2))

    result = run_factor_test(
        database,
        _growth_scorer(),
        formation=_FORMATION,
        horizon_days=_HORIZON,
        buckets=1,
        symbols=["A"],
    )
    assert result.scored == 1
    assert result.buckets[0].symbols == ["A"]


# --- look-ahead through the snapshot, and why names get dropped -------------


def test_a_snapshot_taken_after_formation_is_not_visible() -> None:
    """A snapshot carries its *fetch* date, so "latest" means today's.

    JP snapshots are written by the bulk loader stamped with the day it ran, so
    before this bound a 2024 formation was ranked on 2026 market caps - the
    single most flattering look-ahead a factor test can have, and invisible in
    the output.
    """
    import datetime as dt

    from stock_ai.data.types import Fundamentals
    from stock_ai.database.engine import Database
    from stock_ai.database.repository import FundamentalsRepository, get_or_create_security

    database = Database("sqlite:///:memory:")
    database.create_all()
    with database.session() as session:
        get_or_create_security(session, "7203", market="JP")
        repo = FundamentalsRepository(session)
        repo.upsert_fundamentals(
            Fundamentals(symbol="7203", as_of=dt.date(2026, 8, 3), market_cap=4.0e13), market="JP"
        )
        repo.upsert_fundamentals(
            Fundamentals(symbol="7203", as_of=dt.date(2024, 3, 1), market_cap=3.0e13), market="JP"
        )

    formation = dt.date(2024, 6, 28)
    with database.session() as session:
        repo = FundamentalsRepository(session)
        assert repo.get_latest("7203").as_of == dt.date(2026, 8, 3)  # newest overall
        bounded = repo.get_latest("7203", as_of=formation)
    assert bounded is not None
    assert bounded.as_of == dt.date(2024, 3, 1)
    assert bounded.market_cap == 3.0e13
    database.dispose()


def test_no_snapshot_old_enough_yields_none_not_a_newer_one() -> None:
    """Falling back to a later snapshot would reintroduce the bias silently."""
    import datetime as dt

    from stock_ai.data.types import Fundamentals
    from stock_ai.database.engine import Database
    from stock_ai.database.repository import FundamentalsRepository, get_or_create_security

    database = Database("sqlite:///:memory:")
    database.create_all()
    with database.session() as session:
        get_or_create_security(session, "7203", market="JP")
        FundamentalsRepository(session).upsert_fundamentals(
            Fundamentals(symbol="7203", as_of=dt.date(2026, 8, 3), market_cap=4.0e13), market="JP"
        )
    with database.session() as session:
        assert FundamentalsRepository(session).get_latest("7203", as_of=dt.date(2020, 1, 1)) is None
    database.dispose()


# --- which formation dates the data can support -----------------------------


def _advice_db(n: int, first_disclosure, price_start, today):
    """A database shaped like a plan that serves only recent disclosures."""
    import datetime as dt

    import numpy as np
    import pandas as pd

    from stock_ai.data.types import FinancialReport
    from stock_ai.database.engine import Database
    from stock_ai.database.repository import (
        FinancialStatementRepository,
        PriceRepository,
        get_or_create_security,
    )

    database = Database("sqlite:///:memory:")
    database.create_all()
    index = pd.bdate_range(price_start, today, name="date")
    close = np.full(len(index), 1000.0)
    frame = pd.DataFrame(
        {
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "adj_close": close,
            "volume": 1000,
        },
        index=index,
    )
    with database.session() as session:
        for i in range(n):
            symbol = f"{1300 + i:04d}"
            get_or_create_security(session, symbol, market="JP")
            PriceRepository(session).upsert_prices(symbol, frame, market="JP")
            FinancialStatementRepository(session).upsert_reports(
                symbol,
                [
                    FinancialReport(
                        symbol=symbol,
                        fiscal_year=2024 + k,
                        disclosed_on=first_disclosure + dt.timedelta(days=182 * k),
                        revenue=1e10,
                    )
                    for k in range(4)
                ],
                market="JP",
            )
    return database


def test_the_advised_date_is_not_before_the_first_disclosure() -> None:
    """Advising an earlier date would advise a test on nothing."""
    import datetime as dt

    from stock_ai.backtest.factor_test import suggest_formation

    first = dt.date(2025, 1, 15)
    database = _advice_db(10, first, dt.date(2022, 6, 25), dt.date(2026, 8, 3))
    advice = suggest_formation(database, horizon_days=252)

    assert advice.best is not None
    assert advice.best >= first
    assert advice.best <= advice.latest_feasible
    assert advice.coverage > 0.9
    database.dispose()


def test_a_horizon_longer_than_the_history_is_reported_as_impossible() -> None:
    """Silently advising an unusable date is worse than saying there is none.

    A plan whose disclosures start after the last feasible formation cannot
    support the test at all, and the fix is a shorter horizon - not more data.
    """
    import datetime as dt

    from stock_ai.backtest.factor_test import suggest_formation

    database = _advice_db(10, dt.date(2026, 3, 1), dt.date(2022, 6, 25), dt.date(2026, 8, 3))

    assert suggest_formation(database, horizon_days=252).best is None
    # The same data supports a short hold, which is the actionable alternative.
    assert suggest_formation(database, horizon_days=60).best is not None
    database.dispose()


def test_an_empty_database_gives_no_advice_rather_than_a_wrong_date() -> None:
    from stock_ai.backtest.factor_test import suggest_formation
    from stock_ai.database.engine import Database

    database = Database("sqlite:///:memory:")
    database.create_all()
    advice = suggest_formation(database, horizon_days=252)

    assert advice.best is None
    assert advice.first_disclosure is None
    assert advice.coverage == 0.0
    database.dispose()


def test_missing_statements_and_late_statements_are_counted_apart() -> None:
    """Two causes, opposite fixes: fetch the data, or move the date.

    Reported together they sent a real diagnosis after the wrong one - a run
    where most names had simply never been downloaded was read as "pick a later
    formation date", which cannot help.
    """
    import datetime as dt

    import numpy as np
    import pandas as pd

    from stock_ai.backtest.factor_test import run_factor_test, suggest_formation
    from stock_ai.data.fx import FxConverter
    from stock_ai.data.types import FinancialReport
    from stock_ai.database.engine import Database
    from stock_ai.database.repository import (
        FinancialStatementRepository,
        PriceRepository,
        get_or_create_security,
    )
    from stock_ai.portfolio.growth_factors import tenbagger_weighted_factors
    from stock_ai.portfolio.scoring import WeightedScorer

    formation = dt.date(2024, 6, 28)
    index = pd.bdate_range(dt.date(2022, 6, 25), dt.date(2026, 8, 3), name="date")
    close = np.full(len(index), 1000.0)
    frame = pd.DataFrame(
        {
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "adj_close": close,
            "volume": 1000,
        },
        index=index,
    )

    database = Database("sqlite:///:memory:")
    database.create_all()
    with database.session() as session:
        for i in range(30):
            symbol = f"{1300 + i:04d}"
            get_or_create_security(session, symbol, market="JP")
            PriceRepository(session).upsert_prices(symbol, frame, market="JP")
            if i < 10:
                first = dt.date(2022, 8, 1)  # disclosed before formation
            elif i < 20:
                first = dt.date(2025, 6, 1)  # disclosed only after formation
            else:
                continue  # statements never fetched at all
            FinancialStatementRepository(session).upsert_reports(
                symbol,
                [
                    FinancialReport(
                        symbol=symbol,
                        fiscal_year=2022 + k,
                        disclosed_on=first + dt.timedelta(days=182 * k),
                        revenue=1e10 * 1.2**k,
                        net_income=5e8 * 1.1**k,
                        equity=8e9,
                        eps=100.0,
                    )
                    for k in range(4)
                ],
                market="JP",
            )

    scorer = WeightedScorer(tenbagger_weighted_factors(fx=FxConverter(rates={"JPY": 150.0})))
    result = run_factor_test(database, scorer, formation, horizon_days=252, buckets=3)

    assert result.no_statements_stored == 10
    assert result.no_visible_statements == 10

    # The advisor's ceiling reflects what can never be tested, not just the date.
    advice = suggest_formation(database, 252)
    assert advice.with_statements == 20
    assert advice.universe == 30
    assert advice.coverage <= 20 / 30
    database.dispose()
