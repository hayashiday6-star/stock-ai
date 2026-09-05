"""推定量の校正（`stock_ai.backtest.cross_section`）。

**これは判定ではない。** #6 と #7 は判定済みで閉じている。ここで測るのは
推定量どうしの比であって、説の合否ではない。

固定しているのは、**間違えても例外が出ない**種類の点である。

- 分位スプレッドが 1σチルトの約2.8倍になること。**これが SD 比を使っては
  いけない理由そのもの**で、成り立たなければ理屈の前提が崩れている
- 断面がばらつかない月を 0 で埋めないこと。埋めると「効果が無かった月」として
  平均に混ざる
- 系列の月が揃っていること。揃っていない t を比べると、推定量の差ではなく
  期間の差を測る
"""

from __future__ import annotations

import random

import pytest

from stock_ai.backtest.cross_section import (
    build_estimators,
    long_only_return,
    t_ratio,
    tilt_return,
    zscores,
)


def _section(seed: int, count: int = 200, slope: float = -2.0, noise: float = 0.05):
    """低ボラほどリターンが高い断面を作る。"""
    rng = random.Random(seed)
    rows = []
    for _ in range(count):
        vol = rng.uniform(0.005, 0.05)
        rows.append((vol, 0.01 + slope * vol + rng.gauss(0, noise)))
    return rows


def test_the_quintile_spread_is_about_2_8_times_the_one_sigma_tilt() -> None:
    """**SD 比を使ってはいけない理由が、ここに出ている。**

    推定量を変えると SD も効果も一緒に縮む。SD 比だけ掛けたところに文献の
    分位スプレッドの効果量を当てると、この 2.8倍が無料で出たように見える。
    """
    sections = [_section(seed) for seed in range(120)]

    estimators = build_estimators(sections)

    spread = sum(estimators.quantile_spread) / estimators.months
    tilt = sum(estimators.tilt) / estimators.months
    assert spread / tilt == pytest.approx(2.8, abs=0.3)


def test_a_flat_cross_section_is_dropped_not_zeroed() -> None:
    """**0 で埋めない。** 埋めると「効果が無かった月」として平均に混ざる。"""
    flat = [(0.02, 0.01) for _ in range(200)]

    estimators = build_estimators([_section(0), flat, _section(1)])

    assert estimators.months == 2
    assert estimators.skipped_flat == 1


def test_every_series_has_the_same_number_of_months() -> None:
    """揃っていない系列の t を比べると、期間の差を測ることになる。"""
    estimators = build_estimators([_section(seed) for seed in range(30)])

    assert (
        len(estimators.quantile_spread)
        == len(estimators.tilt)
        == len(estimators.long_only_tilt)
        == len(estimators.quantile_long_only)
        == estimators.months
    )


def test_a_month_too_thin_for_quantiles_is_dropped() -> None:
    estimators = build_estimators([[(0.01, 0.02), (0.02, 0.01)]], quantiles=5)

    assert estimators.months == 0
    assert estimators.skipped_flat == 1


def test_zscores_refuses_a_cross_section_with_no_dispersion() -> None:
    """全銘柄が同じ値なら、その月に情報は無い。0 を返さない。"""
    assert zscores([0.02] * 50) is None
    assert zscores([0.01]) is None
    assert zscores([]) is None


def test_zscores_centres_and_scales() -> None:
    result = zscores([1.0, 2.0, 3.0])

    assert result is not None
    assert sum(result) == pytest.approx(0.0)
    assert result[0] < 0 < result[-1]


def test_the_tilt_is_market_neutral_by_construction() -> None:
    """``Σz = 0`` なので建玉の合計はゼロ。**β を引く必要がない。**

    全銘柄が同じリターンなら、傾きは 0 になる。市場が動いた分は乗らない。
    """
    signals = zscores([1.0, 2.0, 3.0, 4.0, 5.0])
    assert signals is not None

    assert tilt_return(signals, [0.05] * 5) == pytest.approx(0.0, abs=1e-12)


def test_the_tilt_reads_positive_when_the_signal_pays() -> None:
    signals = zscores([1.0, 2.0, 3.0, 4.0, 5.0])
    assert signals is not None

    assert tilt_return(signals, [0.01, 0.02, 0.03, 0.04, 0.05]) > 0


def test_the_long_only_side_carries_the_market_and_says_so() -> None:
    """**β を引いていない。** 全銘柄同じリターンなら、そのリターンが出る。

    チルトが 0 を返すのと対になっている。混ぜて比べないための性質である。
    """
    signals = zscores([1.0, 2.0, 3.0, 4.0, 5.0])
    assert signals is not None

    assert long_only_return(signals, [0.05] * 5) == pytest.approx(0.05)


def test_the_long_only_side_weights_by_the_tilt_not_equally() -> None:
    signals = zscores([1.0, 2.0, 3.0, 4.0, 5.0])
    assert signals is not None

    # 上側は添字3と4。傾きの大きい4のほうに重みが寄る。
    weighted = long_only_return(signals, [0.0, 0.0, 0.0, 0.10, 0.20])
    equal = (0.10 + 0.20) / 2

    assert weighted is not None
    assert weighted > equal


def test_tilt_refuses_mismatched_lengths() -> None:
    with pytest.raises(ValueError):
        tilt_return([1.0, 2.0], [0.01])
    with pytest.raises(ValueError):
        long_only_return([1.0, 2.0], [0.01])


def test_the_ratio_is_withheld_when_the_signs_differ() -> None:
    """片方が負なら「何倍良い」は意味を持たない。"""
    assert t_ratio(2.0, 1.0) == pytest.approx(2.0)
    assert t_ratio(-2.0, -1.0) == pytest.approx(2.0)
    assert t_ratio(2.0, -1.0) is None
    assert t_ratio(-2.0, 1.0) is None
    assert t_ratio(1.0, 0.0) is None


# --- β を引いた側で比べる ------------------------------------------------------
#
# 生のロングオンリーどうしを比べると、**両方の標準誤差が市場リスクに支配される**
# ので推定量の差が埋もれる。実測（2026-09-05）では生の比が 0.99 で「改善なし」に
# 見えたが、#7 の α は β×ベンチを引いてその市場分を除いている（SD 3.14%→1.84%）。


def test_alpha_removes_the_market_and_that_changes_what_is_measured() -> None:
    """市場が丸ごと乗っている系列は、β を引くとばらつきが落ちる。"""
    from stock_ai.backtest.cross_section import EstimatorSeries

    bench = [0.05, -0.04, 0.03, -0.02, 0.06, -0.05]
    # 市場に完全連動 + 小さな一定の上乗せ。
    values = [0.8 * b + 0.001 for b in bench]
    series = EstimatorSeries(len(bench), [], [], [], [], benchmark=bench)

    alpha = series.alpha(values, 0.8)

    assert all(a == pytest.approx(0.001) for a in alpha)


def test_alpha_refuses_a_series_of_a_different_length() -> None:
    """**ずれた月どうしを引き算しない。** 例外が出ないと気付けない。"""
    from stock_ai.backtest.cross_section import EstimatorSeries

    series = EstimatorSeries(2, [], [], [], [], benchmark=[0.01, 0.02])

    with pytest.raises(ValueError):
        series.alpha([0.01, 0.02, 0.03], 0.5)


def test_alpha_refuses_when_no_benchmark_was_carried() -> None:
    from stock_ai.backtest.cross_section import EstimatorSeries

    with pytest.raises(ValueError):
        EstimatorSeries(1, [], [], [], []).alpha([0.01], 0.5)


def test_the_benchmark_is_carried_through_the_dropped_months() -> None:
    """月を落とす推定量に、外から並べたベンチマークを当てるとずれる。"""
    flat = [(0.02, 0.01) for _ in range(200)]
    sections = [_section(0), flat, _section(1)]
    bench = [0.01, 0.02, 0.03]

    estimators = build_estimators(sections, bench)

    # 落ちたのは真ん中の月。持ち帰るベンチマークもその月を抜いている。
    assert estimators.benchmark == [0.01, 0.03]
    assert len(estimators.benchmark) == estimators.months


def test_build_estimators_refuses_a_benchmark_of_the_wrong_length() -> None:
    with pytest.raises(ValueError):
        build_estimators([_section(0), _section(1)], [0.01])


def test_beta_is_a_ratio_of_covariance_so_it_carries_no_mean() -> None:
    """**平均を足しても β は動かない。** 推定期間で計算してよい理由である。"""
    from stock_ai.backtest.cross_section import beta_to_benchmark

    bench = [0.05, -0.04, 0.03, -0.02, 0.06]
    values = [0.7 * b for b in bench]

    plain = beta_to_benchmark(values, bench)
    shifted = beta_to_benchmark([v + 0.02 for v in values], bench)

    assert plain == pytest.approx(0.7)
    assert shifted == pytest.approx(plain)


def test_beta_refuses_a_benchmark_that_does_not_move() -> None:
    from stock_ai.backtest.cross_section import beta_to_benchmark

    with pytest.raises(ValueError):
        beta_to_benchmark([0.01, 0.02], [0.03, 0.03])
    with pytest.raises(ValueError):
        beta_to_benchmark([0.01], [0.03])
