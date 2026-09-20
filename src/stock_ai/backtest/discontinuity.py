"""価格の系列が**不連続**になっている場所を、1つの規則で見つける。

## なぜ切り出したか

**同じ4行が5箇所に書かれていた**（`reversal` / `lowvol` / `lowvol_census` /
`factor_panel`、そして6箇所目を書きかけた）。`CLAUDE.md`「同じ処理を2つ
書かない」に正面から反している。

**片方だけ直したときに気付けない**——そして、この規則を落とすと**落ちずに
数字だけ変わる。** #6 は不連続を除外するだけで SD が **24.42% → 3.64%** に
なった。**桁が違う。**

## 何を不連続と呼ぶか

見つけ方は `session_breaks` に、定数はその下に置いてある。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

#: 1営業日でこれを超える動きは、日本株では値動きではない。
#:
#: **東証には値幅制限がある。** 制限は株価帯ごとに決まっていて、いちばん緩い
#: 低位株でも1日で ±50% を超えることはまず無い。超えているなら、価格の系列が
#: そこで**不連続**になっている——分割・併合か、売買停止をまたいだ再開である。
#:
#: 実測（8308、2005年）:
#:
#:   2005-07-26  終値     197   調整後     2.0   調整係数 0.0100
#:   （2005-07-27 〜 08-01 は足が無い＝併合による売買停止）
#:   2005-08-02  終値 204,000   調整後 2,040.0   調整係数 0.0100
#:
#: **調整係数は前後とも 0.0100 のまま**である。1:1000 の株式併合を、系列が
#: またいでいない。``adj_close`` 自体が不連続なので、``split_adjusted`` を
#: 通しても直らない。
#:
#: 検出は「直前に値のあった日」と比べる。暦に載せ替えたあとの NaN と比べると、
#: **売買停止を挟んだ併合が必ず素通りする**（8308 がまさにこれ）。
MAX_SESSION_MOVE = 0.5


def session_breaks(closes: pd.Series) -> np.ndarray:
    """その足が**不連続**かどうかを、足ごとに返す。

    **直前に値のあった日と比べる。** 暦に載せ替えたあとの `NaN` と比べると、
    **売買停止を挟んだ併合が必ず素通りする**（#6 の 8308 がまさにこれ）。

    Args:
        closes: 調整後終値。**暦に載せ替えたあとでよい**——`ffill` で埋める。

    Returns:
        ``closes`` と同じ長さの真偽値。先頭は常に偽（比べる先が無い）。
    """
    filled = closes.ffill().to_numpy(dtype=float)
    broken = np.zeros(len(filled), dtype=bool)
    if len(filled) < 2:  # noqa: PLR2004 - 1本では比べる先が無い
        return broken
    with np.errstate(divide="ignore", invalid="ignore"):
        step = filled[1:] / filled[:-1]
    broken[1:] = np.isfinite(step) & (np.abs(step - 1.0) > MAX_SESSION_MOVE)
    return broken


def crossings(broken: np.ndarray) -> np.ndarray:
    """位置ごとの「そこより前にある不連続の数」。**区間の判定に使う。**

    区間 ``[a, b]``（両端を含む）に不連続が在るかは
    ``crossings(broken)[b + 1] - crossings(broken)[a] > 0`` で取れる。

    Args:
        broken: :func:`session_breaks` が返したもの。

    Returns:
        長さが1つ多い累積和。
    """
    return np.concatenate(([0], np.cumsum(broken)))


def spans_break(prefix: np.ndarray, first: int, last: int) -> bool:
    """``[first, last]`` が不連続をまたぐか。**両端を含む。**

    Args:
        prefix: :func:`crossings` が返したもの。
        first: 区間の始まり。
        last: 区間の終わり。

    Returns:
        またぐなら真。
    """
    return bool(prefix[last + 1] - prefix[first] > 0)
