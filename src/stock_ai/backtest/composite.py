"""複合型のルールを、文章ではなく**落ちる関門**にする。

`docs/PURPOSE.md` の「複合型のルール」は6つある。**6つとも、破っても例外が
出ない種類のものである。** 人が読んで当てはめると、封印の前でも基準が動く。
#7 の判定を `verdict()` にしたのと同じ理由で、ここも関数にする。

1. 構成要素は `HYPOTHESES.md` に登録済みの説をIDで参照する → :func:`unregistered`
2. 組み合わせ方は封印時に固定する。**成績を見ながら探さない** → :class:`Design`
   が IS で試す通り数を持ち、:func:`over_budget` が超過を弾く
3. 構成要素ごとの単独成績も、**同じ期間・同じ条件で**同時に計算する →
   盤面を1つしか作らない（`factor_panel.Panel.column`）
4. **合格には、最良の構成要素単独を上回ることを必須とする** → :func:`beats_best`
5. 試した組み合わせは1通りにつき1本として累計本数に数える → :attr:`Design.counts_as`
6. AND条件は件数が急減するため、§0 で件数と流動性の内訳を必ず確認する →
   :class:`Coverage`

### なぜ「種類をすべて併記」するのか

2026-09-05 に束ねた3本は、**3本とも `technical` だった。** 種類が1つしか
入っていない束を「複合型」と呼ぶと、**手つかずの組み合わせが在ることが
一覧から見えなくなる。** :func:`kinds` が構成要素の種類を数え上げる。
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence

#: 複合と呼ぶのに要る構成要素の数。
MIN_COMPONENTS = 2

#: 「最良の単独を上回る」を、どれだけ上回れば上回ったと言うか。
#:
#: **0 にしない。** 同じ universe・同じ月で作った2つの系列は強く相関するので、
#: ほんのわずかな差は測定の誤差と区別が付かない。**差そのものの標準誤差を
#: 見るのが正しいが、それには両系列の共分散が要る**（:func:`beats_best` が
#: 受け取る）。ここはその共分散が無いときの下限として置く。
MIN_MARGIN_T = 0.0

PASS = "通す"
FAIL = "通さない"


@dataclasses.dataclass(frozen=True)
class Component:
    """複合の構成要素。**説のIDと、盤面の因子名を結びつける。**"""

    hypothesis_id: str
    """`docs/HYPOTHESES.md` の登録ID。"""
    factor: str
    """`factor_panel` の因子名。"""


@dataclasses.dataclass(frozen=True)
class Design:
    """組み合わせ方。**封印時に固定する。**"""

    components: tuple[Component, ...]
    weights: tuple[float, ...]
    tries_in_is: int
    """IS で試す通り数。**試す前に決めて記録する。**

    予算20本の補正が覆うのは「判定を何回消費したか」であって、**IS の中で
    何通り試したかではない。** IS で20通り回して最も良いものを封印すれば、
    補正はその20回を知らないまま `t ≥ 3.02` を当てることになる。
    """

    def __post_init__(self) -> None:
        """組み合わせ方として成り立っているかを、作った時点で確かめる。"""
        if len(self.components) < MIN_COMPONENTS:
            raise ValueError(
                f"構成要素が {len(self.components)} 個しかない。"
                f"複合と呼ぶには {MIN_COMPONENTS} 個以上が要る。"
            )
        if len(self.weights) != len(self.components):
            raise ValueError(
                f"重みの数が構成要素の数と違う（{len(self.weights)} 対 {len(self.components)}）。"
            )
        if self.tries_in_is < 1:
            raise ValueError("IS で試す通り数は1以上。**0 は「まだ決めていない」である。**")
        seen = [component.hypothesis_id for component in self.components]
        if len(set(seen)) != len(seen):
            raise ValueError(f"同じ説を2度数えている: {seen}。")

    @property
    def factors(self) -> tuple[str, ...]:
        """盤面に頼む因子名。"""
        return tuple(component.factor for component in self.components)

    @property
    def ids(self) -> tuple[str, ...]:
        """構成要素の登録ID。"""
        return tuple(component.hypothesis_id for component in self.components)

    @property
    def counts_as(self) -> int:
        """累計本数に何本として数えるか。**1通りにつき1本。**

        **構成要素の数ではない。** 脚の数字は合格線の比較対象であって、脚に
        判定を出すわけではない。
        """
        return 1


def unregistered(design: Design, registry: Sequence[str]) -> list[str]:
    """登録されていない構成要素のID。**空でなければ封印しない。**

    登録が無い説を脚にすると、**その脚が何を主張しているのかが文書に残らない。**
    公開レポートが「X は誰の主張か」を書けなくなる。
    """
    known = set(registry)
    return [name for name in design.ids if name not in known]


def kinds(design: Design, kind_of: dict[str, str]) -> dict[str, int]:
    """構成要素の種類を数える。**「種類をすべて併記」のための材料。**

    知らないIDは ``"不明"`` に入れる。**黙って落とさない。**
    """
    found: dict[str, int] = {}
    for name in design.ids:
        key = kind_of.get(name, "不明")
        found[key] = found.get(key, 0) + 1
    return found


def single_kind(design: Design, kind_of: dict[str, str]) -> bool:
    """種類が1つしか入っていないか。

    2026-09-05 に束ねた3本は3本とも `technical` だった。**それ自体は禁止では
    ないが、「種類をまたぐ複合が手つかず」であることが見えなくなる。**
    """
    return len(kinds(design, kind_of)) == 1


def over_budget(design: Design, tried: int) -> bool:
    """IS で決めた通り数を超えたか。**超えたら封印しない。**"""
    return tried > design.tries_in_is


@dataclasses.dataclass(frozen=True)
class Coverage:
    """件数と流動性の内訳。**AND条件は件数が急減する。**

    PURPOSE が「§0ゲートで件数と流動性の内訳を必ず確認する」と書いている。
    **表に出ていることと、目に入ることは別である。** :meth:`warnings` が、
    読む側が気付かなくても目に入る側に回す。
    """

    months: int
    median_symbols: int
    excluded_thin: int
    excluded_no_history: int
    excluded_discontinuity: int
    excluded_no_pbr: int
    months_single: int
    """同じ期間で、脚を1本だけにしたときの月数。**減り具合の分母。**"""
    median_symbols_single: int

    @property
    def month_loss(self) -> float:
        """脚を足したことで失った月の割合。"""
        if self.months_single <= 0:
            return float("nan")
        return 1.0 - self.months / self.months_single

    @property
    def symbol_loss(self) -> float:
        """1ヶ月あたりの銘柄が、脚を足したことで減った割合。"""
        if self.median_symbols_single <= 0:
            return float("nan")
        return 1.0 - self.median_symbols / self.median_symbols_single

    def warnings(self) -> list[str]:
        """気付かなくても目に入るべきこと。**表を読ませない。**"""
        found: list[str] = []
        if self.months <= 0:
            found.append("**月が1つも残っていない。** 比べていない。")
        if self.symbol_loss > 0.5:
            found.append(
                f"**1ヶ月あたりの銘柄が {self.symbol_loss:.0%} 減った。** "
                "断面が痩せると分位が効かなくなる。"
            )
        if self.month_loss > 0.2:
            found.append(
                f"**月が {self.month_loss:.0%} 減った。** 判定に使える期数が"
                "そのぶん短くなる。§0 の期数を減らして当て直すこと。"
            )
        if self.excluded_no_pbr > 0 and self.median_symbols_single > 0:
            found.append(
                f"PBR が無くて外した銘柄月が {self.excluded_no_pbr:,} 件ある。"
                "**0 で埋めていない。**"
            )
        return found


def beats_best(
    composite_t: float,
    singles: dict[str, float],
    margin: float = MIN_MARGIN_T,
) -> tuple[str, str]:
    """**合格には、最良の構成要素単独を上回ることを必須とする。**

    Args:
        composite_t: 合成の t。
        singles: 構成要素ごとの単独の t。**同じ盤面から取ったもの。**
        margin: 上回ったと言うのに要る差。

    Returns:
        ``(PASS/FAIL, 理由)``。

    Raises:
        ValueError: 構成要素の t が1つも無い。

    **最良を後から選ぶので、分母は大きくなる。** そのぶん判定は厳しく出る。
    **その向きが安全な側である**——「束ねたら良くなった」を通しにくくする。
    """
    if not singles:
        raise ValueError("構成要素の単独成績が無い。**同じ盤面から取ること。**")
    best_name = max(singles, key=lambda name: singles[name])
    best = singles[best_name]
    if composite_t <= best + margin:
        return FAIL, (
            f"合成 t {composite_t:+.2f} が、最良の単独（{best_name} t {best:+.2f}）を"
            "上回っていない。**束ねた意味が無い。**"
        )
    return PASS, (
        f"合成 t {composite_t:+.2f} が、最良の単独（{best_name} t {best:+.2f}）を上回った。"
    )
