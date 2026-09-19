"""名簿に在って、価格が無い銘柄を数える。**穴は、黙って観測を消す。**

## なぜ要るか

イベント型の陰性対照で、引いた 800,000 件のうち **16,677 件（2.1%）が
「価格が1本も無い銘柄」に当たっていた**（2026-09-18、400回）。

**乱数だからどうでもいい、という話ではない。** 引いているのは
`list_securities` が返す銘柄で、**説の側の候補もそこから出る。** #5 の上方修正
も #8 の増担保も、公表した会社の証券コードで価格を引く。**そこに足が1本も
無ければ、そのイベントは判定に入らないまま消える。**

そして**消えたことは、リターンの側からは見えない。** 平均も `t` も、残った
ものだけで計算される。

## ここが数えるもの

`securities` に行が在って、`price_bars` に1行も無い銘柄。**別々の表なので、
片方だけ埋まることが起こりうる**——名簿を先に入れて価格の取り込みが失敗した、
あるいは J-Quants のプランで取れなかった、など。

そして**足が在っても短すぎる銘柄**も分けて数える。20営業日の窓を開けるには
最低 22 本要るので、それ未満は**在っても使えない。**

## 数えるだけで、直さない

**取り込みはここではやらない。** 何件あってどれなのかを出すところまでで、
取り直すかどうかは見てから決める。**穴の理由は1つではない**（上場前・
プランの範囲外・取り込み失敗）ので、一括で埋めにいくと理由が混ざる。
"""

from __future__ import annotations

import dataclasses

from stock_ai.core.logging import get_logger

logger = get_logger(__name__)

#: 窓を1つも開けられない足の本数。**#5・#8 の窓から出した値である。**
#:
#: 20営業日の窓は `D` → `D+1` 寄付き → `D+20` 終値なので、`D` を含めて
#: **22 本**要る。それ未満の銘柄は、足が在っても**イベントを1件も作れない。**
#:
#: **推測で置いていない。** 窓を変えれば一緒に変わるので、呼ぶ側から渡せる。
THIN_BARS = 22


@dataclasses.dataclass(frozen=True)
class Coverage:
    """名簿と価格の噛み合い。**件数ではなく割合で見る。**"""

    market: str
    listed: int
    """`securities` に在る銘柄数。**`list_securities` が返す数そのもの。**"""

    with_prices: int
    thin_bars: int
    """これ未満の足しか無い銘柄を「短い」と数える。"""

    empty: tuple[tuple[str, str | None], ...]
    """``(銘柄, 名前)``。**足が1本も無い。**"""

    thin: tuple[tuple[str, str | None, int], ...]
    """``(銘柄, 名前, 本数)``。**足は在るが、窓を1つも開けられない。**"""

    @property
    def recent_codes(self) -> tuple[tuple[str, str | None], ...]:
        """穴のうち、**英数字コード**のもの。

        東証が 2024 年から割り当てている `135A` のような形である。
        **ほぼ最近の上場を意味する。**

        **穴が一様でないなら、消える観測も一様でない。** 新規上場に偏って
        いれば、小型や IPO を扱う説ほど多くを失う。**件数だけ見ても、その
        偏りは出てこない。**
        """
        return tuple((symbol, name) for symbol, name in self.empty if _has_letter(symbol))

    @property
    def nameless(self) -> tuple[tuple[str, str | None], ...]:
        """穴のうち、**名前も入っていない**もの。

        価格の取り込みに失敗しただけなら名前は在る。**名前も無いのは、
        その行が別の経路のついでに作られて、一度も埋められていない**という
        ことである。
        """
        return tuple((symbol, name) for symbol, name in self.empty if not name)

    @property
    def empty_share(self) -> float:
        """足が1本も無い銘柄の割合。"""
        return len(self.empty) / self.listed if self.listed else 0.0

    @property
    def thin_share(self) -> float:
        """短すぎる銘柄の割合。"""
        return len(self.thin) / self.listed if self.listed else 0.0

    @property
    def unusable_share(self) -> float:
        """**イベントを1件も作れない銘柄の割合。** 穴と短いものの合計。

        一様に銘柄を引いたとき、**この割合が捨てられる。** 陰性対照の
        「価格が1本も無い銘柄（穴）」の割合と、ここが噛み合うはずである
        ——**別の切り口から同じ数を出して、一致するか見る。**
        """
        return self.empty_share + self.thin_share

    def warnings(self) -> list[str]:
        """気付かなくても目に入るべきこと。"""
        found: list[str] = []
        if not self.listed:
            return [f"**{self.market} の銘柄が1件も無い。**"]
        if self.empty:
            found.append(
                f"**名簿に在って足が1本も無い銘柄が {len(self.empty):,} 件"
                f"（{self.empty_share:.1%}）。** `list_securities` が返すので、"
                "**説の候補にも入る。そこに落ちたイベントは黙って消える。**"
            )
        # **穴が一様かどうかを、読む側に気付かせない。** 偏っていれば、
        # 消える観測も偏る。件数の1行からはそれが出てこない。
        if self.recent_codes:
            share = len(self.recent_codes) / len(self.empty)
            found.append(
                f"**穴の {len(self.recent_codes):,} 件（{share:.0%}）が英数字コード**"
                "（`135A` のような、2024年以降の割り当て）。**穴は最近の上場に"
                "偏っている——消える観測も偏る。**"
            )
        if self.nameless:
            found.append(
                f"**穴の {len(self.nameless):,} 件は名前も入っていない。** "
                "**その行は別の経路のついでに作られて、一度も埋められていない。**"
            )
        if self.thin:
            found.append(
                f"**足が {self.thin_bars} 本未満の銘柄が {len(self.thin):,} 件"
                f"（{self.thin_share:.1%}）。** 在っても窓を1つも開けられない。"
            )
        return found


def _has_letter(symbol: str) -> bool:
    """英数字コードか。**`135A` は 2024年以降の割り当てである。**"""
    return any(character.isalpha() for character in symbol)


def survey(database: object, market: str = "JP", thin_bars: int = THIN_BARS) -> Coverage:
    """名簿と価格を突き合わせて数える。**取り込みはしない。**

    Args:
        database: 価格の保存先。
        market: 数える市場。
        thin_bars: これ未満を「短い」とする足の本数。

    Returns:
        :class:`Coverage`。

    Raises:
        ValueError: ``thin_bars`` が 1 未満。
    """
    from stock_ai.database.repository import list_securities, price_history_spans

    if thin_bars < 1:
        raise ValueError(f"thin_bars must be at least 1; got {thin_bars}.")

    with database.session() as session:  # type: ignore[attr-defined]
        listed = [symbol for symbol, where in list_securities(session) if where == market]
        names = _names(session, market)
        # `price_history_spans` は内部結合なので、**足の無い銘柄はそもそも
        # 出てこない。** 出てこないことが答えなので、名簿から引き算する。
        bars = {
            symbol: count
            for symbol, where, _first, _last, count in price_history_spans(session)
            if where == market
        }

    empty = tuple((symbol, names.get(symbol)) for symbol in listed if symbol not in bars)
    thin = tuple(
        (symbol, names.get(symbol), bars[symbol])
        for symbol in listed
        if symbol in bars and bars[symbol] < thin_bars
    )
    logger.info(
        "%s: 名簿 %d、足あり %d、穴 %d、短い %d",
        market,
        len(listed),
        len(bars),
        len(empty),
        len(thin),
    )
    return Coverage(
        market=market,
        listed=len(listed),
        with_prices=len(bars),
        thin_bars=thin_bars,
        empty=empty,
        thin=thin,
    )


def _names(session: object, market: str) -> dict[str, str | None]:
    """銘柄コードから名前を引く。**コードだけ出しても、誰も判断できない。**"""
    from sqlalchemy import select

    from stock_ai.database.models import Security

    rows = session.execute(  # type: ignore[attr-defined]
        select(Security.symbol, Security.name).where(Security.market == market)
    ).all()
    return dict(rows)
