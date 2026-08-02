"""Tests for the paper broker and the IBKR skeleton."""

from __future__ import annotations

import pytest

from stock_ai.broker.base import Order, OrderSide, OrderType
from stock_ai.broker.ibkr import IBKRBroker
from stock_ai.broker.paper import PaperBroker
from stock_ai.core.exceptions import BrokerError


def _broker(cash: float = 10_000.0, prices: dict[str, float] | None = None) -> PaperBroker:
    prices = prices or {"AAPL": 100.0}
    return PaperBroker(cash=cash, price_source=lambda s: prices[s])


def _buy(symbol: str, qty: float, **kw: object) -> Order:
    return Order(symbol=symbol, side=OrderSide.BUY, quantity=qty, **kw)  # type: ignore[arg-type]


def _sell(symbol: str, qty: float, **kw: object) -> Order:
    return Order(symbol=symbol, side=OrderSide.SELL, quantity=qty, **kw)  # type: ignore[arg-type]


def test_market_buy_debits_cash_and_opens_position() -> None:
    broker = _broker(cash=10_000.0)
    fill = broker.place_order(_buy("AAPL", 10))
    assert fill.price == 100.0
    assert broker.cash() == pytest.approx(9_000.0)
    pos = broker.positions()[0]
    assert pos.quantity == 10
    assert pos.avg_price == 100.0


def test_second_buy_averages_price() -> None:
    prices = {"AAPL": 100.0}
    broker = PaperBroker(20_000.0, lambda s: prices[s])
    broker.place_order(_buy("AAPL", 10))  # @100
    prices["AAPL"] = 120.0
    broker.place_order(_buy("AAPL", 10))  # @120
    pos = broker.positions()[0]
    assert pos.quantity == 20
    assert pos.avg_price == pytest.approx(110.0)


def test_sell_credits_cash_and_reduces_position() -> None:
    broker = _broker(cash=10_000.0)
    broker.place_order(_buy("AAPL", 10))
    broker.place_order(_sell("AAPL", 4))
    assert broker.cash() == pytest.approx(9_400.0)
    assert broker.positions()[0].quantity == 6


def test_selling_entire_position_removes_it() -> None:
    broker = _broker()
    broker.place_order(_buy("AAPL", 5))
    broker.place_order(_sell("AAPL", 5))
    assert broker.positions() == []


def test_insufficient_cash_raises() -> None:
    with pytest.raises(BrokerError, match="Insufficient cash"):
        _broker(cash=500.0).place_order(_buy("AAPL", 10))


def test_insufficient_position_raises() -> None:
    with pytest.raises(BrokerError, match="Insufficient position"):
        _broker().place_order(_sell("AAPL", 1))


def test_non_positive_quantity_raises() -> None:
    with pytest.raises(BrokerError, match="positive"):
        _broker().place_order(_buy("AAPL", 0))


def test_limit_buy_fills_when_marketable() -> None:
    broker = _broker(prices={"AAPL": 90.0})
    fill = broker.place_order(_buy("AAPL", 1, order_type=OrderType.LIMIT, limit_price=95.0))
    assert fill.price == 95.0  # fills at the limit price


def test_limit_buy_not_marketable_raises() -> None:
    broker = _broker(prices={"AAPL": 110.0})
    with pytest.raises(BrokerError, match="not marketable"):
        broker.place_order(_buy("AAPL", 1, order_type=OrderType.LIMIT, limit_price=100.0))


def test_ibkr_skeleton_refuses_to_trade() -> None:
    broker = IBKRBroker()
    with pytest.raises(BrokerError, match="not enabled"):
        broker.place_order(_buy("AAPL", 1))
    with pytest.raises(BrokerError):
        broker.positions()
    with pytest.raises(BrokerError):
        broker.cash()
