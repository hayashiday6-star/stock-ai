"""投資部門別売買状況（`/equities/investor-types`）を読む。

## 何のために在るか

**候補7（需給はすべての材料に優先する）を「設計が決まっていない」と書いて
いたのを直すため**である（2026-09-21）。`wall-survey` には「信用残と空売り
比率は在るが、**何を1観測とするかから**」と書いてあったが、**投資部門別は
週に1行で、1観測が何かは原本のほうが決めている。**

## 週に1つである

`StDate` / `EnDate` がその週の始まりと終わりで、`PubDate` は**その翌週の
木曜あたり**に出る（2008-01-04 の週が 2008-01-16 公表）。

**先読みを外すのは呼ぶ側である**——`PubDate` より前にその週の数字は使えない。
ここは ``(週, 公表日, 値)`` の形で返す。

## 畳み方は、壁を測る前に1つに決めてある

**複数試して良いほうを採ると、その時点で #10 と同じところに落ちる。**
だから**ここに1つだけ書く。**

1. `Section` は :data:`SECTION` の1つだけを採る（全市場の合計）
2. 1観測は**1週**（`EnDate`）
3. 指標は **`FrgnBal / TotTot`** ——外国人の買い越し額を、その週の総売買代金
   で割る。**額そのものを使わない**（市場規模が20年で変わるため）

**`IndBal` も同じ形で持つ。** 候補7は「外国人が買い、個人が売る」を見る説
なので、**片方だけ返すと、もう片方を測るときに2つ目の読み口が要る。**

## 区分の名前は、途中で変わる

配布サンプル（2008-01）の `Section` は ``TokyoNagoya`` / ``TSE1st`` /
``TSE2nd`` / ``TSEMothers`` である。2022-04 の市場再編でプライム・
スタンダード・グロースになっているはずだが、**原本を見るまで分からない。**

**分からないものに名前を付けない**（`CLAUDE.md`）。:class:`InvestorFlows`
は**在った区分を全部数えて返す**ので、:data:`SECTION` が途中で消えていれば
出力に出る——**無いことは、出力に出ない。**
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Iterable
from pathlib import Path

from stock_ai.core.logging import get_logger
from stock_ai.data.jquants_bulk import records_from_csv
from stock_ai.data.jquants_margin import parse_date, parse_number

logger = get_logger(__name__)

#: 読むエンドポイント。**綴りを2箇所に書かない。**
ENDPOINT = "/equities/investor-types"

#: 採る区分。**全市場の合計。** 市場再編で名前が変わる区分を避けるため。
SECTION = "TokyoNagoya"


@dataclasses.dataclass(frozen=True)
class Week:
    """ある週の需給。**1週1観測である。**"""

    start: dt.date
    end: dt.date
    published_on: dt.date
    """`PubDate`。**先読みを外すのは呼ぶ側である。**"""

    foreign_share: float
    """`FrgnBal / TotTot`。外国人の買い越しを総売買代金で割ったもの。"""

    individual_share: float
    """`IndBal / TotTot`。"""

    turnover: float
    """`TotTot`。**規模で割るときの分母。**"""


@dataclasses.dataclass(frozen=True)
class InvestorFlows:
    """読んだ週と、**読めなかったぶんの数。**"""

    weeks: tuple[Week, ...]
    """:data:`SECTION` の週。**`EnDate` の順。**"""

    rows: int
    """読んだ行。"""

    sections: dict[str, int]
    """``区分 -> 行数``。**在った区分を全部数える。**

    :data:`SECTION` が途中で消えていれば、ここに出る。
    """

    no_turnover: int
    """`TotTot` が 0 か空で、割れなかった週。"""

    duplicated: int
    """同じ `EnDate` が2回以上出た数。**後から出たほうを採る。**"""

    @property
    def first(self) -> dt.date | None:
        """いちばん古い週の終わり。"""
        return self.weeks[0].end if self.weeks else None

    @property
    def last(self) -> dt.date | None:
        """いちばん新しい週の終わり。"""
        return self.weeks[-1].end if self.weeks else None

    def by_year(self) -> list[tuple[int, int]]:
        """``(年, 週の数)``。**年の順。**"""
        found: dict[int, int] = {}
        for week in self.weeks:
            found[week.end.year] = found.get(week.end.year, 0) + 1
        return sorted(found.items())

    def summary(self) -> str:
        """1行のまとめ。"""
        if not self.rows:
            return "投資部門別の原本が1行も読めなかった。**材料が無い。**"
        span = f"{self.first} 〜 {self.last}" if self.weeks else "1週も作れなかった"
        return (
            f"{self.rows:,} 行、区分 {len(self.sections)} 種類。"
            f"**`{SECTION}` の週が {len(self.weeks):,}**（{span}）。"
        )

    def warnings(self) -> list[str]:
        """気付かなくても目に入るべきこと。**早期 return しない。**"""
        found: list[str] = []
        if not self.rows:
            return ["**投資部門別の原本が1行も読めなかった。**"]
        if not self.weeks:
            found.append(
                f"**`{SECTION}` の行が1つも無い。** 在ったのは "
                + "、".join(sorted(self.sections))
                + "。**区分の名前が変わっている。**"
            )
        if self.no_turnover:
            found.append(
                f"**{self.no_turnover:,} 週は `TotTot` が 0 か空だった。** "
                "規模で割れないので使っていない。"
            )
        if self.duplicated:
            found.append(
                f"**{self.duplicated:,} 週は同じ `EnDate` が2回以上出た。** "
                "**足していない**——後から公表されたほうを1つだけ採る。"
            )
        return found


def weekly_flows(directory: Path, section: str = SECTION) -> InvestorFlows:
    """保存済みの原本から、**週ごとの需給**を作る。**取りには行かない。**

    Args:
        directory: 原本の置き場所。
        section: 採る区分。

    Returns:
        :class:`InvestorFlows`。
    """
    rows = 0
    sections: dict[str, int] = {}
    # **同じ週が2回出たら、後から公表されたほうを採る。** 足すと2倍になる
    # ——`latest_by_term` で踏んだのと同じ形である。
    best: dict[dt.date, tuple[dt.date, Week]] = {}
    no_turnover = duplicated = 0

    for key, payload in _investor_files(directory):
        try:
            found = records_from_csv(payload)
        except Exception as exc:  # noqa: BLE001 - どこで読めないかが記録に値する
            logger.warning("投資部門別の原本を読めなかった: %s: %s", key, exc)
            continue
        for row in found:
            rows += 1
            name = (row.get("Section") or "").strip()
            sections[name] = sections.get(name, 0) + 1
            if name != section:
                continue
            start = parse_date(row.get("StDate"))
            end = parse_date(row.get("EnDate"))
            published = parse_date(row.get("PubDate"))
            if start is None or end is None or published is None:
                continue
            turnover = parse_number(row.get("TotTot"))
            if turnover is None or turnover <= 0:
                no_turnover += 1
                continue
            foreign = parse_number(row.get("FrgnBal"))
            individual = parse_number(row.get("IndBal"))
            if foreign is None or individual is None:
                continue
            week = Week(
                start=start,
                end=end,
                published_on=published,
                foreign_share=foreign / turnover,
                individual_share=individual / turnover,
                turnover=turnover,
            )
            current = best.get(end)
            if current is not None:
                duplicated += 1
                if current[0] >= published:
                    continue
            best[end] = (published, week)

    return InvestorFlows(
        weeks=tuple(week for _published, week in (best[end] for end in sorted(best))),
        rows=rows,
        sections=sections,
        no_turnover=no_turnover,
        duplicated=duplicated,
    )


def _investor_files(directory: Path) -> Iterable[tuple[str, bytes]]:
    """投資部門別の原本を1本ずつ ``(鍵, 中身)`` で。**取りには行かない。**"""
    from stock_ai.data.jquants_archive import path_for, read_manifest
    from stock_ai.data.jquants_read import endpoint_of, read_archived

    for key in sorted(read_manifest(directory)):
        if endpoint_of(key) != ENDPOINT:
            continue
        try:
            yield key, read_archived(path_for(directory, key))
        except Exception as exc:  # noqa: BLE001 - どこで読めないかが記録に値する
            logger.warning("投資部門別の原本を開けなかった: %s: %s", key, exc)
