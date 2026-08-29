"""The daily message, cut down to something a phone can show.

A notification is not a smaller report - it is a different artefact. The report
exists to be read once, deliberately, with every caveat in view; the message
has to survive being glanced at on a lock screen, and Discord truncates it hard
at 2,000 characters with no warning of its own. Anything that overflows is
silently gone, so the budget is enforced here rather than discovered there.

What survives the cut is chosen on the same principle as the report: the
figures that decide the verdict, and an explicit line about what was not
measured. Dropping the second one would make the message read as a complete
picture, which is exactly the impression the whole package is built to avoid.
"""

from __future__ import annotations

import datetime as dt

from stock_ai.accumulation.pipeline import Row, Run, business_days_until
from stock_ai.accumulation.report import EARNINGS_WARNING_DAYS
from stock_ai.accumulation.types import Missing, is_value, render

#: Discord rejects a webhook body over this; it does not truncate politely.
DISCORD_LIMIT = 2000

#: Reserved so the "…ほか N 件" line and the closing caveat always fit.
_TAIL_BUDGET = 220

#: A/B/C/D in the order a reader cares about them.
_CLASS_ORDER = {"A": 0, "B": 1, "C": 2, "D": 3}


def _class_rank(row: Row) -> int:
    return _CLASS_ORDER.get(row.classification[:1], 9)


def _price(value: object) -> str:
    return f"${float(value):,.2f}" if is_value(value) else "—"  # type: ignore[arg-type]


def _earnings(row: Row, today: dt.date) -> str:
    earnings = row.next_earnings
    if isinstance(earnings, Missing):
        return f"決算 {earnings}"
    days = business_days_until(earnings, today)
    warn = is_value(days) and 0 <= float(days) <= EARNINGS_WARNING_DAYS
    return f"決算 {'⚠️ ' if warn else ''}{earnings.isoformat()}"


def _row_lines(row: Row, today: dt.date) -> list[str]:
    """One symbol, as the two or three lines worth pushing to a phone."""
    name = row.candidate.listing.name
    head = f"**{row.symbol}** {name[:38]} — {row.classification}"
    if row.deep is None:
        return [head, "　深掘り未実施（moomooのレート制限で上位のみ）"]

    metrics = row.candidate.metrics
    body = (
        f"　完了度 {row.deep.completion.percent:.1f}% ／ "
        f"確定度 {row.breakout.confidence if row.breakout else '—'} ／ "
        f"Large10日 {render(row.deep.flow.large_net_in, signed=True)}"
    )
    shape = (
        f"　安値比 +{metrics.above_52w_low * 100:.1f}% ／ "
        f"20日レンジ {metrics.range_20d * 100:.1f}% ／ "
        f"出来高 {metrics.volume_multiple:.2f}倍"
    )
    levels = "　入 —"
    if row.breakout is not None:
        levels = (
            f"　入 {_price(row.breakout.bb_upper)} ／ "
            f"撤退 {_price(row.breakout.stop_bb_middle)} ／ {_earnings(row, today)}"
        )
    return [head, body, shape, levels]


def _header(run: Run, today: dt.date) -> list[str]:
    as_of = run.data_as_of.isoformat() if run.data_as_of else "取得不可"
    lines = [
        f"**米国株 アキュムレーション検出** {today.isoformat()}",
        f"基準日 {as_of} ／ 緩和: {run.screen.relaxation_label} ／ "
        f"{run.screen.priced:,} 銘柄を測定",
    ]
    if run.data_as_of is not None:
        behind = business_days_until(run.data_as_of, run.generated_at.date())
        if is_value(behind) and float(behind) <= -2:
            lines.append(f"⚠️ 最新の日足が {abs(int(behind))} 営業日前です")
    return lines


def _empty_body(run: Run) -> list[str]:
    """What to say when nothing passed - including which test did the rejecting."""
    if not run.screen.rejections:
        return [
            "該当なし。**測定できた銘柄が0件**で、価格データが取得できていない可能性があります。"
        ]
    top = sorted(run.screen.attrition.items(), key=lambda item: -item[1])[:3]
    reasons = "、".join(f"{name} {count}件" for name, count in top)
    return [f"該当なし（緩和ラダーを最後まで適用）。落ちた条件: {reasons}"]


def build_message(run: Run, today: dt.date) -> str:
    """Render the run as one Discord message, guaranteed to fit.

    Rows are ordered A, B, C, D, so what gets cut when the budget runs out is
    the least actionable end of the list rather than an arbitrary one.
    """
    lines = _header(run, today)
    caveat = "_DPI・ブロック取引・借株コスト・大口比率は取得不可。推定値では埋めていません。_"

    if not run.rows:
        lines += ["", *_empty_body(run)]
        return "\n".join(lines)[:DISCORD_LIMIT]

    ordered = sorted(run.rows, key=_class_rank)
    budget = DISCORD_LIMIT - _TAIL_BUDGET
    body: list[str] = []
    shown = 0
    for row in ordered:
        block = _row_lines(row, today)
        candidate_len = len("\n".join([*lines, *body, "", *block]))
        if candidate_len > budget and shown:
            break
        body += ["", *block]
        shown += 1

    lines += body
    if shown < len(ordered):
        lines.append(f"\n…ほか {len(ordered) - shown} 件（全文はレポートを参照）")
    lines += ["", caveat]
    message = "\n".join(lines)
    if len(message) > DISCORD_LIMIT:
        # Belt and braces: a very long company name or an unusual marker could
        # still push it over, and Discord's answer to that is to drop the whole
        # message rather than the tail.
        message = message[: DISCORD_LIMIT - 1] + "…"
    return message


def should_notify(run: Run, *, heartbeat: bool) -> bool:
    """Whether this run is worth a message.

    An empty screen is the common case for this shape, and a message that says
    "該当なし" every single day is one nobody reads by the second week - the
    same failure this project names elsewhere about alarms that always fire.
    ``heartbeat`` is for the opposite worry: without it, a quiet day and a job
    that never ran look identical from the phone.
    """
    return bool(run.rows) or heartbeat
