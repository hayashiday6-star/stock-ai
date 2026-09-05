"""値幅制限に達した日と、売買停止明けの日を、リターンを計算せずに数える。

**8本目を封印する前に、母集団があるかだけを先に確かめる。** #1 では、条件を
満たす銘柄の 97.5% が流動性フィルタで消えた。それが分かったのは検証を組んだ
あとだった。ここでは順序を逆にする。

数えるのは件数・営業日数・分布だけである。**リターンは1つも計算しない**ので、
判定を消費しない。

## なぜこの2つか

7本すべてが、検出力の最も不利な角にいた——長い保有窓と、少ない独立観測。
窓の標準偏差は窓の長さの平方根でおよそ効くので、**窓を短くするだけで検出できる
差が下がる。** どちらも保有1〜5営業日で、価格だけから検出できる。

- **値幅制限**は東証の規則である。制限に達した時点で売買が物理的に成立しなく
  なり、買いたい人が買えないまま翌日に持ち越される
- **売買停止**は情報が止まる。再開時に一度に織り込まれるなら、翌日以降に残りが
  出る

## 値幅制限の検出は近似である

**制限幅は株価帯ごとの階段表で決まり、その表は改定されている。** 過去の表を
持っていないので、ここでは近似する。

  高値 ＝ 安値、出来高あり、前日比がプラス

**表を推測して書かない。** 出典の無い階段表を実装すると、当たっているかどうか
を確かめる手段ごと失う。

### 前日比の分布では、当たり具合を判定できない（2026-09-05 に判明）

`move_histogram` は「制限幅の表が効いているなら前日比は少数の離散値に固まる」
という見込みで作った。**この見込みが間違っていた。**

**制限幅は円建ての階段表である。** 同じ ±300円 でも、1,010円の株なら 29.7%、
1,490円なら 20.1% になる。パーセントで刻めば、**正しく拾えていても連続的に
散る。** 実測（2026-09-05、2,014件）は 12〜25% を中心になだらかな山になった。
これは「近似が外れている」証拠でも「当たっている」証拠でもない。

判定するなら**株価帯ごとに円建ての値幅を見る**必要がある。値幅制限の説は
執行で閉じたので、そこは追っていない。`move_histogram` は分布を見るだけの
ものとして残す。

## 執行できるかを、同じ走査で測る

**ストップ高の翌日に成行で買うのは、他の説と同じ 0.6% では済まない。** 寄り付き
で上に食う。費用の仮定を実測せずに封印すると、**実行できない合格が出る。**
7本の中でまだ経験していない失敗の形である。

同じ走査で3つ測る。リターンではないので、判定は消費しない。

- **翌日も張り付いた件数。** これは費用ではなく、**買えない**ということである。
  約定を仮定した検証は、この件をそのまま「買えた」ことにする
- **翌日始値のギャップ**（始値 ÷ 当日終値 − 1）。**払う分そのもの**
- **始値が当日の高安のどこにあるか。** 1 に寄っていれば、その日のいちばん悪い
  ところで買っている

## 売買停止の検出は暦との差である

その銘柄に足が無く、市場には足がある日を数える。市場の暦は**実データから作る**
——ある日に何銘柄が約定したかを数え、`MIN_MARKET_BREADTH` 以上の日を営業日と
みなす。祝日表を持ち込まなくて済み、持ち込んだ表が古いという失敗も起きない。

分割・併合による停止と、それ以外の停止は**ここでは分けない。** #6 の
`MAX_SESSION_MOVE` を再開日に当てて、またいだ不連続の件数を別に数える。
分けるのは、件数が足りると分かってからでよい。
"""

from __future__ import annotations

import bisect
import datetime as dt
from collections import Counter
from dataclasses import dataclass, field

import pandas as pd
from sqlalchemy.orm import Session

from stock_ai.backtest.pead import MIN_TURNOVER, TURNOVER_WINDOW
from stock_ai.backtest.reversal import BENCHMARK, MAX_SESSION_MOVE
from stock_ai.core.exceptions import DataError
from stock_ai.core.logging import get_logger
from stock_ai.data.schema import CLOSE, HIGH, LOW, OPEN, VOLUME, split_adjusted
from stock_ai.database.engine import Database
from stock_ai.database.repository import PriceRepository, list_securities

logger = get_logger(__name__)

#: その日を営業日とみなすのに要る約定銘柄数。
#:
#: 祝日表を持ち込まずに市場の暦を作るための下限である。実際の営業日には数千
#: 銘柄が約定するので、この線はどこに置いてもほぼ同じ暦になる。効くのは
#: 「数銘柄しか値の付いていない日」を落とすことだけで、そういう日を営業日に
#: 数えると、**その日に足の無い全銘柄が売買停止に見える。**
MIN_MARKET_BREADTH = 50

#: 暦の隙間がこれ以下なら、停止ではなく週末・連休とみなして調べない。
#:
#: 候補を絞るためだけの粗い足切りで、判定そのものは暦との突き合わせで行う。
#: 3連休を挟むと暦日で4日空くので、それより広いものだけを見る。
CANDIDATE_GAP_DAYS = 5


@dataclass(frozen=True)
class EventCensus:
    """数えた結果。**リターンは含まない。**"""

    kind: str
    """``値幅制限`` か ``売買停止明け``。"""

    symbols_scanned: int
    symbols_without_prices: int

    raw_events: int
    """流動性フィルタを通す**前**の件数。"""

    events: int
    """流動性フィルタを通した後の件数。**これが母集団である。**"""

    excluded_thin: int
    """売買代金が下限に届かなかった件数。"""

    excluded_no_history: int
    """売買代金を測るだけの履歴が無かった件数（上場直後など）。"""

    per_day: Counter[dt.date] = field(default_factory=Counter)
    """営業日ごとの件数。"""

    moves: list[float] = field(default_factory=list)
    """検出日の前日比。近似の当たり具合を見るためだけに持つ。"""

    turnovers: list[float] = field(default_factory=list)
    """同じ件の売買代金（円）。``moves`` と同じ順。"""

    lengths: list[int] = field(default_factory=list)
    """停止の長さ（営業日）。値幅制限では空。"""

    crossed_discontinuity: int = 0
    """再開日が #6 の不連続の規則に当たった件数。値幅制限では 0。"""

    unfillable: int = 0
    """翌日も高値＝安値で、**買おうとしても約定しない**件数。

    これは費用ではなく、**取れないということ**である。約定を仮定した検証は、
    この件をそのまま「買えた」ことにしてしまう。例外は出ない。
    """

    no_next_bar: int = 0
    """翌日の足が無い件数（系列の末尾、または翌日から停止）。"""

    gaps: list[float] = field(default_factory=list)
    """買えた件の、翌日始値 ÷ 当日終値 − 1。**これが払う分である。**"""

    open_positions: list[float] = field(default_factory=list)
    """翌日始値が、その日の高安のどこにあるか（0 が安値、1 が高値）。

    1 に寄っていれば、**その日のいちばん悪いところで買っている。**
    """

    @property
    def survival(self) -> float:
        """流動性フィルタを通った割合。**#1 はここが 2.5% だった。**"""
        return self.events / self.raw_events if self.raw_events else 0.0

    @property
    def trading_days(self) -> int:
        """件が1つでもあった営業日の数。"""
        return len(self.per_day)

    def by_year(self) -> list[tuple[int, int, int]]:
        """年ごとの (年, 件数, その年に件のあった営業日数)。"""
        years = sorted({day.year for day in self.per_day})
        return [
            (
                year,
                sum(count for day, count in self.per_day.items() if day.year == year),
                sum(1 for day in self.per_day if day.year == year),
            )
            for year in years
        ]

    def breadth(self) -> list[tuple[str, int]]:
        """1営業日あたりの件数の分布。"""
        return _quantiles(sorted(self.per_day.values()), (0.0, 0.5, 0.95, 1.0))

    def concentration(self, share: float = 0.1) -> float:
        """上位 ``share`` の日に、件数の何割が乗っているか。

        **これが 52週高値の要である。** イベントが同じ日に固まると、独立観測は
        件数より少なくなり、標準誤差はその平方根ぶん大きくなる。件数だけ数えて
        「1,000件ある」と読むと、**検出できる差を実際より小さく見積もる。**

        均等に散っていれば ``share`` に近づく。1.0 に近ければ、ほとんどの件が
        少数の日に乗っている。
        """
        if not self.per_day:
            return 0.0
        counts = sorted(self.per_day.values(), reverse=True)
        top = max(1, int(len(counts) * share))
        return sum(counts[:top]) / sum(counts)

    def effective_days(self) -> float:
        """日をひとかたまりと見たときの、実効的な独立観測数。

        1日を1つの観測と数える（同じ日の銘柄は同じ市場の動きを共有するので、
        独立ではない）。**件数ではなくこれが標準誤差を決める。**
        """
        return float(len(self.per_day))

    def move_histogram(self, buckets: int = 12) -> list[tuple[str, int]]:
        """前日比の分布。

        **近似の当たり具合はここでは判定できない。** 制限幅は円建てなので、
        正しく拾えていてもパーセントでは連続的に散る（モジュールの説明を参照）。
        分布を見るためだけのものである。
        """
        if not self.moves:
            return []
        top = max(self.moves)
        if top <= 0:
            return []
        width = top / buckets
        counted: Counter[int] = Counter()
        for move in self.moves:
            counted[min(buckets - 1, int(move / width))] += 1
        return [
            (f"{index * width:.1%}〜{(index + 1) * width:.1%}", counted[index])
            for index in range(buckets)
            if counted[index]
        ]

    def length_histogram(self) -> list[tuple[str, int]]:
        """停止の長さの分布（営業日）。"""
        if not self.lengths:
            return []
        counted = Counter(self.lengths)
        return [
            (f"{length}日" if length < 10 else "10日以上", count)
            for length, count in sorted(counted.items())
        ]

    @property
    def fillable(self) -> int:
        """翌日に買える件数。**これが実際に使える母集団である。**"""
        return len(self.gaps)

    def gap_quantiles(self) -> list[tuple[str, float]]:
        """翌日始値のギャップの分布。

        **費用の仮定はここから決める。** 0.6%（他の説と同じ往復費用）で置いて
        よいのは、この分布が 0.6% に収まっているときだけである。収まって
        いなければ、**実行できない合格が出る。**
        """
        return [
            (name, float(value))
            for name, value in _quantiles(sorted(self.gaps), (0.05, 0.25, 0.5, 0.75, 0.95))
        ]

    def open_position_quantiles(self) -> list[tuple[str, float]]:
        """翌日始値の、その日の高安の中での位置。"""
        return [
            (name, float(value))
            for name, value in _quantiles(sorted(self.open_positions), (0.25, 0.5, 0.75))
        ]

    def turnover_quantiles(self) -> list[tuple[str, float]]:
        """検出した件の売買代金の分布（億円）。

        **フィルタを通ってなお小型に寄っていないか。** #1 で消えた 97.5% は
        フィルタの外側の話だが、内側でも偏りうる。
        """
        values = sorted(value / 1e8 for value in self.turnovers)
        return [(name, float(value)) for name, value in _quantiles(values, (0.05, 0.5, 0.95))]


def _quantiles(values: list[float] | list[int], fractions: tuple[float, ...]) -> list:
    """並べ替え済みの列から分位を拾う。"""
    if not values:
        return []
    return [
        (f"p{int(fraction * 100)}", values[min(len(values) - 1, int(fraction * len(values)))])
        for fraction in fractions
    ]


def _jp_symbols(session: Session, exclude: frozenset[str] = frozenset({BENCHMARK})) -> list[str]:
    """JP の銘柄コード。**ベンチマークは除く。**

    ``list_securities`` は市場の絞り込み引数を**取らない。** 渡すと
    ``TypeError`` になる（本番で一度出した）。返ってきた組を絞る。

    ベンチマーク（1306、TOPIX連動ETF）も JP の銘柄として保存されているので、
    除かないと**指数そのものがイベントの母集団に入る。** 例外は出ない——
    ETF は毎日のように52週高値を更新し、しかも売買代金は十分にある。

    **2026-09-05 のセンサス（値幅制限 2,014件・売買停止明け 37件・52週高値
    248,217件）は、この除外を入れる前の値である。** 4,303 銘柄のうち1銘柄なので
    結論は動かないが、数字はそのまま引き写さずに、再測定したら書き換えること。
    """
    return [
        symbol
        for symbol, market in list_securities(session)
        if market == "JP" and symbol not in exclude
    ]


def _turnover_floor(raw: pd.DataFrame) -> pd.Series:
    """前日までの20営業日平均売買代金。

    生値で測る。調整済み終値に実出来高を掛けると、分割前のバーを分割比率の
    ぶん過小に見積もる。
    """
    return (raw[CLOSE] * raw[VOLUME]).rolling(TURNOVER_WINDOW).mean().shift(1)


def count_limit_moves(
    database: Database,
    symbols: list[str] | None = None,
    min_turnover: float = MIN_TURNOVER,
) -> EventCensus:
    """値幅制限に達したとみられる日を数える（上側だけ）。

    上側だけを数えるのは、**ロングオンリーで使うのが上側だから**である。
    ストップ安は破綻銘柄に集中するので生存バイアス感応度が高く、いま持って
    いる名簿（2021-09 以降）では直しきれない。

    Args:
        database: 価格の保存先。
        symbols: 対象銘柄。省略時は JP の全銘柄。
        min_turnover: 流動性の下限（円）。他の説と同じ1億円。

    Returns:
        件数と分布。**リターンは含まない。**
    """
    per_day: Counter[dt.date] = Counter()
    moves: list[float] = []
    turnovers: list[float] = []
    gaps: list[float] = []
    positions: list[float] = []
    raw_events = thin = no_history = no_prices = 0
    unfillable = no_next_bar = 0

    with database.session() as session:
        if symbols is None:
            symbols = _jp_symbols(session)
        prices = PriceRepository(session)

        for symbol in symbols:
            raw = prices.get_raw_prices(symbol)
            if raw.empty:
                no_prices += 1
                continue

            adjusted = split_adjusted(raw)
            close = adjusted[CLOSE].to_numpy(dtype=float)
            floor = _turnover_floor(raw).to_numpy(dtype=float)
            highs = raw[HIGH].to_numpy(dtype=float)
            lows = raw[LOW].to_numpy(dtype=float)
            volumes = raw[VOLUME].to_numpy(dtype=float)
            # 執行は調整後で測る。翌日が分割の初日だと、生値では始値だけが
            # 別の尺度になり、ギャップが分割比率そのものになる。
            opens = adjusted[OPEN].to_numpy(dtype=float)
            adj_high = adjusted[HIGH].to_numpy(dtype=float)
            adj_low = adjusted[LOW].to_numpy(dtype=float)
            index = adjusted.index

            for position in range(1, len(index)):
                if not (highs[position] == lows[position] and volumes[position] > 0):
                    continue
                previous = close[position - 1]
                if not previous > 0:
                    continue
                move = close[position] / previous - 1.0
                if move <= 0:
                    continue
                # #6 と同じ規則。1日でこれを超えるのは値動きではなく、分割・
                # 併合か停止明けの不連続である。**制限に達した日ではない。**
                if move > MAX_SESSION_MOVE:
                    continue

                raw_events += 1
                level = floor[position]
                if pd.isna(level):
                    no_history += 1
                    continue
                if level < min_turnover:
                    thin += 1
                    continue

                per_day[index[position].date()] += 1
                moves.append(move)
                turnovers.append(float(level))

                # **翌日そこで買えるのか。** ここを飛ばすと、約定しない日を
                # 「買えた」ことにした検証ができあがる。
                nxt = position + 1
                if nxt >= len(index):
                    no_next_bar += 1
                    continue
                if highs[nxt] == lows[nxt] and volumes[nxt] > 0:
                    # 翌日も張り付いている。成行を出しても約定しない。
                    unfillable += 1
                    continue
                if not (volumes[nxt] > 0 and opens[nxt] > 0 and close[position] > 0):
                    no_next_bar += 1
                    continue
                gaps.append(opens[nxt] / close[position] - 1.0)
                span = adj_high[nxt] - adj_low[nxt]
                if span > 0:
                    positions.append((opens[nxt] - adj_low[nxt]) / span)

    return EventCensus(
        kind="値幅制限",
        symbols_scanned=len(symbols),
        symbols_without_prices=no_prices,
        raw_events=raw_events,
        events=len(moves),
        excluded_thin=thin,
        excluded_no_history=no_history,
        per_day=per_day,
        moves=moves,
        turnovers=turnovers,
        unfillable=unfillable,
        no_next_bar=no_next_bar,
        gaps=gaps,
        open_positions=positions,
    )


#: 52週高値を測る営業日数。1年はおよそ250営業日。
HIGH_LOOKBACK = 250


def count_52w_highs(
    database: Database,
    symbols: list[str] | None = None,
    min_turnover: float = MIN_TURNOVER,
    lookback: int = HIGH_LOOKBACK,
) -> EventCensus:
    """52週高値を更新した日を数える。

    **ここで見るのは件数ではなく、固まり具合である。** 更新は上昇局面に集中
    するので、件数が多くても独立観測はそれより少ない。`concentration` と
    `effective_days` がそれを出す。

    値幅制限と同じ執行の欄も埋める。翌日始値で買う前提は変わらないので、
    ギャップは同じように効く。

    Args:
        database: 価格の保存先。
        symbols: 対象銘柄。省略時は JP の全銘柄。
        min_turnover: 流動性の下限（円）。他の説と同じ1億円。
        lookback: 高値を測る営業日数。

    Returns:
        件数と分布。**リターンは含まない。**
    """
    per_day: Counter[dt.date] = Counter()
    moves: list[float] = []
    turnovers: list[float] = []
    gaps: list[float] = []
    positions: list[float] = []
    raw_events = thin = no_history = no_prices = 0
    unfillable = no_next_bar = 0

    with database.session() as session:
        if symbols is None:
            symbols = _jp_symbols(session)
        prices = PriceRepository(session)

        for symbol in symbols:
            raw = prices.get_raw_prices(symbol)
            if raw.empty:
                no_prices += 1
                continue

            adjusted = split_adjusted(raw)
            close = adjusted[CLOSE].to_numpy(dtype=float)
            if len(close) <= lookback:
                no_history += 1
                continue
            floor = _turnover_floor(raw).to_numpy(dtype=float)
            opens = adjusted[OPEN].to_numpy(dtype=float)
            adj_high = adjusted[HIGH].to_numpy(dtype=float)
            adj_low = adjusted[LOW].to_numpy(dtype=float)
            highs = raw[HIGH].to_numpy(dtype=float)
            lows = raw[LOW].to_numpy(dtype=float)
            volumes = raw[VOLUME].to_numpy(dtype=float)
            index = adjusted.index

            # **その日を含めない**過去 lookback 営業日の最高値。含めると、
            # 自分自身を超えられないので更新が1件も出ない。
            trailing = adjusted[CLOSE].rolling(lookback).max().shift(1).to_numpy(dtype=float)

            for position in range(lookback, len(index)):
                previous_high = trailing[position]
                if pd.isna(previous_high) or not close[position] > previous_high:
                    continue
                if not (close[position] > 0 and previous_high > 0):
                    continue

                raw_events += 1
                level = floor[position]
                if pd.isna(level):
                    no_history += 1
                    continue
                if level < min_turnover:
                    thin += 1
                    continue

                per_day[index[position].date()] += 1
                moves.append(close[position] / previous_high - 1.0)
                turnovers.append(float(level))

                nxt = position + 1
                if nxt >= len(index):
                    no_next_bar += 1
                    continue
                if highs[nxt] == lows[nxt] and volumes[nxt] > 0:
                    unfillable += 1
                    continue
                if not (volumes[nxt] > 0 and opens[nxt] > 0):
                    no_next_bar += 1
                    continue
                gaps.append(opens[nxt] / close[position] - 1.0)
                span = adj_high[nxt] - adj_low[nxt]
                if span > 0:
                    positions.append((opens[nxt] - adj_low[nxt]) / span)

    return EventCensus(
        kind="52週高値更新",
        symbols_scanned=len(symbols),
        symbols_without_prices=no_prices,
        raw_events=raw_events,
        events=len(moves),
        excluded_thin=thin,
        excluded_no_history=no_history,
        per_day=per_day,
        moves=moves,
        turnovers=turnovers,
        unfillable=unfillable,
        no_next_bar=no_next_bar,
        gaps=gaps,
        open_positions=positions,
    )


def high_event_returns(
    database: Database,
    holding: int,
    symbols: list[str] | None = None,
    min_turnover: float = MIN_TURNOVER,
    lookback: int = HIGH_LOOKBACK,
    benchmark: str = BENCHMARK,
) -> list[float]:
    """52週高値更新の等加重バスケットの、**イベント日ごとの超過リターン**。

    **平均は返さない側の道具である。** ここが返すのは並びだけで、呼び出し側は
    `estimate_power` に渡して分散と重なりの膨張だけを取る。§0 に入れる「検出
    できる差」は、そこから出る。

    **分散を測ることは判定を消費しない。** 効果の大きさではなく、散らばりを
    測っているからである。#6・#7 と同じ手順で、同じ理由による。

    入り方は他のイベント型と同じ。更新日 D の**翌日の寄付き**で等加重に買い、
    ``holding`` 営業日後の終値で降りる。同じ期間のベンチマークを引く。**D の
    終値では買えない**——更新は引けにしか分からない。

    重なりは残す。毎日入るので ``holding`` 日ぶん重なるが、それは設計の一部で
    あって欠陥ではない。`estimate_power(values, lags=holding)` が織り込む。

    Args:
        database: 価格の保存先。
        holding: 保有営業日数。
        symbols: 対象銘柄。省略時は JP の全銘柄。
        min_turnover: 流動性の下限（円）。
        lookback: 高値を測る営業日数。
        benchmark: 控除するベンチマークの銘柄コード。

    Returns:
        イベント日ごとの超過リターン。**古い順。**
    """
    if holding < 1:
        raise ValueError(f"holding must be at least 1; got {holding}.")

    by_day: dict[dt.date, list[float]] = {}

    with database.session() as session:
        prices = PriceRepository(session)
        bench = split_adjusted(prices.get_raw_prices(benchmark))
        if bench.empty:
            raise DataError(f"ベンチマーク {benchmark!r} の価格がありません。")
        bench_open = bench[OPEN].to_numpy(dtype=float)
        bench_close = bench[CLOSE].to_numpy(dtype=float)
        bench_at = {stamp.date(): index for index, stamp in enumerate(bench.index)}

        if symbols is None:
            symbols = _jp_symbols(session)

        for symbol in symbols:
            raw = prices.get_raw_prices(symbol)
            if raw.empty:
                continue
            adjusted = split_adjusted(raw)
            close = adjusted[CLOSE].to_numpy(dtype=float)
            if len(close) <= lookback + holding:
                continue
            opens = adjusted[OPEN].to_numpy(dtype=float)
            floor = _turnover_floor(raw).to_numpy(dtype=float)
            index = adjusted.index
            trailing = adjusted[CLOSE].rolling(lookback).max().shift(1).to_numpy(dtype=float)

            for position in range(lookback, len(index) - holding):
                previous_high = trailing[position]
                if pd.isna(previous_high) or not close[position] > previous_high:
                    continue
                level = floor[position]
                if pd.isna(level) or level < min_turnover:
                    continue

                entry, exit_at = opens[position + 1], close[position + holding]
                if not (entry > 0 and exit_at > 0):
                    continue

                # ベンチマークは**同じ日付**で取る。位置で取ると、その銘柄に
                # 足の無い日があったぶんだけずれる。
                when = index[position].date()
                mark = bench_at.get(index[position + 1].date())
                leave = bench_at.get(index[position + holding].date())
                if mark is None or leave is None:
                    continue
                if not (bench_open[mark] > 0 and bench_close[leave] > 0):
                    continue

                excess = (exit_at / entry) - (bench_close[leave] / bench_open[mark])
                by_day.setdefault(when, []).append(excess)

    return [sum(values) / len(values) for _day, values in sorted(by_day.items())]


@dataclass(frozen=True)
class _HaltCandidate:
    """暦と突き合わせる前の、停止らしき隙間。"""

    symbol: str
    resumed_on: dt.date
    last_seen: dt.date
    turnover: float
    """停止に入る前の20営業日平均売買代金。``NaN`` なら履歴が足りない。"""
    step: float
    """停止をまたいだ終値の比。不連続かどうかを再開日で見る。"""


def count_halt_resumptions(
    database: Database,
    symbols: list[str] | None = None,
    min_turnover: float = MIN_TURNOVER,
) -> EventCensus:
    """売買停止が明けた日を数える。

    市場の暦は実データから作る。ある日に ``MIN_MARKET_BREADTH`` 以上の銘柄が
    約定していれば営業日とみなす。**祝日表を持ち込まない**ので、表が古いこと
    による誤検出が起きない。

    Args:
        database: 価格の保存先。
        symbols: 対象銘柄。省略時は JP の全銘柄。
        min_turnover: 流動性の下限（円）。停止に**入る前**の売買代金で測る。

    Returns:
        件数と分布。**リターンは含まない。**
    """
    market_days: Counter[dt.date] = Counter()
    candidates: list[_HaltCandidate] = []
    no_prices = 0

    with database.session() as session:
        if symbols is None:
            symbols = _jp_symbols(session)
        prices = PriceRepository(session)

        for symbol in symbols:
            raw = prices.get_raw_prices(symbol)
            if raw.empty:
                no_prices += 1
                continue

            dates = [stamp.date() for stamp in raw.index]
            market_days.update(dates)

            adjusted = split_adjusted(raw)
            close = adjusted[CLOSE].to_numpy(dtype=float)
            floor = _turnover_floor(raw).to_numpy(dtype=float)

            for position in range(1, len(dates)):
                # 粗い足切り。週末・連休で空くぶんは調べない。判定そのものは
                # 暦との突き合わせで行う。
                if (dates[position] - dates[position - 1]).days <= CANDIDATE_GAP_DAYS:
                    continue
                before, after = close[position - 1], close[position]
                candidates.append(
                    _HaltCandidate(
                        symbol=symbol,
                        resumed_on=dates[position],
                        last_seen=dates[position - 1],
                        turnover=float(floor[position]),
                        step=(after / before) if before > 0 else float("nan"),
                    )
                )

    calendar = sorted(day for day, count in market_days.items() if count >= MIN_MARKET_BREADTH)
    logger.info(
        "市場の暦: %d 営業日（%d 銘柄以上が約定した日）、停止の候補 %d 件",
        len(calendar),
        MIN_MARKET_BREADTH,
        len(candidates),
    )

    per_day: Counter[dt.date] = Counter()
    lengths: list[int] = []
    turnovers: list[float] = []
    raw_events = thin = no_history = crossed = 0

    for candidate in candidates:
        # 前後の足の間に、市場が開いていた日が何日あるか。
        left = bisect.bisect_right(calendar, candidate.last_seen)
        right = bisect.bisect_left(calendar, candidate.resumed_on)
        missing = right - left
        if missing < 1:
            continue

        raw_events += 1
        if pd.isna(candidate.turnover):
            no_history += 1
            continue
        if candidate.turnover < min_turnover:
            thin += 1
            continue

        per_day[candidate.resumed_on] += 1
        lengths.append(missing)
        turnovers.append(candidate.turnover)
        if pd.notna(candidate.step) and abs(candidate.step - 1.0) > MAX_SESSION_MOVE:
            crossed += 1

    return EventCensus(
        kind="売買停止明け",
        symbols_scanned=len(symbols),
        symbols_without_prices=no_prices,
        raw_events=raw_events,
        events=len(lengths),
        excluded_thin=thin,
        excluded_no_history=no_history,
        per_day=per_day,
        moves=[],
        turnovers=turnovers,
        lengths=lengths,
        crossed_discontinuity=crossed,
    )
