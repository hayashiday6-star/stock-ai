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
from pathlib import Path

import pandas as pd
import streamlit as st

from stock_ai.ai.analysis import analyze_sentiment
from stock_ai.ai.analysis import summarize as ai_summarize
from stock_ai.ai.anthropic_provider import DEFAULT_MODEL as ANTHROPIC_DEFAULT_MODEL
from stock_ai.ai.factory import get_ai_provider
from stock_ai.ai.pricing import UsageLedger
from stock_ai.config.settings import get_settings
from stock_ai.core.exceptions import BacktestError, NotificationError
from stock_ai.dashboard import data
from stock_ai.data.types import Importance
from stock_ai.database.engine import Database
from stock_ai.notification.factory import get_notifier
from stock_ai.screening.base import All, Condition
from stock_ai.screening.conditions import (
    MaxPayoutRatio,
    MaxPBR,
    MaxPER,
    MinConsecutiveDividendIncreases,
    MinDividendGrowth,
    MinDividendYield,
    MinProfitGrowth,
    MinRevenueGrowth,
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


def _repo_commit() -> str:
    """Return the commit currently checked out on disk, or "不明"."""
    import subprocess

    root = Path(__file__).resolve().parents[3]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "不明"
    return result.stdout.strip() or "不明"


#: The commit as it was when this process imported the module - which is the
#: code actually running. Reading it fresh on every render was a mistake: after
#: a git pull the sidebar showed the new commit while Python went on executing
#: the modules it had already imported, so the version line confirmed an update
#: that had not taken effect. Streamlit reloads the app file, not imported
#: modules, so a pull genuinely requires restarting the process.
_LOADED_COMMIT = _repo_commit()


def _module_installed(module: str) -> bool:
    """Whether ``module`` can be imported, without importing it."""
    import importlib.util

    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):  # a broken or partially removed install
        return False


def _sidebar_status(database: Database) -> None:
    """バージョンと、保存済みデータの中身を出す。"""
    st.sidebar.divider()
    on_disk = _repo_commit()
    st.sidebar.caption(f"コード: `{_LOADED_COMMIT}`")
    if on_disk != _LOADED_COMMIT and "不明" not in (on_disk, _LOADED_COMMIT):
        st.sidebar.error(
            f"ディスク上は `{on_disk}` に更新されていますが、動いているのは "
            f"`{_LOADED_COMMIT}` です。**ダッシュボードを再起動してください** "
            "(黒いウィンドウで Ctrl+C → .bat を再実行)。Streamlit は画面の"
            "ファイルだけを読み直し、読み込み済みのモジュールは差し替えません。"
        )

    # Mirrors what `stock-ai info` reports. A key that is set and an SDK that
    # is missing look identical from here otherwise - the configuration reads
    # as complete and cannot make a single call.
    settings = get_settings()
    model = settings.anthropic_model or ANTHROPIC_DEFAULT_MODEL
    if _module_installed("anthropic"):
        st.sidebar.caption(f"AIモデル: `{model}`")
    else:
        st.sidebar.warning(
            "anthropic パッケージが入っていません。AI機能は動きません"
            "（キーの問題ではありません）。`uv sync` で入ります。"
        )

    counts = data.stored_counts(database)
    st.sidebar.caption(
        f"銘柄 {counts['securities']:,} / 株価あり {counts['with_prices']:,} / "
        f"財務あり {counts['with_statements']:,} / 指標あり {counts['with_fundamentals']:,}"
    )
    if counts["securities"] and not counts["with_fundamentals"]:
        st.sidebar.warning(
            "指標(PER/PBR等)が0件です。`bulk-fetch --what statements --segment stored` "
            "を実行すると埋まります。"
        )


# --- 各画面 ---------------------------------------------------------------


def _page_data(database: Database) -> None:
    st.header("📥 データ取得")
    st.caption("まずはここで銘柄の株価と財務データを取り込みます。")

    # JP_PRICE_SOURCE / JP_STATEMENT_SOURCE が決める。CLI の bulk-fetch や screen
    # と同じ設定を見る - ここだけ J-Quants に固定されていると、解約後にこの画面
    # だけ気付かれずに壊れる。
    settings = get_settings()
    price_source = settings.jp_price_source.strip().lower()
    statement_source = settings.jp_statement_source.strip().lower()

    col1, col2 = st.columns(2)
    with col1:
        symbols_text = st.text_input(
            "銘柄コード（スペース区切り）",
            value="AAPL MSFT",
            help="例: AAPL MSFT / 日本株は 7203 など",
        )
        market_label = st.radio("市場", ["米国株 (yfinance)", f"日本株 ({price_source})"])
        is_jp = market_label.startswith("日本")
    with col2:
        start = st.date_input("開始日", value=dt.date.today() - dt.timedelta(days=365))
        end = st.date_input("終了日", value=dt.date.today())

    if is_jp and price_source != statement_source:
        st.caption(
            f"価格: {price_source} ／ 財務: {statement_source}"
            "（.env の JP_PRICE_SOURCE / JP_STATEMENT_SOURCE で決まります）"
        )

    symbols = _parse_symbols(symbols_text)
    price_fetch_source = price_source if is_jp else "yfinance"
    statement_fetch_source = statement_source if is_jp else "yfinance"

    c1, c2 = st.columns(2)
    with c1:
        if st.button("📈 株価を取得", type="primary", width="stretch"):
            if not symbols:
                st.warning("銘柄コードを入力してください。")
            else:
                with st.spinner("株価を取得中..."):
                    results = data.ingest_prices(database, symbols, price_fetch_source, start, end)
                st.dataframe(data.results_frame(results), width="stretch")
    with c2:
        if st.button("🧾 財務を取得", width="stretch"):
            if not symbols:
                st.warning("銘柄コードを入力してください。")
            else:
                with st.spinner("財務データを取得中..."):
                    results = data.ingest_fundamentals(database, symbols, statement_fetch_source)
                st.dataframe(data.results_frame(results), width="stretch")
        if statement_fetch_source == "jquants":
            st.caption("日本株の財務は売上・利益・ROEのみ（PER/PBRは株価が必要なため未取得）。")
        elif statement_fetch_source == "edinet":
            st.caption("EDINET経由はEPS・BPS・営業利益が空欄になります（1株配当は取得できます）。")

    st.divider()
    st.subheader("🔄 一括更新")
    st.caption(
        "保存済みの銘柄を全部まとめて更新します。市場は銘柄ごとにDBの記録から決まる"
        "ので、米国株・日本株が混ざっていてもそれぞれ正しい取得元に振り分けられます。"
    )
    stored_symbols = data.available_symbols(database)
    if not stored_symbols:
        st.info("まだ銘柄が保存されていません。上でまず何件か取得してください。")
    else:
        st.caption(
            f"対象: {len(stored_symbols)} 銘柄。数千件規模の全市場取得はここではなく "
            "CLI の `bulk-fetch`（レジューム・スロットリング付き）を使ってください。"
        )
        b1, b2 = st.columns(2)
        with b1:
            bulk_prices = st.checkbox("株価を更新", value=True, key="bulk_prices")
        with b2:
            bulk_fundamentals = st.checkbox("財務を更新", value=False, key="bulk_fundamentals")

        if st.button("🔄 保存済み銘柄をまとめて更新", width="stretch"):
            if not bulk_prices and not bulk_fundamentals:
                st.warning("株価・財務のどちらかは選んでください。")
            else:
                bar = st.progress(0.0)
                status = st.empty()

                def _on_progress(done: int, total: int, symbol: str) -> None:
                    bar.progress(done / total if total else 1.0)
                    status.caption(f"{done}/{total}: {symbol}")

                by_dataset = data.bulk_update_stored(
                    database,
                    fetch_prices=bulk_prices,
                    fetch_fundamentals=bulk_fundamentals,
                    progress=_on_progress,
                )
                status.empty()
                labels = {"prices": "📈 株価", "fundamentals": "🧾 財務"}
                for key, label in labels.items():
                    rows = by_dataset.get(key)
                    if rows is None:
                        continue
                    ok = sum(1 for r in rows if r.ok)
                    st.write(f"**{label}**: {ok}/{len(rows)} 件成功")
                    st.dataframe(data.results_frame(rows), width="stretch")

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


def _build_condition(parts: list[tuple[bool, Condition]]) -> Condition | None:
    """Combine the enabled conditions with AND, or ``None`` if none are on.

    Takes pairs rather than a parameter per control: the screen has grown from
    four criteria to nine, and a positional signature that long is where the
    wrong threshold gets passed to the wrong condition.
    """
    conditions = [condition for enabled, condition in parts if enabled]
    if not conditions:
        return None
    return conditions[0] if len(conditions) == 1 else All(*conditions)


def _page_screen(database: Database) -> None:
    st.header("🔍 スクリーニング")
    st.caption("条件を有効にして、合致する銘柄を絞り込みます（すべて AND 条件）。")

    st.subheader("割安さ（バリュエーション）")
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

    st.subheader("成長性・配当の継続性")
    st.caption(
        "これらは保存済みの財務時系列（statements）を読みます。"
        "取得していない銘柄は、条件を満たさないものとして除外されます。"
    )
    years = st.slider("成長率を何期分さかのぼって比較するか", 1, 5, 1)
    col3, col4 = st.columns(2)
    with col3:
        use_rev = st.checkbox("増収率 下限", value=False)
        rev = st.number_input("増収率 >=", value=0.10, step=0.05, format="%.2f")
        use_profit = st.checkbox("増益率 下限", value=False)
        profit = st.number_input("増益率 >=", value=0.10, step=0.05, format="%.2f")
        use_payout = st.checkbox("配当性向 上限", value=False)
        payout = st.number_input("配当性向 <=", value=0.60, step=0.05, format="%.2f")
    with col4:
        use_divgrow = st.checkbox("増配率 下限", value=False)
        divgrow = st.number_input(
            "増配率 >=",
            value=0.0,
            step=0.05,
            format="%.2f",
            help="0 より大きくすると「実際に増配した」銘柄のみになります。",
        )
        use_streak = st.checkbox("連続増配年数 下限", value=False)
        streak = st.number_input("連続増配 >= (年)", value=3, step=1, min_value=1)

    growth_parts = [
        (use_rev, MinRevenueGrowth(rev, years=years)),
        (use_profit, MinProfitGrowth(profit, years=years)),
        (use_divgrow, MinDividendGrowth(divgrow, years=years)),
        (use_streak, MinConsecutiveDividendIncreases(int(streak))),
        (use_payout, MaxPayoutRatio(payout)),
    ]
    needs_statements = any(enabled for enabled, _ in growth_parts)

    if st.button("🔎 スクリーニング実行", type="primary"):
        condition = _build_condition(
            [
                (use_roe, MinROE(roe)),
                (use_per, MaxPER(per)),
                (use_pbr, MaxPBR(pbr)),
                (use_div, MinDividendYield(div)),
                *growth_parts,
            ]
        )
        if condition is None:
            st.warning("条件を1つ以上有効にしてください。")
            return
        st.caption(f"条件: {condition}")
        # The statement series is only loaded when something reads it: attaching
        # it costs a query per symbol, and at 1,500 symbols that is the
        # difference between an instant screen and a slow one.
        report = data.screen_table(database, condition, load_statements=needs_statements)
        st.success(f"{len(report)} 銘柄が合致しました。")
        if report.empty:
            st.info(
                "0 件は答えのひとつです（条件が厳しすぎるだけかもしれません）。"
                "条件を1つずつ外して、どれが効いているか確かめてください。"
            )
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
    # The windows shown must belong to the strategy selected. One shared "長期"
    # box defaulting to 50 is what let sma200 quietly run a 50-day filter.
    fast, slow, window = 20, 50, 200
    if strategy == "sma":
        col1, col2 = st.columns(2)
        with col1:
            fast = int(st.number_input("短期移動平均（日）", min_value=2, value=20))
        with col2:
            slow = int(st.number_input("長期移動平均（日）", min_value=3, value=50))
        if fast >= slow:
            st.warning("短期は長期より小さくしてください。")
            return
    elif strategy == "sma200":
        window = int(st.number_input("トレンド（日）", min_value=3, value=200))
    if st.button("▶️ バックテスト実行", type="primary"):
        equity, metrics = data.backtest_comparison(
            database, symbol, fast, slow, strategy=strategy, window=window
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
    if provider != "dummy":
        st.caption(
            "この分析は2回モデルを呼びます（要約とセンチメント）。"
            "実際に使った額は結果の下に出ます。"
        )
    if st.button("🧠 分析する", type="primary") and text.strip():
        ai = get_ai_provider(provider, get_settings())
        try:
            with st.spinner("AIが分析中..."):
                summary = ai_summarize(ai, text)
                sentiment = analyze_sentiment(ai, text)
            st.subheader("要約")
            st.write(summary)
            st.subheader("センチメント")
            st.metric("判定", sentiment)
        except Exception as exc:  # surface provider/config errors to the user
            st.error(f"分析に失敗しました: {exc}")
        finally:
            # A call that failed after the model answered is still billed, and
            # that is the run where the reader most wants the figure.
            _render_spend(getattr(ai, "usage", None))
    st.caption(
        "要約は元の文章に忠実であることしか保証できません。"
        "事実確認の仕組みはないので、数値は原典で確認してください。"
        "また、要約は入力と同じ言語で返ります（日本語を入れれば日本語）。"
    )


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
    col2.metric("含み損益", "-" if total is None else f"{total:+.2%}")
    col3.metric(
        "年率ボラティリティ",
        "-" if analysis.annual_volatility is None else f"{analysis.annual_volatility:.2%}",
    )
    col4.metric(
        "実効銘柄数",
        "-" if analysis.effective_positions is None else f"{analysis.effective_positions:.2f}",
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


def _money(value: float | None) -> str:
    """Render dollars, or a dash when the model has no cached price."""
    if value is None:
        return "—"
    return f"${value:,.4f}"


def _render_spend(usage: UsageLedger | None) -> None:
    """Say what the run just spent, in the same shape as the estimate.

    Nothing at all for the dummy provider: "$0.0000" would read as a bill that
    happened to be zero, when in fact no account was touched.
    """
    if usage is None:
        return
    model = "/".join(usage.models) if usage.models else "?"
    st.caption(
        f"使用量: {usage.calls} 回 / {model} / 入力 {usage.input_tokens:,} ・ "
        f"出力 {usage.output_tokens:,} トークン → **{_money(usage.cost)}**"
    )
    if not usage.priced:
        st.caption(
            f"うち {usage.unpriced_calls} 回は価格表にないモデルでした。"
            "トークン数は実測ですが、金額は推測せずに伏せています。"
        )


def _render_cost_estimate(database: Database, provider: str, feed: str, lookback_days: int) -> None:
    """Price the next monitoring pass without making a billed call.

    Only Claude is priced. Saying so beats showing an Anthropic figure beside
    an OpenAI selection, which is the same mistake as ``ai-cost --model``
    pricing a model the run could not actually select.
    """
    if provider.lower() not in {"claude", "anthropic"}:
        st.info(
            f"見積もりに対応しているのは Claude のみです（選択中: {provider}）。"
            "他のプロバイダの料金体系は取り込んでいないため、"
            "Claude の金額を出すと別物の数字を見せることになります。"
        )
        return

    bar = st.progress(0.0, text="トークンを数えています（課金なし）")
    try:
        estimate = data.estimate_monitor_cost(
            database,
            feed=feed,
            lookback_days=lookback_days,
            on_progress=lambda done, total: bar.progress(done / total if total else 1.0),
        )
    except Exception as exc:  # surface provider/config errors to the user
        bar.empty()
        st.error(f"見積もれませんでした: {exc}")
        st.caption(
            "トークンの計数には anthropic パッケージ（`uv sync`）と、APIが受け付ける"
            "キーの両方が要ります。上のメッセージがどちらなのかを示しています。"
        )
        return
    bar.empty()

    if estimate is None:
        st.success("保留中の開示はありません。次回の実行は無料です。")
        return

    rating_out = estimate.rating_output_cap * estimate.items
    summary_out = estimate.summary_output_cap * estimate.items
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "内訳": "判定のみ",
                    "開示": estimate.items,
                    "入力トークン": f"{estimate.rating_input_tokens:,}",
                    "出力上限": f"{rating_out:,}",
                    "費用(USD)": _money(estimate.low),
                },
                {
                    "内訳": "判定＋要約",
                    "開示": estimate.items,
                    "入力トークン": (
                        f"{estimate.rating_input_tokens + estimate.summary_input_tokens:,}"
                    ),
                    "出力上限": f"{rating_out + summary_out:,}",
                    "費用(USD)": _money(estimate.high),
                },
            ]
        ),
        width="stretch",
        hide_index=True,
    )
    st.caption(
        f"モデル: `{estimate.model}`。**2行ともに最悪ケース**で、ありそうな値の幅では"
        "ありません。入力は実測（推測ではありません）ですが、出力は各呼び出しの"
        f"上限値です。一語で答える判定は上限{estimate.rating_output_cap}に対して"
        "実測30〜60トークン程度なので、実額はどちらの行よりかなり下に着地します。"
        "実際にいくら使ったかは、実行後に下へ表示されます。"
    )
    if not estimate.priced:
        st.warning(
            f"`{estimate.model}` は価格表にありません。トークン数は実測ですが、"
            "金額は推測せずに伏せています。"
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

    # The estimate is deliberately its own button, and it is first. This is a
    # control in a browser: there is no console line to notice afterwards, and
    # a button that bills an account without ever saying so is exactly what the
    # cost feature exists to prevent.
    #
    # The labels follow the selected provider rather than always warning. A
    # button that says "課金あり" when dummy is selected is wrong, and a warning
    # that is wrong half the time is one people learn to click through - which
    # would leave the real one unread.
    paid = provider != "dummy"
    col_est, col_run = st.columns(2)
    if paid and col_est.button("💰 費用を見積もる（無料）"):
        _render_cost_estimate(database, provider, feed, int(lookback))

    run_label = "🔎 チェックする（課金あり）" if paid else "🔎 チェックする（dummy・無料）"
    if col_run.button(run_label, type="primary"):
        if paid:
            st.caption(f"{provider} を呼びます。実際に使った額は結果の下に出ます。")
        with st.spinner("開示を取得して判定中..."):
            run = data.run_monitor(database, provider, feed=feed, lookback_days=int(lookback))
        result = run.result
        st.write(f"新規 {result.checked} 件を判定、既報 {result.skipped} 件をスキップ。")
        _render_spend(run.usage)
        if result.unjudged:
            st.warning(
                f"{result.unjudged} 件はAIプロバイダの失敗により判定できませんでした。"
                "既読にはしていないので次回再試行されます。\n\n"
                "再試行は一時的な障害には正しい動作ですが、原因が恒久的なら"
                "毎回課金されるだけになります。この件数が0にならない場合は、"
                "実行ログの警告に原因が書かれています。"
            )
        if result.alerts:
            for alert in sorted(result.alerts, key=lambda a: a.importance.rank, reverse=True):
                level = alert.importance.value.upper()
                # The feed is named because "all" mixes a statutory filing with
                # a third party writing about the company, and read as the
                # former a news item carries a weight it has not earned.
                origin = f" — via {alert.disclosure.source}" if alert.disclosure.source else ""
                st.markdown(
                    f"**[{level}] {alert.entry.symbol}**{origin} - {alert.disclosure.title}"
                )
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
        col2.metric("上位バケットの超過", "-" if excess is None else f"{excess:+.2%}")
        t_stat = result.spread_t_stat
        col3.metric("上位−下位 t値", "-" if t_stat is None else f"{t_stat:+.2f}")

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
    _sidebar_status(database)
    pages[choice]()


if __name__ == "__main__":
    main()
