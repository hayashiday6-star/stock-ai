"""1株あたりの指標と時価総額を読む（`/equities/valuation`）。

## なぜ要るのか

**時価総額が手元に無かった。** サイズ（小型株効果）を使う説はどれも、時価総額
を自前で組み立てるしかなかった——株価と発行済株式数を掛けるのだが、その株式数
は分割を跨ぐと尺度が変わる。**このプロジェクトが繰り返し踏んでいる形そのもの
である。**

このエンドポイントは、J-Quants の側で組み立て済みの値を返す。

## 列（全11。2026-09-15 に原本から数えた）

```
Date, Code, EPS, FwdEPS, BPS, ROE, FwdROE, PER, FwdPER, PBR, MktCap
```

`Fwd` が付くものは**会社予想**で、付かないものは**実績**である。混ぜると、
発表前から予想を知っていたことになる（先読み）。**別の列として持つ。**

## 2008〜2010 年頃は空が多い

公式の注記に「算出に使う株式数や財務情報が揃っていないので Null の多い銘柄・
項目がある」とある。**注記を引き写して終わりにしない。** :func:`census` が
年ごとに実数を出す。引用した注意書きと、手元のファイルが同じことを言っている
とは限らない。

## 中身を、このファイルだけで確かめられる

```
PER × EPS ≈ 終値       PBR × BPS ≈ 終値
```

**別の原本（`/equities/bars/daily`）と突き合わせる前に、ここだけで合うかが
分かる。** 合わなければ、読み違えているか、列の意味が想像と違う。
:func:`identity_check` がこれを数える。

**「1銘柄で合った」で終わらせない。** 何件中何件が合うかを出す。
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections import Counter
from pathlib import Path

import pandas as pd

from stock_ai.core.logging import get_logger
from stock_ai.data.jquants_bulk import records_from_csv
from stock_ai.data.jquants_margin import parse_date, parse_number
from stock_ai.data.jquants_read import endpoint_of, read_archived
from stock_ai.data.schema import DATE
from stock_ai.data.universe import four_digit_code

logger = get_logger(__name__)

#: 取り出し元のエンドポイント。
VALUATION_ENDPOINT = "/equities/valuation"

#: 銘柄コードの列名。
SYMBOL = "symbol"

#: 実績の列。**原本の綴り → こちらの綴り。**
ACTUAL_COLUMNS: dict[str, str] = {
    "EPS": "eps",
    "BPS": "bps",
    "ROE": "roe",
    "PER": "per",
    "PBR": "pbr",
    "MktCap": "market_cap",
}

#: 会社予想の列。**実績と混ぜない。** 混ぜると、発表前から予想を知っていた
#: ことになる。
FORECAST_COLUMNS: dict[str, str] = {
    "FwdEPS": "forward_eps",
    "FwdROE": "forward_roe",
    "FwdPER": "forward_per",
}

COLUMN_MAP: dict[str, str] = {**ACTUAL_COLUMNS, **FORECAST_COLUMNS}

#: 原本にある列の全部。**ここに無い列が出たら、向こうが増やしている。**
KNOWN_COLUMNS: frozenset[str] = frozenset({"Date", "Code", *COLUMN_MAP})

#: :func:`identity_check` が「合っている」と見なす幅。
#:
#: 終値は円単位で丸められ、EPS・BPS は小数である。掛け算の結果がぴったり
#: 一致することは無い。**1% は、丸めなら通り、読み違いなら通らない幅である。**
IDENTITY_TOLERANCE = 0.01

#: 掛け算で終値を復元するのに使える最小の値。
#:
#: **0 近傍で割らない。** EPS が 0.01 の銘柄で PER × EPS を作ると、PER の
#: 丸め誤差が何倍にもなって出てくる。合否ではなく**判定できるかどうか**の
#: 線引きなので、除いた件数も一緒に返す。
IDENTITY_FLOOR = 1.0


def parse_valuation(payload: bytes) -> pd.DataFrame:
    """展開済みの CSV を、``date`` と ``symbol`` を持つ表にする。

    **空欄は ``None`` のまま置く。0 で埋めない。** PBR が空の銘柄と PBR が
    0 の銘柄は別のもので、0 を入れると後者として並ぶ——**割安な銘柄を探す
    仕組みに、いちばん割安な顔をして入ってくる。**

    5桁コードのうち普通株でないもの（優先株・種類株）は落とす。判断は
    ``four_digit_code`` に借りる。**符号の規則を2つ持たない。**
    """
    rows: list[dict[str, object]] = []
    for record in records_from_csv(payload):
        date = parse_date(record.get("Date"))
        symbol = four_digit_code((record.get("Code") or "").strip() or None)
        if date is None or symbol is None:
            continue
        row: dict[str, object] = {DATE: date, SYMBOL: symbol}
        for source, target in COLUMN_MAP.items():
            row[target] = parse_number(record.get(source))
        rows.append(row)
    if not rows:
        return pd.DataFrame(columns=[DATE, SYMBOL, *COLUMN_MAP.values()])
    return pd.DataFrame(rows)


def unknown_columns(payload: bytes) -> list[str]:
    """原本に、こちらが知らない列があれば返す。

    **向こうが列を増やしたことは、例外にならない。** 黙って読み飛ばすので、
    増えた列は無かったことになる。数えれば気付ける。
    """
    records = records_from_csv(payload)
    if not records:
        return []
    return sorted(set(records[0]) - KNOWN_COLUMNS)


def from_archive(
    directory: Path,
    endpoint: str = VALUATION_ENDPOINT,
    limit: int | None = None,
    progress: object = None,
) -> pd.DataFrame:
    """保存した原本を読んで、1つの表にする。**取りには行かない。**

    同じ日付が2本のファイルに入ることがある（月次の `historical` と日次の
    `live` が重なる）。**重なったぶんは落とす**——残すと、同じ銘柄日が2度
    数えられる。件数は :class:`Census` が出す。
    """
    from stock_ai.data.jquants_archive import path_for, read_manifest

    keys = sorted(key for key in read_manifest(directory) if endpoint_of(key) == endpoint)
    if limit is not None:
        keys = keys[:limit]

    frames = []
    for index, key in enumerate(keys, start=1):
        if callable(progress):
            progress(index, len(keys), key)
        frames.append(parse_valuation(read_archived(path_for(directory, key))))
    if not frames:
        return pd.DataFrame(columns=[DATE, SYMBOL, *COLUMN_MAP.values()])
    combined = pd.concat(frames, ignore_index=True)
    return combined.drop_duplicates(subset=[DATE, SYMBOL], keep="first").reset_index(drop=True)


@dataclasses.dataclass
class Census:
    """年ごとに、列がどれだけ埋まっているか。

    **公式の注意書きを引き写さない。** 「2008〜2010 は Null が多い」と書いて
    あるが、どの列がどれだけ空なのかは書いていない。手元のファイルが答える。
    """

    rows_by_year: dict[int, int]
    filled_by_year: dict[int, dict[str, int]]
    symbols: int
    first: dt.date | None
    last: dt.date | None

    def share(self, year: int, column: str) -> float:
        """その年、その列が埋まっている割合。行が無ければ 0。"""
        rows = self.rows_by_year.get(year, 0)
        if not rows:
            return 0.0
        return self.filled_by_year.get(year, {}).get(column, 0) / rows

    def thin_years(self, column: str, threshold: float = 0.5) -> list[int]:
        """その列が半分も埋まっていない年。**使う前に見る場所である。**"""
        return sorted(year for year in self.rows_by_year if self.share(year, column) < threshold)


def census(frame: pd.DataFrame) -> Census:
    """読んだ表を年ごとに数える。**空を数えることが目的である。**"""
    if frame.empty:
        return Census({}, {}, 0, None, None)
    years = pd.to_datetime(frame[DATE]).dt.year
    rows_by_year = Counter(int(year) for year in years)
    filled: dict[int, dict[str, int]] = {}
    for year in rows_by_year:
        block = frame[years == year]
        filled[year] = {column: int(block[column].notna().sum()) for column in COLUMN_MAP.values()}
    return Census(
        rows_by_year=dict(sorted(rows_by_year.items())),
        filled_by_year=filled,
        symbols=int(frame[SYMBOL].nunique()),
        first=min(frame[DATE]),
        last=max(frame[DATE]),
    )


@dataclasses.dataclass
class IdentityReport:
    """``PER × EPS`` と ``PBR × BPS`` が、同じ終値を指しているか。

    **別の原本を持ち出す前に、このファイルだけで確かめられる。** 2つの掛け算
    が同じ数にならないなら、列の意味がこちらの想像と違う。
    """

    checked: int
    agreed: int
    skipped_small: int
    """小さすぎて判定できなかった行。

    EPS か BPS が :data:`IDENTITY_FLOOR` 未満のもの**と、掛け算の結果が
    それ未満のもの**。`pbr` が 0 なら `bps` が大きくても積は 0 になるので、
    片方ずつ見るだけでは足りない。
    """

    skipped_missing: int
    """どれかの列が空で、判定できなかった行。"""

    worst: list[tuple[dt.date, str, float, float]] = dataclasses.field(default_factory=list)

    @property
    def rate(self) -> float:
        """判定できた行のうち、合った割合。判定できた行が無ければ 0。"""
        return self.agreed / self.checked if self.checked else 0.0

    def summary(self) -> str:
        """1行のまとめ。"""
        return (
            f"判定できた {self.checked:,} 行のうち {self.agreed:,} 行が一致"
            f"（{self.rate:.1%}）。"
            f"小さすぎて判定できない {self.skipped_small:,} 行、"
            f"空で判定できない {self.skipped_missing:,} 行。"
        )


def identity_check(frame: pd.DataFrame, limit: int = 5) -> IdentityReport:
    """``PER × EPS`` と ``PBR × BPS`` が一致するかを数える。

    どちらも終値を復元するはずの掛け算である。**一致するかどうかは、この
    ファイルの中だけで決まる**——株価の原本を持ち出す必要が無い。

    **「合わない」と「判定できない」を分ける。** 空の行と、EPS が 0 近傍で
    誤差が何倍にもなる行は、合否に数えない。数えると、2008 年頃の空の多さが
    そのまま「不一致率」として出てきて、読み違いと見分けが付かなくなる。

    Args:
        frame: :func:`parse_valuation` が返す表。
        limit: ずれの大きいものを何件まで返すか。

    Returns:
        :class:`IdentityReport`。
    """
    if frame.empty:
        return IdentityReport(0, 0, 0, 0)

    needed = ["eps", "per", "bps", "pbr"]
    present = frame[needed].notna().all(axis=1)
    missing = int((~present).sum())

    usable = frame[present]
    # **掛け算の結果そのものを見る。** 最初は `eps` と `bps` だけを見ていて、
    # 「片方が 0 になる行は判定から外れているはず」とコメントに書いた。
    # **外れていなかった。** `pbr` が 0 なら、`bps` がどれだけ大きくても積は
    # 0 になる。実データ 1,588万行で `pd.NA` が入り、`_gap` が object 型に
    # なって落ちた（2026-09-15）。
    #
    # 想定を書いたのに、その想定が成り立つかを確かめていなかった。**積に床を
    # 当てれば、前提そのものが要らなくなる。**
    from_earnings = usable["per"] * usable["eps"]
    from_book = usable["pbr"] * usable["bps"]
    big = (
        (usable["eps"].abs() >= IDENTITY_FLOOR)
        & (usable["bps"].abs() >= IDENTITY_FLOOR)
        & (from_book.abs() >= IDENTITY_FLOOR)
    )
    small = int((~big).sum())

    judged = usable[big]
    if judged.empty:
        return IdentityReport(0, 0, small, missing)

    from_earnings, from_book = from_earnings[big], from_book[big]
    gap = (from_earnings - from_book).abs() / from_book.abs()
    agreed = gap <= IDENTITY_TOLERANCE

    # **ずれの大きい順に並べる。** 件数だけでは、丸めの積み重なりなのか、
    # 特定の銘柄が外れているのかが分からない。
    off = judged.loc[~agreed].assign(_gap=gap[~agreed])
    worst = [
        (
            row[DATE],
            str(row[SYMBOL]),
            float(row["per"] * row["eps"]),
            float(row["pbr"] * row["bps"]),
        )
        for _, row in off.nlargest(limit, "_gap").iterrows()
    ]

    return IdentityReport(
        checked=int(len(judged)),
        agreed=int(agreed.sum()),
        skipped_small=small,
        skipped_missing=missing,
        worst=worst,
    )
