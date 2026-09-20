"""配当金情報（`/fins/dividend`）を読む。

Premium 専用で、20年前まで遡れる。

## 1銘柄1行ではない

**同じ公表日に、期の違う行が並ぶ。** 配布サンプルでは 2022-04-26 に3行あり、
`IFTerm` が `2022-09` / `2023-03` / `2022-03` である。

```
PubDate=2022-04-26  RefNo=...B00020  IFTerm=2022-09
PubDate=2022-04-26  RefNo=...B00021  IFTerm=2023-03
PubDate=2022-04-26  RefNo=...B00019  IFTerm=2022-03
```

**足すと年間配当が3倍になる。** 例外は出ないし、桁も変わらないので、
利回りが「やけに高い会社」として並ぶだけである。期ごとに1本にするには
:func:`latest_by_term` を通すこと。

## 訂正がある

同じ期について後から別の行が出る。**新しいほうだけを使う。** 公表日時が
同じときは `RefNo` の大きいほうを後とみなす——同じ日の中の順序を、他に
決める材料が無いためである。

## 符号は符号のまま持つ

`StatCode` / `IFCode` / `FRCode` / `CommSpecCode` の意味は、配布サンプル
からは分からない。**分からないものに名前を付けない。** 文字列のまま持って
おき、原本を落としたあとに実物の分布を見てから決める。

## 分割をまたぐ配当は直せない

1株あたりの配当は、分割の前後で尺度が変わる。**分割日をまたぐ区間の配当を
足すと、尺度の違う値を足すことになる。** これは倍率で直せる種類の間違いでは
ない（`docs/HYPOTHESES.md` に記録がある）ので、ここでは**名前を付けて置く**
だけにしてある——:func:`straddles_a_split` を見ること。
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Iterable
from pathlib import Path

from stock_ai.core.logging import get_logger
from stock_ai.data.jquants_bulk import records_from_csv
from stock_ai.data.jquants_details import parse_time
from stock_ai.data.jquants_margin import parse_date, parse_number
from stock_ai.data.universe import four_digit_code

logger = get_logger(__name__)


@dataclasses.dataclass(frozen=True)
class Dividend:
    """配当の1行。**1銘柄1行ではなく、1銘柄1期1回の公表である。**"""

    symbol: str
    published_on: dt.date
    published_at: dt.time | None
    reference: str
    """`RefNo`。同じ日の複数行を区別できる唯一の列。"""

    term: str
    """`IFTerm`。**これが違えば別の配当である。足さない。**"""

    rate: float | None
    """`DivRate`。1株あたり。**分割の前後で尺度が変わる。**"""

    ordinary_rate: float | None
    special_rate: float | None
    ex_date: dt.date | None
    record_date: dt.date | None
    pay_date: dt.date | None
    status_code: str
    """`StatCode`。**意味は分からないので符号のまま持つ。**"""


def parse_dividends(payload: bytes) -> list[Dividend]:
    """配当の CSV を読む。**公表日か銘柄コードの無い行は落とす。**"""
    items: list[Dividend] = []
    for row in records_from_csv(payload):
        symbol = four_digit_code((row.get("Code") or "").strip())
        published = parse_date(row.get("PubDate"))
        if symbol is None or published is None:
            continue
        items.append(
            Dividend(
                symbol=symbol,
                published_on=published,
                published_at=parse_time(row.get("PubTime")),
                reference=(row.get("RefNo") or "").strip(),
                term=(row.get("IFTerm") or "").strip(),
                rate=parse_number(row.get("DivRate")),
                ordinary_rate=parse_number(row.get("CommDivRate")),
                special_rate=parse_number(row.get("SpecDivRate")),
                ex_date=parse_date(row.get("ExDate")),
                record_date=parse_date(row.get("RecDate")),
                pay_date=parse_date(row.get("PayDate")),
                status_code=(row.get("StatCode") or "").strip(),
            )
        )
    return items


def latest_by_term(dividends: Iterable[Dividend]) -> dict[tuple[str, str], Dividend]:
    """``(銘柄, 期)`` ごとに**最後の公表だけ**を残す。

    そのまま足すと、同じ公表日に並んだ期の違う行が全部入り、**年間配当が
    3倍になる。** 桁も変わらないので、利回りの高い会社として並ぶだけである。

    公表日時が同じときは `RefNo` の大きいほうを後とみなす。同じ日の中の順序を
    決める材料が他に無いためで、**推測であることを名前に残しておく**より、
    ここに書いておくほうが読める。
    """
    best: dict[tuple[str, str], Dividend] = {}
    for item in dividends:
        key = (item.symbol, item.term)
        current = best.get(key)
        if current is None or _order(item) > _order(current):
            best[key] = item
    return best


def _order(item: Dividend) -> tuple[dt.date, dt.time, str]:
    return (item.published_on, item.published_at or dt.time.min, item.reference)


def straddles_a_split(dividend: Dividend, split_days: Iterable[dt.date]) -> bool:
    """権利確定日と権利落ち日のあいだに分割日が入っているか。

    **入っていたら、1株あたりの配当は尺度が混ざっている。** 倍率を掛けて直せる
    種類の間違いではない——どちらの尺度の値なのかが決まらないためである。

    ここでは**直さずに名前を付ける。** 黙って使うと、分割した会社の配当が
    半分または倍で並ぶ。
    """
    start = dividend.ex_date
    end = dividend.record_date
    if start is None or end is None:
        return False
    low, high = (start, end) if start <= end else (end, start)
    return any(low <= day <= high for day in split_days)


@dataclasses.dataclass
class ExDateCoverage:
    """権利落ち日が、原本にどれだけ入っているか。

    **#9（窓は埋まる）の設計がこれに掛かっている。** 3% の下窓は、権利落ちが
    そう見える——外せなければ、事象の定義が配当を拾う。

    **列ごとに独立に数える。** 行が読めたことと、`ExDate` が埋まっていることは
    別である（`CLAUDE.md`「0 を『読めた』と読まない」）。
    """

    files: int
    rows: int
    with_ex_date: int
    """`ExDate` が埋まっていた行。**行数と別に数える。**"""

    symbols: int
    days: int
    """**別々の（銘柄, 権利落ち日）の数。** 外す対象はこれである。"""

    first: dt.date | None
    last: dt.date | None
    in_is: int
    """IS に入る**（銘柄, 日）**。**行ではない。**"""

    in_oos: int
    """OOS に入る**（銘柄, 日）**。"""

    after_oos: int
    """OOS より後の**（銘柄, 日）**。

    **在る。** 配当は前もって公表されるので、判定期間の先の権利落ち日が原本に
    入っている（実データで 2027年まで）。**別に数えないと、3つ目が黙って
    どこかに混ざる。**
    """

    def __post_init__(self) -> None:
        """**3つの内訳が、別々の（銘柄, 日）の数に足し合わさること。**

        **同じ列に行と（銘柄 × 日）を混ぜていた**（2026-09-19、ユーザーが
        発見）。IS と OOS を行で数えていて、すぐ上の「外す対象」の行とは
        **2.6倍違う数**が同じ列に並んでいた。

        `CLAUDE.md`「独立な観測を、件数で数えない」「系列を作るときの単位と、
        検出力を計算するときの単位を揃える」に当たる形である。**そして
        例外は出ない**——だから、**足して合わなければここで落とす。**

        Raises:
            ValueError: 内訳が ``days`` に足し合わさらない。
        """
        parts = self.in_is + self.in_oos + self.after_oos
        if parts != self.days:
            raise ValueError(
                f"内訳 {parts} が、別々の権利落ち {self.days} に合わない。"
                "**単位が混ざっている**（行と（銘柄 × 日））。"
            )

    def summary(self) -> str:
        """1行のまとめ。"""
        if not self.rows:
            return "配当の原本が1行も読めなかった。**外す材料が無い。**"
        span = f"{self.first} 〜 {self.last}" if self.first else "日付が1つも無い"
        return (
            f"{self.files:,} 本、{self.rows:,} 行。"
            f"`ExDate` が埋まっていたのは {self.with_ex_date:,} 行"
            f"（{self.with_ex_date / self.rows:.1%}）。"
            f"{self.symbols:,} 銘柄、**別々の権利落ち {self.days:,} 件**（{span}）。"
        )

    def warnings(self) -> list[str]:
        """気付かなくても目に入るべきこと。**早期 return しない。**"""
        found: list[str] = []
        if not self.rows:
            return ["**配当の原本が1行も読めなかった。**"]
        if self.after_oos:
            found.append(
                f"**{self.after_oos:,} 件は OOS より後の権利落ちである。** "
                "配当は前もって公表されるので、判定期間の先が入っている。"
            )
        missing = self.rows - self.with_ex_date
        if missing:
            found.append(
                f"**{missing:,} 行は `ExDate` が空だった**（{missing / self.rows:.1%}）。"
                "その配当は外せない。"
            )
        if not self.in_is:
            found.append("**IS（〜2017-12）に権利落ちが1件も無い。** 推定に使えない。")
        if not self.in_oos:
            found.append("**OOS（2018-01〜）に権利落ちが1件も無い。** 判定に使えない。")
        return found


def ex_date_coverage(
    directory: Path,
    is_end: dt.date = dt.date(2017, 12, 31),
    oos_from: dt.date = dt.date(2018, 1, 1),
    oos_end: dt.date = dt.date(2026, 8, 31),
) -> ExDateCoverage:
    """保存済みの原本から、権利落ち日がどれだけ取れるかを数える。

    **落としには行かない。** `/fins/dividend` は Premium のエンドポイントなので、
    解約後はここに在るものがすべてである。

    Args:
        directory: 原本の置き場所。
        is_end: IS の最終日。
        oos_from: OOS の初日。
        oos_end: OOS の最終日。**これより後は別に数える。**

    Returns:
        :class:`ExDateCoverage`。
    """
    from stock_ai.data.jquants_archive import path_for, read_manifest
    from stock_ai.data.jquants_read import endpoint_of, read_archived

    files = rows = with_ex = 0
    symbols: set[str] = set()
    days: set[tuple[str, dt.date]] = set()
    for key in sorted(read_manifest(directory)):
        if endpoint_of(key) != "/fins/dividend":
            continue
        files += 1
        try:
            found = parse_dividends(read_archived(path_for(directory, key)))
        except Exception as exc:  # noqa: BLE001 - どこで読めないかが記録に値する
            logger.warning("配当の原本を読めなかった: %s: %s", key, exc)
            continue
        for item in found:
            rows += 1
            symbols.add(item.symbol)
            if item.ex_date is None:
                continue
            with_ex += 1
            days.add((item.symbol, item.ex_date))

    # **期間で分けるのは、別々の（銘柄, 日）になってからである。** 行ごとに
    # 数えると、同じ列に2つの単位が並ぶ（2026-09-19 に 2.6倍ずれていた）。
    dates = [when for _symbol, when in days]
    in_is = sum(1 for when in dates if when <= is_end)
    in_oos = sum(1 for when in dates if oos_from <= when <= oos_end)
    after = sum(1 for when in dates if when > oos_end)
    return ExDateCoverage(
        files=files,
        rows=rows,
        with_ex_date=with_ex,
        symbols=len(symbols),
        days=len(days),
        first=min(dates) if dates else None,
        last=max(dates) if dates else None,
        in_is=in_is,
        in_oos=in_oos,
        after_oos=after,
    )


def ex_dates(directory: Path) -> dict[str, set[dt.date]]:
    """銘柄ごとの権利落ち日。**公表がその日より前のものだけを集めるのは呼ぶ側。**

    ここが返すのは ``(公表日, 権利落ち日)`` ではなく**権利落ち日の集合**である
    ——外す側は「その日が権利落ちか」しか要らない。

    **先読みを入れないための口は、別に置いてある**（:func:`ex_dates_known_by`）。

    Args:
        directory: 原本の置き場所。

    Returns:
        ``銘柄 -> 権利落ち日の集合``。
    """
    found: dict[str, set[dt.date]] = {}
    for symbol, _published, when, _rate in _ex_date_rows(directory):
        found.setdefault(symbol, set()).add(when)
    return found


@dataclasses.dataclass(frozen=True)
class AnnouncedExDates:
    """先読みを入れずに引ける権利落ち日と、**落とした理由の数。**"""

    by_symbol: dict[str, list[tuple[dt.date, dt.date]]]
    """``銘柄 -> [(公表日, 権利落ち日), ...]``。**公表日の順に並ぶ。**"""

    kept: int
    """権利落ちとして扱う ``(銘柄, 日)``。"""

    dropped_zero: int
    """**額 0 で落とした ``(銘柄, 日)``。** 無配の公表にも `ExDate` は入る。"""

    unknown_amount: int
    """額が未公表。**残す側に倒している**（外し漏れより外し過ぎを採る）。"""

    def summary(self) -> str:
        """1行のまとめ。"""
        return (
            f"権利落ちとして扱うのは {self.kept:,} 件"
            f"（**額 0 で落とした {self.dropped_zero:,} 件**、"
            f"額が未公表で残した {self.unknown_amount:,} 件）。"
        )

    def warnings(self) -> list[str]:
        """気付かなくても目に入るべきこと。**早期 return しない。**"""
        found: list[str] = []
        if not self.kept:
            return ["**権利落ちを1件も引けなかった。**"]
        if self.unknown_amount:
            found.append(
                f"**{self.unknown_amount:,} 件は額が未公表のまま残している。** "
                "落ちるかどうかが分からないので、**外す側に倒した。**"
            )
        return found


def ex_dates_known_by(directory: Path) -> AnnouncedExDates:
    """銘柄ごとの ``(公表日, 権利落ち日)``。**先読みを外すのに使う。**

    権利落ち日は前もって分かる情報だが、**後から出た訂正を使えば先読みになる。**
    呼ぶ側は ``公表日 <= その日`` のものだけを見ること
    （:func:`~stock_ai.backtest.gap_fill.known_ex_dates`）。

    **額 0 の公表は権利落ちとして扱わない。** 無配の公表にも `ExDate` は入る
    ので、額を見ないと**落ちるものが無い日で事象を外す**ことになる。実データで
    `#16` が急落 371 件をそれで外していた（2026-09-20、ユーザーが指摘）。
    **`#15` も同じ口を使っている。**

    **額が未公表（`DivRate` が空）の日は残す。** 落ちるかどうかが分からない
    ので、**外す側に倒す**——外し漏れのほうが、事象の定義に機械的な値下がりを
    混ぜるので悪い。

    **判定は、その日までに公表された額で行う。** 後から 0 に訂正されたことを
    使えば先読みになるので、**その時点で正の額が1度でも公表されていれば
    権利落ちとして扱う。**

    Args:
        directory: 原本の置き場所。

    Returns:
        :class:`AnnouncedExDates`。
    """
    # **(銘柄, 権利落ち日) ごとに、いちばん早い「額が 0 でない」公表日を採る。**
    # 額 0 の公表しか無ければ、その日は権利落ちとして扱わない。
    positive: dict[tuple[str, dt.date], dt.date] = {}
    unknown: dict[tuple[str, dt.date], dt.date] = {}
    zero: set[tuple[str, dt.date]] = set()
    for symbol, published, when, rate in _ex_date_rows(directory):
        key = (symbol, when)
        if rate is None:
            current = unknown.get(key)
            if current is None or published < current:
                unknown[key] = published
        elif rate > 0:
            current = positive.get(key)
            if current is None or published < current:
                positive[key] = published
        else:
            zero.add(key)

    found: dict[str, list[tuple[dt.date, dt.date]]] = {}
    kept = 0
    for source in (positive, unknown):
        for (symbol, when), published in source.items():
            if when in {day for _p, day in found.get(symbol, [])}:
                continue
            found.setdefault(symbol, []).append((published, when))
            kept += 1
    for rows in found.values():
        rows.sort()

    return AnnouncedExDates(
        by_symbol=found,
        kept=kept,
        dropped_zero=len(zero - set(positive) - set(unknown)),
        unknown_amount=len(set(unknown) - set(positive)),
    )


@dataclasses.dataclass(frozen=True)
class ExDividend:
    """ある権利落ち日に落ちる配当。**普通と特別を分けて持つ。**"""

    rate: float
    """1株あたり。**円。調整前の終値で割ること。** 無配の公表なら 0。"""

    special: float
    """うち特別配当。**大きければ「特別配当だった」と言える。**"""

    @property
    def has_special(self) -> bool:
        """特別配当が乗っているか。"""
        return self.special > 0

    @property
    def is_zero(self) -> bool:
        """無配。**「読めない」ではなく「落ちるものが無い」。**"""
        return self.rate <= 0


@dataclasses.dataclass(frozen=True)
class ExDividends:
    """読んだ配当と、**読み方が正しかったかを言うための数。**"""

    rates: dict[str, dict[dt.date, ExDividend]]
    rows: int
    """`ExDate` と `DivRate` の両方が在った行。"""

    ex_dates: int
    """``(銘柄, 権利落ち日)`` の数。**行数ではない。**"""

    multi_row: int
    """2行以上が同じ ``(銘柄, 権利落ち日)`` に乗っていた数。"""

    max_rows: int
    zero_rate: int
    """額が 0 だった権利落ち。**無配の公表にも `ExDate` は入る。**"""

    def summary(self) -> str:
        """1行のまとめ。"""
        if not self.ex_dates:
            return "配当の額が1件も読めなかった。"
        return (
            f"{len(self.rates):,} 銘柄・{self.rows:,} 行から、"
            f"**{self.ex_dates:,} 件の権利落ち**を作った"
            f"（同じ日に2行以上あったのは {self.multi_row:,} 件、最大 {self.max_rows} 行）。"
            f"**額が 0 の公表が {self.zero_rate:,} 件。**"
        )

    def warnings(self) -> list[str]:
        """気付かなくても目に入るべきこと。**早期 return しない。**"""
        found: list[str] = []
        if not self.ex_dates:
            return ["**配当の額が1件も読めなかった。**"]
        if self.multi_row:
            share = self.multi_row / self.ex_dates
            found.append(
                f"**{self.multi_row:,} 件（{share:.1%}）は同じ権利落ち日に2行以上ある。** "
                "**足していない**——`latest_by_term` と同じく最後の公表を1行だけ採る。"
                "足すと年間配当が3倍になる（2026-09-20、実際にそうしていた）。"
            )
        if self.zero_rate:
            share = self.zero_rate / self.ex_dates
            found.append(
                f"**額が 0 の権利落ちが {self.zero_rate:,} 件（{share:.1%}）。** "
                "**`ExDate` が在るだけで外すと、落ちるものが無い日で外すことになる。**"
            )
        return found


def ex_dividend_rates(directory: Path) -> ExDividends:
    """銘柄・権利落ち日ごとの **1株あたり配当**。**監査専用である。**

    **最後に公表された値を採る**ので、先読みが入る。**売買の判定に使わない**
    ——外した件数の中身を見るためだけのものである（`ex-date-audit`）。
    先読みを外して権利落ち日を引くのは :func:`ex_dates_known_by`。

    **足さない。** 同じ権利落ち日に複数行が乗ることがあるが、それは同じ支払の
    公表・訂正であって、別々の配当ではない。:func:`latest_by_term` と同じ規則
    （公表日時、同じなら `RefNo` の大きいほう）で**1行だけ**採る。
    **足すと年間配当が3倍になる**——その注意書きは `latest_by_term` の説明に
    既に書いてあり、それを読まずに2つ目を書いて踏んだ（2026-09-20）。

    **`DivRate` は1株あたりの円**で、分割の前後で尺度が変わる。**割るのは
    調整前の終値**であること（調整後で割ると分割ぶんずれる）。

    Args:
        directory: 原本の置き場所。

    Returns:
        :class:`ExDividends`。**額の無い行は入らない。額が 0 の行は入る。**
    """
    from stock_ai.data.jquants_archive import path_for, read_manifest
    from stock_ai.data.jquants_read import endpoint_of, read_archived

    seen: dict[str, dict[dt.date, list[Dividend]]] = {}
    rows = 0
    for key in sorted(read_manifest(directory)):
        if endpoint_of(key) != "/fins/dividend":
            continue
        try:
            found = parse_dividends(read_archived(path_for(directory, key)))
        except Exception as exc:  # noqa: BLE001 - どこで読めないかが記録に値する
            logger.warning("配当の原本を読めなかった: %s: %s", key, exc)
            continue
        for item in found:
            if item.ex_date is None or item.rate is None:
                continue
            rows += 1
            seen.setdefault(item.symbol, {}).setdefault(item.ex_date, []).append(item)

    rates: dict[str, dict[dt.date, ExDividend]] = {}
    ex_dates = multi_row = max_rows = zero_rate = 0
    for symbol, dates in seen.items():
        for when, items in dates.items():
            ex_dates += 1
            max_rows = max(max_rows, len(items))
            if len(items) > 1:
                multi_row += 1
            # **最後の公表を1行だけ。** `latest_by_term` と同じ並べ方である。
            last = max(items, key=_order)
            found_rate = ExDividend(rate=last.rate or 0.0, special=last.special_rate or 0.0)
            if found_rate.is_zero:
                zero_rate += 1
            rates.setdefault(symbol, {})[when] = found_rate

    return ExDividends(
        rates=rates,
        rows=rows,
        ex_dates=ex_dates,
        multi_row=multi_row,
        max_rows=max_rows,
        zero_rate=zero_rate,
    )


def _ex_date_rows(directory: Path) -> Iterable[tuple[str, dt.date, dt.date, float | None]]:
    """原本から ``(銘柄, 公表日, 権利落ち日, 額)`` を1行ずつ。**取りには行かない。**

    **額も返す。** 額を落とすと、呼ぶ側は無配の公表と区別できない。
    """
    from stock_ai.data.jquants_archive import path_for, read_manifest
    from stock_ai.data.jquants_read import endpoint_of, read_archived

    for key in sorted(read_manifest(directory)):
        if endpoint_of(key) != "/fins/dividend":
            continue
        try:
            found = parse_dividends(read_archived(path_for(directory, key)))
        except Exception as exc:  # noqa: BLE001 - どこで読めないかが記録に値する
            logger.warning("配当の原本を読めなかった: %s: %s", key, exc)
            continue
        for item in found:
            if item.ex_date is not None:
                yield item.symbol, item.published_on, item.ex_date, item.rate
