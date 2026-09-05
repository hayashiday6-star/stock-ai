"""Growth, dividend, and streak metrics derived from a statement series.

Everything here reads a list of :class:`~stock_ai.data.types.FinancialReport`
sorted oldest first - the annual series, unless stated otherwise. Mixing
quarters into a year-over-year comparison would silently corrupt it, so the
caller is expected to have filtered to one period type (which
``FinancialStatementRepository.get_reports`` does by default).

Every function returns ``None`` when the series cannot support the answer,
rather than a zero that reads as a real measurement.

**1株当たりの値は、比べる前に分割の尺度を揃える。** 開示は各期の当時の株数で
報告されるので、分割をまたぐと系列が不連続に飛ぶ。実測（日立 6501, 保存済み
データ）:

- 2024年度の1株配当 180円 と 2025年度の 43円 を並べると増配率 **-76.1%**。
  1:5分割を挟んでいるだけで、実際は **+19.4% の増配**。
- 連続増配年数が **1** と出る。分割の年で系列が切れるため。
- EPS の4年 CAGR が **-26.4%**。

どれも例外は出ない。数字が変わるだけで、減配した会社として画面に出る。
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence

from stock_ai.data.types import FinancialReport

#: 当時の株数で報告される項目。年をまたぐ前に尺度を揃える必要がある。
PER_SHARE_FIELDS = frozenset({"eps", "bps", "dividend_per_share"})

#: 株式数の比がこれ以上動いたときだけ分割を疑う。
#:
#: 自社株買いや新株発行でも株数は動く。そちらは**実際の**1株当たりの増減なので、
#: 均してはいけない。日立の分割以外の年の比は 0.97〜0.99 に収まっており、実際の
#: 分割は 5.0 だった。1.5 はその間に十分な幅で入る。
_SPLIT_THRESHOLD = 1.5

#: 検出した比が「きれいな倍率」からどれだけ離れてよいか。
#:
#: 分割と自社株買いが同じ年に起きると比はぴったりにならない。日立の 4.94 は
#: 5 から 1.2% ずれている。
_SPLIT_TOLERANCE = 0.05


def split_factor(previous_shares: float | None, current_shares: float | None) -> float | None:
    """2期の株式数から分割の倍率を読む。分割でなければ ``None``。

    整数倍（またはその逆数）に十分近いときだけ分割とみなす。数％の増減は自社株買い
    や新株発行で、それによる1株当たりの変化は現実のものだから均さない。
    """
    if not _usable(previous_shares) or not _usable(current_shares):
        return None
    if previous_shares <= 0 or current_shares <= 0:  # type: ignore[operator]
        return None

    ratio = current_shares / previous_shares  # type: ignore[operator]
    if ratio >= _SPLIT_THRESHOLD:
        candidate = float(round(ratio))
    elif ratio <= 1.0 / _SPLIT_THRESHOLD:
        divisor = round(1.0 / ratio)
        candidate = 1.0 / divisor if divisor else 0.0
    else:
        return None

    if candidate <= 0:
        return None
    if abs(ratio - candidate) / candidate > _SPLIT_TOLERANCE:
        # きれいな倍率から遠い。合併に伴う大量発行などで、分割ではない。
        return None
    return candidate


def dividends_crossing_a_split(
    reports: Sequence[FinancialReport],
) -> list[tuple[FinancialReport, float]]:
    """分割が起きた期のうち、年間配当を持つものと、その倍率を返す。

    **``restated`` はこの期の配当を直せない。** 直せるのは1期まるごとが1つの
    尺度に乗っているときだけで、分割をまたぐ期の年間配当は「分割前の中間配当
    ＋ 分割後の期末配当」になっている可能性がある。そのままでも倍率で割っても
    正しくならない。**どの倍率を掛けても正しくない値は、直すのではなく言う。**

    実例（`docs/JQUANTS_EXIT.md`）: トヨタ 7203 の FY2022 は 1株配当 148.00 に
    対して EPS 205.23 で、配当性向が 72%。前後の年は 20〜33% で、分割は
    2021-09-30、FY2022 はそれをまたぐ。**同じ行の EPS・BPS・株式数は分割後の
    尺度で公表値と一致している**ので、その行の中で配当だけ尺度が違う。

    疑いであって断定ではない。期末配当しか出さない会社なら、またいでいても
    混ざらない。返すのは「確かめる価値のある期」である。

    Args:
        reports: 古い順の年次の並び。

    Returns:
        ``(その期の報告, 分割の倍率)`` の並び。分割が無ければ空。
    """
    suspect = []
    for previous, current in zip(reports, reports[1:], strict=False):
        factor = split_factor(previous.shares_outstanding, current.shares_outstanding)
        if factor is not None and current.dividend_per_share is not None:
            suspect.append((current, factor))
    return suspect


def restated(reports: Sequence[FinancialReport]) -> list[FinancialReport]:
    """1株当たりの値を、**最新期の株数の尺度**に揃えた並びを返す。

    絶対額（売上・純利益・自己資本）はそのまま。分割で変わらないものを触ると、
    直したつもりで別の誤差を入れることになる。

    日立で検算できる: 2024年度の EPS 634.57 を5で割ると 126.91 で、EDINET が
    同じ期を「前々期」として報告している restated EPS と一致する。

    **1期まるごとの尺度しか直せない。** 1つの値の中で尺度が混ざっているものは、
    どの倍率を掛けても正しくならない。分割をまたぐ期の年間配当がそれで、中間
    配当が分割前、期末配当が分割後の尺度で報告され、その和が1つの列に入りうる。
    その期は ``dividends_crossing_a_split`` が名指しする。取り込みのときに1度だけ
    警告を出す——``restated`` は何度も呼ばれるので、ここで出すと同じ行が並ぶ。
    """
    if len(reports) < 2:
        return list(reports)

    scales = [1.0] * len(reports)
    cumulative = 1.0
    for index in range(len(reports) - 1, 0, -1):
        factor = split_factor(
            reports[index - 1].shares_outstanding, reports[index].shares_outstanding
        )
        if factor is not None:
            cumulative *= factor
        scales[index - 1] = cumulative

    return [
        report.model_copy(
            update={
                field: value / scale
                for field in PER_SHARE_FIELDS
                if (value := getattr(report, field)) is not None
            }
        )
        if scale != 1.0
        else report
        for report, scale in zip(reports, scales, strict=True)
    ]


def _usable(value: float | None) -> bool:
    """Whether a figure can take part in arithmetic."""
    return value is not None and math.isfinite(value)


def _growth(previous: float | None, current: float | None) -> float | None:
    """Year-over-year change as a fraction, or ``None`` if not meaningful.

    A non-positive base makes the percentage meaningless - going from a JPY 1bn
    loss to a JPY 2bn profit is not "-300% growth" - so those return ``None``
    instead of a number that would sort wrongly against real growth rates.
    """
    if not _usable(previous) or not _usable(current) or previous <= 0:
        return None
    return current / previous - 1.0


def _field_growth(reports: Sequence[FinancialReport], field: str, periods: int = 1) -> float | None:
    """Growth of ``field`` over the last ``periods`` fiscal years."""
    if len(reports) <= periods:
        return None
    return _growth(
        getattr(reports[-1 - periods], field),
        getattr(reports[-1], field),
    )


def revenue_growth(reports: Sequence[FinancialReport], periods: int = 1) -> float | None:
    """Revenue growth over the last ``periods`` fiscal years (増収率)."""
    return _field_growth(reports, "revenue", periods)


def profit_growth(reports: Sequence[FinancialReport], periods: int = 1) -> float | None:
    """Net income growth over the last ``periods`` fiscal years (増益率)."""
    return _field_growth(reports, "net_income", periods)


def dividend_growth(reports: Sequence[FinancialReport], periods: int = 1) -> float | None:
    """Dividend-per-share growth over the last ``periods`` fiscal years (増配率).

    分割の尺度を揃えてから比べる。揃えないと、分割した会社が減配した会社に見える。
    """
    return _field_growth(restated(reports), "dividend_per_share", periods)


def cagr(reports: Sequence[FinancialReport], field: str, years: int) -> float | None:
    """Compound annual growth rate of ``field`` over ``years`` fiscal years.

    Smoother than a single year-over-year figure, which one exceptional year
    can dominate.
    """
    if years <= 0 or len(reports) <= years:
        return None
    if field in PER_SHARE_FIELDS:
        reports = restated(reports)
    start = getattr(reports[-1 - years], field)
    end = getattr(reports[-1], field)
    if not _usable(start) or not _usable(end) or start <= 0 or end <= 0:
        return None
    return (end / start) ** (1.0 / years) - 1.0


def _streak(reports: Sequence[FinancialReport], holds: Callable[[float, float], bool]) -> int:
    """Count consecutive most-recent years where ``holds(previous, current)``.

    Walks backwards from the latest year and stops at the first break or at the
    first pair that cannot be compared, so a gap in the data ends the streak
    rather than being counted through.
    """
    reports = restated(reports)
    count = 0
    for current, previous in zip(reversed(reports), reversed(reports[:-1]), strict=False):
        current_value = current.dividend_per_share
        previous_value = previous.dividend_per_share
        if not _usable(current_value) or not _usable(previous_value):
            break
        if not holds(previous_value, current_value):
            break
        count += 1
    return count


def consecutive_dividend_increases(reports: Sequence[FinancialReport]) -> int:
    """Consecutive years the dividend per share was raised (連続増配年数).

    Counted as year-over-year *increases*, so a company on its 10th raise
    returns 10. A flat year ends the streak; a missing figure ends it too,
    because an unknown year cannot be shown to have been a raise.
    """
    return _streak(reports, lambda previous, current: current > previous)


def consecutive_dividend_non_cuts(reports: Sequence[FinancialReport]) -> int:
    """Consecutive years the dividend was maintained or raised (連続非減配年数)."""
    return _streak(reports, lambda previous, current: current >= previous)


def latest_payout_ratio(reports: Sequence[FinancialReport]) -> float | None:
    """Payout ratio of the most recent report that can express one."""
    for report in reversed(reports):
        ratio = report.payout_ratio
        if ratio is not None:
            return ratio
    return None
