"""#5 上方修正 — 件数センサス。**リターンを1つも計算しない。**

事前登録 `docs/PREREG_REVISION_JP.md` §0 の表を埋める。

### 名前を当てずっぽうで書かない

`FNP`（会社予想の当期純利益）と会計年度末の綴りは、**このモジュールで決めない。**
`jquants_fundamentals` が「実レスポンスで存在を確認した名前しか置かない」と
書いて持っているので、そこから引く。

**同じ轍が既に1度ある**——推測の略記だけを並べて「該当なし」を返し続け、
`fiscal_year_end` が一度も埋まらなかった。

### 読めなかったものを 0 にしない

予想が読めない行、前回の予想が無い行、会計年度末が読めない行は、**それぞれ
数えて出す。** まとめて「修正なし」に落とすと、**読めていないことが「修正が
無かった」に化ける。**
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Mapping, Sequence
from statistics import median

from stock_ai.backtest.forecast_revision import DEFAULT_MIN_CHANGE
from stock_ai.backtest.margin_census import busiest_share
from stock_ai.core.logging import get_logger
from stock_ai.data.jquants_details import STATEMENT_MARKER, parse_date
from stock_ai.data.jquants_fundamentals import (
    _FORECAST_KEYS,
    _FY_END_KEYS,
    _fiscal_year_end_of,
)
from stock_ai.data.universe import four_digit_code

logger = get_logger(__name__)

#: 上方修正と呼ぶ最低幅。**この登録のために決めた数字ではない。**
#:
#: `forecast_revision.DEFAULT_MIN_CHANGE` を引く。決め打つと、その 5% が
#: どこから来たのかを書けない。
UPWARD_MIN = DEFAULT_MIN_CHANGE

#: 予想修正を表す書類種別。**業績予想だけ。** 配当予想は §9 で外してある。
REVISION_TYPE = "EarnForecastRevision"

#: 見る科目。`jquants_fundamentals` が実レスポンスで確認した名前を引く。
FORECAST_KEYS: tuple[str, ...] = _FORECAST_KEYS["net_income"]

#: 会計年度末を名乗りうる鍵。同じく `jquants_fundamentals` から引く。
#:
#: **`FS` の中を探していた。** 一括 CSV では `CurFYEn` も `FNP` も**原本の列
#: そのもの**で、`FS` には鍵が1つも入っていない（2026-09-16、72,156 件で確認）。
#: `jquants_fundamentals` が「実レスポンスで確認した」と書いているのは、まさに
#: この**生の行**のことだった。**確認した場所と、読む場所を取り違えていた。**
FISCAL_KEYS: tuple[str, ...] = _FY_END_KEYS

#: OOS がこれを割ったら設計を見直す（§10）。
MIN_EVENTS_OOS = 1_000

#: `FNP` が空だった行で、代わりに何が入っているかを見る列。
#:
#: **`FNC…` は単体（非連結）である。** `F…` は連結。**混ぜない**——「連結と
#: 単体を取り違える」は、このプロジェクトが名指しで戒めている形そのものである。
#: ここは**数えるだけ**で、読み替えはしない。
NEIGHBOURS: tuple[str, ...] = (
    "FNP",
    "FNCNP",
    "FOP",
    "FNCOP",
    "FSales",
    "FNCSales",
    "FEPS",
    "FNCEPS",
    "FDivAnn",
    "FNP2Q",
    "NxFNP",
)


@dataclasses.dataclass(frozen=True)
class RevisionEvent:
    """1件の上方修正。**リターンは持たない。**"""

    symbol: str
    disclosed_on: dt.date
    change: float
    on_statement_day: bool
    """同じ銘柄・同じ日に決算短信があったか。**§2 で外す側。**"""


@dataclasses.dataclass
class Readability:
    """読めたもの・読めなかったものの内訳。**0 に落とさず数える。**"""

    rows: int = 0
    no_forecast: int = 0
    no_fiscal_year: int = 0
    no_previous: int = 0
    downward: int = 0
    too_small: int = 0
    upward: int = 0
    missing_fields: dict[str, int] = dataclasses.field(default_factory=dict)
    """`FNP` が空だった行で、代わりに埋まっていた列と件数。

    **読み替えるためではない。** どういう行が落ちているのかを見るためである。
    """

    missing_by_year: dict[int, int] = dataclasses.field(default_factory=dict)
    """`FNP` が空だった行の年ごとの件数。**時代に偏っていないかを見る。**"""

    keys_seen: dict[str, int] = dataclasses.field(default_factory=dict)
    """`EarnForecastRevision` の行に実際に載っていた鍵と件数。

    **予想が1件も読めなかったときに、ここが答える。** 「無い」のか「名前が
    違う」のかを分けられる。
    """

    def missing_profile(self) -> list[tuple[str, int, float]]:
        """`FNP` が空の行で何が埋まっていたか。**件数の多い順に、割合つき。**

        **割合で見る。** 件数だけだと「1件でもあれば」で読んでしまう
        （`CLAUDE.md`「『ゼロでない』を根拠に断定しない」）。
        """
        if not self.no_forecast:
            return []
        return [
            (name, count, count / self.no_forecast)
            for name, count in sorted(
                self.missing_fields.items(), key=lambda pair: (-pair[1], pair[0])
            )
        ]

    def inventory(self, limit: int = 20) -> str:
        """`FS` に実際に載っていた鍵。**「無い」と「名前が違う」を分ける。**"""
        if not self.keys_seen:
            return "`FS` に鍵が1つも無い。"
        ordered = sorted(self.keys_seen.items(), key=lambda pair: (-pair[1], pair[0]))
        names = "、".join(f"{key}({count:,})" for key, count in ordered[:limit])
        tail = f" ほか {len(ordered) - limit} 個" if len(ordered) > limit else ""
        return f"実際に載っていた鍵: {names}{tail}。"

    def warnings(self) -> list[str]:
        """気付かなくても目に入るべきこと。"""
        found: list[str] = []
        if not self.rows:
            found.append(f"**`{REVISION_TYPE}` の行が1件も無い。** 原本を読めていない。")
            return found
        # **列ごとに、全滅したかどうかを見る。**
        #
        # 最初は会社予想の列にしか鍵の一覧を出していなかった。予想は読めたのに
        # **会計年度末が 72,156 件すべてで読めず**、一覧は出なかった
        # （2026-09-16）。**表に出ていることと、目に入ることは別である。**
        # 見張りは、見ている列を絞らない。
        for label, missing, names in (
            ("会社予想", self.no_forecast, FORECAST_KEYS),
            ("会計年度末", self.no_fiscal_year, FISCAL_KEYS),
        ):
            if missing == self.rows:
                found.append(
                    f"**{label}を1件も読めなかった。** 探した名前は {names}。"
                    f"{self.inventory()} **「無い」ではなく「名前が違う」を疑う。**"
                )
            elif missing:
                found.append(
                    f"{label}を読めなかった行が {missing:,} 件ある"
                    f"（{missing / self.rows:.0%}）。**0 に落としていない。**"
                )
        if self.no_previous:
            found.append(
                f"前回の予想が無くて比べられなかった行が {self.no_previous:,} 件ある"
                f"（{self.no_previous / self.rows:.0%}）。"
                "**原本の先頭は「そこで変わった」ではなく「そこから見え始めた」。**"
            )
        return found


def _forecast_of(record: Mapping[str, str]) -> float | None:
    """会社予想の当期純利益。読めなければ ``None``。**0 を返さない。**"""
    for name in FORECAST_KEYS:
        text = record.get(name)
        if text is not None and text.strip():
            try:
                return float(text)
            except ValueError:
                return None
    return None


def find_upward(
    records: Sequence[Mapping[str, str]],
    min_change: float = UPWARD_MIN,
) -> tuple[list[RevisionEvent], Readability]:
    """上方修正のイベントを拾う。**リターンを1つも計算しない。**

    同じ会計年度の中でだけ比べる。**年度をまたいだ比較は「修正」ではなく、
    別の期の話である。**

    比べる相手は、その銘柄・その年度で**直前に見えた予想**である。書類種別は
    問わない——決算短信が出した予想を、後の修正が上書きする形が普通である。

    Args:
        records: `fins/summary` の生の行（列名 → 値）。順序は問わない。
        min_change: 上方修正と呼ぶ最低幅。

    Returns:
        ``(イベント, 読めた内訳)``。イベントは開示日の昇順。
    """
    rows: list[tuple[dt.date, str, str, Mapping[str, str]]] = []
    for record in records:
        symbol = four_digit_code((record.get("Code") or "").strip())
        day = parse_date(record.get("DiscDate"))
        if symbol is None or day is None:
            continue
        rows.append((day, symbol, (record.get("DocType") or "").strip(), record))

    ordered = sorted(rows, key=lambda row: (row[0], row[1], row[3].get("DiscNo") or ""))
    statement_days = {
        (symbol, day) for day, symbol, doc_type, _ in ordered if STATEMENT_MARKER in doc_type
    }

    latest: dict[tuple[str, dt.date], float] = {}
    found: list[RevisionEvent] = []
    seen = Readability()

    for day, symbol, doc_type, record in ordered:
        fiscal = _fiscal_year_end_of(record)
        forecast = _forecast_of(record)
        revision = doc_type == REVISION_TYPE

        # **列ごとに独立に数える。** 先に `continue` すると、後ろのカウンタが
        # 一度も動かず **0 が「読めた」に見える**（2026-09-16 に実際そうなった
        # ——年度末が全滅していたので、予想の欄は 0 のままだった）。
        if revision:
            seen.rows += 1
            for key in record:
                seen.keys_seen[key] = seen.keys_seen.get(key, 0) + 1
            if fiscal is None:
                seen.no_fiscal_year += 1
            if forecast is None or forecast == 0.0:
                seen.no_forecast += 1
                seen.missing_by_year[day.year] = seen.missing_by_year.get(day.year, 0) + 1
                filled = [
                    name for name in NEIGHBOURS if (record.get(name) or "").strip() not in {"", "0"}
                ]
                for name in filled or ["(どれも空)"]:
                    seen.missing_fields[name] = seen.missing_fields.get(name, 0) + 1

        if fiscal is None or forecast is None or forecast == 0.0:
            continue

        key = (symbol, fiscal)
        previous = latest.get(key)
        # **先に覚える前に比べる。** 覚えてから比べると、自分自身と比べることになる。
        if revision:
            if previous is None:
                seen.no_previous += 1
            else:
                # **前回予想を分母にする。** 負の予想（赤字見込み）から
                # 正に変わる形もあるので、絶対値で割る——負で割ると符号が反転する。
                change = (forecast - previous) / abs(previous)
                if change >= min_change:
                    seen.upward += 1
                    found.append(
                        RevisionEvent(
                            symbol=symbol,
                            disclosed_on=day,
                            change=change,
                            on_statement_day=(symbol, day) in statement_days,
                        )
                    )
                elif change <= -min_change:
                    seen.downward += 1
                else:
                    seen.too_small += 1
        latest[key] = forecast

    return found, seen


@dataclasses.dataclass(frozen=True)
class RevisionCensusReport:
    """§0 の表。**リターンは入っていない。**"""

    events: int
    by_year: dict[int, int]
    first: dt.date | None
    last: dt.date | None
    days_with_events: int
    busiest_share: float
    same_day_median: int
    on_statement_day: int
    after_liquidity: int
    split_on: dt.date | None
    events_is: int
    events_oos: int
    change_median: float
    readability: Readability
    days_is: int = 0
    days_oos: int = 0
    """**独立な観測は「日」である。** 同じ日の修正は等加重の1つにまとめるので、
    系列の長さはイベント数ではなく**日数**になる。

    **件数で割ると n を水増しする。** IS は 1,827 件が 831 日にまとまった——
    2.2倍である。検出できる差はその平方根ぶん、**1.48倍甘く出ていた**
    （2026-09-17）。
    """

    @property
    def standalone(self) -> int:
        """決算と別の日に出たもの。**§2 で残す側。**"""
        return self.events - self.on_statement_day

    def summary(self) -> str:
        """1行のまとめ。"""
        if not self.events:
            return "上方修正が1件も無い。**数えていない。**"
        return (
            f"上方修正 {self.events:,} 件（{self.first} 〜 {self.last}）。"
            f"決算と同じ日が {self.on_statement_day:,} 件で、**外して残るのが "
            f"{self.standalone:,} 件**。流動性を通すと {self.after_liquidity:,} 件。"
        )

    def warnings(self) -> list[str]:
        """気付かなくても目に入るべきこと。**表を読ませない。**"""
        found = list(self.readability.warnings())
        if not self.events:
            return found
        if self.busiest_share > 0.40:
            found.append(
                f"**上位1割の日が全体の {self.busiest_share:.0%} を占める。** "
                "同じ日に固まると独立な観測が減り、標準誤差が膨らむ。"
            )
        if self.days_oos and self.events_oos and self.days_oos < self.events_oos / 2:
            found.append(
                f"**OOS の {self.events_oos:,} 件は {self.days_oos:,} 日に固まっている。** "
                "独立な観測は日のほうで、**件数で検出力を計算すると甘く出る。**"
            )
        if self.events_oos and self.events_oos < MIN_EVENTS_OOS:
            found.append(
                f"**OOS のイベントが {self.events_oos:,} 件しかない。** "
                f"§10 の中止条件（{MIN_EVENTS_OOS:,} 件）を割っている。設計を見直すこと。"
            )
        if self.after_liquidity < self.standalone:
            lost = self.standalone - self.after_liquidity
            found.append(
                f"流動性の下限で {lost:,} 件落ちた"
                f"（{lost / self.standalone:.0%}）。**イベント型は件数が命である。**"
            )
        return found


def census(
    records: Sequence[Mapping[str, str]],
    liquid_on: object = None,
    split_on: dt.date | None = None,
    min_change: float = UPWARD_MIN,
) -> RevisionCensusReport:
    """§0 の表を埋める。**リターンを1つも計算しない。**

    Args:
        records: `fins/summary` の生の行（列名 → 値）。
        liquid_on: ``(symbol, date) -> bool``。省略すると絞らない。
        split_on: IS と OOS の境。省略すると覆う期間の**真ん中**。
        min_change: 上方修正と呼ぶ最低幅。

    Returns:
        :class:`RevisionCensusReport`。
    """
    events, seen = find_upward(records, min_change=min_change)
    if not events:
        return RevisionCensusReport(
            0, {}, None, None, 0, float("nan"), 0, 0, 0, None, 0, 0, float("nan"), seen, 0, 0
        )

    # **決算と同じ日は外す**（§2）。#2・#3 と同じ日付集合を使わないため。
    kept = [event for event in events if not event.on_statement_day]
    liquid = [
        event for event in kept if liquid_on is None or liquid_on(event.symbol, event.disclosed_on)
    ]

    per_day: dict[dt.date, int] = {}
    by_year: dict[int, int] = {}
    for event in liquid:
        per_day[event.disclosed_on] = per_day.get(event.disclosed_on, 0) + 1
        by_year[event.disclosed_on.year] = by_year.get(event.disclosed_on.year, 0) + 1

    covered = (events[0].disclosed_on, events[-1].disclosed_on)
    split = split_on or covered[0] + (covered[1] - covered[0]) / 2

    report = RevisionCensusReport(
        events=len(events),
        by_year=dict(sorted(by_year.items())),
        first=covered[0],
        last=covered[1],
        days_with_events=len(per_day),
        busiest_share=busiest_share(list(per_day.values())) if per_day else float("nan"),
        same_day_median=int(median(per_day.values())) if per_day else 0,
        on_statement_day=sum(1 for event in events if event.on_statement_day),
        after_liquidity=len(liquid),
        split_on=split,
        events_is=sum(1 for event in liquid if event.disclosed_on <= split),
        events_oos=sum(1 for event in liquid if event.disclosed_on > split),
        days_is=len({event.disclosed_on for event in liquid if event.disclosed_on <= split}),
        days_oos=len({event.disclosed_on for event in liquid if event.disclosed_on > split}),
        change_median=median([event.change for event in liquid]) if liquid else float("nan"),
        readability=seen,
    )
    logger.info(
        "上方修正センサス: %d 件、決算と別の日 %d 件、流動性通過 %d 件（IS %d / OOS %d）",
        report.events,
        report.standalone,
        report.after_liquidity,
        report.events_is,
        report.events_oos,
    )
    return report
