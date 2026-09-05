"""J-Quants fundamentals provider (Japanese equities).

Reads the V2 ``/fins/summary`` endpoint (available on the free plan; the richer
``/fins/details`` requires a paid plan) and maps the latest disclosure to the
canonical :class:`~stock_ai.data.types.Fundamentals`:

- ``Sales`` → revenue, ``NP`` → net income, ``NP / Eq`` → ROE
- ``EPS``, ``BPS``, ``DivAnn`` combine with an optional current price to give
  PER, PBR, and dividend yield; without a price those stay ``None``.

The HTTP call is injectable, so the provider is unit-testable without network.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Callable
from typing import Any

from pydantic import SecretStr

from stock_ai.core.exceptions import DataError
from stock_ai.core.logging import get_logger
from stock_ai.data.http import raise_for_status
from stock_ai.data.sanity import plausible_dividend_yield
from stock_ai.data.types import FinancialReport, FiscalPeriod, Fundamentals
from stock_ai.fundamental.growth import dividends_crossing_a_split

logger = get_logger(__name__)

# A fetcher takes a symbol and returns raw statement records.
StatementFetcher = Callable[[str], list[dict[str, Any]]]
Clock = Callable[[], dt.date]
PriceLookup = Callable[[str], float | None]

_STATEMENTS_URL = "https://api.jquants.com/v2/fins/summary"


def _to_float(value: Any) -> float | None:
    """Parse a J-Quants numeric field (strings, blanks) to ``float`` or ``None``.

    Non-finite results map to ``None`` as well: ``float("nan")`` parses happily,
    and a ``NaN`` that escapes here poisons every ratio derived from it
    downstream (scoring treats non-finite input as missing, not as a score).
    """
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _latest(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the most recently disclosed record (V2 ``DiscDate``, V1 fallback)."""
    return max(
        records,
        key=lambda r: str(r.get("DiscDate") or r.get("DisclosedDate") or ""),
    )


def _first(record: dict[str, Any], *keys: str) -> float | None:
    """Return the first parseable numeric value among ``keys``."""
    for key in keys:
        value = _to_float(record.get(key))
        if value is not None:
            return value
    return None


def _newest_value(records: list[dict[str, Any]], *keys: str) -> float | None:
    """Return ``keys`` from the newest record that actually carries a value.

    Taking a field from the newest record *only* throws away data that is
    plainly available: a quarterly disclosure often omits BPS and the annual
    dividend, so a company whose latest filing is a quarter loses both even
    though last year's report has them. Observed live: PBR was present for 31%
    of TSE and dividend yield for 12%, purely from this.

    Walking back is safe for balance-sheet and per-share fields, which describe
    a point in time rather than a period. It is **not** safe for cumulative flow
    figures, which is why sales, profit and EPS go through
    :func:`_latest_annual` instead.
    """
    for record in sorted(records, key=_disclosure_key, reverse=True):
        value = _first(record, *keys)
        if value is not None:
            return value
    return None


def _disclosure_key(record: dict[str, Any]) -> str:
    """Sort key placing the most recently disclosed record last."""
    return str(record.get("DiscDate") or record.get("DisclosedDate") or "")


def _earnings_are_consistent(symbol: str, record: dict[str, Any]) -> bool:
    """Whether a record's profit figure agrees with the rest of the same row.

    A payout ratio is dividends divided by profit, so a **positive** payout
    ratio alongside a **negative** profit is not a judgement call - the row
    contradicts itself. Observed live on 6758 (FY to 2026-03):
    ``NP = -0.327兆``, ``DivTotalAnn = 0.149兆``, ``PayoutRatioAnn = 0.145``.
    That ratio implies a profit of +1.02兆. The same arithmetic reconciles
    exactly on the previous year's row, so the check is sound and the row is
    not.

    When they disagree neither figure is picked. Choosing one would be a guess
    presented as data, and this project's rule is that a wrong number costs more
    than a missing one: missing is excluded from screens and scores, wrong is
    ranked.
    """
    net_income = _first(record, "NP", "Profit")
    payout = _first(record, "PayoutRatioAnn")
    if net_income is None or payout is None:
        return True
    if net_income < 0 < payout:
        logger.warning(
            "%s: the annual row disagrees with itself - net income %.3g but "
            "payout ratio %.3f, which requires a positive profit. Earnings "
            "figures (PER, ROE, net income) are left unset for this symbol.",
            symbol,
            net_income,
            payout,
        )
        return False
    return True


#: 集計値から出した比率と1株当たりから出した比率が、これ以上食い違ったら知らせる。
#:
#: 分割は倍率2以上で起きる。1割程度のずれは、自己株式を除いた期中平均株数で EPS が
#: 計算されることによるもので、正常。
_STALE_PER_SHARE_RATIO = 1.5


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    """``numerator / denominator``。分母が無い・0 なら ``None``。

    0 除算だけでなく ``None`` も弾くので、集計値が欠けていれば呼び出し側は次の
    候補へ落ちられる。
    """
    if numerator is None or not denominator:
        return None
    return numerator / denominator


def _warn_if_per_share_is_stale(
    symbol: str, aggregate: float | None, per_share: float | None
) -> None:
    """同じ比率が2通りで大きく食い違うなら、1株当たりの値が古い尺度にある。

    値そのものは集計値のほうを使うので実害は無いが、この差は分割の倍率そのもので、
    同じ古い尺度の値がどこか別の場所で使われていないかを疑う手掛かりになる。
    """
    if aggregate is None or per_share is None or aggregate <= 0 or per_share <= 0:
        return
    spread = max(aggregate, per_share) / min(aggregate, per_share)
    if spread >= _STALE_PER_SHARE_RATIO:
        logger.warning(
            "%s: PER が集計値で %.2f、1株当たりで %.2f（%.1f倍の差）。"
            "直近の通期開示のあとに株式分割があった可能性があります。集計値を使います。",
            symbol,
            aggregate,
            per_share,
            spread,
        )


def _latest_annual(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the newest record covering a full fiscal year, if there is one.

    When **no** record carries a period marker the payload cannot be split, so
    the newest is treated as annual - the documented fallback, and what the V1
    shape needs. Refusing to compute anything there would trade a wrong PER for
    no PER at all, on payloads that were never quarterly to begin with.

    Once *any* record is marked, the markers are trusted and only ``FY`` rows
    qualify: a mixed payload is exactly the case where a quarter would otherwise
    masquerade as a year.
    """
    if not any(_has_period_marker(record) for record in records):
        return _latest(records) if records else None
    annual = [record for record in records if _period_of(record) is FiscalPeriod.FY]
    return _latest(annual) if annual else None


def normalize_statement(
    symbol: str,
    records: list[dict[str, Any]],
    as_of: dt.date,
    price: float | None = None,
) -> Fundamentals:
    """Build a :class:`Fundamentals` snapshot from ``fins/summary`` records.

    Flow figures (sales, profit, EPS) are taken from the most recent **annual**
    disclosure; balance-sheet figures (equity, BPS, shares) from the most recent
    disclosure of any period.

    That split is the whole point. ``Sales``, ``NP`` and ``EPS`` are cumulative
    from the start of the fiscal year, so the newest disclosure is usually a
    quarter holding three or six months. Dividing a price by a half-year EPS
    doubles the PER, and every screen with a PER ceiling then rejects companies
    that are in fact cheap. Equity and BPS are point-in-time, so for those the
    freshest disclosure is simply the best one.

    Args:
        symbol: The security code.
        records: J-Quants summary records.
        as_of: Snapshot date.
        price: Current share price; enables PER, PBR, dividend yield, market cap.

    Returns:
        A fundamentals snapshot. Ratios needing a price stay ``None`` without one.

    Raises:
        DataError: If ``records`` is empty.
    """
    if not records:
        raise DataError(f"No J-Quants statements for {symbol!r}.")

    # No annual disclosure yet (a recent listing) means no trustworthy earnings
    # figure. Reporting a quarter as if it were a year would be worse than
    # reporting nothing, so the earnings-based ratios stay None.
    annual = _latest_annual(records)

    annual_records = [annual] if annual else []
    trustworthy = annual is None or _earnings_are_consistent(symbol, annual)

    revenue = _newest_value(annual_records, "Sales", "NetSales")
    net_income = _newest_value(annual_records, "NP", "Profit") if trustworthy else None
    eps = _newest_value(annual_records, "EPS") if trustworthy else None

    # Point-in-time fields: the newest record that has them, not merely the
    # newest record.
    #
    # **自己資本であって、純資産ではない。** ここは長らく `Eq`（純資産）を直に
    # 読んでいた。一括取り込み側（`normalize_statements`）は `ShEq` を先に見るよう
    # 直してあったのに、スナップショット側が取り残されていたため、**同じ銘柄の
    # snapshot と statements で分母が違っていた。** PBR と、報告 ROE が無いときの
    # 自前計算がその影響を受ける。
    equity, borrowed_equity = _newest_equity(records)
    bps = _newest_value(records, "BPS")
    dividend = _newest_value(records, "DivAnn")
    dividend_total = _newest_value(records, "DivTotalAnn")
    shares = _newest_value(records, "ShOutFY")

    # The exchange publishes its own ROE. Prefer it: it is computed against
    # equity attributable to owners, where ``Eq`` includes non-controlling
    # interests, so the two differ even when both are right.
    published_roe = _newest_value(annual_records, "ROE") if trustworthy else None
    roe = published_roe
    if roe is None and trustworthy and net_income is not None and equity:
        roe = net_income / equity
    market_cap = price * shares if (price is not None and shares) else None

    # 比率は集計値どうしで出す。1株当たりの値は開示時点の株数で報告され、価格は
    # 今日のもの。その間に分割が入ると、どちらも正しいまま比が桁で狂う。
    #
    # 実測（2026-08-10 の保存データ）: price/eps で出した PER は東京きらぼし
    # 1.228（時価総額÷純利益では 11.56）、住友電工 4.353（同 17.73）、花王
    # 12.324（同 24.23）。ずれの比 9.42 / 4.07 / 1.97 はそのまま分割の倍率で、
    # 銀行が PBR 0.14 倍の万年割安株として画面の先頭に並んでいた。
    #
    # 時価総額・純利益・純資産・配当総額はどれも会社全体の量なので、株数を何倍に
    # しても変わらない。
    per = _ratio(market_cap, net_income) or _ratio(price, eps)
    pbr = _ratio(market_cap, equity) or _ratio(price, bps)
    dividend_yield = plausible_dividend_yield(
        _ratio(dividend_total, market_cap) or _ratio(dividend, price), symbol
    )
    _warn_if_per_share_is_stale(symbol, per, _ratio(price, eps))
    if borrowed_equity and equity is not None:
        # 一括取り込みでは `normalize_statements` が同じことを警告するので、
        # ここは info にとどめる。**黙って混ぜないことのほうが目的である。**
        logger.info(
            "%s: ShEq（自己資本）が無く Eq（純資産）で代用しました。"
            "非支配株主持分を含むぶん、PBR と ROE の分母が大きくなります。",
            symbol,
        )

    if annual is None:
        logger.info(
            "%s has no annual disclosure in the fetched window; PER, ROE, "
            "revenue and net income are left unset rather than filled from a "
            "part-year figure.",
            symbol,
        )

    return Fundamentals(
        symbol=symbol,
        as_of=as_of,
        roe=roe,
        per=per,
        pbr=pbr,
        dividend_yield=dividend_yield,
        market_cap=market_cap,
        revenue=revenue,
        net_income=net_income,
    )


# V2 abbreviates period markers; V1-style spellings are kept as fallbacks.
_PERIOD_ALIASES: dict[str, FiscalPeriod] = {
    "1Q": FiscalPeriod.Q1,
    "Q1": FiscalPeriod.Q1,
    "2Q": FiscalPeriod.Q2,
    "Q2": FiscalPeriod.Q2,
    "HY": FiscalPeriod.Q2,  # a half-year report closes the second quarter
    "3Q": FiscalPeriod.Q3,
    "Q3": FiscalPeriod.Q3,
    "FY": FiscalPeriod.FY,
    "4Q": FiscalPeriod.FY,
}


# Chronological order within a fiscal year; alphabetical would put FY first.
_PERIOD_ORDER: dict[FiscalPeriod, int] = {
    FiscalPeriod.Q1: 1,
    FiscalPeriod.Q2: 2,
    FiscalPeriod.Q3: 3,
    FiscalPeriod.FY: 4,
}


def _text(record: dict[str, Any], *keys: str) -> str | None:
    """Return the first non-empty string among ``keys``."""
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _parse_date(value: str | None) -> dt.date | None:
    """Parse an ISO-ish J-Quants date, tolerating slashes and stray time parts."""
    if not value:
        return None
    text = value.replace("/", "-")[:10]
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        return None


def _parse_time(value: str | None) -> dt.time | None:
    """開示時刻を読む。``14:00:00`` も ``14:00`` も受ける。

    時刻は日付と違って**無いことがある**。読めなければ ``None`` を返し、
    「時刻不明」として扱わせる。ここで正午などの既定値を入れると、場中開示か
    引け後開示かを取り違えたまま先に進んでしまう - 一番避けたい壊れ方である。
    """
    if not value:
        return None
    text = str(value).strip()
    for shape in ("%H:%M:%S", "%H:%M"):
        try:
            return dt.datetime.strptime(text, shape).time()
        except ValueError:
            continue
    return None


#: 開示時刻を名乗りうるキー。v2 は略記、v1 は綴り。
_TIME_KEYS = ("DiscTime", "DisclosedTime")

#: 開示の種類を名乗りうるキー。
_DOC_TYPE_KEYS = ("DocType", "TypeOfDocument")

#: 当期通期の会社予想。**すべて実レスポンスで存在を確認した名前である。**
#:
#: `_FY_END_KEYS` は推測の略記だけを並べて「該当なし」を返し続けていた
#: （`fiscal_year_end` が一度も埋まらなかった原因）。同じ轍を踏まないよう、
#: ここには probe で実際に値が入っていた名前しか置かない。綴りの長い v1 形式は
#: 実物を見るまで足さない - 当てずっぽうを混ぜると、次に読む人が「確認済み」と
#: 誤解する。
_FORECAST_KEYS: dict[str, tuple[str, ...]] = {
    "revenue": ("FSales",),
    "operating_income": ("FOP",),
    "net_income": ("FNP",),
    "eps": ("FEPS",),
}


#: 自己資本を名乗りうるキー。**順番に意味がある。**
#:
#: `ShEq` は「自己資本」（親会社持分）、`Eq` は「純資産」（非支配株主持分を
#: 含む）である。J-Quants 自身のスキーマがそう書いている。**別の量である。**
#:
#: 実測（トヨタ 7203、2026-09-04）: 一括取り込みが `Eq` で上書きした結果、
#: 全年で自己資本が 0.91〜1.10兆 増えた。**差はちょうど非支配株主持分**で、
#: EDINET が入れていた親会社持分（26.2兆）が純資産（27.15兆）に化けていた。
#: 同じ行の BPS は親会社持分ベースのままなので、1行の中で定義が2つになった。
#:
#: この列は ROE（自己資本利益率）と PBR の分母に使われる。どちらも慣行として
#: 自己資本を使う。だから `ShEq` が先である。
_EQUITY_KEYS = ("ShEq", "ShareholdersEquity")

#: `ShEq` が無いときに代わりに読むもの。**黙って使わない。**
_EQUITY_FALLBACK_KEYS = ("Eq", "Equity")


def _equity_of(record: dict[str, Any]) -> tuple[float | None, bool]:
    """自己資本と、「純資産で代用したか」を返す。

    代用したこと自体は返り値に出す。**黙って混ぜない**——混ざったことに
    気付けないのが、この種の不具合が高く付く理由である。
    """
    equity = _first(record, *_EQUITY_KEYS)
    if equity is not None:
        return equity, False
    return _first(record, *_EQUITY_FALLBACK_KEYS), True


def _newest_equity(records: list[dict[str, Any]]) -> tuple[float | None, bool]:
    """新しい開示から順に自己資本を探し、「純資産で代用したか」を添えて返す。

    :func:`_newest_value` と同じ歩き方だが、**1つの開示の中では `ShEq` を
    `Eq` より先に見る。** 素の `_newest_value(records, "Eq", ...)` だと、
    自己資本を持っている開示があっても純資産のほうを掴む。
    """
    for record in sorted(records, key=_disclosure_key, reverse=True):
        equity, borrowed = _equity_of(record)
        if equity is not None:
            return equity, borrowed
    return None, False


#: Field names that may carry the period marker, newest spelling first.
_PERIOD_FIELDS = ("CurPerType", "Period", "TypeOfCurrentPeriod", "PeriodType")


def _period_of(record: dict[str, Any]) -> FiscalPeriod:
    """Map a record's period marker onto :class:`FiscalPeriod`.

    V2 calls this ``CurPerType``. Reading only the older spellings made every
    record look annual, and that is not a cosmetic mistake: ``Sales`` and ``NP``
    are **cumulative from the start of the fiscal year**, so a 3Q row holds nine
    months. Filed under FY alongside a real twelve-month row, the next
    year-over-year comparison invents about a third of a year's growth out of
    nothing - which is exactly what a screen showing three quarters of the
    market growing 10%+ looks like.

    Anything unrecognised is still treated as a full year, because some payloads
    genuinely omit the marker on annual rows. :func:`normalize_statements`
    counts those and warns, so a future rename is loud rather than silent.
    """
    marker = _text(record, *_PERIOD_FIELDS)
    if marker is None:
        return FiscalPeriod.FY
    return _PERIOD_ALIASES.get(marker.strip().upper(), FiscalPeriod.FY)


def _has_period_marker(record: dict[str, Any]) -> bool:
    """Whether the record said which period it covers at all."""
    return _text(record, *_PERIOD_FIELDS) is not None


def _fiscal_year_of(record: dict[str, Any]) -> int | None:
    """Determine the fiscal year a record belongs to.

    Prefers an explicit fiscal-year field, then the fiscal-year-end date, and
    finally the disclosure date - a disclosure names the year it reports on far
    more reliably than the year it was published, so it is the last resort.
    """
    explicit = _to_float(_text(record, "FY", "FiscalYear"))
    if explicit is not None and 1900 <= explicit <= 2999:
        return int(explicit)

    parsed = _fiscal_year_end_of(record)
    if parsed is not None:
        return parsed.year

    disclosed = _parse_date(_text(record, "DiscDate", "DisclosedDate"))
    return disclosed.year if disclosed else None


#: 決算期末日を名乗りうるキー。v2 は略記、v1 は綴り。
#:
#: **``CurFYEn`` は実データで確認した名前である。** ほかは推測で置いた候補で、
#: 実際の v2 レスポンスには1つも現れない - つまりこの一覧は、当てずっぽうの
#: 略記だけを並べて「該当なし」を返し続けていた。``fiscal_year_end`` が
#: J-Quants から一度も埋まっていなかったのはそのためで、例外は出ないので
#: 気付けなかった（``tools/jquants_disclosure_probe.py`` で実物のキー名を
#: 並べて初めて分かった）。
_FY_END_KEYS = ("CurFYEn", "FYEnd", "CurrentFiscalYearEndDate", "FiscalYearEnd", "PeriodEnd")


def _fiscal_year_end_of(record: dict[str, Any]) -> dt.date | None:
    """決算期末日そのものを返す。年だけでは権利確定日の月が決まらない。"""
    for key in _FY_END_KEYS:
        parsed = _parse_date(_text(record, key))
        if parsed is not None:
            return parsed
    return None


def normalize_statements(symbol: str, records: list[dict[str, Any]]) -> list[FinancialReport]:
    """Turn raw ``fins/summary`` records into a fiscal-period report series.

    Every record is kept, not just the newest: the payload already carries the
    company's disclosure history, and that history is exactly what growth rates
    and dividend streaks are computed from.

    Records whose fiscal year cannot be determined are dropped - placing them
    on the wrong year would corrupt a year-over-year comparison, which is worse
    than omitting them.

    Args:
        symbol: The security code.
        records: J-Quants summary records.

    Returns:
        Reports sorted oldest first. Restatements of the same period collapse
        to the latest disclosure.
    """
    by_period: dict[tuple[int, FiscalPeriod], tuple[str, FinancialReport]] = {}
    skipped = 0
    unmarked = 0
    substituted_equity = 0

    for record in records:
        fiscal_year = _fiscal_year_of(record)
        if fiscal_year is None:
            skipped += 1
            continue

        period = _period_of(record)
        if not _has_period_marker(record):
            unmarked += 1
        disclosed_text = _text(record, "DiscDate", "DisclosedDate") or ""
        equity, borrowed = _equity_of(record)
        if borrowed and equity is not None:
            substituted_equity += 1
        report = FinancialReport(
            symbol=symbol,
            fiscal_year=fiscal_year,
            period=period,
            disclosed_on=_parse_date(disclosed_text),
            disclosed_at=_parse_time(_text(record, *_TIME_KEYS)),
            doc_type=_text(record, *_DOC_TYPE_KEYS),
            forecast_revenue=_first(record, *_FORECAST_KEYS["revenue"]),
            forecast_operating_income=_first(record, *_FORECAST_KEYS["operating_income"]),
            forecast_net_income=_first(record, *_FORECAST_KEYS["net_income"]),
            forecast_eps=_first(record, *_FORECAST_KEYS["eps"]),
            fiscal_year_end=_fiscal_year_end_of(record),
            revenue=_first(record, "Sales", "NetSales"),
            operating_income=_first(record, "OP", "OperatingProfit"),
            net_income=_first(record, "NP", "Profit"),
            equity=equity,
            eps=_first(record, "EPS"),
            bps=_first(record, "BPS"),
            dividend_per_share=_first(record, "DivAnn"),
            shares_outstanding=_first(record, "ShOutFY"),
        )

        key = (fiscal_year, period)
        previous = by_period.get(key)
        # A restatement supersedes the earlier disclosure of the same period.
        if previous is None or disclosed_text >= previous[0]:
            by_period[key] = (disclosed_text, report)

    if skipped:
        logger.warning("Dropped %d %s statement(s) with no resolvable fiscal year", skipped, symbol)
    if unmarked and records:
        # Every record defaulting to annual is the signature of a renamed field,
        # and it corrupts quietly: quarterly figures are cumulative, so filing
        # nine months as a full year manufactures growth on the next comparison.
        level = logger.error if unmarked == len(records) else logger.warning
        level(
            "%d of %d %s statement(s) carried no period marker in any of %s "
            "and were filed as annual. Quarterly rows are cumulative, so this "
            "invents year-over-year growth. Check the payload's field names.",
            unmarked,
            len(records),
            symbol,
            ", ".join(_PERIOD_FIELDS),
        )

    if substituted_equity:
        # **列は「自己資本」である。** 純資産を入れるなら、入れたと言う。
        # 混ざったこと自体は誰も例外で教えてくれない。
        logger.warning(
            "%d of %d %s statement(s) had no ShEq (自己資本) and fell back to "
            "Eq (純資産, 非支配株主持分を含む). The two differ by the minority "
            "interest, and ROE and PBR read this column.",
            substituted_equity,
            len(records),
            symbol,
        )

    ordered = sorted(by_period.items(), key=lambda kv: (kv[0][0], _PERIOD_ORDER[kv[0][1]]))
    reports = [report for _key, (_disclosed, report) in ordered]

    # **restated が直せない期をここで名指しする。** 1つの値の中で尺度が混ざって
    # いるので、どの倍率を掛けても正しくならない。取り込みで1度だけ出す——
    # `restated` は増配率・CAGR・連続増配のそれぞれから呼ばれるため、あちらで
    # 出すと同じ行が並ぶ。
    annual = [report for report in reports if report.period is FiscalPeriod.FY]
    for report, factor in dividends_crossing_a_split(annual):
        logger.warning(
            "%s FY%s: 株式数が %.2f 倍になった期の1株配当が %.2f です。"
            "分割前の中間配当と分割後の期末配当の和になっている可能性があり、"
            "restated では直せません（配当性向と増配率が狂います）。",
            symbol,
            report.fiscal_year,
            factor,
            report.dividend_per_share,
        )

    return reports


def _default_fetcher(api_key: SecretStr | None) -> StatementFetcher:
    """Build a fetcher that calls the J-Quants V2 statements endpoint."""

    def fetch(symbol: str) -> list[dict[str, Any]]:
        import httpx

        headers = {"x-api-key": api_key.get_secret_value()} if api_key else {}
        records: list[dict[str, Any]] = []
        pagination_key: str | None = None
        with httpx.Client(timeout=30.0) as client:
            while True:
                params = {"code": symbol}
                if pagination_key:
                    params["pagination_key"] = pagination_key
                response = client.get(_STATEMENTS_URL, headers=headers, params=params)
                raise_for_status(response, f"statements for {symbol}")
                payload = response.json()
                # V2 returns {"data": [...]}; older shapes used "statements".
                records.extend(payload.get("data") or payload.get("statements") or [])
                pagination_key = payload.get("pagination_key")
                if not pagination_key:
                    break
        return records

    return fetch


class JQuantsFundamentalsProvider:
    """Fetch Japanese fundamentals via the J-Quants statements API."""

    name = "jquants"

    def __init__(
        self,
        api_key: SecretStr | None = None,
        fetcher: StatementFetcher | None = None,
        clock: Clock | None = None,
        price_source: PriceLookup | None = None,
    ) -> None:
        """Create the provider.

        Args:
            api_key: J-Quants V2 API key.
            fetcher: Callable performing the raw fetch; injected in tests.
            clock: Callable returning the snapshot date; defaults to today.
            price_source: Optional callable returning a symbol's current price,
                which enables PER, PBR, dividend yield, and market cap.
        """
        self._fetch = fetcher or _default_fetcher(api_key)
        self._today = clock or dt.date.today
        self._price_source = price_source

    def fetch_fundamentals(self, symbol: str) -> Fundamentals:
        """Fetch and normalize the latest fundamentals for ``symbol``."""
        records = self._fetch(symbol)
        price: float | None = None
        if self._price_source is not None:
            try:
                price = self._price_source(symbol)
            except Exception as exc:  # price is optional - never fail the fetch
                logger.warning("Price lookup failed for %s: %s", symbol, exc)
        snapshot = normalize_statement(symbol, records, self._today(), price)
        logger.info("Fetched J-Quants fundamentals for %s", symbol)
        return snapshot

    def fetch_snapshot_and_statements(
        self, symbol: str
    ) -> tuple[Fundamentals, list[FinancialReport]]:
        """Return both products of a single ``fins/summary`` request.

        The snapshot and the series come from the same records, so fetching them
        separately spends two requests on one answer. At TSE Prime scale that is
        1,600 avoidable calls, which is the difference between a bulk load
        finishing and a rate limit stopping it.

        This exists because storing only the series left every valuation screen
        silently empty on JP names: ``screen --max-per`` reads the snapshot
        table, and nothing was writing to it outside the US path.
        """
        records = self._fetch(symbol)
        price: float | None = None
        if self._price_source is not None:
            try:
                price = self._price_source(symbol)
            except Exception as exc:  # price is optional - never fail the fetch
                logger.warning("Price lookup failed for %s: %s", symbol, exc)
        snapshot = normalize_statement(symbol, records, self._today(), price)
        reports = normalize_statements(symbol, records)
        logger.info("Fetched J-Quants snapshot and %d statement(s) for %s", len(reports), symbol)
        return snapshot, reports

    def fetch_statements(self, symbol: str) -> list[FinancialReport]:
        """Fetch ``symbol``'s full disclosed statement history, oldest first.

        The same request behind :meth:`fetch_fundamentals` already returns every
        disclosure the plan covers; this keeps them all instead of collapsing to
        the latest one.
        """
        reports = normalize_statements(symbol, self._fetch(symbol))
        logger.info("Fetched %d J-Quants statement(s) for %s", len(reports), symbol)
        return reports
