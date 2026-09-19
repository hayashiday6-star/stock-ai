"""コンソールに出す価値の無い行を落とす（`stock_ai.core.logging`）。

**このプロジェクトの検証は、ユーザーが `.bat` を実行して出力を貼る形で進む。**
出力の長さはそのまま毎回のトークン消費になり、**長い出力は信号を埋める。**

`_QUIET_ON_CONSOLE` は `httpx` のような「いつ何回出ても読む価値が無い」
ライブラリの固定リストである。**こちら側の部品はそこに入れられない**——
1回だけ回すときは、件数も警告も読む価値がある。

要らないのは**ループの中で同じ行が400回出るとき**だけなので、
**呼ぶ側が一時的に黙らせる**（`quiet_on_console`）。

陰性対照が `turn_of_month.build_series` を400回呼び、出力が **112KB** に
なった（2026-09-19）。
"""

from __future__ import annotations

import logging

from stock_ai.core.logging import ConsoleNoiseFilter, quiet_on_console


def _record(name: str, level: int = logging.INFO) -> logging.LogRecord:
    return logging.LogRecord(name, level, __file__, 1, "x", None, None)


class TestTheFixedListStillWorks:
    def test_a_noisy_library_is_dropped(self) -> None:
        assert not ConsoleNoiseFilter().filter(_record("httpx"))

    def test_our_own_module_is_not_dropped_by_default(self) -> None:
        """**1回だけ回すときは、件数も警告も読む価値がある。**"""
        assert ConsoleNoiseFilter().filter(_record("stock_ai.backtest.turn_of_month"))

    def test_a_warning_always_gets_through(self) -> None:
        assert ConsoleNoiseFilter().filter(_record("httpx", logging.WARNING))


class TestTheLoopCanSilenceItsOwnParts:
    """**ループの中だけ黙らせる。** モジュールごと固定リストに入れない。"""

    def test_it_is_dropped_inside_the_block(self) -> None:
        with quiet_on_console("stock_ai.backtest.turn_of_month"):
            assert not ConsoleNoiseFilter().filter(_record("stock_ai.backtest.turn_of_month"))

    def test_it_comes_back_after_the_block(self) -> None:
        """**出しっぱなしにしない。** 次のコマンドが黙ったままになる。"""
        with quiet_on_console("stock_ai.backtest.turn_of_month"):
            pass

        assert ConsoleNoiseFilter().filter(_record("stock_ai.backtest.turn_of_month"))

    def test_it_comes_back_even_when_the_block_raises(self) -> None:
        with contextlib_suppress(), quiet_on_console("stock_ai.backtest.turn_of_month"):
            raise RuntimeError("boom")

        assert ConsoleNoiseFilter().filter(_record("stock_ai.backtest.turn_of_month"))

    def test_a_warning_still_gets_through_inside_the_block(self) -> None:
        """**黙らせるのは判断の材料ではなく、繰り返される定型だけ。**"""
        with quiet_on_console("stock_ai.backtest.turn_of_month"):
            assert ConsoleNoiseFilter().filter(
                _record("stock_ai.backtest.turn_of_month", logging.WARNING)
            )

    def test_another_logger_is_untouched(self) -> None:
        with quiet_on_console("stock_ai.backtest.turn_of_month"):
            assert ConsoleNoiseFilter().filter(_record("stock_ai.backtest.momentum"))

    def test_nested_blocks_do_not_undo_each_other(self) -> None:
        with quiet_on_console("stock_ai.a"):
            with quiet_on_console("stock_ai.a"):
                pass
            assert not ConsoleNoiseFilter().filter(_record("stock_ai.a"))


class TestTheControlActuallyUsesIt:
    """**口を開けただけで配線を忘れる**形を止める（`BulkIngester` で複数回）。"""

    def test_every_control_quiets_the_power_estimate(self) -> None:
        """**400回回すループは、全部が同じ1行記録を出す。**

        `power.estimate_power` は1回ごとに「検出力の見積もり」を出す。暦の
        対照では、それだけで 400行になった（2026-09-19、出力 112KB のうち
        半分）。**片方だけ黙らせて済ませない。**
        """
        import inspect

        from stock_ai import cli

        for command in (cli.rehearsal, cli.rehearsal_events, cli.rehearsal_calendar):
            body = inspect.getsource(command)
            assert "quiet_on_console(" in body, command.__name__
            assert '"stock_ai.backtest.power"' in body, command.__name__

    def test_the_calendar_control_also_quiets_the_builder(self) -> None:
        """暦の対照だけは、`build_series` も1回ごとに1行出す。"""
        import inspect

        from stock_ai import cli

        body = inspect.getsource(cli.rehearsal_calendar)

        assert '"stock_ai.backtest.turn_of_month"' in body

    def test_the_calendar_control_excludes_the_real_window(self) -> None:
        """**汚染を止める配線が在ること。**"""
        import inspect

        from stock_ai import cli

        body = inspect.getsource(cli.rehearsal_calendar)

        assert "exclude=real" in body
        assert "range(end, end + WINDOW_DAYS)" in body

    def test_the_control_reports_the_level_not_only_the_t(self) -> None:
        """**`t` だけ見ていて、分解に2手かかった**（イベント型の +0.49）。"""
        import inspect

        from stock_ai import cli

        body = inspect.getsource(cli.rehearsal_calendar)

        assert "levels.append" in body
        assert "1月替わりあたりの差（年率）" in body

    def test_the_control_prints_the_span_it_used(self) -> None:
        """**価格の全履歴を出していたので、使っていない年まで使ったように見えた。**"""
        import inspect

        from stock_ai import cli

        body = inspect.getsource(cli.rehearsal_calendar)

        assert "shape.months[0]" in body
        assert "len(dates):,} 営業日" not in body


def contextlib_suppress():
    """`RuntimeError` だけ飲む小さな入れ物。"""
    import contextlib

    return contextlib.suppress(RuntimeError)


class TestTheJanuaryCommandQuietsItsParts:
    """#14 も、**貼られる出力に1行記録を混ぜない。**

    こちらは 400回のループではないが、`build_panel` が全銘柄を読み、
    `estimate_power` が推定量の数だけ1行ずつ出す。**貼る側から見れば同じ
    ことである。**
    """

    def test_it_quiets_the_panel_builder(self) -> None:
        import inspect

        from stock_ai import cli

        body = inspect.getsource(cli.january_power)

        assert '"stock_ai.backtest.quantile_series"' in body

    def test_it_quiets_the_power_estimate(self) -> None:
        import inspect

        from stock_ai import cli

        body = inspect.getsource(cli.january_power)

        assert '"stock_ai.backtest.power"' in body

    def test_it_quiets_its_own_module(self) -> None:
        import inspect

        from stock_ai import cli

        body = inspect.getsource(cli.january_power)

        assert '"stock_ai.backtest.january"' in body

    def test_the_conclusion_is_printed_once_at_the_end(self) -> None:
        """**途中の行を結論と読まれない。** 線と関門はどちらか一方でも閉じる。"""
        import inspect

        from stock_ai import cli

        body = inspect.getsource(cli.january_power)

        assert body.count("結論: ") == 3
        assert "結論" not in body[: body.index("held = mean >= JANUARY_FLOOR")]
