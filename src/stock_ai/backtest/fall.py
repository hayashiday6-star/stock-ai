"""「基準から `drop` 以上下げたか」の判定。**線の当て方はここが正本である。**

`#16`（落ちるナイフ・5営業日で −20%）と `#15`（窓は埋まる・前日終値から
−3%）が、**同じ式を別々に書いていた。**

```python
knife.py     fell[usable] = after[usable] / before[usable] - 1.0 <= -drop
gap_fill.py  fell[usable] = opened[usable] / previous[usable] - 1.0 <= -gap
```

## その式は「以下」ではなく「未満」だった

事前登録は「**−20% 以下**」と書いてある。ところが `680 → 544`（ちょうど
−20%）は拾われない。

```
544.0 / 680.0 - 1.0  =  -0.19999999999999996   ← 線を超えない
```

`544/680` の真の値は 0.8 だが、**その 0.8 は double で表せない。** 近いほう
に丸めると 0.8 のわずかに上に来るので、1 を引いた結果も線のわずかに上に
残る。**割り算の丸め 1 回で、等号が消えていた。**

**割り算をやめれば消える。** `544.0 <= 680.0 * 0.8` は `680.0 * 0.8` が
**厳密に 544.0** になるので、素直に通る。

## 大きさ

人工の値動き（1円刻み・600日 × 400銘柄・238,000 組）で、厳密な有理数と
突き合わせた結果。

| | 食い違い |
|---|---|
| `a/b - 1.0 <= -drop` | **2**（どちらも線上。取りこぼし） |
| `a <= b * (1.0 - drop)` | **0** |

**その 2 件は、急落 99 件のうちの 2 件**である——**2%。** 丸めの粒ではない。

## 残っている限界

`1.0 - drop` 自体は double なので、線は**真の値のわずか上**に来る
（`1.0 - 0.2` は 4/5 より約 4e-17 大きい）。つまり **−19.999999999999996%
の下げも拾う。** どの呼び値よりも細かい幅なので、**そちら側に倒す**
——事前登録が「以下」と書いているほうに合わせる。

**イプシロンは足していない。** 幅を推測で置くより、**式を変えるほうが根拠を
書ける**（`CLAUDE.md`「許容幅を推測で置かない」）。
"""

from __future__ import annotations

import numpy as np


def fell_at_least(reference: np.ndarray, later: np.ndarray, drop: float) -> np.ndarray:
    """``later`` が ``reference`` から ``drop`` 以上下げたか。**線を含む。**

    **割り算を挟まない。** `later / reference - 1.0 <= -drop` と書くと、
    割り算の丸めで**ちょうど線上の下げが落ちる**（モジュールの説明を見ること）。

    Args:
        reference: 基準の値（正でないものは判定しない）。
        later: 後の値（同上）。**``reference`` と同じ長さ。**
        drop: 下げ幅。``0.20`` なら 20% 以上の下げ。

    Returns:
        bool の配列。**どちらかが 0 以下の要素は ``False``。**

    Raises:
        ValueError: 長さが揃っていない、または ``drop`` が 0〜1 の外。
    """
    if len(reference) != len(later):
        raise ValueError(f"reference {len(reference)} と later {len(later)} の長さが違う。")
    if not 0.0 < drop < 1.0:
        raise ValueError(f"drop must be between 0 and 1; got {drop}.")

    usable = (reference > 0) & (later > 0)
    fell = np.zeros(len(reference), dtype=bool)
    fell[usable] = later[usable] <= reference[usable] * (1.0 - drop)
    return fell
