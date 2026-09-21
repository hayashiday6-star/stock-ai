"""日経225オプション（`/derivatives/bars/daily/options/225`）を読む。

## 何のために在るか

**候補6（悲観の中に生まれ）を「測れない」と書いていたのを直すため**である
（2026-09-21）。`docs/CANDIDATES.md` と `wall-survey` の両方に「心理指標を
1つも持っていない」と書いてあったが、**オプションの建値から出した予想変動率
は心理指標である。** 材料は保存済みの原本に在った。

## 畳み方は、壁を測る前に1つに決めてある

**複数試して良いほうを採ると、その時点で #10 と同じところに落ちる**
（`CLAUDE.md`「設計を検出力で選ぶのは、答えを見て選ぶのとは違う」の、
やってはいけない側）。だから**ここに1つだけ書く。**

1. `IV` / `UnderPx` / `Strike` / `SQD` / `PCDiv` が全部埋まっている行だけ
2. その日の限月のうち、**SQ 日までが :data:`MIN_TENOR_DAYS` 日以上で
   いちばん近いもの**
3. その限月の中で、**`UnderPx` にいちばん近い行使価格**
4. そこにあるコールとプットの `IV` を**平均**する（片側しか無ければその側）

**満期の週を外すのは、そこで値が跳ねるからである。** 実データ（2026-01）で、
残存4営業日の限月を採ると `BaseVol` と 2.1 ポイント食い違い、**7日以上で
切ると 19 日すべてで一致した。**

## 別の切り口で同じ数字を出す

原本には `BaseVol` という**日ごとに1つの列**が在る。意味は書かれていないが、
上の畳み方で作った値と**実データで完全に一致する**（2026-01 の 19 日すべて、
差 0.5 ポイント未満）。

**一致は当たり前ではない。** 限月の選び方を1日ずらすだけで崩れる——実際、
`MIN_TENOR_DAYS` を 0 にすると 4 日で食い違った。だから
:class:`ImpliedVolatility` は**食い違った日を持って返る**。

## `IV` は昔の原本に入っていない

配布されている 2008-05 の原本では、`IV` / `BaseVol` / `UnderPx` / `Settle` /
`Theo` / `IR` / `SQD` / `LTD` / `VoOA` の **9 列が全行で空**である。2026-01
では全部埋まっている。**どこから埋まるのかは、数えないと分からない。**

**無いことは、出力に出ない**（`CLAUDE.md`）。だから
:meth:`ImpliedVolatility.by_year` が年ごとに数える——**在るべき日を先に
決めて引き算する**のと同じ形である。
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
ENDPOINT = "/derivatives/bars/daily/options/225"

#: SQ 日までこれだけ残っている限月から採る。**満期の週を外すため。**
#:
#: 実データ（2026-01）で、残存4日の限月を採ると `BaseVol` と 2.1 ポイント
#: 食い違った。**7 日で切ると 19 日すべて一致する。**
MIN_TENOR_DAYS = 7

#: `BaseVol` と食い違いと呼ぶ幅（ポイント）。
#:
#: **広すぎる幅は、狭すぎる幅より悪い**（`CLAUDE.md`）。実データの一致は
#: 小数第4位まで揃っているので、0.5 は「畳み方が変わった」ときにだけ鳴る。
AGREEMENT_BAND = 0.5


@dataclasses.dataclass(frozen=True)
class Disagreement:
    """こちらの ATM と原本の `BaseVol` が食い違った日。**中身を持って返る。**

    **件数だけ返すと、次の一手が打てない。** 警告に「限月の選び方を疑う
    こと」と書いておきながら、**疑う材料を1つも返していなかった**
    （2026-09-21。`CLAUDE.md`「『見ること』と書いただけで、見る道具を
    置いていないか」——直前にその節を書いた本人がやった）。
    """

    when: dt.date
    atm: float
    """こちらが作った値。"""

    base: float
    """原本の `BaseVol`。"""

    expiry: dt.date
    """採った限月の SQ 日。**疑うならここである。**"""

    tenor: int
    """``when`` から ``expiry`` までの暦日。"""

    strike: float
    under: float
    rows: int
    """その行使価格に在った行の数。**1ならコールかプットの片側だけ。**"""

    @property
    def gap(self) -> float:
        """食い違いの大きさ（ポイント）。"""
        return self.atm - self.base


@dataclasses.dataclass(frozen=True)
class ImpliedVolatility:
    """日ごとの ATM 予想変動率と、**読めなかったぶんの数。**"""

    levels: dict[dt.date, float]
    """``日 -> ATM の IV``（ポイント。20.9 なら年率 20.9%）。"""

    rows: int
    """読んだ行。"""

    with_iv: int
    """`IV` が埋まっていた行。**行数と別に数える。**"""

    days: int
    """原本に在った日数。**水準を作れた日数とは別である。**"""

    no_tenor: int
    """限月が :data:`MIN_TENOR_DAYS` 日以上残っていなかった日。"""

    disagreed: tuple[Disagreement, ...] = ()
    """食い違った日だけ。**中身を持っている**——`checks` がそれを刷る。"""

    checked: int = 0
    """`BaseVol` と突き合わせられた日。**分母である。**"""

    per_year: dict[int, tuple[int, int]] = dataclasses.field(default_factory=dict)
    """``年 -> (原本に在った日, 水準を作れた日)``。"""

    @property
    def first(self) -> dt.date | None:
        """水準を作れた最初の日。"""
        return min(self.levels) if self.levels else None

    @property
    def last(self) -> dt.date | None:
        """水準を作れた最後の日。"""
        return max(self.levels) if self.levels else None

    def by_year(self) -> list[tuple[int, int, int]]:
        """``(年, 原本に在った日, 水準を作れた日)``。**年の順。**

        **`IV` がいつから入っているかは、数えないと分からない。** 2008-05 の
        原本では9列が全行で空だった。
        """
        return [(year, found[0], found[1]) for year, found in sorted(self.per_year.items())]

    def summary(self) -> str:
        """1行のまとめ。"""
        if not self.rows:
            return "オプションの原本が1行も読めなかった。**材料が無い。**"
        span = f"{self.first} 〜 {self.last}" if self.levels else "1日も作れなかった"
        return (
            f"{self.rows:,} 行、{self.days:,} 日。`IV` が埋まっていたのは "
            f"{self.with_iv:,} 行（{self.with_iv / self.rows:.1%}）。"
            f"**ATM の水準を作れたのは {len(self.levels):,} 日**（{span}）。"
        )

    def warnings(self) -> list[str]:
        """気付かなくても目に入るべきこと。**早期 return しない。**"""
        found: list[str] = []
        if not self.rows:
            return ["**オプションの原本が1行も読めなかった。**"]
        missing = self.rows - self.with_iv
        if missing:
            found.append(
                f"**{missing:,} 行は `IV` が空だった**（{missing / self.rows:.1%}）。"
                "**古い原本には入っていない**——2008-05 では9列が全行で空である。"
            )
        if self.no_tenor:
            found.append(
                f"**{self.no_tenor:,} 日は、残存 {MIN_TENOR_DAYS} 日以上の限月が"
                "無かった。** その日の水準は作っていない。"
            )
        if self.disagreed:
            share = len(self.disagreed) / self.checked if self.checked else 0.0
            found.append(
                f"**{len(self.disagreed):,} 日（{share:.1%}）で、こちらの ATM と原本の "
                f"`BaseVol` が {AGREEMENT_BAND} ポイント以上食い違う。** "
                "**畳み方が原本の想定とずれている可能性がある**"
                "——下の表に、採った限月と残存日数が出る。"
            )
        if not self.levels:
            found.append("**ATM の水準を1日も作れなかった。** 候補Aの材料にならない。")
        return found


def daily_atm_iv(directory: Path, min_tenor: int = MIN_TENOR_DAYS) -> ImpliedVolatility:
    """保存済みの原本から、**日ごとの ATM 予想変動率**を作る。

    畳み方は module の説明に書いてある1つだけである。**取りには行かない。**

    Args:
        directory: 原本の置き場所。
        min_tenor: SQ 日までこれだけ残っている限月から採る。

    Returns:
        :class:`ImpliedVolatility`。
    """
    rows = with_iv = 0
    # **日ごとに畳む前に、日ごとに集める。** ファイルが月ごとに割れていても
    # 同じ日が2本にまたがることは無いが、**またがっても答えが変わらない形**
    # にしておく。
    seen: dict[dt.date, list[tuple[dt.date, float, float, float]]] = {}
    base_vol: dict[dt.date, float] = {}

    for key, payload in _option_files(directory):
        try:
            found = records_from_csv(payload)
        except Exception as exc:  # noqa: BLE001 - どこで読めないかが記録に値する
            logger.warning("オプションの原本を読めなかった: %s: %s", key, exc)
            continue
        for row in found:
            rows += 1
            when = parse_date(row.get("Date"))
            if when is None:
                continue
            seen.setdefault(when, [])
            iv = parse_number(row.get("IV"))
            if iv is None:
                continue
            with_iv += 1
            under = parse_number(row.get("UnderPx"))
            strike = parse_number(row.get("Strike"))
            sq = parse_date(row.get("SQD"))
            if under is None or strike is None or sq is None or under <= 0:
                continue
            seen[when].append((sq, strike, under, iv))
            level = parse_number(row.get("BaseVol"))
            if level is not None:
                base_vol[when] = level

    levels: dict[dt.date, float] = {}
    per_year: dict[int, tuple[int, int]] = {}
    disagreed: list[Disagreement] = []
    no_tenor = checked = 0
    for when in sorted(seen):
        days, made = per_year.get(when.year, (0, 0))
        picked = _atm_on(when, seen[when], min_tenor)
        if picked is None:
            if seen[when]:
                no_tenor += 1
        else:
            level, expiry, strike, under, count = picked
            levels[when] = level
            made += 1
            # **別の切り口で同じ数字を出す。** 一致は当たり前ではない
            # ——限月の選び方を1日ずらすだけで崩れる。
            theirs = base_vol.get(when)
            if theirs is not None:
                checked += 1
                if abs(level - theirs) >= AGREEMENT_BAND:
                    # **中身を持って返る。** 件数だけだと、疑う材料が無い。
                    disagreed.append(
                        Disagreement(
                            when=when,
                            atm=level,
                            base=theirs,
                            expiry=expiry,
                            tenor=(expiry - when).days,
                            strike=strike,
                            under=under,
                            rows=count,
                        )
                    )
        per_year[when.year] = (days + 1, made)

    return ImpliedVolatility(
        levels=levels,
        rows=rows,
        with_iv=with_iv,
        days=len(seen),
        no_tenor=no_tenor,
        disagreed=tuple(disagreed),
        checked=checked,
        per_year=per_year,
    )


def _atm_on(
    when: dt.date,
    rows: list[tuple[dt.date, float, float, float]],
    min_tenor: int,
) -> tuple[float, dt.date, float, float, int] | None:
    """その日の ATM の IV。**畳み方はここが正本である。**

    Args:
        when: その日。
        rows: ``(SQ 日, 行使価格, 原資産, IV)``。
        min_tenor: SQ 日までこれだけ残っている限月から採る。

    Returns:
        ``(ATM の IV, 採った SQ 日, 行使価格, 原資産, 使った行数)``。
        **どれを採ったかも返す**——食い違ったときに疑う材料がそこである。
        作れなければ ``None``。
    """
    usable = [row for row in rows if (row[0] - when).days >= min_tenor]
    if not usable:
        return None
    # 2. 残存が足りる限月のうち、いちばん近いもの
    nearest = min(sq for sq, _strike, _under, _iv in usable)
    same = [row for row in usable if row[0] == nearest]
    # 3. `UnderPx` にいちばん近い行使価格
    under = same[0][2]
    strike = min((row[1] for row in same), key=lambda value: (abs(value - under), value))
    # 4. コールとプットの平均（片側しか無ければその側）
    picked = [iv for _sq, value, _under, iv in same if value == strike]
    if not picked:
        return None
    return sum(picked) / len(picked), nearest, strike, under, len(picked)


def _option_files(directory: Path) -> Iterable[tuple[str, bytes]]:
    """オプションの原本を1本ずつ ``(鍵, 中身)`` で。**取りには行かない。**"""
    from stock_ai.data.jquants_archive import path_for, read_manifest
    from stock_ai.data.jquants_read import endpoint_of, read_archived

    for key in sorted(read_manifest(directory)):
        if endpoint_of(key) != ENDPOINT:
            continue
        try:
            yield key, read_archived(path_for(directory, key))
        except Exception as exc:  # noqa: BLE001 - どこで読めないかが記録に値する
            logger.warning("オプションの原本を開けなかった: %s: %s", key, exc)
