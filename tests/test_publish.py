"""note に載せる記事（`stock_ai.publish`）。

**書く前に止める検査が、止めるべきときに止めること**を、両向きに置いて見る。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from stock_ai.hypotheses import read_registry
from stock_ai.publish import (
    FORBIDDEN_TERMS,
    INTRO,
    build_all,
    check_article,
    draw_bars,
    find_font,
    jargon_in,
    read_public,
    render_note,
    table_lines,
)

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"


def _article(
    identifier: str = "PEAD_JP",
    limits: str = "上場廃止した会社は含めていません。手数料は差し引いています。",
    extra: str = "",
    figure: str = "",
) -> str:
    return f"""## {identifier}

### タイトル

決算の後の値動きは続くのか

### 説の中身

決算で上がった株は、その後も上がり続ける、という説です。{extra}

### 結論

続きませんでした。

### なぜそう言えるか

練習期間と本番期間で、向きが逆になりました。
{figure}
### この検証の限界

{limits}
"""


_FIGURE = """
### 図: 上位と下位の差（%）

| 項目 | 値 |
|---|---|
| 練習期間 | +1.10 |
| 本番期間 | -0.78 |

出典: PREREG_PEAD_JP.md
"""

_INTRO = f"""## {INTRO}

### タイトル

投資の言い伝えを確かめる

### このシリーズについて

言い伝えを、先に条件を決めてから確かめます。
"""


class TestReading:
    def test_sections_and_figures_come_back_in_order(self) -> None:
        articles, problems = read_public(_INTRO + _article(figure=_FIGURE))

        assert not problems
        article = articles["PEAD_JP"]
        assert article.title == "決算の後の値動きは続くのか"
        names = [name for name, _body in article.parts]
        assert names == ["説の中身", "結論", "なぜそう言えるか", "図", "この検証の限界"]
        (figure,) = article.figures
        assert figure.rows == (("練習期間", 1.10), ("本番期間", -0.78))
        assert figure.source == "PREREG_PEAD_JP.md"

    def test_a_figure_without_numbers_is_reported(self) -> None:
        empty = "\n### 図: 空の図\n\n出典: PREREG_PEAD_JP.md\n"

        _articles, problems = read_public(_article(figure=empty))

        assert any("数字の行が無い" in line for line in problems)


class TestTheChecksStopWhatTheyShould:
    """**両向きに置く。** 通るものは通り、止めるものは止まる。"""

    def _problems(self, text: str) -> list[str]:
        articles, problems = read_public(text)
        return problems + check_article(articles["PEAD_JP"], DOCS)

    def test_a_clean_article_passes(self) -> None:
        assert self._problems(_article(figure=_FIGURE)) == []

    @pytest.mark.parametrize("term", ["IS", "OOS", "t値", "分位", "§0", "有意"])
    def test_an_internal_word_is_stopped(self, term: str) -> None:
        problems = self._problems(_article(extra=f"{term} で測りました。"))

        assert any(f"「{term}」" in line for line in problems), problems

    def test_an_english_word_that_merely_contains_a_term_is_not_stopped(self) -> None:
        """``ISO`` を ``IS`` と数えない。"""
        assert jargon_in("ISO 8601 の日付") == []
        assert jargon_in("IS の期間") == ["IS"]

    def test_every_forbidden_term_is_caught_somewhere(self) -> None:
        """**落ちようのない語を置かない。** 全部の語が、それぞれ捕まること。"""
        for term in FORBIDDEN_TERMS:
            assert term in jargon_in(f"ここに {term} がある"), term

    def test_limits_must_say_how_delisting_and_fees_were_handled(self) -> None:
        problems = self._problems(_article(limits="特に書くことはありません。"))

        assert any("上場廃止" in line for line in problems)
        assert any("手数料" in line for line in problems)

    def test_a_missing_section_is_named(self) -> None:
        text = _article().replace("### 結論\n\n続きませんでした。\n", "")

        assert any("見出しが足りない（結論）" in line for line in self._problems(text))

    def test_a_figure_without_a_source_is_stopped(self) -> None:
        figure = _FIGURE.replace("出典: PREREG_PEAD_JP.md\n", "")

        assert any("出典の行が無い" in line for line in self._problems(_article(figure=figure)))

    def test_a_source_that_does_not_exist_is_stopped(self) -> None:
        figure = _FIGURE.replace("PREREG_PEAD_JP.md", "PREREG_NOWHERE_JP.md")

        assert any("が無い" in line for line in self._problems(_article(figure=figure)))

    def test_a_table_in_the_body_is_stopped(self) -> None:
        """**note は表を表示できない。** 数字は図にする。"""
        problems = self._problems(_article(extra="\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"))

        assert any("表がある" in line for line in problems)

    def test_the_intro_is_required_and_ids_must_be_registered(self) -> None:
        registry = read_registry(DOCS / "HYPOTHESES.md")
        articles, _problems = read_public(_article(identifier="NOT_A_THING_JP"))

        problems = build_all(articles, registry, DOCS)

        assert any(INTRO in line for line in problems)
        assert any("登録に無い ID" in line for line in problems)


class TestTheNoteText:
    def test_there_is_no_table_and_the_figure_has_a_marker(self) -> None:
        registry = {item.identifier: item for item in read_registry(DOCS / "HYPOTHESES.md")}
        articles, _problems = read_public(_article(figure=_FIGURE))

        text = render_note(articles["PEAD_JP"], registry["PEAD_JP"])

        assert table_lines(text) == []
        assert "［図1をここに挿入: PEAD_JP-1.png］" in text
        assert "reports/PEAD_JP/README.md" in text

    def test_an_untraceable_source_is_said_plainly(self) -> None:
        """**あとから論文を探して埋めない。** 空欄のほうが正直である。"""
        registry = {item.identifier: item for item in read_registry(DOCS / "HYPOTHESES.md")}
        articles, _problems = read_public(_article())

        text = render_note(articles["PEAD_JP"], registry["PEAD_JP"])

        assert "どこでこの説を見たかは記録していません" in text


def _font() -> Path:
    try:
        return find_font()
    except FileNotFoundError:
        pytest.fail("日本語のフォントが無い環境では、図の検査ができない。")


class TestTheFigure:
    def test_a_png_of_the_right_size_is_written(self, tmp_path) -> None:
        from PIL import Image

        articles, _problems = read_public(_article(figure=_FIGURE))
        target = tmp_path / "f.png"

        draw_bars(articles["PEAD_JP"].figures[0], target, _font())

        with Image.open(target) as image:
            assert image.format == "PNG"
            assert image.size == (1200, 675)

    def test_a_negative_bar_hangs_below_the_zero_line(self, tmp_path) -> None:
        """**負の値を上向きに描かない。** 0 の線より下に、負の色が在ること。"""
        from PIL import Image

        from stock_ai.publish import _NEGATIVE, _POSITIVE, Figure

        figure = Figure(title="t", rows=(("上", 1.0), ("下", -1.0)), source="x")
        target = tmp_path / "f.png"

        draw_bars(figure, target, _font())

        with Image.open(target) as image:
            pixels = image.convert("RGB")
            # 棒の中心（左右それぞれ）を、上から下までなめる。
            left = [pixels.getpixel((80 + 530 // 2, y)) for y in range(675)]
            right = [pixels.getpixel((80 + 530 + 530 // 2, y)) for y in range(675)]
        first_positive = left.index(_POSITIVE)
        first_negative = right.index(_NEGATIVE)
        assert first_negative > first_positive

    def test_no_text_overlaps_or_runs_off_the_picture(self, tmp_path) -> None:
        """**刷った図を見て、題が右で切れ、負の値の札が下の札に重なっていた**（2026-09-26）。"""
        from stock_ai.publish import FIGURE_SIZE, Figure

        figure = Figure(
            title=(
                "練習期間 — 会社予想との差で5つに分けた、その後の値動き（%、左ほど予想を下回った）"
            ),
            rows=(("1", -0.98), ("2", 0.55), ("3", 1.22), ("4", 1.29), ("5", 1.64)),
            source="HYPOTHESES.md（#3）、PREREG_SUE_JP.md",
        )

        boxes = draw_bars(figure, tmp_path / "f.png", _font())

        width, height = FIGURE_SIZE
        for box in boxes:
            assert box[0] >= 0 and box[2] <= width and box[1] >= 0 and box[3] <= height, box
        for index, first in enumerate(boxes):
            for second in boxes[index + 1 :]:
                apart = (
                    first[2] <= second[0]
                    or second[2] <= first[0]
                    or first[3] <= second[1]
                    or second[3] <= first[1]
                )
                assert apart, (first, second)

    def test_a_line_does_not_start_with_punctuation(self) -> None:
        from PIL import Image, ImageDraw, ImageFont

        from stock_ai.publish import _wrap

        draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
        face = ImageFont.truetype(str(_font()), 36)
        text = "その後の値動き（%、左ほど予想を下回った）" * 3

        for width in range(200, 900, 37):
            lines = _wrap(draw, text, face, width)
            assert "".join(lines) == text
            assert all(line[0] not in "、。）%" for line in lines[1:]), (width, lines)

    def test_no_font_means_no_figure(self, monkeypatch) -> None:
        """**字の無い図を黙って出さない。**"""
        monkeypatch.delenv("STOCK_AI_FONT", raising=False)

        with pytest.raises(FileNotFoundError, match="日本語のフォント"):
            find_font(candidates=("/nowhere/font.ttc",))


class TestTheCommandRunsEndToEnd:
    def _run(self, tmp_path, text: str):
        from typer.testing import CliRunner

        from stock_ai import cli

        public = tmp_path / "PUBLIC.md"
        public.write_text(text, encoding="utf-8")
        # 出典の文書を探すのは PUBLIC.md の隣なので、本物の docs を写す。
        (tmp_path / "PREREG_PEAD_JP.md").write_text("x", encoding="utf-8")
        into = tmp_path / "note"
        result = CliRunner().invoke(
            cli.app,
            [
                "note-articles",
                "--public",
                str(public),
                "--registry",
                str(DOCS / "HYPOTHESES.md"),
                "--into",
                str(into),
            ],
        )
        return result, into

    def test_articles_and_figures_are_written(self, tmp_path) -> None:
        _font()
        result, into = self._run(tmp_path, _INTRO + _article(figure=_FIGURE))

        assert result.exit_code == 0, result.output
        names = sorted(path.name for path in into.iterdir())
        assert names == ["00-はじめに.md", "01-PEAD_JP.md", "PEAD_JP-1.png"]
        intro = (into / "00-はじめに.md").read_text(encoding="utf-8")
        assert "- 決算の後の値動きは続くのか" in intro

    def test_one_problem_stops_every_article(self, tmp_path) -> None:
        """**1つでも当たれば、1本も書かない。**"""
        result, into = self._run(tmp_path, _INTRO + _article(extra="OOS で測りました。"))

        assert result.exit_code == 1
        assert "1本も書かなかった" in result.output
        assert not into.exists()


def test_the_output_is_not_tracked() -> None:
    """**手元のフォントで中身が変わる生成物である。** 追跡すると早送りを止める。"""
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert "reports/note/" in lines


def test_the_repository_link_points_at_this_repository() -> None:
    from stock_ai.publish import REPOSITORY_URL

    assert re.fullmatch(r"https://github\.com/[\w-]+/stock-ai", REPOSITORY_URL)
