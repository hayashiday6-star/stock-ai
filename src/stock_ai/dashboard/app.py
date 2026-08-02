"""stock-ai ダッシュボード（日本語UI）。

コードを書かずに、データ取得からスクリーニング・スコアリング・バックテスト・
AI分析・通知までをブラウザ画面から操作できます。

起動方法::

    uv sync --extra data --extra db --extra dashboard
    uv run streamlit run src/stock_ai/dashboard/app.py

（Windows では同梱の「ダッシュボード起動.bat」をダブルクリックでもOK）

このファイルは画面描画に専念し、データ処理は stock_ai.dashboard.data に置いています。
"""

from __future__ import annotations

import datetime as dt

import streamlit as st

from stock_ai.ai.analysis import analyze_sentiment
from stock_ai.ai.analysis import summarize as ai_summarize
from stock_ai.ai.factory import get_ai_provider
from stock_ai.config.settings import get_settings
from stock_ai.core.exceptions import NotificationError
from stock_ai.dashboard import data
from stock_ai.database.engine import Database
from stock_ai.notification.factory import get_notifier
from stock_ai.screening.base import All, Condition
from stock_ai.screening.conditions import (
    MaxPBR,
    MaxPER,
    MinDividendYield,
    MinROE,
)


@st.cache_resource
def _database() -> Database:
    """作成済みのデータベース（1プロセス1つ）を返す。"""
    database = Database()
    database.create_all()
    return database


def _parse_symbols(text: str) -> list[str]:
    """カンマ・空白・改行区切りの銘柄入力を大文字リストに整形する。"""
    raw = text.replace(",", " ").replace("\n", " ")
    return [token.strip().upper() for token in raw.split(" ") if token.strip()]


# --- 各画面 ---------------------------------------------------------------


def _page_data(database: Database) -> None:
    st.header("📥 データ取得")
    st.caption("まずはここで銘柄の株価と財務データを取り込みます。")

    col1, col2 = st.columns(2)
    with col1:
        symbols_text = st.text_input(
            "銘柄コード（スペース区切り）",
            value="AAPL MSFT",
            help="例: AAPL MSFT / 日本株は 7203 など",
        )
        source_label = st.radio("市場・データ元", ["米国株 (yfinance)", "日本株 (J-Quants)"])
        source = "yfinance" if source_label.startswith("米国") else "jquants"
    with col2:
        start = st.date_input("開始日", value=dt.date.today() - dt.timedelta(days=365))
        end = st.date_input("終了日", value=dt.date.today())

    symbols = _parse_symbols(symbols_text)

    c1, c2 = st.columns(2)
    with c1:
        if st.button("📈 株価を取得", type="primary", use_container_width=True):
            if not symbols:
                st.warning("銘柄コードを入力してください。")
            else:
                with st.spinner("株価を取得中..."):
                    results = data.ingest_prices(database, symbols, source, start, end)
                st.dataframe(data.results_frame(results), use_container_width=True)
    with c2:
        if st.button("🧾 財務を取得", use_container_width=True):
            if not symbols:
                st.warning("銘柄コードを入力してください。")
            else:
                with st.spinner("財務データを取得中..."):
                    results = data.ingest_fundamentals(database, symbols, source)
                st.dataframe(data.results_frame(results), use_container_width=True)
        if source == "jquants":
            st.caption("日本株の財務は売上・利益・ROEのみ（PER/PBRは株価が必要なため未取得）。")

    st.divider()
    st.subheader("取り込み済みデータ")
    overview = data.stored_overview(database)
    if overview.empty:
        st.info("まだデータがありません。上のボタンで取得してください。")
    else:
        st.dataframe(overview, use_container_width=True)


def _page_ranking(database: Database) -> None:
    st.header("🏆 スコアランキング")
    st.caption("財務と価格モメンタムから 0〜100 点で総合評価します（高いほど良い）。")
    symbols = data.available_symbols(database)
    if not symbols:
        st.info("先に「データ取得」で株価・財務を取り込んでください。")
        return
    table = data.score_table(database, symbols)
    st.dataframe(table, use_container_width=True)
    if "score" in table.columns and not table.empty:
        st.bar_chart(table.set_index("symbol")["score"])


def _build_condition(
    use_roe: bool,
    roe: float,
    use_per: bool,
    per: float,
    use_pbr: bool,
    pbr: float,
    use_div: bool,
    div: float,
) -> Condition | None:
    conditions: list[Condition] = []
    if use_roe:
        conditions.append(MinROE(roe))
    if use_per:
        conditions.append(MaxPER(per))
    if use_pbr:
        conditions.append(MaxPBR(pbr))
    if use_div:
        conditions.append(MinDividendYield(div))
    if not conditions:
        return None
    return conditions[0] if len(conditions) == 1 else All(*conditions)


def _page_screen(database: Database) -> None:
    st.header("🔍 スクリーニング")
    st.caption("条件を有効にして、合致する銘柄を絞り込みます（すべて AND 条件）。")

    col1, col2 = st.columns(2)
    with col1:
        use_roe = st.checkbox("ROE 下限", value=True)
        roe = st.number_input("ROE >=", value=0.15, step=0.01, format="%.2f")
        use_per = st.checkbox("PER 上限", value=True)
        per = st.number_input("PER <=", value=20.0, step=1.0)
    with col2:
        use_pbr = st.checkbox("PBR 上限", value=False)
        pbr = st.number_input("PBR <=", value=3.0, step=0.5)
        use_div = st.checkbox("配当利回り 下限", value=False)
        div = st.number_input("配当利回り >=", value=0.02, step=0.01, format="%.2f")

    if st.button("🔎 スクリーニング実行", type="primary"):
        condition = _build_condition(use_roe, roe, use_per, per, use_pbr, pbr, use_div, div)
        if condition is None:
            st.warning("条件を1つ以上有効にしてください。")
            return
        st.caption(f"条件: {condition}")
        report = data.screen_table(database, condition)
        st.success(f"{len(report)} 銘柄が合致しました。")
        st.dataframe(report, use_container_width=True)
        if not report.empty:
            st.download_button(
                "CSVをダウンロード",
                report.to_csv(index=False).encode("utf-8-sig"),
                file_name="screen.csv",
                mime="text/csv",
            )


def _page_backtest(database: Database) -> None:
    st.header("📊 バックテスト")
    st.caption("移動平均クロス戦略と「買って持ち続ける」を比較します。翌日寄付約定・日次時価評価。")
    symbols = data.available_symbols(database)
    if not symbols:
        st.info("先に「データ取得」で株価を取り込んでください。")
        return
    symbol = st.selectbox("銘柄", symbols)
    strategy_labels = {
        "移動平均クロス (sma)": "sma",
        "200日線より上 (sma200)": "sma200",
        "MACDクロス (macd)": "macd",
        "RSI逆張り (rsi)": "rsi",
    }
    strategy_label = st.selectbox("戦略", list(strategy_labels.keys()))
    strategy = strategy_labels[strategy_label]
    col1, col2 = st.columns(2)
    with col1:
        fast = st.number_input("短期移動平均（日）", min_value=2, value=20)
    with col2:
        slow = st.number_input("長期移動平均・トレンド（日）", min_value=3, value=50)
    if strategy == "sma" and fast >= slow:
        st.warning("短期は長期より小さくしてください。")
        return
    if st.button("▶️ バックテスト実行", type="primary"):
        equity, metrics = data.backtest_comparison(
            database, symbol, int(fast), int(slow), strategy=strategy
        )
        st.subheader("資産推移")
        st.line_chart(equity)
        st.subheader("成績")
        st.dataframe(metrics, use_container_width=True)


def _page_ai() -> None:
    st.header("🤖 AI分析")
    st.caption("ニュースやIRの文章を貼り付けて、要約とセンチメントを得ます。")
    provider = st.selectbox(
        "AIプロバイダ",
        ["dummy", "claude", "openai", "gemini"],
        help="dummy はAPIキー不要のテスト用。claude/openai/gemini は .env にAPIキーが必要。",
    )
    text = st.text_area("分析する文章", height=180)
    if st.button("🧠 分析する", type="primary") and text.strip():
        try:
            ai = get_ai_provider(provider, get_settings())
            with st.spinner("AIが分析中..."):
                summary = ai_summarize(ai, text)
                sentiment = analyze_sentiment(ai, text)
            st.subheader("要約")
            st.write(summary)
            st.subheader("センチメント")
            st.metric("判定", sentiment)
        except Exception as exc:  # surface provider/config errors to the user
            st.error(f"分析に失敗しました: {exc}")


def _page_notify() -> None:
    st.header("🔔 通知テスト")
    st.caption("メッセージを各チャネルへ送信します（console は画面表示のみ・安全）。")
    channel = st.selectbox("送信先", ["console", "discord", "telegram", "line"])
    message = st.text_input("メッセージ", value="stock-ai テスト通知")
    if st.button("📨 送信", type="primary"):
        try:
            get_notifier(channel, get_settings()).send(message)
            st.success("送信しました（console は下のログ/ターミナルに表示）。")
        except NotificationError as exc:
            st.error(f"送信に失敗しました: {exc}")


# --- エントリ --------------------------------------------------------------


def main() -> None:
    """ダッシュボードを描画する。"""
    st.set_page_config(page_title="stock-ai ダッシュボード", page_icon="📈", layout="wide")
    st.title("📈 stock-ai ダッシュボード")

    database = _database()
    pages = {
        "📥 データ取得": lambda: _page_data(database),
        "🏆 ランキング": lambda: _page_ranking(database),
        "🔍 スクリーニング": lambda: _page_screen(database),
        "📊 バックテスト": lambda: _page_backtest(database),
        "🤖 AI分析": _page_ai,
        "🔔 通知テスト": _page_notify,
    }
    choice = st.sidebar.radio("メニュー", list(pages.keys()))
    st.sidebar.divider()
    st.sidebar.caption("使い方: まず「データ取得」→ その後 各画面で分析します。")
    pages[choice]()


if __name__ == "__main__":
    main()
