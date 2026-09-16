"""複合型のルールを、関門として当てる（`stock_ai.backtest.composite`）。

**6つとも、破っても例外が出ない種類のルールである。** 人が読んで当てはめると、
封印の前でも基準が動く。ここで固定するのはその6つと、**関門が実際に落ちること**。

「検査を作ったら、それが落ちる条件を1つ実際に作って、落ちることを見る」
（`CLAUDE.md`）。**落ちない検査は、確かめたつもりを作る。**
"""

from __future__ import annotations

import datetime as dt
import pathlib

import numpy as np
import pandas as pd
import pytest

from stock_ai.backtest.composite import (
    FAIL,
    PASS,
    Component,
    Coverage,
    Design,
    beats_best,
    kinds,
    over_budget,
    single_kind,
    unregistered,
)
from stock_ai.data.schema import ADJ_CLOSE, CLOSE, HIGH, LOW, OPEN, VOLUME
from stock_ai.database.engine import Database
from stock_ai.database.repository import PriceRepository


def _registered_pair() -> str:
    """`.bat` が既定で渡す組を、`.ps1` から読む。

    **押される組を、そのまま試す。** ここに文字列を書き写すと、`.ps1` の
    既定だけ変わったときに**テストは緑のまま**になる。
    """
    import re

    body = (
        pathlib.Path(__file__)
        .resolve()
        .parent.parent.joinpath("scripts", "composite-gate.ps1")
        .read_text(encoding="utf-8")
    )
    found = re.search(r"\[string\]\$Components\s*=\s*'([^']+)'", body)
    assert found, "composite-gate.ps1 の -Components 既定を読めない"
    return found.group(1)


_LOW = Component("LOWVOL_JP", "低ボラ")
_VALUE = Component("VALUE_JP", "バリュー")


def _design(components=(_LOW, _VALUE), tries: int = 1) -> Design:
    return Design(tuple(components), (1.0,) * len(components), tries_in_is=tries)


class TestADesignHasToBeOneBeforeItIsMeasured:
    def test_one_leg_is_not_a_composite(self) -> None:
        with pytest.raises(ValueError, match="構成要素"):
            Design((_LOW,), (1.0,), tries_in_is=1)

    def test_the_weights_have_to_match_the_legs(self) -> None:
        with pytest.raises(ValueError, match="重み"):
            Design((_LOW, _VALUE), (1.0,), tries_in_is=1)

    def test_the_same_hypothesis_cannot_be_counted_twice(self) -> None:
        """**同じ説を2度数えると、重みがこっそり2倍になる。** 例外は出ない。"""
        with pytest.raises(ValueError, match="2度"):
            Design((_LOW, Component("LOWVOL_JP", "短期リバーサル")), (1.0, 1.0), tries_in_is=1)

    def test_not_deciding_how_many_tries_is_refused(self) -> None:
        """**0 は「まだ決めていない」である。** 決めずに回すと後から増やせる。"""
        with pytest.raises(ValueError, match="1以上"):
            Design((_LOW, _VALUE), (1.0, 1.0), tries_in_is=0)

    def test_a_composite_counts_as_one_not_as_its_legs(self) -> None:
        """**1通りにつき1本。** 脚の数字は合格線であって、脚の判定ではない。"""
        assert _design().counts_as == 1


class TestTheLegsHaveToBeRegistered:
    def test_an_unregistered_leg_is_named(self) -> None:
        missing = unregistered(_design(), ["LOWVOL_JP"])

        assert missing == ["VALUE_JP"]

    def test_registered_legs_leave_nothing(self) -> None:
        assert unregistered(_design(), ["LOWVOL_JP", "VALUE_JP"]) == []


class TestTheKindsAreCountedRatherThanAssumed:
    def test_three_technical_factors_are_a_single_kind(self) -> None:
        """**2026-09-05 に束ねた3本は3本とも technical だった。**"""
        design = Design(
            (_LOW, Component("REVERSAL_JP", "短期リバーサル")), (1.0, 1.0), tries_in_is=1
        )
        kind_of = {"LOWVOL_JP": "technical", "REVERSAL_JP": "technical"}

        assert single_kind(design, kind_of)
        assert kinds(design, kind_of) == {"technical": 2}

    def test_crossing_kinds_is_seen(self) -> None:
        kind_of = {"LOWVOL_JP": "technical", "VALUE_JP": "fundamental"}

        assert not single_kind(_design(), kind_of)
        assert kinds(_design(), kind_of) == {"technical": 1, "fundamental": 1}

    def test_an_id_with_no_kind_is_not_dropped(self) -> None:
        """**黙って落とさない。** 落とすと、種類が1つに見える。"""
        assert kinds(_design(), {"LOWVOL_JP": "technical"}) == {"technical": 1, "不明": 1}


class TestTheNumberOfTriesIsFixedBeforeLooking:
    def test_staying_within_the_declared_count_is_fine(self) -> None:
        assert not over_budget(_design(tries=3), tried=3)

    def test_going_past_it_is_refused(self) -> None:
        """**予算20本の補正は、IS の中で何通り試したかを知らない。**"""
        assert over_budget(_design(tries=1), tried=2)


class TestTheCountsAreLookedAtRatherThanTabulated:
    """**表に出ていることと、目に入ることは別である。**"""

    @staticmethod
    def _coverage(**changes) -> Coverage:
        fields = {
            "months": 100,
            "median_symbols": 900,
            "excluded_thin": 0,
            "excluded_no_history": 0,
            "excluded_discontinuity": 0,
            "excluded_no_pbr": 0,
            "months_single": 100,
            "median_symbols_single": 900,
        }
        fields.update(changes)
        return Coverage(**fields)

    def test_a_full_panel_says_nothing(self) -> None:
        """**鳴りっぱなしの警告は読まれなくなる。**"""
        assert self._coverage().warnings() == []

    def test_halving_the_cross_section_warns(self) -> None:
        found = self._coverage(median_symbols=400).warnings()

        assert any("銘柄" in line for line in found)

    def test_losing_months_warns_and_says_why_it_matters(self) -> None:
        found = self._coverage(months=70).warnings()

        assert any("期数" in line for line in found)

    def test_an_empty_panel_says_it_did_not_compare(self) -> None:
        found = self._coverage(months=0, months_single=0).warnings()

        assert any("比べていない" in line for line in found)

    def test_dropped_pbr_rows_are_reported_not_filled(self) -> None:
        found = self._coverage(excluded_no_pbr=3_910).warnings()

        assert any("0 で埋めていない" in line for line in found)


class TestBeatingTheBestLegIsRequired:
    def test_a_composite_below_its_best_leg_fails(self) -> None:
        verdict, reason = beats_best(1.20, {"低ボラ": 1.69, "バリュー": 0.40})

        assert verdict == FAIL
        assert "低ボラ" in reason

    def test_a_tie_is_not_beating_it(self) -> None:
        """**「束ねたら良くなった」を通しにくい側に倒す。**"""
        verdict, _ = beats_best(1.69, {"低ボラ": 1.69})

        assert verdict == FAIL

    def test_a_composite_above_every_leg_passes(self) -> None:
        verdict, reason = beats_best(2.40, {"低ボラ": 1.69, "バリュー": 0.40})

        assert verdict == PASS
        assert "低ボラ" in reason

    def test_the_sign_flip_of_2026_09_05_fails(self) -> None:
        """実測の形。合成 t −0.14、最良の単独 +1.69。**「何倍良いか」が成り立たない。**"""
        verdict, _ = beats_best(-0.14, {"低ボラ": 1.69})

        assert verdict == FAIL

    def test_no_legs_at_all_is_an_error_rather_than_a_pass(self) -> None:
        """**空の辞書を素通しして緑になる**形を作らない。"""
        with pytest.raises(ValueError):
            beats_best(2.0, {})


# --- 組み立てを1本通す ---------------------------------------------------------
#
# **部品だけでは足りない。** `factor_panel` は部品を13個テストしていたのに
# `build_panel` を一度も呼んでおらず、存在しない引数が本番まで出て行った。

_BARS = 700
_INDEX = pd.bdate_range("2014-01-06", periods=_BARS, name="date")


def _frame(seed: int, volatility: float = 0.01) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 1_000.0 * np.exp(np.cumsum(rng.normal(0.0, volatility, _BARS)))
    return pd.DataFrame(
        {
            OPEN: close,
            HIGH: close,
            LOW: close,
            CLOSE: close,
            ADJ_CLOSE: close,
            VOLUME: [500_000.0] * _BARS,
        },
        index=_INDEX,
    )


def _database(count: int = 30) -> tuple[Database, list[str]]:
    database = Database("sqlite:///:memory:")
    database.create_all()
    symbols = [f"{7200 + index:04d}" for index in range(count)]
    with database.session() as session:
        repo = PriceRepository(session)
        repo.upsert_prices("1306", _frame(99), market="JP")
        for index, symbol in enumerate(symbols):
            repo.upsert_prices(symbol, _frame(index, 0.004 + 0.002 * index), market="JP")
    return database, symbols


def _valuation_file(path, symbols: list[str]) -> str:
    rows = []
    for month in pd.Series(_INDEX).dt.to_period("M").unique():
        last = max(day for day in _INDEX if day.to_period("M") == month)
        for index, symbol in enumerate(symbols):
            rows.append(
                {
                    "date": last.date(),
                    "symbol": symbol,
                    "pbr": 0.5 + index * 0.1,
                    "per": 1.0,
                    "bps": 1.0,
                    "market_cap": 1.0,
                }
            )
    target = path / "valuation_monthly.csv.gz"
    pd.DataFrame(rows).to_csv(target, index=False, compression="gzip")
    return str(target)


class TestTheGateRunsEndToEnd:
    """**関門が実際に当たること。** 部品が全部緑でも、繋ぎ忘れは出る。"""

    @staticmethod
    def _run(tmp_path, monkeypatch, components: str, tries: int = 1, seen=None):
        from typer.testing import CliRunner

        from stock_ai import cli

        database, symbols = _database()
        monkeypatch.setattr(cli, "Database", lambda *a, **k: database)
        if seen is not None:
            from stock_ai.backtest import factor_panel

            real = factor_panel.build_panel

            def watched(*args, **kwargs):
                seen.append((kwargs.get("start"), kwargs.get("end")))
                return real(*args, **kwargs)

            monkeypatch.setattr(cli, "build_panel", watched, raising=False)
            monkeypatch.setattr(factor_panel, "build_panel", watched)
        return CliRunner().invoke(
            cli.app,
            [
                "composite-gate",
                "--components",
                components,
                "--valuation",
                _valuation_file(tmp_path, symbols),
                "--is-start",
                "2014-01-01",
                "--is-end",
                "2016-12-31",
                "--window",
                "60",
                "--min-symbols",
                "10",
                "--tries",
                str(tries),
            ],
        )

    def test_a_cross_kind_composite_reaches_the_gate(self, tmp_path, monkeypatch) -> None:
        result = self._run(tmp_path, monkeypatch, _registered_pair())

        assert result.exit_code == 0, result.output
        assert "構成要素は全部登録済み" in result.output
        assert "件数と流動性の内訳" in result.output
        assert "§0 に入れる材料" in result.output
        assert "脚ごとの単独" in result.output
        assert "1 本" in result.output
        # **合成の t を、読む側に逆算させない。**
        assert "IS では:" in result.output
        assert "最良の単独" in result.output

    def test_an_unregistered_leg_stops_it(self, tmp_path, monkeypatch) -> None:
        """**登録が無い脚は、何を主張しているのかが文書に残らない。**"""
        result = self._run(tmp_path, monkeypatch, "LOWVOL_JP:低ボラ,QUALITY_JP:バリュー")

        assert result.exit_code == 1
        assert "QUALITY_JP" in result.output
        assert "§0 に入れる材料" not in result.output

    def test_one_leg_is_refused_before_anything_is_measured(self, tmp_path, monkeypatch) -> None:
        result = self._run(tmp_path, monkeypatch, "LOWVOL_JP:低ボラ")

        assert result.exit_code == 1
        assert "件数と流動性の内訳" not in result.output

    def test_the_window_is_the_one_the_prereg_fixed(self, tmp_path, monkeypatch) -> None:
        """**渡し忘れても例外は出ない。** 月数を数えて初めて分かる。

        実際に出た（2026-09-16）。事前登録 §6 は 2009-01〜2017-12 の 108ヶ月と
        決めているのに、盤面は 2008-09 まで遡って **112ヶ月**を返していた。
        """
        seen: list = []

        result = self._run(tmp_path, monkeypatch, _registered_pair(), seen=seen)

        assert result.exit_code == 0, result.output
        assert seen, "build_panel が呼ばれていない"
        assert all(start == dt.date(2014, 1, 1) for start, _end in seen), seen

    def test_the_leg_on_its_own_is_built_on_the_same_window(self, tmp_path, monkeypatch) -> None:
        """**窓が違うと「脚を足して失ったもの」ではなく「窓の差」を測る。**

        揃えずに出したことがある——合成 112ヶ月、脚だけ 184ヶ月で「月が 39%
        減った」と警告した。**減ったのではなく、最初から別の窓だった。**
        """
        seen: list = []

        self._run(tmp_path, monkeypatch, _registered_pair(), seen=seen)

        assert len(seen) == 2, seen
        # **`None` どうしでも一致してしまう。** 渡していないことを一致と読まない。
        assert seen[0][0] is not None, "そもそも窓を渡していない"
        assert seen[0] == seen[1], "合成と脚だけで窓が違う"

    def test_a_backwards_window_is_refused(self, tmp_path, monkeypatch) -> None:
        from typer.testing import CliRunner

        from stock_ai import cli

        database, symbols = _database()
        monkeypatch.setattr(cli, "Database", lambda *a, **k: database)

        result = CliRunner().invoke(
            cli.app,
            [
                "composite-gate",
                "--components",
                _registered_pair(),
                "--valuation",
                _valuation_file(tmp_path, symbols),
                "--is-start",
                "2016-01-01",
                "--is-end",
                "2015-01-01",
            ],
        )

        assert result.exit_code != 0
        # **「知らない引数」でも 0 以外になる。** 理由のほうを見る。
        assert "before" in result.output

    def test_a_single_kind_bundle_is_flagged_but_not_blocked(self, tmp_path, monkeypatch) -> None:
        """**禁止ではない。** ただし種類をまたぐ複合が手つかずなことは言う。"""
        result = self._run(tmp_path, monkeypatch, "LOWVOL_JP:低ボラ,REVERSAL_JP:短期リバーサル")

        assert result.exit_code == 0, result.output
        assert "種類が1つしか入っていない" in result.output
