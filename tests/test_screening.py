"""Tests for screening conditions, composition, and the engine (no network)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator

import pytest

from stock_ai.data.types import Fundamentals
from stock_ai.database.engine import Database
from stock_ai.database.repository import FundamentalsRepository
from stock_ai.screening.base import ScreeningContext
from stock_ai.screening.conditions import (
    MaxPBR,
    MaxPER,
    MinDividendYield,
    MinROE,
)
from stock_ai.screening.engine import ScreeningEngine

_TODAY = dt.date(2024, 6, 30)


def _ctx(symbol: str, **metrics: float) -> ScreeningContext:
    return ScreeningContext(
        symbol=symbol,
        fundamentals=Fundamentals(symbol=symbol, as_of=_TODAY, **metrics),
    )


def test_min_condition_passes_and_fails() -> None:
    cond = MinROE(0.5)
    assert cond.evaluate(_ctx("A", roe=1.4)) is True
    assert cond.evaluate(_ctx("B", roe=0.2)) is False


def test_max_condition_passes_and_fails() -> None:
    cond = MaxPER(15.0)
    assert cond.evaluate(_ctx("A", per=10.0)) is True
    assert cond.evaluate(_ctx("B", per=35.0)) is False


def test_missing_metric_never_passes() -> None:
    # roe absent -> None -> excluded, for both min and max style conditions.
    assert MinROE(0.1).evaluate(_ctx("A", per=10.0)) is False
    # No fundamentals at all.
    assert MinROE(0.1).evaluate(ScreeningContext("A")) is False


def test_and_composition() -> None:
    cheap_and_profitable = MinROE(0.1) & MaxPBR(2.0)
    assert cheap_and_profitable.evaluate(_ctx("VALUE", roe=0.2, pbr=1.2)) is True
    assert cheap_and_profitable.evaluate(_ctx("GROWTH", roe=1.4, pbr=42.0)) is False


def test_or_and_not_composition() -> None:
    either = MaxPER(12.0) | MinDividendYield(0.03)
    assert either.evaluate(_ctx("A", per=30.0, dividend_yield=0.05)) is True
    assert either.evaluate(_ctx("B", per=30.0, dividend_yield=0.01)) is False

    negated = ~MaxPER(12.0)
    assert negated.evaluate(_ctx("A", per=30.0)) is True


def test_describe_is_readable() -> None:
    cond = MinROE(0.1) & MaxPBR(2.0)
    assert cond.describe() == "(ROE >= 0.1 AND PBR <= 2.0)"


@pytest.fixture
def db() -> Iterator[Database]:
    database = Database("sqlite:///:memory:")
    database.create_all()
    yield database
    database.dispose()


def _store(db: Database, symbol: str, **metrics: float) -> None:
    with db.session() as s:
        FundamentalsRepository(s).upsert_fundamentals(
            Fundamentals(symbol=symbol, as_of=_TODAY, **metrics)
        )


def test_engine_screens_all_stored_symbols(db: Database) -> None:
    _store(db, "AAPL", roe=1.4, per=35.0, pbr=42.0)
    _store(db, "VALUE", roe=0.2, per=10.0, pbr=1.2)
    _store(db, "MID", roe=0.6, per=14.0, pbr=1.8)

    passing = ScreeningEngine(db).screen(MinROE(0.5) & MaxPBR(2.0))
    assert passing == ["MID"]  # AAPL: PBR too high; VALUE: ROE too low


def test_engine_respects_explicit_symbol_list(db: Database) -> None:
    _store(db, "AAPL", roe=1.4, pbr=42.0)
    _store(db, "VALUE", roe=0.2, pbr=1.2)

    passing = ScreeningEngine(db).screen(MinROE(0.1), symbols=["VALUE"])
    assert passing == ["VALUE"]


def test_engine_excludes_symbols_without_fundamentals(db: Database) -> None:
    _store(db, "AAPL", roe=1.4)
    passing = ScreeningEngine(db).screen(MinROE(0.5), symbols=["AAPL", "UNKNOWN"])
    assert passing == ["AAPL"]


def test_the_report_can_carry_company_names() -> None:
    """A four-digit code is not a company to anyone reading the output.

    The one judgement a screen cannot make - "is this list plausible?" - needs
    the name to be made at all.
    """
    import datetime as dt

    from stock_ai.data.types import Fundamentals
    from stock_ai.screening.report import build_report

    items = [
        Fundamentals(symbol="7203", as_of=dt.date(2026, 8, 8), per=10.0),
        Fundamentals(symbol="6758", as_of=dt.date(2026, 8, 8), per=12.0),
    ]
    frame = build_report(items, names={"7203": "トヨタ自動車"})

    assert list(frame.columns)[:3] == ["symbol", "name", "as_of"]
    assert frame.loc[frame["symbol"] == "7203", "name"].iloc[0] == "トヨタ自動車"
    # A missing name must not drop the row or print "None".
    assert frame.loc[frame["symbol"] == "6758", "name"].iloc[0] == ""


def test_the_report_without_names_is_unchanged() -> None:
    """Callers that never asked for names must not gain an empty column."""
    import datetime as dt

    from stock_ai.data.types import Fundamentals
    from stock_ai.screening.report import build_report

    frame = build_report([Fundamentals(symbol="7203", as_of=dt.date(2026, 8, 8))])

    assert "name" not in frame.columns
