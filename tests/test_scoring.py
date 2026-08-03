"""Tests for scoring factors, the weighted scorer, and the ``score`` CLI."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator

import pandas as pd
import pytest
from typer.testing import CliRunner

from stock_ai import cli
from stock_ai.data.schema import ADJ_CLOSE, CLOSE, HIGH, LOW, OPEN, VOLUME
from stock_ai.data.types import Fundamentals
from stock_ai.database.engine import Database
from stock_ai.database.repository import FundamentalsRepository, PriceRepository
from stock_ai.portfolio.factors import (
    Factor,
    MomentumFactor,
    ROEFactor,
    ValueFactor,
)
from stock_ai.portfolio.scoring import WeightedScorer, default_weighted_factors
from stock_ai.screening.base import ScreeningContext

runner = CliRunner()
_TODAY = dt.date(2024, 6, 30)


def _ctx(
    symbol: str = "X", prices: pd.DataFrame | None = None, **metrics: float
) -> ScreeningContext:
    fundamentals = Fundamentals(symbol=symbol, as_of=_TODAY, **metrics) if metrics else None
    return ScreeningContext(symbol=symbol, fundamentals=fundamentals, prices=prices)


def _prices(adj_closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=len(adj_closes), freq="D", name="date")
    return pd.DataFrame(
        {
            OPEN: adj_closes,
            HIGH: adj_closes,
            LOW: adj_closes,
            CLOSE: adj_closes,
            ADJ_CLOSE: adj_closes,
            VOLUME: [1000] * len(adj_closes),
        },
        index=idx,
    )


# --- factors ---------------------------------------------------------------


def test_roe_factor_normalizes_and_clamps() -> None:
    assert ROEFactor(target=0.30).score(_ctx(roe=0.15)) == pytest.approx(0.5)
    assert ROEFactor(target=0.30).score(_ctx(roe=0.60)) == 1.0  # clamped
    assert ROEFactor().score(_ctx(per=10.0)) is None  # roe absent
    assert ROEFactor().score(ScreeningContext("X")) is None  # no fundamentals


def test_value_factor_prefers_low_per() -> None:
    assert ValueFactor(ceiling=40).score(_ctx(per=10.0)) == pytest.approx(0.75)
    assert ValueFactor(ceiling=40).score(_ctx(per=50.0)) == 0.0  # above ceiling
    assert ValueFactor().score(_ctx(per=-5.0)) is None  # non-positive excluded


def test_momentum_factor() -> None:
    up = MomentumFactor(lookback=100, floor=0.2, cap=0.4).score(
        _ctx(prices=_prices([100.0, 140.0]))
    )
    assert up == pytest.approx(1.0)  # +40% -> full
    flat = MomentumFactor().score(_ctx(prices=_prices([100.0, 100.0])))
    assert flat == pytest.approx(0.2 / 0.6)  # 0% return
    assert MomentumFactor().score(_ctx()) is None  # no prices


# --- scorer ----------------------------------------------------------------


def test_scorer_renormalizes_over_available_factors() -> None:
    # Only ROE is available; its sub-score must map straight to the 0..100 scale.
    scorer = WeightedScorer(default_weighted_factors())
    result = scorer.score(_ctx(roe=0.15))  # roe sub-score 0.5, others None
    assert result.breakdown == {"roe": pytest.approx(0.5)}
    assert result.score == pytest.approx(50.0)


def test_scorer_zero_when_nothing_applies() -> None:
    result = WeightedScorer(default_weighted_factors()).score(ScreeningContext("X"))
    assert result.score == 0.0
    assert result.breakdown == {}


def test_scorer_weights_two_factors() -> None:
    class _Const(Factor):
        def __init__(self, name: str, value: float) -> None:
            self._name = name
            self._value = value

        @property
        def name(self) -> str:
            return self._name

        def score(self, context: ScreeningContext) -> float:
            return self._value

    scorer = WeightedScorer([(_Const("a", 1.0), 3.0), (_Const("b", 0.0), 1.0)])
    # weighted mean = (1*3 + 0*1) / 4 = 0.75 -> 75
    assert scorer.score(_ctx(roe=0.1)).score == pytest.approx(75.0)


# --- CLI -------------------------------------------------------------------


@pytest.fixture
def db() -> Iterator[Database]:
    database = Database("sqlite:///:memory:")
    database.create_all()
    with database.session() as s:
        FundamentalsRepository(s).upsert_fundamentals(
            Fundamentals(symbol="AAPL", as_of=_TODAY, roe=0.30, per=10.0)
        )
        PriceRepository(s).upsert_prices("AAPL", _prices([100.0, 140.0]))
    yield database
    database.dispose()


def test_score_cli(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "Database", lambda: db)
    monkeypatch.setenv("COLUMNS", "200")
    result = runner.invoke(cli.app, ["score", "AAPL"])
    assert result.exit_code == 0
    assert "AAPL" in result.stdout
    assert "roe=" in result.stdout


# --- non-finite input must never score ------------------------------------


def test_nan_metric_is_not_computable_rather_than_full_marks() -> None:
    """A NaN metric must read as missing, not as a perfect sub-score.

    ``min``/``max`` treat NaN as unordered and hand back the bound, so a naive
    clamp silently awarded 1.0 and floated broken data to the top of the rank.
    """
    nan = float("nan")
    assert ROEFactor().score(_ctx(roe=nan)) is None
    assert ValueFactor().score(_ctx(per=nan)) is None  # per <= 0 does not catch NaN


def test_nan_price_does_not_score_momentum() -> None:
    prices = _prices([100.0, float("nan")])
    assert MomentumFactor().score(_ctx(prices=prices)) is None


def test_scorer_drops_nan_factor_instead_of_inflating_the_score() -> None:
    """A NaN ROE must be renormalized away, not counted as a perfect 25%."""
    scorer = WeightedScorer([(ROEFactor(), 0.5), (ValueFactor(), 0.5)])
    result = scorer.score(_ctx(roe=float("nan"), per=20.0))
    assert "roe" not in result.breakdown
    assert result.score == pytest.approx(50.0)  # value_per alone: 1 - 20/40
