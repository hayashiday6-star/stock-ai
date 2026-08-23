"""分割をまたぐ価格の扱い。

このファイルは1つの欠陥のために存在する。戦略も指標もバックテストエンジンも
``close`` と ``open`` ――実際に取引された価格――を読んでいた。分割日にそれは
不連続に飛び、下流には暴落と区別がつかない。エラーは出ない。数字が変わるだけ。

実測（日立 6501, 2001-2026）:

- 分割をまたぐ2ヶ月の買い持ち: -80.14% と報告。実際は -0.72%。
- 25年の sma200: -8.99% と報告。実際は +373.54%。戦略の評価が反転していた。
- 25年の買い持ち: 最大DD -84.71% と報告。実際は -83.01%。報告値の底は
  2025-04-07 で、5倍の尺度にある分割前の高値から測っていた。

25年の買い持ちの**リターンだけ**は偶然正しかった。6501 は2018年の5倍併合と
2024年の0.2倍分割が打ち消し合い、両端では調整の有無が一致するため。既存の
テストがこれを捕まえられなかったのも同じ理由――区別できるのは分割をまたぐ
期間だけで、テストの価格系列に分割は無かった。
"""

from __future__ import annotations

import pandas as pd
import pytest

from stock_ai.data.schema import ADJ_CLOSE, CLOSE, HIGH, LOW, OPEN, VOLUME, split_adjusted


def frame(rows: list[tuple[str, float, float, float, float, float]]) -> pd.DataFrame:
    """(日付, 始値, 高値, 安値, 終値, 調整後終値) から OHLCV を作る。"""
    return pd.DataFrame(
        {
            OPEN: [r[1] for r in rows],
            HIGH: [r[2] for r in rows],
            LOW: [r[3] for r in rows],
            CLOSE: [r[4] for r in rows],
            ADJ_CLOSE: [r[5] for r in rows],
            VOLUME: [100] * len(rows),
        },
        index=pd.DatetimeIndex(pd.to_datetime([r[0] for r in rows]), name="date"),
    )


#: 6501 の 2024-06-27（1→5 分割）前後。実データそのまま。
HITACHI_SPLIT = frame(
    [
        ("2024-06-25", 17000.0, 17615.0, 16940.0, 17525.0, 3505.0),
        ("2024-06-26", 17425.0, 17710.0, 17260.0, 17620.0, 3524.0),
        ("2024-06-27", 3510.0, 3673.0, 3505.0, 3654.0, 3654.0),
        ("2024-06-28", 3647.0, 3673.0, 3586.0, 3601.0, 3601.0),
    ]
)


def test_the_whole_bar_moves_onto_the_adjusted_scale() -> None:
    """高値も安値も始値も、その日の終値と同じ尺度にある。"""
    adjusted = split_adjusted(HITACHI_SPLIT)

    # 分割前: すべて 1/5 に。
    assert adjusted[CLOSE].iloc[1] == pytest.approx(3524.0)
    assert adjusted[OPEN].iloc[1] == pytest.approx(3485.0)
    assert adjusted[HIGH].iloc[1] == pytest.approx(3542.0)
    assert adjusted[LOW].iloc[1] == pytest.approx(3452.0)
    # 分割後: 変わらない。
    assert adjusted[CLOSE].iloc[2] == pytest.approx(3654.0)
    assert adjusted[OPEN].iloc[2] == pytest.approx(3510.0)


def test_the_latest_bar_is_never_moved() -> None:
    """現在値を読む処理を壊さないための性質。

    係数は ``adj_close / close`` で、最新のバーより後に分割は無いので必ず 1.0。
    保有評価額や現在株価の表示は、この変更の影響を受けない。
    """
    adjusted = split_adjusted(HITACHI_SPLIT)

    for column in (OPEN, HIGH, LOW, CLOSE):
        assert adjusted[column].iloc[-1] == HITACHI_SPLIT[column].iloc[-1]


def test_volume_is_left_alone() -> None:
    """出来高は価格ではない。株数の尺度で調整するかは別の判断。"""
    assert (split_adjusted(HITACHI_SPLIT)[VOLUME] == HITACHI_SPLIT[VOLUME]).all()


def test_the_input_frame_is_not_modified() -> None:
    """呼び出し側が生の価格を持ち続けられるように。"""
    before = HITACHI_SPLIT[CLOSE].tolist()
    split_adjusted(HITACHI_SPLIT)
    assert HITACHI_SPLIT[CLOSE].tolist() == before


def test_a_series_without_splits_is_untouched() -> None:
    """調整が要らない銘柄に副作用を出さない。"""
    plain = frame(
        [
            ("2024-01-04", 100.0, 105.0, 99.0, 102.0, 102.0),
            ("2024-01-05", 102.0, 108.0, 101.0, 107.0, 107.0),
        ]
    )
    adjusted = split_adjusted(plain)
    for column in (OPEN, HIGH, LOW, CLOSE):
        assert adjusted[column].tolist() == plain[column].tolist()


def test_a_zero_close_does_not_produce_infinities() -> None:
    """売買が成立しなかった行が混じっても、系列全体を壊さない。"""
    with_gap = frame(
        [
            ("2020-09-30", 3614.0, 3641.0, 3543.0, 3543.0, 708.6),
            ("2020-10-01", 0.0, 0.0, 0.0, 0.0, 0.0),
            ("2020-10-02", 3543.0, 3597.0, 3488.0, 3508.0, 701.6),
        ]
    )
    adjusted = split_adjusted(with_gap)

    assert adjusted[CLOSE].iloc[0] == pytest.approx(708.6)
    assert adjusted[CLOSE].iloc[1] == 0.0  # 触らない。落とすかは呼び出し側の判断
    assert adjusted[CLOSE].notna().all()
    assert (adjusted[CLOSE].abs() != float("inf")).all()


def test_a_frame_without_the_adjusted_column_passes_through() -> None:
    """調整後終値を持たない取得元があっても落ちない。"""
    bare = HITACHI_SPLIT.drop(columns=[ADJ_CLOSE])
    assert split_adjusted(bare)[CLOSE].tolist() == bare[CLOSE].tolist()


# --- 実際に叩かれる経路 -------------------------------------------------------


def test_stored_prices_come_back_adjusted(tmp_path) -> None:
    """調整は取得元ではなく ``get_prices`` で起きる。

    呼び出し元は十数箇所あり、一箇所忘れれば同じ欠陥が戻る。保存されるのは
    実際に取引された価格のままで、読み出しで調整する。
    """
    from stock_ai.database.engine import Database
    from stock_ai.database.repository import PriceRepository

    database = Database(url=f"sqlite:///{tmp_path / 'p.db'}")
    database.create_all()
    with database.session() as session:
        PriceRepository(session).upsert_prices("6501", HITACHI_SPLIT, market="JP")

    with database.session() as session:
        stored = PriceRepository(session).get_prices("6501")

    # 分割前の終値が、分割後と同じ尺度で返る。
    assert stored[CLOSE].iloc[1] == pytest.approx(3524.0)
    assert stored[OPEN].iloc[1] == pytest.approx(3485.0)
    # 最新のバーは動かない。
    assert stored[CLOSE].iloc[-1] == pytest.approx(3601.0)


def test_a_split_is_not_reported_as_a_crash(tmp_path) -> None:
    """これが欠陥の本体。

    修正前、この2ヶ月の買い持ちは -80.14% と報告した。分割日に価格が 1/5 に
    なるのを、エンジンが暴落として読んでいた。実際の損益はほぼ横ばい。
    """
    from stock_ai.backtest.engine import BacktestEngine
    from stock_ai.backtest.strategy import BuyAndHold
    from stock_ai.database.engine import Database
    from stock_ai.database.repository import PriceRepository

    database = Database(url=f"sqlite:///{tmp_path / 'b.db'}")
    database.create_all()
    with database.session() as session:
        PriceRepository(session).upsert_prices("6501", HITACHI_SPLIT, market="JP")
    with database.session() as session:
        prices = PriceRepository(session).get_prices("6501")

    result = BacktestEngine(100_000.0).run(prices, BuyAndHold().generate_signals(prices))

    # 未調整なら約 -79%。調整後は数パーセント。
    assert result.metrics.total_return > -0.2
    assert result.metrics.max_drawdown > -0.2


def test_a_moving_average_does_not_straddle_two_scales() -> None:
    """分割をまたぐ移動平均は、調整しなければ意味を持たない。

    17,620 と 3,654 の平均に意味は無く、そこから出る売買シグナルにも無い。
    """
    from stock_ai.technical.indicators import sma

    window = 2
    raw_avg = sma(HITACHI_SPLIT, window).iloc[2]
    adjusted_avg = sma(split_adjusted(HITACHI_SPLIT), window).iloc[2]

    # 未調整の平均は、その日の終値の倍以上に浮く。
    assert raw_avg > HITACHI_SPLIT[CLOSE].iloc[2] * 2
    # 調整後は前後の終値の間に収まる。
    assert 3524.0 <= adjusted_avg <= 3654.0
