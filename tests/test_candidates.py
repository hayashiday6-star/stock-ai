"""検証待ちの説（`docs/CANDIDATES.md`）。

**2箇所に同じ説が載っていると、どちらが本当か分からなくなる。**
`docs/HYPOTHESES.md` が「説の状態が変わったら、事前登録と一覧の両方を直す」
と言っているのと同じ理由である——ここは**3箇所目**になるので、ずれる余地が
1つ増えた。

**登録したら候補の表から消す。** 消し忘れをここで止める。
"""

from __future__ import annotations

import pathlib
import re

import pytest

from stock_ai.hypotheses import read_registry, read_table

_DOCS = pathlib.Path(__file__).resolve().parent.parent / "docs"
_CANDIDATES = _DOCS / "CANDIDATES.md"
_REGISTRY = _DOCS / "HYPOTHESES.md"


def _candidate_rows() -> list[dict[str, str]]:
    return read_table(_CANDIDATES.read_text(encoding="utf-8"), "## 順番")


def _candidate_ids() -> list[str]:
    return [re.sub(r"[`*]", "", row.get("登録名（予定）", "")).strip() for row in _candidate_rows()]


class TestTheCandidateListIsReadable:
    def test_the_document_exists(self) -> None:
        assert _CANDIDATES.exists()

    def test_the_order_table_has_rows(self) -> None:
        assert len(_candidate_rows()) >= 1

    def test_every_row_proposes_a_name(self) -> None:
        """**名前が無いと、登録されたときに突き合わせられない。**"""
        for identifier in _candidate_ids():
            assert identifier, "登録名（予定）が空の行がある"

    def test_no_name_is_proposed_twice(self) -> None:
        found = _candidate_ids()

        assert len(found) == len(set(found)), "同じ登録名が2行にある"


class TestACandidateNeverLivesInTwoPlaces:
    """**登録したら候補の表から消す。**

    残したままだと、`docs/HYPOTHESES.md` が「検証中」、`docs/CANDIDATES.md`
    が「まだ登録していない」と、**同じ説について違うことを言う。**
    """

    def test_no_candidate_is_already_registered(self) -> None:
        registered = {found.identifier for found in read_registry(_REGISTRY)}
        clash = sorted(set(_candidate_ids()) & registered)

        assert not clash, (
            f"{clash} は登録済みなのに候補の表に残っている。**登録したら候補から消すこと。**"
        )


class TestTheProvenanceIsWrittenDown:
    """**出典の無い数字を書かない**の裏返し——**会話にしか無い判断も残さない。**"""

    def test_it_says_where_the_ranking_came_from(self) -> None:
        body = _CANDIDATES.read_text(encoding="utf-8")

        assert "会話でユーザーが決めた" in body
        assert "測って出した順位ではない" in body

    def test_it_marks_which_part_is_the_projects_own(self) -> None:
        """**混ぜない。** どこまでが受け取ったもので、どこからが追記か。"""
        body = _CANDIDATES.read_text(encoding="utf-8")

        assert "こちら側の追記" in body

    def test_it_does_not_transcribe_the_remaining_budget(self) -> None:
        """**書き写せば、古いまま、もっともらしく見え続ける。**

        残り本数は `hypothesis-report` が数える。ここに数字で書かない。
        """
        body = _CANDIDATES.read_text(encoding="utf-8")

        assert "hypothesis-report" in body
        assert not re.search(r"残り\s*\d+\s*本", body)


class TestTheLinesItQuotesAreTheMeasuredOnes:
    """**数字を書き写さない。** 線が動けばここも古くなる。

    文書なので計算はできない。**せめて、いまの値と一致していることを見る。**
    """

    @staticmethod
    def _line(inflation: float) -> str:
        from stock_ai.backtest.multiplicity import HYPOTHESIS_BUDGET, calibrated_t

        return f"{calibrated_t(HYPOTHESIS_BUDGET, inflation=inflation):.2f}"

    def test_the_monthly_line_matches(self) -> None:
        from stock_ai.backtest.multiplicity import MEASURED_INFLATION

        body = _CANDIDATES.read_text(encoding="utf-8")

        assert f"**{self._line(MEASURED_INFLATION)}**" in body

    def test_the_event_line_matches(self) -> None:
        from stock_ai.backtest.multiplicity import MEASURED_INFLATION_EVENT

        body = _CANDIDATES.read_text(encoding="utf-8")

        assert f"**{self._line(MEASURED_INFLATION_EVENT)}**" in body

    def test_it_would_notice_a_line_that_moved(self) -> None:
        """**この検査が落ちる条件を、実際に1つ作る。**"""
        body = _CANDIDATES.read_text(encoding="utf-8")

        assert f"**{self._line(2.0)}**" not in body


@pytest.mark.parametrize("heading", ["## 順番", "## 最低限必要な判定基準"])
def test_the_headings_the_tests_depend_on_are_there(heading: str) -> None:
    """**見出しで場所を決めている。** 変えるならテストも一緒に直す。"""
    assert heading in _CANDIDATES.read_text(encoding="utf-8")
