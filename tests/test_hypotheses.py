"""`docs/HYPOTHESES.md` を読み、`reports/<ID>/` を組み立てる。

**散文を解釈しない。** 表だけを読み、見出しで区切られた節は切り出すだけで
中身に触らない。日本語の散文を解析すると、書き方が少し変わっただけで節が
黙って抜ける——**中身の無い報告書は、無い報告書より悪い。**

**空欄を埋めない。** 出典が「未記載」なら報告書にもそう出る。
"""

from __future__ import annotations

from pathlib import Path

from stock_ai.hypotheses import (
    MISSING,
    VERDICTS,
    Hypothesis,
    _plain,
    read_registry,
    read_table,
    report_for,
    section_for,
    write_reports,
)

REGISTRY = Path(__file__).resolve().parent.parent / "docs" / "HYPOTHESES.md"

SAMPLE = """# 見出し

## 一覧

### 登録

| ID | # | 説 | 種類 | 構成 | 市場 | 出典 | 事前登録 |
|---|---|---|---|---|---|---|---|
| `AAA_JP` | 1 | ためし | technical | single | jp | 未記載 | [PREREG_AAA_JP.md](PREREG_AAA_JP.md) |
| `BBB_JP` | 2 | もうひとつ | anomaly | single | jp | Someone (1999) | 未作成 |

### 判定

| ID | 判定 | 封印日 | ひとことで言うと |
|---|---|---|---|
| `AAA_JP` | **不合格** | 2026-01-01 | 落ちた |
| `BBB_JP` | 未判定（未封印） | — | まだ |

## 1. ためし — 不合格

本文がここにある。

### 見つかった実装の欠陥

これも入るべきである。

## 2. もうひとつ — 未判定

こちらの本文。
"""


class TestReadingTablesNotProse:
    def test_the_table_is_found_by_its_heading(self) -> None:
        """**何番目の表か、で数えない。** 上に1つ増えただけで別の表を読む。"""
        rows = read_table(SAMPLE, "### 判定")

        assert [row["ID"] for row in rows] == ["`AAA_JP`", "`BBB_JP`"]

    def test_a_missing_heading_gives_nothing_rather_than_the_wrong_table(self) -> None:
        assert read_table(SAMPLE, "### 存在しない") == []

    def test_the_separator_row_is_not_data(self) -> None:
        assert len(read_table(SAMPLE, "### 登録")) == 2

    def test_decorations_are_stripped_but_the_text_is_kept(self) -> None:
        found = read_registry_from(SAMPLE)

        assert found[0].identifier == "AAA_JP"
        assert found[0].verdict == "不合格"
        assert found[0].prereg == "PREREG_AAA_JP.md"


def read_registry_from(text: str, tmp: Path | None = None) -> list[Hypothesis]:
    import tempfile

    folder = tmp or Path(tempfile.mkdtemp())
    path = folder / "HYPOTHESES.md"
    path.write_text(text, encoding="utf-8")
    return read_registry(path)


class TestJoiningTheTwoTables:
    def test_registration_and_verdict_are_matched_by_id(self) -> None:
        found = read_registry_from(SAMPLE)

        assert found[0].sealed_on == "2026-01-01"
        assert found[1].sealed_on == "—"

    def test_a_hypothesis_with_no_verdict_row_is_kept(self) -> None:
        """**落とすと、表がずれていることに気付けない。**"""
        text = SAMPLE.replace("| `BBB_JP` | 未判定（未封印） | — | まだ |\n", "")
        found = read_registry_from(text)

        assert [h.identifier for h in found] == ["AAA_JP", "BBB_JP"]
        assert found[1].verdict == ""


class TestCountingWhatConsumedAJudgement:
    def test_only_the_four_words_count(self) -> None:
        found = read_registry_from(SAMPLE)

        assert found[0].judged
        assert not found[1].judged

    def test_a_pending_state_is_not_a_judgement(self) -> None:
        assert not Hypothesis("X", "1", "", "", "", "", "", "", "未判定（未封印）", "", "").judged

    def test_every_allowed_verdict_counts(self) -> None:
        for word in VERDICTS:
            assert Hypothesis("X", "1", "", "", "", "", "", "", word, "", "").judged

    def test_the_real_registry_counts_five(self) -> None:
        """**手で数えた本数と、読んだ本数が合うこと。**

        `docs/HYPOTHESES.md` に「判定を消費したのは 5 本」と書いてある。
        別の切り口で同じ数が出るかを見る。
        """
        judged = [h for h in read_registry(REGISTRY) if h.judged]

        assert len(judged) == 5
        assert {h.number for h in judged} == {"1", "2", "3", "6", "7"}


class TestSlicingTheSectionWithoutReadingIt:
    def test_the_whole_section_comes_through(self) -> None:
        section = section_for(SAMPLE, "1")

        assert "本文がここにある" in section
        assert "見つかった実装の欠陥" in section

    def test_the_next_section_is_not_swept_in(self) -> None:
        assert "こちらの本文" not in section_for(SAMPLE, "1")

    def test_a_number_with_no_section_gives_empty(self) -> None:
        assert section_for(SAMPLE, "9") == ""

    def test_the_real_registry_has_a_section_for_a_judged_hypothesis(self) -> None:
        text = REGISTRY.read_text(encoding="utf-8")

        assert "低ボラティリティ" in section_for(text, "7")


class TestNotFillingInBlanks:
    def test_an_unrecorded_source_stays_unrecorded(self) -> None:
        found = read_registry_from(SAMPLE)
        report = report_for(found[0], "## 1. ためし")

        assert MISSING in report
        assert "何も記録されていない" in report

    def test_a_recorded_source_is_not_flagged(self) -> None:
        found = read_registry_from(SAMPLE)

        assert found[1].source_recorded
        assert "もっともらしい" not in report_for(found[1], "## 2. もうひとつ")

    def test_an_unjudged_hypothesis_says_so(self) -> None:
        found = read_registry_from(SAMPLE)

        assert "まだ判定していない" in report_for(found[1], "## 2.")

    def test_a_missing_section_is_named_rather_than_left_blank(self) -> None:
        """**節が抜けたことを、静かに通さない。**"""
        found = read_registry_from(SAMPLE)

        assert "対応する節が無い" in report_for(found[0], "")


class TestWritingTheFiles:
    def test_one_folder_per_hypothesis(self, tmp_path: Path) -> None:
        source = tmp_path / "HYPOTHESES.md"
        source.write_text(SAMPLE, encoding="utf-8")

        written = write_reports(source, tmp_path / "reports")

        assert [name for name, _ in written] == ["AAA_JP", "BBB_JP"]
        assert (tmp_path / "reports" / "AAA_JP" / "README.md").is_file()

    def test_the_section_reaches_the_file(self, tmp_path: Path) -> None:
        source = tmp_path / "HYPOTHESES.md"
        source.write_text(SAMPLE, encoding="utf-8")

        write_reports(source, tmp_path / "reports")
        body = (tmp_path / "reports" / "AAA_JP" / "README.md").read_text(encoding="utf-8")

        assert "見つかった実装の欠陥" in body
        assert "この文書は生成物である" in body

    def test_running_twice_gives_the_same_file(self, tmp_path: Path) -> None:
        """**生成物なので、回すたびに変わらない。**"""
        source = tmp_path / "HYPOTHESES.md"
        source.write_text(SAMPLE, encoding="utf-8")
        target = tmp_path / "reports" / "AAA_JP" / "README.md"

        write_reports(source, tmp_path / "reports")
        first = target.read_text(encoding="utf-8")
        write_reports(source, tmp_path / "reports")

        assert target.read_text(encoding="utf-8") == first


class TestALinkDoesNotLoseItsDestination:
    """**出典の要は、そこへ行けることである。**

    最初は `[文字](URL)` を文字だけにしていた。**URL が黙って消え**、たどれ
    ない出典を「たどれる」と表示していた（2026-09-16、テストが捕まえた）。
    行き先を捨てたら、残るのは出典の見た目だけである。
    """

    def test_a_url_survives_flattening(self) -> None:
        rows = read_table(
            "### x\n\n| 出典 |\n|---|\n| [名前](https://example.com/a) |\n",
            "### x",
        )

        assert "https://example.com/a" in _plain(rows[0]["出典"])

    def test_a_self_referencing_link_is_not_doubled(self) -> None:
        """`[PREREG_X.md](PREREG_X.md)` を2度書かない。"""
        assert _plain("[PREREG_X.md](PREREG_X.md)") == "PREREG_X.md"

    def test_bold_is_still_stripped(self) -> None:
        assert _plain("**不合格**") == "不合格"


class TestBlankIsNotTheSameAsUntraceable:
    """**「記録が無い」と「たどれない」は別である。**

    「ネット記事等（URL未記録）」は出所の**種類**は記録されているが、読みに
    行けない。**どちらも空欄と同じに扱うと、正直に書いたことが罰される。**
    """

    def _with_source(self, source: str) -> Hypothesis:
        return Hypothesis("X", "1", "", "", "", "", source, "", "不合格", "", "")

    def test_a_blank_source_is_neither_recorded_nor_traceable(self) -> None:
        for blank in ("", MISSING, "—"):
            hypothesis = self._with_source(blank)
            assert not hypothesis.source_recorded
            assert not hypothesis.source_traceable

    def test_a_net_article_without_a_url_is_recorded_but_not_traceable(self) -> None:
        hypothesis = self._with_source("ネット記事等（URL未記録）")

        assert hypothesis.source_recorded
        assert not hypothesis.source_traceable

    def test_a_real_citation_is_both(self) -> None:
        hypothesis = self._with_source("https://example.com/article （2026-09-16 閲覧）")

        assert hypothesis.source_recorded
        assert hypothesis.source_traceable

    def test_the_report_says_not_traceable_rather_than_not_recorded(self) -> None:
        report = report_for(self._with_source("ネット記事等（URL未記録）"), "## 1.")

        assert "たどれない" in report
        assert "何も記録されていない" not in report

    def test_the_report_refuses_to_invent_a_citation(self) -> None:
        """**あとから論文を探して埋めない。** 空欄のほうが正直である。"""
        report = report_for(self._with_source("ネット記事等（URL未記録）"), "## 1.")

        assert "空欄のほうが正直" in report

    def test_every_registered_hypothesis_says_something_about_its_source(self) -> None:
        """**空欄を残さない。** 「ネット記事等（URL未記録）」も答えである。"""
        assert all(h.source_recorded for h in read_registry(REGISTRY))

    def test_the_first_hypothesis_registered_with_a_real_source_kept_it(self) -> None:
        """`ANTIVALUE_JP` が、出典つきで登録した最初の1本である（2026-09-16）。

        **ここが空に戻ったら、出典を書く習慣が消えたということである。**
        """
        found = {h.identifier: h for h in read_registry(REGISTRY)}

        assert found["ANTIVALUE_JP"].source_traceable
        assert "jsda.or.jp" in found["ANTIVALUE_JP"].source
