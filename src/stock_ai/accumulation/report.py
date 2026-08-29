"""Rendering. The one place a number becomes text, and the one place it cannot.

Every figure in this report goes through
:func:`~stock_ai.accumulation.types.render`, which prints a stated absence
where a value is missing. That is the whole reason the marker is a type: a
table cell cannot be filled with a plausible number by accident, because there
is no code path from a missing metric to a digit.
"""

from __future__ import annotations

import datetime as dt

from rich.console import Console
from rich.table import Table

from stock_ai.accumulation.pipeline import Row, Run, business_days_until
from stock_ai.accumulation.types import Absence, Measure, Missing, is_value, render, render_pct

#: An earnings date this close makes every technical read provisional.
EARNINGS_WARNING_DAYS = 5


def _money(measure: Measure) -> str:
    return render(measure, signed=True)


def _price(measure: Measure) -> str:
    return f"${float(measure):,.2f}" if is_value(measure) else str(measure)


def print_header(console: Console, run: Run) -> None:
    """Say what the report is made of before saying anything about a symbol."""
    as_of = run.data_as_of.isoformat() if run.data_as_of else "取得不可"
    console.print("[bold]米国株 アキュムレーション検出レポート[/]")
    console.print(f"データ基準日（ET終値）: [cyan]{as_of}[/]")
    console.print(f"取得日時（UTC）      : {run.generated_at.strftime('%Y-%m-%d %H:%M:%S')}")
    console.print(
        f"ユニバース           : {run.screen.universe_size:,} 銘柄中 "
        f"{run.screen.priced:,} 銘柄を測定"
    )
    console.print(f"緩和レベル           : [yellow]{run.screen.relaxation_label}[/]")
    # A daily feed that is a session behind judges the wrong day. The volume
    # spike this screen turns on is a statement about "the latest bar", so a
    # stale one quietly changes what was tested.
    if run.data_as_of is not None:
        behind = business_days_until(run.data_as_of, run.generated_at.date())
        if is_value(behind) and float(behind) <= -2:
            console.print(
                f"[yellow]注意: 最新の日足が {abs(int(behind))} 営業日前です。[/]"
                "出来高倍率は「最新足」で判定するため、直近の値動きは反映されていません。"
            )
    for note in run.notes:
        console.print(f"[dim]{note}[/]")
    console.print()


def print_phase1(console: Console, run: Run) -> None:
    """The screen result."""
    table = Table(title="フェーズ1: 一次スクリーニング")
    for column in ("ティッカー", "社名", "セクター"):
        table.add_column(column, style="cyan" if column == "ティッカー" else None)
    table.add_column("出来高倍率", justify="right")
    table.add_column("52週安値比", justify="right")
    table.add_column("20日レンジ", justify="right")
    table.add_column("時価総額", justify="right")
    table.add_column("大口10日NetIn", justify="right")

    for row in run.rows:
        metrics = row.candidate.metrics
        name = row.candidate.listing.name
        table.add_row(
            row.symbol,
            name if len(name) <= 34 else f"{name[:31]}...",
            str(row.candidate.sector),
            f"{metrics.volume_multiple:.2f}x",
            f"+{metrics.above_52w_low * 100:.1f}%",
            f"{metrics.range_20d * 100:.1f}%",
            render(row.candidate.market_cap),
            _money(row.candidate.flow_net_in_10d),
        )
    console.print(table)
    if not run.rows:
        _print_attrition(console, run)
    console.print()


#: Beyond this many rejected symbols the per-symbol list stops being readable
#: and the tally above it carries the same information.
MAX_REJECTIONS_LISTED = 20


def _print_attrition(console: Console, run: Run) -> None:
    """Say which test emptied the screen, not merely that it is empty.

    An empty result reads identically whether the symbols were the wrong shape
    for this screen, the thresholds were too tight, or the prices never loaded.
    Those need opposite responses, so the numbers that decided it are printed
    rather than left to be guessed at.
    """
    console.print(
        "[yellow]緩和ラダーを最後まで適用しても該当なし。[/]以下は最も緩い条件でも外れた理由です。"
    )
    if not run.screen.rejections:
        console.print(
            "[red]測定できた銘柄が1つもありません。[/]"
            "価格データが取得できていない可能性があります（履歴不足、または銘柄コード違い）。"
        )
        return

    tally = Table(title="どの条件で落ちたか（最も緩い条件で）")
    tally.add_column("条件", style="cyan")
    tally.add_column("落ちた銘柄数", justify="right")
    for name, count in sorted(run.screen.attrition.items(), key=lambda item: -item[1]):
        tally.add_row(name, f"{count} / {run.screen.priced}")
    console.print(tally)

    detail = Table(title="銘柄ごとの実測値")
    detail.add_column("ティッカー", style="cyan")
    detail.add_column("外れた条件")
    for rejection in run.screen.rejections[:MAX_REJECTIONS_LISTED]:
        detail.add_row(rejection.symbol, " / ".join(rejection.misses))
    console.print(detail)
    if len(run.screen.rejections) > MAX_REJECTIONS_LISTED:
        console.print(
            f"[dim]ほか {len(run.screen.rejections) - MAX_REJECTIONS_LISTED} 銘柄は省略。[/]"
        )
    console.print(
        "[dim]この画面は「条件に合う形の銘柄が無かった」であって、"
        "「その銘柄が悪い」ではありません。仕込みの形は 52週安値の近くで"
        "レンジが締まっている銘柄に出るので、高値圏の大型株では原理的に出ません。[/]"
    )


def _print_measure_table(
    console: Console, title: str, rows: list[tuple[str, Measure | str]]
) -> None:
    table = Table(title=title, show_header=False, title_justify="left")
    table.add_column("項目", style="cyan", no_wrap=True)
    table.add_column("値")
    for label, value in rows:
        table.add_row(label, value if isinstance(value, str) else render(value))
    console.print(table)


def print_phase2(console: Console, row: Row) -> None:
    """The deep dive for one symbol."""
    if row.deep is None:
        return
    deep = row.deep
    console.print(f"[bold cyan]── {row.symbol} ── {row.candidate.listing.name}[/]")

    _print_measure_table(
        console,
        "1. 資金フロー（10日）",
        [
            ("Large NetIn（特大+大口）", _money(deep.flow.large_net_in)),
            ("Medium NetIn（中口）", _money(deep.flow.medium_net_in)),
            ("Small NetIn（小口）", _money(deep.flow.small_net_in)),
            ("全体 NetIn", _money(deep.flow.total_net_in)),
            ("大口比率（Large÷出来高）", str(deep.flow.large_share_of_volume)),
            (
                "└ 代替: Large NetIn ÷ 売買代金",
                render_pct(deep.flow.large_net_in_over_turnover, signed=True),
            ),
            ("寄付き30分/引け30分の偏り", str(deep.flow.open30_close30_skew)),
            ("プレ/アフター異常出来高", str(deep.flow.prepost_abnormal_volume)),
        ],
    )
    _print_measure_table(
        console,
        "2. 機関投資家",
        [
            ("ダークプール比率 DPI", str(deep.institutional.dark_pool_index)),
            ("ブロック取引（1万株超）", str(deep.institutional.block_trades)),
            ("13F / 13D・13G 増減", str(deep.institutional.form_13f_change)),
            ("Form 4（直近90日）", str(deep.institutional.form_4_activity)),
        ],
    )
    _print_measure_table(
        console,
        "3. 空売り",
        [
            ("Short Interest（浮動株比）", render_pct(deep.short.short_interest_of_float)),
            ("└ 前月", render_pct(deep.short.short_interest_prior)),
            ("└ 増減", render_pct(deep.short.short_interest_change, signed=True)),
            ("└ 1回前の増減", str(deep.short.short_interest_change_prior)),
            ("Days to Cover", render(deep.short.days_to_cover)),
            ("借株コスト", str(deep.short.borrow_fee)),
        ],
    )
    _print_measure_table(
        console,
        "4. テクニカル",
        [
            ("ボリンジャーバンド幅 BW", render_pct(deep.technical.bollinger_width, digits=2)),
            ("SMA 5/10/20/50 最大乖離", render_pct(deep.technical.sma_max_divergence, digits=2)),
            ("RSI(14)", render(deep.technical.rsi14)),
            ("MACD ヒストグラム", render(deep.technical.macd_histogram, signed=True)),
            ("OBV 10日変化", render(deep.technical.obv_change_10d, signed=True)),
            ("A/Dライン 10日変化", render(deep.technical.ad_line_change_10d, signed=True)),
            ("VWAP(20日アンカー)", _price(deep.technical.vwap20)),
            ("現値のVWAP乖離", render_pct(deep.technical.price_vs_vwap, signed=True)),
        ],
    )

    table = Table(title=f"アキュムレーション完了度: {deep.completion.percent:.1f}%")
    table.add_column("条件", style="cyan")
    table.add_column("判定", justify="center")
    table.add_column("実測")
    for condition in deep.completion.conditions:
        if isinstance(condition.met, Missing):
            verdict = "[dim]判定不可[/]"
        else:
            verdict = "[green]達成[/]" if condition.met else "[red]未達[/]"
        table.add_row(condition.label, verdict, condition.detail)
    console.print(table)
    judgeable = deep.completion.percent_of_judgeable
    console.print(
        f"[dim]達成 {deep.completion.achieved}/7 条件 = "
        f"{deep.completion.percent:.1f}%（仕様どおり7条件で算出）。"
        f"判定できた {deep.completion.judgeable} 条件に限れば "
        f"{render(judgeable, digits=1)}%。判定不可の条件は銘柄への評価ではありません。[/]"
    )
    console.print()


def print_phase3(console: Console, row: Row) -> None:
    """The breakout verdict for one symbol."""
    if row.breakout is None or not row.breakout.checks:
        return
    breakout = row.breakout
    table = Table(title=f"フェーズ3: {row.symbol} ブレイクアウト判定 {breakout.confidence}")
    table.add_column("条件", style="cyan")
    table.add_column("", justify="center")
    table.add_column("実測")
    table.add_column("達成に必要な水準")
    for check in breakout.checks:
        table.add_row(check.label, check.mark, check.detail, check.needed or "—")
    console.print(table)
    console.print(
        f"  想定エントリー（BB上限突破）: [bold]{_price(breakout.bb_upper)}[/]    "
        f"現値 {_price(breakout.last_close)}"
    )
    console.print(
        f"  撤退ライン: SMA20割れ {_price(breakout.stop_bb_middle)} / "
        f"20日安値割れ {_price(breakout.stop_20d_low)} / "
        f"ATR2倍 {_price(breakout.stop_atr)}"
    )
    console.print(
        "[dim]  ダマシ判定: 上抜け後にSMA20を終値で下回った時点で撤退。"
        "20日安値割れは仕込み自体の否定。[/]"
    )
    console.print()


def print_summary(console: Console, run: Run, today: dt.date) -> None:
    """The final table, one row per screened symbol."""
    table = Table(title="最終統合サマリー")
    for column in ("ティッカー", "社名", "完了度", "確定度", "判定区分"):
        table.add_column(column, style="cyan" if column == "ティッカー" else None)
    table.add_column("想定エントリー", justify="right")
    table.add_column("撤退ライン", justify="right")
    table.add_column("次回決算日")

    for row in run.rows:
        completion = f"{row.deep.completion.percent:.1f}%" if row.deep else "未実施"
        confidence = row.breakout.confidence if row.breakout else "未実施"
        entry = _price(row.breakout.bb_upper) if row.breakout else "—"
        stop = _price(row.breakout.stop_bb_middle) if row.breakout else "—"

        earnings = row.next_earnings
        if isinstance(earnings, Missing):
            earnings_text = str(earnings)
        else:
            days = business_days_until(earnings, today)
            warn = is_value(days) and 0 <= float(days) <= EARNINGS_WARNING_DAYS
            earnings_text = f"{'⚠️ ' if warn else ''}{earnings.isoformat()}"
        table.add_row(
            row.symbol,
            row.candidate.listing.name[:24],
            completion,
            confidence,
            row.classification,
            entry,
            stop,
            earnings_text,
        )
    console.print(table)
    console.print(
        "[dim]A=ブレイクアウト確定(5/5) B=初動確認(3〜4/5) "
        "C=仕込み継続中(完了度60%以上・確定度2/5以下) D=見送り(完了度60%未満)。"
        f"⚠️ は次回決算まで{EARNINGS_WARNING_DAYS}営業日以内。[/]"
    )
    console.print()


def print_unavailable(console: Console, run: Run) -> None:
    """List what could not be obtained, so no reader has to infer it."""
    seen: dict[str, str] = {}
    for row in run.rows:
        if row.deep is None:
            continue
        deep = row.deep
        for label, value in (
            ("大口比率（Large÷出来高）", deep.flow.large_share_of_volume),
            ("寄付き30分/引け30分の偏り", deep.flow.open30_close30_skew),
            ("プレ/アフター異常出来高", deep.flow.prepost_abnormal_volume),
            ("ダークプール比率 DPI", deep.institutional.dark_pool_index),
            ("ブロック取引", deep.institutional.block_trades),
            ("13F / 13D・13G", deep.institutional.form_13f_change),
            ("Form 4", deep.institutional.form_4_activity),
            ("借株コスト", deep.short.borrow_fee),
        ):
            if isinstance(value, Missing):
                seen[label] = f"{value.kind}: {value.reason}"
    if not seen:
        return
    table = Table(title="取得できなかった項目と理由")
    table.add_column("項目", style="cyan")
    table.add_column("理由")
    for label, reason in seen.items():
        table.add_row(label, reason)
    console.print(table)
    console.print(
        f"[dim]「{Absence.UNAVAILABLE}」は到達できるデータ源が無いもの、"
        f"「{Absence.NOT_IMPLEMENTED}」は取得可能だが本コマンドが未対応のものです。"
        "いずれも推定値では埋めていません。[/]"
    )


def print_report(console: Console, run: Run, today: dt.date) -> None:
    """The whole report, in the brief's order."""
    print_header(console, run)
    print_phase1(console, run)
    for row in run.rows:
        print_phase2(console, row)
        print_phase3(console, row)
    print_summary(console, run, today)
    print_unavailable(console, run)
