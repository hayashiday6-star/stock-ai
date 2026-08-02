"""Streamlit dashboard for stock-ai.

Run with::

    uv sync --extra dashboard
    uv run streamlit run src/stock_ai/dashboard/app.py

Rendering only — all data access lives in :mod:`stock_ai.dashboard.data`.
"""

from __future__ import annotations

import streamlit as st

from stock_ai.ai.analysis import analyze_sentiment
from stock_ai.ai.analysis import summarize as ai_summarize
from stock_ai.ai.factory import get_ai_provider
from stock_ai.config.settings import get_settings
from stock_ai.dashboard import data
from stock_ai.database.engine import Database


def _database() -> Database:
    """Create the on-disk database and ensure the schema exists."""
    database = Database()
    database.create_all()
    return database


def _rankings(database: Database) -> None:
    st.header("Rankings")
    symbols = data.available_symbols(database)
    if not symbols:
        st.info("No data yet. Run `stock-ai fetch` and `stock-ai fundamentals` first.")
        return
    st.dataframe(data.score_table(database, symbols), width="stretch")


def _backtest(database: Database) -> None:
    st.header("Backtest")
    symbols = data.available_symbols(database)
    if not symbols:
        st.info("No price data yet. Run `stock-ai fetch` first.")
        return
    symbol = st.selectbox("Symbol", symbols)
    fast = st.number_input("Fast SMA", min_value=2, value=20)
    slow = st.number_input("Slow SMA", min_value=3, value=50)
    if fast >= slow:
        st.warning("Fast window must be smaller than slow.")
        return
    equity, metrics = data.backtest_comparison(database, symbol, int(fast), int(slow))
    st.line_chart(equity)
    st.dataframe(metrics, width="stretch")


def _ai_analysis() -> None:
    st.header("AI Analysis")
    provider = st.selectbox("Provider", ["dummy", "claude", "openai", "gemini"])
    text = st.text_area("Text (IR excerpt, news, ...)")
    if st.button("Analyze") and text.strip():
        ai = get_ai_provider(provider, get_settings())
        st.subheader("Summary")
        st.write(ai_summarize(ai, text))
        st.subheader("Sentiment")
        st.write(analyze_sentiment(ai, text))


def main() -> None:
    """Render the dashboard."""
    st.set_page_config(page_title="stock-ai", layout="wide")
    st.title("stock-ai")

    database = _database()
    section = st.sidebar.radio("Section", ["Rankings", "Backtest", "AI Analysis"])
    if section == "Rankings":
        _rankings(database)
    elif section == "Backtest":
        _backtest(database)
    else:
        _ai_analysis()


if __name__ == "__main__":
    main()
