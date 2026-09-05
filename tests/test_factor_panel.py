"""複数因子の盤面（`stock_ai.backtest.factor_panel`）。

**これは判定ではない。** 合成の利得（r）を測る校正で、閾値は
`docs/HYPOTHESES.md` に測る前から書いてある。

固定しているのは、**間違えても例外が出ない**種類の点である。

- 合成が**標準化してから足す**こと。生値のまま足すと、単位の大きい因子が
  重みを独占する。動いてしまうので気付けない
- signal が**大きいほど買う側**にそろっていること。符号を1つ間違えても
  例外は出ない
- 単一因子を**合成と同じ盤面から**取り出すこと。別々に組むと、比が
  「合成の利得」ではなく「universe の差」を含む
- 要る履歴が**頼まれた因子から決まる**こと。全因子の最大でそろえると、
  低ボラだけの盤面が #7 と違う universe になり、再現による検算ができない
"""

from __future__ import annotations

import datetime as dt

import pytest

from stock_ai.backtest.factor_panel import (
    DEFAULT_FACTORS,
    FACTOR_HISTORY,
    MOMENTUM_SKIP,
    MOMENTUM_WINDOW,
    REVERSAL_WINDOW,
    Panel,
    _signals,
)


def _panel(sections, factors=("低ボラ", "モメンタム")) -> Panel:
    months = [dt.date(2024, 1 + index, 28) for index in range(len(sections))]
    return Panel(factors, months, [0.0] * len(sections), sections)


def test_the_composite_standardises_before_adding() -> None:
    """**生値のまま足すと、単位の大きい因子が重みを独占する。**

    因子Aは 0.01 の桁、因子Bは 100 の桁で、完全に逆向き。標準化していれば
    打ち消し合って 0 になる。していなければ B がそのまま残る。
    """
    month = [((0.01 * i, 100.0 * (9 - i)), 0.0) for i in range(10)]

    composite = _panel([month]).composite()[0]

    assert all(value == pytest.approx(0.0, abs=1e-9) for value, _forward in composite)


def test_the_composite_keeps_the_signal_when_the_factors_agree() -> None:
    """逆向きでなければ、合成は消えない。上のテストが空振りでないこと。"""
    month = [((0.01 * i, 100.0 * i), 0.0) for i in range(10)]

    composite = _panel([month]).composite()[0]

    assert composite[0][0] < 0 < composite[-1][0]


def test_the_composite_drops_a_month_where_a_factor_is_flat() -> None:
    """**0 で埋めない。** 埋めると、その月だけ因子が1本減った合成になる。"""
    flat = [((0.02, 1.0), 0.0) for _ in range(10)]

    composite = _panel([flat]).composite()

    assert composite == [[]]


def test_the_composite_refuses_a_weight_per_factor_mismatch() -> None:
    month = [((0.01 * i, float(i)), 0.0) for i in range(10)]

    with pytest.raises(ValueError):
        _panel([month]).composite(weights=[1.0])


def test_weights_shift_the_blend() -> None:
    month = [((0.01 * i, 100.0 * (9 - i)), 0.0) for i in range(10)]

    only_first = _panel([month]).composite(weights=[1.0, 0.0])[0]

    # 因子Aだけなら打ち消されず、昇順のまま残る。
    assert only_first[0][0] < 0 < only_first[-1][0]


def test_a_single_factor_comes_out_of_the_same_panel() -> None:
    """別々に組むと、比が「合成の利得」ではなく「universe の差」を含む。"""
    month = [((0.05, 0.20), 0.01), ((0.02, 0.10), 0.03)]
    panel = _panel([month])

    assert panel.column("低ボラ") == [[(0.05, 0.01), (0.02, 0.03)]]
    assert panel.column("モメンタム") == [[(0.20, 0.01), (0.10, 0.03)]]


def test_asking_for_a_factor_the_panel_does_not_carry_is_an_error() -> None:
    with pytest.raises(ValueError):
        _panel([[((0.01, 0.02), 0.0)]]).column("短期リバーサル")


# --- signal は「大きいほど買う側」にそろえる ----------------------------------
#
# そろえていないと、合成のときに符号を1つ間違えても例外が出ない。


def _closes(count: int, rising: bool) -> list[float]:
    """単調に上がる／下がる終値。**下げても 0 を割らせない。**

    最初の版は ``100 - index`` にしていて、300日目に −200 円になった。
    コードの ``> 0`` ガードが正しく弾き、テストのほうが間違っていた。
    """
    rate = 1.003 if rising else 0.997
    return [100.0 * rate**index for index in range(count)]


def test_low_volatility_is_negated_so_larger_is_better() -> None:
    import numpy as np

    calm = np.full(300, 0.001)
    wild = np.concatenate([np.full(150, 0.05), np.full(150, -0.05)])
    close = np.array(_closes(400, rising=True))

    calm_signal = _signals(("低ボラ",), calm, 250, close, 300)
    wild_signal = _signals(("低ボラ",), wild, 250, close, 300)

    assert calm_signal is not None and wild_signal is not None
    assert calm_signal[0] > wild_signal[0]


def test_momentum_is_positive_for_a_rising_price() -> None:
    import numpy as np

    sample = np.full(300, 0.0)
    rising = np.array(_closes(400, rising=True))
    falling = np.array(_closes(400, rising=False))

    up = _signals(("モメンタム",), sample, 250, rising, 300)
    down = _signals(("モメンタム",), sample, 250, falling, 300)

    assert up is not None and down is not None
    assert up[0] > 0 > down[0]


def test_momentum_skips_the_most_recent_month() -> None:
    """**直近を含めると短期リバーサルと逆向きに重なる。** 合成する意味が薄れる。

    直近1ヶ月だけを動かしても、モメンタムは変わらないこと。
    """
    import numpy as np

    sample = np.full(300, 0.0)
    base = np.array(_closes(400, rising=True))
    spiked = base.copy()
    spiked[400 - MOMENTUM_SKIP :] *= 2.0  # 直近1ヶ月だけ跳ねさせる

    plain = _signals(("モメンタム",), sample, 250, base, 399 - MOMENTUM_SKIP)
    moved = _signals(("モメンタム",), sample, 250, spiked, 399 - MOMENTUM_SKIP)

    assert plain is not None and moved is not None
    assert plain[0] == pytest.approx(moved[0])


def test_short_term_reversal_is_negated_so_a_faller_is_bought() -> None:
    import numpy as np

    sample = np.full(300, 0.0)
    rising = np.array(_closes(400, rising=True))
    falling = np.array(_closes(400, rising=False))

    after_rise = _signals(("短期リバーサル",), sample, 250, rising, 300)
    after_fall = _signals(("短期リバーサル",), sample, 250, falling, 300)

    assert after_rise is not None and after_fall is not None
    assert after_fall[0] > after_rise[0]


def test_a_symbol_without_enough_history_for_a_factor_is_dropped() -> None:
    """**None を返す。** 0 を返すと「情報が無い」が「平均的」に化ける。"""
    import numpy as np

    sample = np.full(300, 0.0)
    close = np.array(_closes(400, rising=True))

    assert _signals(("モメンタム",), sample, 250, close, MOMENTUM_WINDOW - 2) is None
    assert _signals(("短期リバーサル",), sample, 250, close, REVERSAL_WINDOW - 1) is None


def test_the_required_history_comes_from_the_factors_asked_for() -> None:
    """全因子の最大でそろえると、低ボラだけの盤面が #7 と違う universe になる。"""
    assert FACTOR_HISTORY["低ボラ"] == 250
    assert FACTOR_HISTORY["モメンタム"] == MOMENTUM_WINDOW
    assert FACTOR_HISTORY["低ボラ"] < FACTOR_HISTORY["モメンタム"]
    assert set(DEFAULT_FACTORS) == set(FACTOR_HISTORY)
