"""合格に要るリターンと、合格の条件。**数字は書き写さない。**

**線が変われば、要るリターンも全部変わる。** 実際、2026-09-17 に線が
3.02 → 3.39 に動いた。そのとき書き写した数字が文書に残っていれば、**古いまま
もっともらしく見え続ける。**

だからここは**測った散らばりだけを持ち、要るリターンはそのつど計算する。**

### 散らばりは、どこで測ったかごと持つ

**出典の無い数字を書かない**（`CLAUDE.md`）。`Shape` は事前登録のどこに書いて
あるかを持っていて、`docs/PASSING.md` にもそれが出る。

### 年率に直せないものがある

イベント型は「1イベントあたり」でしか言えない。**年率に直すには資金をどれだけ
張るかを決める必要があり、事前登録にその指定が無い。** 決めずに掛け算すると、
**根拠の無い年率が文書に載る。**
"""

from __future__ import annotations

import dataclasses

#: 下見に使った期間の始まり。**出典は事前登録である。**
#:
#: `PREREG_MOMENTUM_JP.md` と `PREREG_ANTIVALUE_JP.md` の両方に
#: 「IS（推定に使う） 2009-01 〜 2017-12」と書いてある。**会話にしか無い
#: 値を書かない**（`CLAUDE.md`「出典の無い数字を書かない」）。
#:
#: **これが在るのは、年数を増やせないことを出力に出すためである。**
#: 2009-01 〜 2017-12 は 8本の説で下見済みなので、そこを OOS に入れ替えると
#: **覗いた後の「本番」**になる（`docs/PASSING.md` 条件②）。
#: **無いことは出力に出ない**のと同じで、**動かせないことも出力に出ない。**
LOOKED_FROM = "2009-01"


@dataclasses.dataclass(frozen=True)
class Shape:
    """測った設計の形。**散らばりだけを持ち、要るリターンは持たない。**"""

    name: str
    sd: float
    """1期あたりの標準偏差。"""

    inflation: float
    """重なりによる標準誤差の膨張。"""

    periods: int
    """判定に使える期数。**OOS のぶんだけ。**"""

    unit: str
    """期の単位。表示に使う。"""

    per_year: int
    """1年あたりの期数。**0 なら年率に直さない。**"""

    source: str
    """どこに書いてあるか。**出典の無い数字を書かないため。**"""

    pipe: str = "monthly"
    """どの管で測ったか。**線は管ごとに違う。**

    **1つの線を全部に当てていた**（2026-09-18 まで）。#5 はイベント型なのに、
    月次の盤面で測った 3.39 で「要るリターン」を出していた。**線が管ごとだと
    決めた日に、ここだけ直し忘れていた。**

    `monthly` か `event`。`line()` がこれを見て線を選ぶ。
    """

    def line(self) -> float:
        """この形に当てる線。**管ごとに違う。**

        Returns:
            封印に使う `t`。

        Raises:
            ValueError: 知らない ``pipe``。
        """
        from stock_ai.backtest.multiplicity import line_for

        return line_for(self.pipe)

    def standard_error(self) -> float:
        """OOS の平均の標準誤差。**式は `power` に1つだけ置いてある。**"""
        from stock_ai.backtest.power import standard_error

        return standard_error(self.sd, self.inflation, self.periods)

    def required(self, target: float) -> float:
        """合格に要る、1期あたりの大きさ。"""
        return target * self.standard_error()

    def required_annual(self, target: float) -> float | None:
        """年率。**直せないなら `None`。**"""
        if self.per_year <= 0:
            return None
        return self.required(target) * self.per_year

    @property
    def period_years(self) -> float | None:
        """**判定に使える年数。** 年率に直せない設計では ``None``。

        **`periods ÷ per_year` から作る。** 別々に持つと、片方だけ直した
        ときに**同じ行の2つの列が別々の標本を指す**——`CLAUDE.md` に
        5度書いてある形である。
        """
        return None if self.per_year <= 0 else self.periods / self.per_year

    def required_ir(self) -> float | None:
        """合格に要る**年率の情報比**。**式は `power` に1つだけ置いてある。**

        **設計によらない1つの数である**（`線 × 膨張 ÷ √年数`）。要る
        リターンは設計ごとに単位も桁も違うが、**散らばりで割ると n も SD も
        消える。**

        **重なる窓には出さない。** イベント型は :attr:`per_year` が 0 で、
        1観測が取引できる系列ではない——`required_annual` と同じ理由で
        ``None`` を返す。**決めずに割ると、根拠の無い情報比が文書に載る。**
        """
        from stock_ai.backtest.power import required_information_ratio

        years = self.period_years
        if years is None:
            return None
        return required_information_ratio(self.line(), self.inflation, years)


#: 測った設計。**すべて事前登録に記録がある。**
#:
#: 順番は「要るリターンが小さい順」——**いちばん甘い設計でどれだけ要るか**が
#: 先頭に来るようにする。
SHAPES: tuple[Shape, ...] = (
    Shape(
        name="#7 の形（低ボラ・ロングのみ・α）",
        sd=0.0184,
        inflation=1.09,
        periods=104,
        unit="月",
        per_year=12,
        source="PREREG_LOWVOL_JP.md §8（分位1 − β×ベンチ）",
    ),
    Shape(
        name="#13 の形（月替わり・暦・指数）",
        sd=0.0312,
        inflation=0.97,
        periods=104,
        unit="月替わり",
        per_year=12,
        source="PREREG_TURN_OF_MONTH_JP.md §0（IS 107 回、窓 4 営業日）",
        pipe="calendar",
    ),
    Shape(
        name="#11 の形（複合・ロングショート・α）",
        sd=0.0446,
        inflation=0.95,
        periods=104,
        unit="月",
        per_year=12,
        source="PREREG_LOWVOL_VALUE_JP.md §0",
    ),
    Shape(
        name="#9 の形（バリュー・ロングショート・生の差）",
        sd=0.0493,
        inflation=1.15,
        periods=104,
        unit="月",
        per_year=12,
        source="PREREG_ANTIVALUE_JP.md §0",
    ),
    Shape(
        name="#12 の形（モメンタム・ロングショート・生の差）",
        sd=0.0542,
        inflation=1.17,
        periods=104,
        unit="月",
        per_year=12,
        source="PREREG_MOMENTUM_JP.md §0（IS 106ヶ月、入れ替わり 29.5%／月）",
    ),
    Shape(
        name="#5 の形（イベント型・20営業日）",
        sd=0.1616,
        inflation=1.04,
        periods=950,
        unit="イベント日",
        per_year=0,
        source="PREREG_REVISION_JP.md §0（1,827 件が 831 日。OOS は約 950 日）",
        pipe="event",
    ),
    Shape(
        name="#15 の形（窓は埋まる・イベント型・20営業日）",
        sd=0.0997,
        inflation=1.68,
        periods=2109,
        unit="イベント日",
        per_year=0,
        source="PREREG_GAP_FILL_JP.md §0（IS 31,617 件が 1,220 日。OOS は 2,109 日）",
        pipe="event",
    ),
    Shape(
        name="#16 の形（落ちるナイフ・イベント型・5営業日）",
        sd=0.0993,
        inflation=1.38,
        periods=1898,
        unit="イベント日",
        per_year=0,
        source="PREREG_KNIFE_JP.md §0（IS 5,576 件が 1,018 日。OOS は 1,898 日）",
        pipe="event",
    ),
)

#: 合格の条件。**手順そのもの。** 番号は文書と揃える。
#:
#: **言葉を足さない。** ここに書いたものが `docs/PASSING.md` にそのまま出る。
CONDITIONS: tuple[tuple[str, str], ...] = (
    (
        "① 先に全部、紙に書く",
        "何を買うか、いつ買うか、いつ売るか、どこで測るか。**売買する前に**文書に"
        "して、日付を付けて残す。**あとから「やっぱりこの条件を足そう」はできない。**"
        "都合のいい条件は、結果を見た後ならいくらでも思い付くからである。",
    ),
    (
        "② 期間を前半・後半に分ける",
        "**前半は下見**で、何度見てもかまわない。**後半は本番**で、**一度しか"
        "見られない。** 一度目が悪かったので条件を変えて二度目、をやると、それは"
        "もう検証ではない。",
    ),
    (
        "③ 前半で「そもそも見える大きさか」を確かめる",
        "前半の散らばりから、本番で見分けられる最小の大きさを計算する（§0）。"
        "**見込みがその下なら、本番を使わずに閉じる。** 見えない検査に、貴重な"
        "一度きりを使わないためである。**#8・#9・#11 はここで止まった。**",
    ),
    (
        "④ 後半を一度だけ回す",
        "**手数料を引く**（往復 0.4%）。**上場廃止した会社も入れる**——残った会社"
        "だけで測ると成績が良く見える。**その日時点の情報だけを使う**（発表の"
        "翌日から買う、など）。",
    ),
    (
        "⑤ 結果が「まぐれ」で説明できないこと",
        "**何の力も無くても、20回も試せばどれか1回は当たって見える。** `t` は"
        "「まぐれにしては大きすぎるか」を測る物差しで、線は**20回ぶんのまぐれを"
        "差し引いてもなお残る大きさ**に置いてある。",
    ),
)
