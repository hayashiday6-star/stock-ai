"""ダッシュボードの「自動売買 運用」画面。

自動売買そのものは WSL 上の正典リポジトリで動いている。この画面はそれを読みに行く
だけで、売買ルールも帳簿の解釈も持たない(:mod:`stock_ai.ops.bridge` 参照)。

安全設計(正典の交渉不可ルールをそのまま引き継ぐ):
  - 実発注・帳簿(positions.json)の変更・broker切替・リスク上限の変更はできない。
  - キルスイッチは**発動**だけできる。解除する口はこの画面にもこの下の層にも無い。
  - 「発注チェック」はドライラン固定。本番発注は正典の cron だけが行う。

表示の作りで1つだけ意図がある: 6つのビューを ``st.tabs`` ではなく
``st.segmented_control`` で切り替えている。タブは表示されていない中身も毎回実行する
ので、選んでいない画面のぶんまで WSL を叩きに行ってしまう(資産推移は数十秒かかる)。
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from stock_ai.core.exceptions import OpsError, OpsUnavailableError
from stock_ai.ops.bridge import OpsBridge, get_bridge

VIEWS = ["状態", "資産推移", "売買履歴", "通知設定", "検証(ドライラン)", "運用操作"]

_KILL_SCOPES = ["日本株", "米国株", "両方"]


# --- 取得(キャッシュつき) --------------------------------------------------
# 先頭が "_" の引数は Streamlit がハッシュ対象から外す。橋渡しはハッシュできないので
# ``_bridge`` で渡し、代わりに参照先(distro:path)を ``target`` としてキーに含める。
# 参照先を切り替えたのに前の正典の数字が残る、という壊れ方をさせないため。


@st.cache_data(ttl=30, show_spinner="正典リポジトリを確認中...")
def _ping(_bridge: OpsBridge, target: str) -> dict[str, Any]:
    return _bridge.ping()


@st.cache_data(ttl=60, show_spinner="正典の状態を取得中...")
def _status(_bridge: OpsBridge, target: str) -> dict[str, Any]:
    return _bridge.status()


@st.cache_data(ttl=60, show_spinner="売買履歴を取得中...")
def _history(_bridge: OpsBridge, target: str) -> dict[str, Any]:
    return _bridge.trade_history()


@st.cache_data(ttl=600, show_spinner="資産推移を計算中(数十秒かかります)...")
def _equity(_bridge: OpsBridge, target: str) -> dict[str, Any]:
    return _bridge.equity()


@st.cache_data(ttl=60, show_spinner="通知設定を取得中...")
def _notify_config(_bridge: OpsBridge, target: str) -> dict[str, Any]:
    return _bridge.notify_config()


@st.cache_data(ttl=600, show_spinner="ジョブ一覧を取得中...")
def _jobs(_bridge: OpsBridge, target: str) -> list[str]:
    return _bridge.jobs()


@st.cache_data(ttl=600, show_spinner="ドライラン実行中(市場データ読込に時間がかかります)...")
def _dry_run(
    _bridge: OpsBridge, target: str, high_window: int, vol_mult: float, capital: float
) -> dict[str, Any]:
    return _bridge.dry_run(high_window, vol_mult, capital)


# --- 部品 -------------------------------------------------------------------


def _fail(exc: Exception) -> None:
    """正典に届かなかった/断られたことを、原因が分かる形で出す。"""
    if isinstance(exc, OpsUnavailableError):
        st.error(f"正典リポジトリを参照できません。\n\n{exc}")
        st.caption(
            "WSL が停止していると参照できません。PowerShell で `wsl -d Ubuntu-24.04 -- true` "
            "を一度実行して起動してください。参照先は .env の OPS_WSL_DISTRO / OPS_REPO_PATH。"
        )
    else:
        st.error(f"正典への問い合わせが失敗しました: {exc}")


def _view_status(bridge: OpsBridge, target: str) -> None:
    """キルスイッチ・cron・保有・翌営業日の注文・ログ末尾。"""
    status = _status(bridge, target)

    kills = status.get("kill_switches") or []
    cron = status.get("cron") or []
    risk = status.get("risk") or {}
    # st.metric の delta は数値の増減を表す矢印つきで出るので、状態表示には使わない
    # (「🟢 なし」に上向き矢印が付くと、良し悪しを取り違える読み方ができてしまう)。
    with st.container(horizontal=True):
        with st.container(border=True):
            st.markdown("**キルスイッチ**")
            st.markdown("🔴 発動中" if kills else "🟢 なし(正常)")
            st.caption(", ".join(kills) if kills else "発注は止まっていません")
        with st.container(border=True):
            st.markdown("**cron(正典の定時ジョブ)**")
            st.markdown(f"{len(cron)}本 登録")
            st.caption("0本なら自動売買は動いていません")
        with st.container(border=True):
            st.markdown("**broker**")
            st.markdown(str(risk.get("broker", "?")))
            st.caption(f"運用資金 {risk.get('capital', 0):,}円")
    if kills:
        st.error(
            "キルスイッチが立っている間、正典は発注しません。**解除はこの画面からはできません** — "
            "原因を特定したうえで、あなた自身が正典側で該当ファイルを削除してください。"
        )
    if cron:
        with st.expander(f"cron に登録されているジョブ({len(cron)}本)"):
            st.code("\n".join(cron))

    st.subheader("日本株トラックA(60日ブレイクアウト)")
    positions = status.get("jp_positions") or []
    if positions:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "コード": p.get("code"),
                        "銘柄": p.get("name", ""),
                        "株数": p.get("shares"),
                        "状態": p.get("status"),
                        "シグナル日": p.get("signal_date"),
                        "約定日": p.get("exec_date"),
                        "取得単価": p.get("entry_px"),
                    }
                    for p in positions
                ]
            ),
            hide_index=True,
        )
    else:
        st.write("保有なし")

    orders = status.get("orders_next") or {}
    st.write(
        f"翌営業日の注文: 執行日 **{orders.get('exec_date', '?')}** / "
        f"売り {len(orders.get('sells', []))}件・買い {len(orders.get('buys', []))}件"
    )

    st.subheader("米国株トラックC")
    momentum = status.get("us_momentum") or {}
    st.write(
        f"モメンタム(月次): 保有 {len(momentum.get('positions', []))}銘柄"
        if momentum
        else "モメンタム: 状態ファイルなし"
    )
    us_positions = status.get("us_positions") or []
    if us_positions:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "コード": p.get("code"),
                        "株数": p.get("shares"),
                        "状態": p.get("status"),
                        "entry_date": p.get("entry_date"),
                    }
                    for p in us_positions
                ]
            ),
            hide_index=True,
        )

    st.subheader("直近ログ")
    for name, tail in (status.get("logs") or {}).items():
        with st.expander(name):
            st.code(tail or "(空)")


def _curve_frame(curve: dict[str, Any], key: str = "points") -> pd.DataFrame:
    """``[[日付, 値], ...]`` を日付を索引にした1列のフレームにする。"""
    frame = pd.DataFrame(curve[key], columns=["date", "equity"])
    frame["date"] = pd.to_datetime(frame["date"])
    return frame.set_index("date")


def _show_curve(curve: dict[str, Any] | None, empty_message: str) -> None:
    """1トラックぶんの資産推移。"""
    if curve is None:
        st.write(empty_message)
        return
    capital = curve["capital"]
    pnl = curve["equity"] - capital
    st.metric(
        f"{curve['label']} — {curve['asof']}時点",
        f"{curve['equity']:,.0f} {curve['currency']}",
        delta=f"{pnl:+,.0f} {curve['currency']}({pnl / capital * 100:+.2f}%)",
    )
    frame = _curve_frame(curve).rename(columns={"equity": "資産"})
    frame["元本"] = capital
    st.line_chart(frame, y=["資産", "元本"], y_label=f"資産({curve['currency']})")


def _view_equity(bridge: OpsBridge, target: str) -> None:
    """帳簿とキャッシュ済み日足から再計算した日次時価評価。"""
    st.subheader("資産推移(日次時価評価)")
    st.caption(
        "正典の帳簿とキャッシュ済み日足終値から、毎日の資産額を再計算しています。"
        "データ最終日の終値ベースなので、実口座の評価額とは端数・約定タイミング分ずれます。"
    )
    curves = _equity(bridge, target)

    _show_curve(curves.get("jp"), "日本株: 約定済みポジションがまだありません")
    st.divider()
    momentum = curves.get("us_momentum")
    _show_curve(momentum, "米国株モメンタム: 約定記録がまだありません")
    if momentum and momentum.get("marks"):
        recomputed = _curve_frame(momentum)["equity"]
        marks = _curve_frame(momentum, "marks")["equity"]
        common = marks.index.intersection(recomputed.index)
        if len(common) > 0:
            gap = (recomputed[common] - marks[common]).abs().max()
            if gap > momentum["capital"] * 0.01:
                st.warning(
                    f"⚠️ 再計算値とジョブ記録(mark)の乖離が最大 {gap:,.0f} ドルあります。"
                    "帳簿か価格データの確認を推奨します。"
                )
            else:
                st.caption(f"ジョブ自身の日次評価(mark)と突合済み(最大乖離 {gap:,.0f} ドル)")
    st.divider()
    _show_curve(curves.get("us_swing"), "米国株スイング: 約定済みポジションがまだありません")
    st.caption("注: 日付不明の旧記録は評価不能のため曲線に含めていません。")


def _view_history(bridge: OpsBridge, target: str) -> None:
    """全トラックの売買と、その根拠。"""
    st.subheader("売買履歴と根拠")
    st.caption(
        "「根拠(自動)」は各戦略のルールから機械的に導出した理由です。「メモ」は自由記入で、"
        "正典の trade_notes.json にのみ保存され、帳簿(positions.json)・発注・審査には"
        "一切影響しません。"
    )
    payload = _history(bridge, target)
    rows = payload.get("rows") or []
    if not rows:
        st.write("売買履歴がまだありません。")
        return

    notes = payload.get("notes") or {}
    frame = pd.DataFrame(
        [
            {
                "日付": r["date"],
                "トラック": r["track"],
                "コード": r["code"],
                "銘柄": r["name"],
                "売買": r["side"],
                "株数": r["shares"],
                "価格": r["price"],
                "状態": r["status"],
                "根拠(自動)": r["rationale"],
                "メモ": notes.get(r["key"], ""),
            }
            for r in rows
        ],
        index=[r["key"] for r in rows],
    )
    read_only = st.column_config.TextColumn(disabled=True)
    edited = st.data_editor(
        frame,
        hide_index=True,
        column_config={
            "日付": read_only,
            "トラック": read_only,
            "コード": read_only,
            "銘柄": read_only,
            "売買": st.column_config.TextColumn(disabled=True, width="small"),
            "株数": st.column_config.NumberColumn(disabled=True),
            "価格": read_only,
            "状態": st.column_config.TextColumn(disabled=True, width="small"),
            "根拠(自動)": st.column_config.TextColumn(disabled=True, width="large"),
            "メモ": st.column_config.TextColumn(
                "メモ(手動・任意)",
                width="large",
                help="この売買の根拠・気づきを自由に記入。下の保存ボタンで正典の "
                "trade_notes.json に保存されます(帳簿は変更しません)。",
            ),
        },
    )

    if st.button("メモを保存", type="primary", icon=":material/save:", key="ops_save_notes"):
        merged = dict(notes)
        merged.update(dict(zip(frame.index, edited["メモ"].tolist(), strict=True)))
        saved = bridge.save_notes(merged)
        _history.clear()
        st.success(f"メモ {saved}件を正典の trade_notes.json に保存しました(帳簿は変更なし)。")


def _view_notify(bridge: OpsBridge, target: str) -> None:
    """Discord通知のイベント別ON/OFF。"""
    st.subheader("Discord通知のイベント別ON/OFF")
    st.caption(
        "⚠️ ★・ERROR・キルスイッチ・違反・中止などの異常系は、OFFにしても必ず送信されます"
        "(正典側の安全設計)。"
    )
    config = _notify_config(bridge, target)
    events = config.get("events") or {}
    names = config.get("all_events") or list(events)

    updated: dict[str, bool] = {}
    columns = st.columns(3)
    for index, name in enumerate(names):
        with columns[index % 3]:
            updated[name] = st.toggle(
                name, value=bool(events.get(name, True)), key=f"ops_ev_{name}"
            )

    if st.button("保存", type="primary", icon=":material/save:", key="ops_save_notify"):
        bridge.save_notify_config(updated)
        _notify_config.clear()
        st.success("正典の notify_config.json に保存しました。次回の通知から反映されます。")


def _view_dry_run(bridge: OpsBridge, target: str) -> None:
    """日本株トラックAのシグナル条件を、帳簿のコピー上で試す。"""
    st.subheader("シグナル条件のドライラン検証(日本株トラックA)")
    st.warning(
        "ここでの変更は**プレビュー専用**です。本番のシグナル条件・帳簿・通知には一切"
        "書き込まれません。本番条件の変更は3段階審査(strategy-researcher → risk-officer → "
        "ユーザー承認)が必要です。",
        icon=":material/lock:",
    )
    col1, col2, col3 = st.columns(3)
    with col1:
        high_window = st.slider("高値ブレイク期間(営業日)", 10, 120, 60, step=5, help="本番は60日")
    with col2:
        vol_mult = st.slider("出来高倍率(20日平均比)", 1.0, 3.0, 1.5, step=0.1, help="本番は1.5倍")
    with col3:
        capital = st.number_input(
            "運用資金(円)", value=20_000_000, step=1_000_000, min_value=1_000_000
        )

    if not st.button(
        "ドライラン実行", type="primary", icon=":material/play_arrow:", key="ops_dry_run"
    ):
        return
    result = _dry_run(bridge, target, high_window, vol_mult, float(capital))
    st.metric("本日シグナル銘柄数", result["n_signals_today"])
    st.text(f"実行条件: {high_window}日高値 / 出来高{vol_mult:.1f}倍 / 資金{capital:,.0f}円")
    st.text(f"データ最終日: {result['asof']} / 空き枠: {result['slots']}")
    if result["buys"]:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "コード": b["code"],
                        "銘柄": b["name"],
                        "株数": b["shares"],
                        "参考終値": f"{b['ref_px']:,.0f}",
                        "出来高倍率": f"{b['vol_ratio']}x",
                    }
                    for b in result["buys"]
                ]
            ),
            hide_index=True,
        )
    if result["skipped"]:
        st.caption("単元価格が枠を超えてスキップ: " + " / ".join(result["skipped"]))
    st.subheader("この条件なら飛ぶ通知のプレビュー")
    st.code(result["message"])


def _view_operations(bridge: OpsBridge, target: str) -> None:
    """停止方向の操作と、定時ジョブの手動実行。"""
    st.subheader("🛑 緊急停止(キルスイッチ発動)")
    st.caption(
        "発動すると該当トラックの発注が止まります。**解除はこのアプリからはできません** — "
        "原因を特定した上で、あなた自身が正典側で該当ファイルを削除してください。"
    )
    scope = st.segmented_control(
        "対象", _KILL_SCOPES, default=_KILL_SCOPES[0], key="ops_kill_scope"
    )
    reason = st.text_input(
        "発動理由(必須)",
        placeholder="例: 帳簿と口座の残高が合わない",
        key="ops_kill_reason",
    )
    if st.button(
        "キルスイッチ発動",
        type="primary",
        icon=":material/emergency_home:",
        disabled=not (reason.strip() and scope),
        key="ops_kill",
    ):
        created = bridge.activate_kill_switch(reason.strip(), scope)
        _status.clear()
        st.error("発動しました: " + " / ".join(created))
        st.caption(
            "解除コマンド(原因特定後に、正典側のターミナルで): "
            + " ".join(f"`rm {path}`" for path in created)
        )

    st.divider()
    st.subheader("▶️ 手動実行")
    st.caption(
        "cron の定時実行と同じ処理をいま実行します。発注はドライラン(チェックのみ)です — "
        "本番発注は正典の cron だけが行います。"
    )
    job = st.selectbox("ジョブ", _jobs(bridge, target), key="ops_job")
    if st.button("実行", icon=":material/terminal:", key="ops_run_job"):
        with st.spinner(f"{job} を実行中..."):
            output = bridge.run_job(job)
        _status.clear()
        st.code(output or "(出力なし)")


_RENDERERS = {
    "状態": _view_status,
    "資産推移": _view_equity,
    "売買履歴": _view_history,
    "通知設定": _view_notify,
    "検証(ドライラン)": _view_dry_run,
    "運用操作": _view_operations,
}


def render(bridge: OpsBridge | None = None) -> None:
    """「自動売買 運用」画面を描画する。"""
    bridge = bridge or get_bridge()
    target = bridge.target.label

    st.header("🛰️ 自動売買 運用")
    st.caption(
        f"参照先(正典): `{target}` — できること: 状態確認・通知設定・ドライラン検証・"
        "緊急停止・手動実行。できないこと: 実発注・帳簿変更・キルスイッチ解除・broker切替。"
    )

    try:
        _ping(bridge, target)
    except (OpsError, OpsUnavailableError) as exc:
        _fail(exc)
        return

    col1, col2 = st.columns([4, 1])
    with col1:
        view = st.segmented_control(
            "表示", VIEWS, default=VIEWS[0], label_visibility="collapsed", key="ops_view"
        )
    with col2:
        if st.button("再読み込み", icon=":material/refresh:", width="stretch", key="ops_reload"):
            for cached in (_ping, _status, _history, _equity, _notify_config, _jobs, _dry_run):
                cached.clear()
            st.rerun()

    try:
        _RENDERERS[view or VIEWS[0]](bridge, target)
    except (OpsError, OpsUnavailableError) as exc:
        _fail(exc)
