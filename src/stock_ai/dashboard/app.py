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
from stock_ai.core.exceptions import BacktestError, NotificationError
from stock_ai.dashboard import data
from stock_ai.data.types import Importance
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
        if st.button("📈 株価を取得", type="primary", width="stretch"):
            if not symbols:
                st.warning("銘柄コードを入力してください。")
            else:
                with st.spinner("株価を取得中..."):
                    results = data.ingest_prices(database, symbols, source, start, end)
                st.dataframe(data.results_frame(results), width="stretch")
    with c2:
        if st.button("🧾 財務を取得", width="stretch"):
            if not symbols:
                st.warning("銘柄コードを入力してください。")
            else:
                with st.spinner("財務データを取得中..."):
                    results = data.ingest_fundamentals(database, symbols, source)
                st.dataframe(data.results_frame(results), width="stretch")
        if source == "jquants":
            st.caption("日本株の財務は売上・利益・ROEのみ（PER/PBRは株価が必要なため未取得）。")

    st.divider()
    st.subheader("取り込み済みデータ")
    overview = data.stored_overview(database)
    if overview.empty:
        st.info("まだデータがありません。上のボタンで取得してください。")
    else:
        st.dataframe(overview, width="stretch")


def _page_ranking(database: Database) -> None:
    st.header("🏆 スコアランキング")
    st.caption("財務と価格モメンタムから 0〜100 点で総合評価します（高いほど良い）。")
    symbols = data.available_symbols(database)
    if not symbols:
        st.info("先に「データ取得」で株価・財務を取り込んでください。")
        return
    table = data.score_table(database, symbols)
    st.dataframe(table, width="stretch")
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
        st.dataframe(report, width="stretch")
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
        st.dataframe(metrics, width="stretch")


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


def _fx_rates(text: str) -> dict[str, float]:
    """Parse "JPY=0.0066" style input into a rate map.

    Pinning the rate keeps a report reproducible; leaving it blank fetches live
    rates, which makes the same screen give slightly different numbers each run.
    """
    rates: dict[str, float] = {}
    for token in text.replace(",", " ").split():
        currency, _, value = token.partition("=")
        try:
            rates[currency.strip().upper()] = float(value)
        except ValueError:
            st.warning(f"為替レート '{token}' を読めませんでした（例: JPY=0.0066）。")
    return rates


def _page_cross_market(database: Database) -> None:
    st.header("🌏 日米統合ランキング")
    st.caption(
        "スコアは無次元の比率で構成されるため元から市場をまたいで比較できます。"
        "時価総額だけは通貨建てなので、基準通貨に換算して表示します。"
    )

    col1, col2, col3 = st.columns(3)
    with col1:
        preset = st.selectbox(
            "ファクター",
            ["default", "tenbagger"],
            format_func=lambda k: "標準" if k == "default" else "テンバガー候補（小型成長）",
        )
    with col2:
        base = st.selectbox("基準通貨", ["USD", "JPY"])
    with col3:
        fx_text = st.text_input("為替を固定（任意）", value="JPY=0.0066", help="例: JPY=0.0066")

    cap_limit = st.number_input(
        "時価総額の上限（基準通貨、0 で無制限）", min_value=0.0, value=0.0, step=1e8, format="%.0f"
    )
    if preset == "tenbagger":
        st.info(
            "テンバガー候補は財務時系列を読みます（CLI の `statements` で取得）。"
            "予測ではなくヒューリスティックなので、下の「ファクター検証」で"
            "有効性を確かめてから使ってください。"
        )

    if st.button("🌏 ランキングを作成", type="primary"):
        with st.spinner("集計中..."):
            frame = data.cross_market_table(
                database,
                base=base,
                rates=_fx_rates(fx_text),
                preset=preset,
                max_market_cap=cap_limit or None,
            )
        if frame.empty:
            st.warning("該当なし。まず「データ取得」で銘柄を取り込んでください。")
            return
        st.dataframe(frame, width="stretch")


def _page_portfolio(database: Database) -> None:
    st.header("💼 ポートフォリオ")
    st.caption("保有を登録すると、セクター比率と実現リスクを算出します。")

    with st.expander("保有を登録・更新する", expanded=False):
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            symbol = st.text_input("銘柄", value="AAPL")
        with col2:
            quantity = st.number_input("数量（0 で削除）", min_value=0.0, value=100.0)
        with col3:
            cost = st.number_input("取得単価", min_value=0.0, value=120.0)
        with col4:
            market = st.selectbox("市場", ["US", "JP"])
        if st.button("💾 登録"):
            data.set_position(database, symbol.strip().upper(), quantity, cost, market=market)
            st.success(f"{symbol.upper()} を更新しました。")

    col1, col2 = st.columns(2)
    with col1:
        base = st.selectbox("基準通貨", ["USD", "JPY"], key="pf_base")
    with col2:
        fx_text = st.text_input("為替を固定（任意）", value="JPY=0.0066", key="pf_fx")

    analysis = data.portfolio_view(database, base=base, rates=_fx_rates(fx_text))
    if not analysis.positions:
        st.info(
            "価格のある保有がありません。上で登録し、「データ取得」で株価を取り込んでください。"
        )
        return

    st.dataframe(data.positions_frame(analysis), width="stretch")

    col1, col2, col3, col4 = st.columns(4)
    total = analysis.unrealized_return
    col1.metric(f"評価額 ({base})", f"{analysis.total_value:,.0f}")
    col2.metric("含み損益", "—" if total is None else f"{total:+.2%}")
    col3.metric(
        "年率ボラティリティ",
        "—" if analysis.annual_volatility is None else f"{analysis.annual_volatility:.2%}",
    )
    col4.metric(
        "実効銘柄数",
        "—" if analysis.effective_positions is None else f"{analysis.effective_positions:.2f}",
        help="ヘルフィンダール集中度の逆数。等ウェイト換算で何銘柄ぶんの分散か。",
    )

    st.subheader("セクター比率")
    st.bar_chart(data.exposure_frame(analysis))

    if analysis.correlations is not None:
        st.subheader("相関")
        st.dataframe(analysis.correlations.round(2), width="stretch")
    if analysis.unpriced:
        st.warning("株価が未取得のため比率から除外: " + ", ".join(analysis.unpriced))
    st.caption(
        "期待リターンは意図的に出していません。過去平均の年率化は推定誤差が"
        "シグナルを上回るため、実績値のみを表示しています。"
    )


def _page_watchlist(database: Database) -> None:
    st.header("👀 監視リスト")
    st.caption("登録銘柄の開示・ニュースをAIが判定し、重要なものだけを抽出します。")

    with st.expander("銘柄を追加する", expanded=False):
        col1, col2, col3 = st.columns(3)
        with col1:
            symbol = st.text_input("銘柄", value="4593.T", key="w_sym")
        with col2:
            importance = st.selectbox("通知しきい値", ["high", "medium", "low"], index=1)
        with col3:
            market = st.selectbox("市場", ["JP", "US"], key="w_mkt")
        note = st.text_input("メモ（任意）", value="", key="w_note")
        col_add, col_del = st.columns(2)
        if col_add.button("➕ 追加"):
            data.add_watch(
                database,
                symbol.strip().upper(),
                note or None,
                Importance(importance),
                market=market,
            )
            st.success(f"{symbol.upper()} を監視に追加しました。")
        if col_del.button("🗑 削除"):
            removed = data.remove_watch(database, symbol.strip().upper())
            st.success("削除しました。") if removed else st.info("登録がありません。")

    frame = data.watchlist_frame(database)
    if frame.empty:
        st.info("監視リストが空です。上で銘柄を追加してください。")
        return
    st.dataframe(frame, width="stretch")

    st.subheader("開示チェック")
    col1, col2, col3 = st.columns(3)
    with col1:
        provider = st.selectbox("AIプロバイダ", ["dummy", "claude", "openai", "gemini"], key="w_ai")
    with col2:
        feed = st.selectbox(
            "開示ソース",
            ["all", "edinet", "news"],
            format_func=lambda k: {
                "all": "両方",
                "edinet": "EDINET（日本の法定開示）",
                "news": "ニュース",
            }[k],
        )
    with col3:
        lookback = st.number_input("EDINETを遡る日数", min_value=1, max_value=30, value=7)

    if st.button("🔎 チェックする", type="primary"):
        with st.spinner("開示を取得して判定中..."):
            result = data.run_monitor(database, provider, feed=feed, lookback_days=int(lookback))
        st.write(f"新規 {result.checked} 件を判定、既報 {result.skipped} 件をスキップ。")
        if result.unjudged:
            st.warning(
                f"{result.unjudged} 件はAIプロバイダの失敗により判定できませんでした。"
                "既読にはしていないので次回再試行されます。"
            )
        if result.alerts:
            for alert in sorted(result.alerts, key=lambda a: a.importance.rank, reverse=True):
                level = alert.importance.value.upper()
                st.markdown(f"**[{level}] {alert.entry.symbol}** — {alert.disclosure.title}")
                if alert.summary:
                    st.caption(alert.summary)
                if alert.disclosure.url:
                    st.caption(alert.disclosure.url)
        else:
            st.info("しきい値を超える開示はありませんでした。")
    st.caption(
        "ニュースソースは日本の小型株でほぼ空です。日本株は EDINET（要 EDINET_API_KEY）"
        "を使ってください。適時開示（TDnet）は別途アダプタが必要です。"
    )


def _page_factor_test(database: Database) -> None:
    st.header("🧪 ファクター検証")
    st.caption(
        "指定日にランキングを作り、その後の実リターンを等ウェイトの母集団と比較します。"
        "スコアに情報がなければ母集団に勝てません。"
    )

    col1, col2, col3 = st.columns(3)
    with col1:
        formation = st.date_input("形成日", value=dt.date.today() - dt.timedelta(days=400))
    with col2:
        horizon = st.number_input("保有営業日数", min_value=20, max_value=1000, value=252)
    with col3:
        preset = st.selectbox(
            "ファクター",
            ["tenbagger", "default"],
            format_func=lambda k: "テンバガー候補" if k == "tenbagger" else "標準",
            key="ft_preset",
        )

    if st.button("🧪 検証する", type="primary"):
        try:
            with st.spinner("ランキングと将来リターンを計算中..."):
                result = data.factor_test(
                    database,
                    formation=formation,
                    preset=preset,
                    horizon_days=int(horizon),
                )
        except BacktestError as exc:
            st.error(str(exc))
            return

        st.dataframe(result.to_frame(), width="stretch")
        col1, col2, col3 = st.columns(3)
        col1.metric("母集団（等ウェイト）", f"{result.universe_return:+.2%}")
        excess = result.excess_return
        col2.metric("上位バケットの超過", "—" if excess is None else f"{excess:+.2%}")
        t_stat = result.spread_t_stat
        col3.metric("上位−下位 t値", "—" if t_stat is None else f"{t_stat:+.2f}")

        if t_stat is None:
            st.warning("銘柄数が少なく、シグナルとノイズを区別できません。")
        elif not result.is_significant:
            st.warning(
                f"t = {t_stat:+.2f} は 2σ の内側で、偶然と区別がつきません。"
                "銘柄数が少ないと数%の超過は普通に発生します。"
            )
        else:
            st.success(f"t = {t_stat:+.2f}（2σ を超えています）。")
        if not result.is_monotonic:
            st.warning("バケット間でリターンが単調に減衰していません。順序の情報量は乏しいです。")
        st.caption(
            "母集団はローカルDBにある銘柄のみで、上場廃止銘柄を含みません"
            "（生存者バイアス）。スコアを否定する材料にはなりますが、"
            "有効性の証明にはなりません。"
        )


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
        "🌏 日米統合ランキング": lambda: _page_cross_market(database),
        "🔍 スクリーニング": lambda: _page_screen(database),
        "💼 ポートフォリオ": lambda: _page_portfolio(database),
        "👀 監視リスト": lambda: _page_watchlist(database),
        "📊 バックテスト": lambda: _page_backtest(database),
        "🧪 ファクター検証": lambda: _page_factor_test(database),
        "🤖 AI分析": _page_ai,
        "🔔 通知テスト": _page_notify,
    }
    choice = st.sidebar.radio("メニュー", list(pages.keys()))
    st.sidebar.divider()
    st.sidebar.caption("使い方: まず「データ取得」→ その後 各画面で分析します。")
    pages[choice]()


if __name__ == "__main__":
    main()
