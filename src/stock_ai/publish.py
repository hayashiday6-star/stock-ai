"""note に載せる記事を、`docs/PUBLIC.md` から作る。

## 正本は `docs/PUBLIC.md` の1つだけ

`reports/note/` は**生成物**である（git に載せない。`.gitignore` に理由）。
読む人向けの文章は `docs/PUBLIC.md` にしか書かない。**直すならそちらを直す。**

## note の制約に合わせる

- **表が使えない。** 数字は図（PNG）で見せ、本文は段落と箇条書きにする
- **SVG が使えない。** 図は Pillow で PNG に描く
- 貼り付けたときに Markdown がどこまで効くかは確かめていない。見出し・太字・
  箇条書きだけを使う

## 専門用語は、注意ではなく検査で止める

`FORBIDDEN_TERMS` に当たる語が1つでもあれば、**1本も書かずに止まる。**
内部の言葉（練習期間を IS と呼ぶ、など）は、2ヶ月で作った内輪の語で、読む人には
意味が無い（2026-09-26、LLM Council の指摘）。

## 数字は写した先を書く

図の元の表には、必ず出典の行を置く（事前登録か `HYPOTHESES.md`）。**出典の無い
図は書かない。** 本文の数字も、事前登録から写したものだけを置く。
"""

from __future__ import annotations

import dataclasses
import os
import re
from collections.abc import Sequence
from pathlib import Path

from stock_ai.hypotheses import Hypothesis

#: 記事の見出し。**この順で、この4つが全部要る。** 先頭に「タイトル」も要る。
SECTIONS = ("説の中身", "結論", "なぜそう言えるか", "この検証の限界")

#: タイトルの節の名前。
TITLE = "タイトル"

#: 前書き（記事一覧つき）の節の名前。説の ID ではない。
INTRO = "はじめに"

#: 図の節の見出しの頭。``### 図: 題（単位）``。
FIGURE_PREFIX = "図:"

#: 図の出典の行の頭。
SOURCE_PREFIX = "出典:"

#: 技術的な記録の置き場所（公開リポジトリ）。
REPOSITORY_URL = "https://github.com/hayashiday6-star/stock-ai"

#: 読む人向けの文章に置かない語。**内部の言葉である。**
#:
#: 英字の語は、前後が英字でないときだけ当てる（``ISO`` を ``IS`` と数えない）。
FORBIDDEN_TERMS = (
    "IS",
    "OOS",
    "SD",
    "t値",
    "t 値",
    "分位",
    "§0",
    "膨張",
    "封印",
    "合格線",
    "ベンチマーク",
    "ユニバース",
    "universe",
    "α",
    "β",
    "有意",
    "事前登録",
)

#: 「この検証の限界」に必ず書くこと。**上場廃止の扱いと、手数料の扱い。**
REQUIRED_IN_LIMITS = ("上場廃止", "手数料")

_HEADING2 = re.compile(r"^## (.+?)\s*$", re.M)
_HEADING3 = re.compile(r"^### (.+?)\s*$", re.M)
_ROW = re.compile(r"^\|(.+)\|\s*$")
_DOC = re.compile(r"[A-Z][A-Z_]+\.md")


@dataclasses.dataclass(frozen=True)
class Figure:
    """図1枚。**値は記録済みの数字を写したもの。**"""

    title: str
    rows: tuple[tuple[str, float], ...]
    source: str


@dataclasses.dataclass(frozen=True)
class Article:
    """1本の記事。節は `docs/PUBLIC.md` に書いた順に持つ。"""

    identifier: str
    title: str
    parts: tuple[tuple[str, str | Figure], ...]
    """``(見出し, 本文)`` か ``("図", Figure)``。**書いた順のまま。**"""

    @property
    def sections(self) -> dict[str, str]:
        """見出しから本文へ。"""
        return {name: body for name, body in self.parts if isinstance(body, str)}

    @property
    def figures(self) -> list[Figure]:
        """図だけ。"""
        return [body for _name, body in self.parts if isinstance(body, Figure)]


def _figure(title: str, body: str) -> tuple[Figure | None, str | None]:
    """図の節を読む。読めなければ理由を返す。"""
    rows: list[tuple[str, float]] = []
    source = ""
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith(SOURCE_PREFIX):
            source = stripped[len(SOURCE_PREFIX) :].strip()
            continue
        found = _ROW.match(stripped)
        if not found:
            continue
        cells = [cell.strip() for cell in found.group(1).split("|")]
        if len(cells) != 2 or set(cells[1]) <= set("-: "):  # noqa: PLR2004 - 札と値
            continue
        try:
            rows.append((cells[0], float(cells[1].replace(",", "").replace("+", ""))))
        except ValueError:
            continue
    if not rows:
        return None, f"図「{title}」に数字の行が無い"
    return Figure(title=title, rows=tuple(rows), source=source), None


def read_public(text: str) -> tuple[dict[str, Article], list[str]]:
    """`docs/PUBLIC.md` を記事に分ける。**読めなかったものは理由を返す。**

    Returns:
        ``(ID から記事, 読めなかった理由)``。
    """
    articles: dict[str, Article] = {}
    problems: list[str] = []
    heads = list(_HEADING2.finditer(text))
    for index, head in enumerate(heads):
        identifier = head.group(1).strip()
        end = heads[index + 1].start() if index + 1 < len(heads) else len(text)
        block = text[head.end() : end]
        subs = list(_HEADING3.finditer(block))
        parts: list[tuple[str, str | Figure]] = []
        title = ""
        for position, sub in enumerate(subs):
            name = sub.group(1).strip()
            stop = subs[position + 1].start() if position + 1 < len(subs) else len(block)
            body = block[sub.end() : stop].strip()
            if name == TITLE:
                title = body.splitlines()[0].strip() if body else ""
            elif name.startswith(FIGURE_PREFIX):
                figure, reason = _figure(name[len(FIGURE_PREFIX) :].strip(), body)
                if figure is None:
                    problems.append(f"{identifier}: {reason}")
                else:
                    parts.append(("図", figure))
            else:
                parts.append((name, body))
        articles[identifier] = Article(identifier=identifier, title=title, parts=tuple(parts))
    return articles, problems


def jargon_in(text: str) -> list[str]:
    """``text`` に含まれる :data:`FORBIDDEN_TERMS`。"""
    found = []
    for term in FORBIDDEN_TERMS:
        if term.isascii() and term.isalpha():
            pattern = rf"(?<![A-Za-z]){re.escape(term)}(?![A-Za-z])"
        else:
            pattern = re.escape(term)
        if re.search(pattern, text):
            found.append(term)
    return found


def check_article(article: Article, docs: Path) -> list[str]:
    """記事が守るべきことを数える。**1つでも当たれば書かない。**

    Args:
        article: 記事。
        docs: 出典の文書を探す場所（`docs/`）。
    """
    problems: list[str] = []
    name = article.identifier
    if not article.title:
        problems.append(f"{name}: タイトルが無い")
    if name != INTRO:
        sections = article.sections
        missing = [section for section in SECTIONS if section not in sections]
        if missing:
            problems.append(f"{name}: 見出しが足りない（{'・'.join(missing)}）")
        limits = sections.get("この検証の限界", "")
        for word in REQUIRED_IN_LIMITS:
            if word not in limits:
                problems.append(f"{name}: 「この検証の限界」に「{word}」の扱いが無い")
    for figure in article.figures:
        if not figure.source:
            problems.append(f"{name}: 図「{figure.title}」に出典の行が無い")
            continue
        cited = _DOC.findall(figure.source)
        if not cited:
            problems.append(f"{name}: 図「{figure.title}」の出典が文書を指していない")
        for document in cited:
            if not (docs / document).is_file():
                problems.append(f"{name}: 図「{figure.title}」の出典 {document} が無い")
    words = " ".join(
        [article.title]
        + list(article.sections.values())
        + [figure.title for figure in article.figures]
        + [label for figure in article.figures for label, _value in figure.rows]
    )
    for section, body in article.sections.items():
        if table_lines(body):
            problems.append(f"{name}: 「{section}」に表がある（note は表を表示できない）")
    for term in jargon_in(words):
        problems.append(f"{name}: 内部の言葉「{term}」がある")
    return problems


def figure_name(identifier: str, number: int) -> str:
    """図のファイル名。"""
    return f"{identifier}-{number}.png"


def render_note(article: Article, hypothesis: Hypothesis | None) -> str:
    """Note に貼る本文。**表を使わない。図の場所には印を置く。**"""
    lines = [article.title, ""]
    figure_number = 0
    for name, body in article.parts:
        if isinstance(body, Figure):
            figure_number += 1
            picture = figure_name(article.identifier, figure_number)
            lines += [f"［図{figure_number}をここに挿入: {picture}］", ""]
            continue
        lines += [f"## {name}", "", body.strip(), ""]
    if hypothesis is not None:
        lines += ["---", ""]
        if hypothesis.source_traceable:
            lines.append(f"説の出どころ: {hypothesis.source}")
        else:
            lines.append(
                "説の出どころ: どこでこの説を見たかは記録していません。"
                "あとから論文などを探して埋めることはしていません。"
            )
        lines += [
            "",
            "数字の出どころと検証の手順（技術的な記録）: "
            f"{REPOSITORY_URL}/blob/main/reports/{hypothesis.identifier}/README.md",
        ]
    return "\n".join(lines).rstrip() + "\n"


def table_lines(text: str) -> list[str]:
    """表の行（``|`` で始まり ``|`` で終わる行）。**note では表示されない。**"""
    return [line for line in text.splitlines() if _ROW.match(line.strip())]


# --- 図 ------------------------------------------------------------------------

#: 日本語の字形があるフォントの候補。**見つからなければ止まる**（豆腐を出さない）。
FONT_CANDIDATES = (
    r"C:\Windows\Fonts\meiryo.ttc",
    r"C:\Windows\Fonts\YuGothM.ttc",
    r"C:\Windows\Fonts\msgothic.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
)

#: 図の大きさ（ピクセル）。
FIGURE_SIZE = (1200, 675)

_POSITIVE = (37, 99, 175)
_NEGATIVE = (201, 94, 43)
_INK = (40, 40, 40)
_FAINT = (120, 120, 120)
_PAPER = (255, 255, 255)


def find_font(candidates: Sequence[str] = FONT_CANDIDATES) -> Path:
    """日本語のフォント。環境変数 ``STOCK_AI_FONT`` が在ればそれを使う。

    Raises:
        FileNotFoundError: 候補が1つも無い。**字の無い図を黙って出さない。**
    """
    override = os.environ.get("STOCK_AI_FONT")
    for candidate in ([override] if override else []) + list(candidates):
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise FileNotFoundError(
        "日本語のフォントが見つからない。STOCK_AI_FONT にフォントの場所を入れる。"
    )


#: 行頭に来てはいけない文字。
_NO_LINE_START = "、。，．）」』%％"

#: 比べる相手の棒の札。**群と色を分ける**（灰色）。
REFERENCE_LABEL = "市場"


def _wrap(draw, text: str, font, width: float) -> list[str]:
    """``width`` に収まるように、文字単位で折り返す（日本語は語の区切りが無い）。"""
    lines: list[str] = []
    current = ""
    for char in text:
        trial = current + char
        box = draw.textbbox((0, 0), trial, font=font)
        # **行頭に句読点・閉じ括弧を置かない**（はみ出しても前の行に付ける）。
        if current and box[2] - box[0] > width and char not in _NO_LINE_START:
            lines.append(current)
            current = char
        else:
            current = trial
    if current:
        lines.append(current)
    return lines


def draw_bars(figure: Figure, target: Path, font: Path) -> list[tuple[int, int, int, int]]:
    """棒グラフを PNG に描く。**0 の線を必ず引く**（負の棒が在りうる）。

    **書いた文字の箱を返す。** 重なっていないこと・はみ出していないことを、
    テストが確かめる——題が右で切れ、負の値の札が下の札に重なったのを、刷った
    図を見て気付いた（2026-09-26）。
    """
    from PIL import Image, ImageDraw, ImageFont

    width, height = FIGURE_SIZE
    image = Image.new("RGB", FIGURE_SIZE, _PAPER)
    draw = ImageDraw.Draw(image)
    title_font = ImageFont.truetype(str(font), 36)
    label_font = ImageFont.truetype(str(font), 28)
    small_font = ImageFont.truetype(str(font), 22)
    boxes: list[tuple[int, int, int, int]] = []

    def put(position: tuple[float, float], text: str, face, fill) -> tuple[int, int, int, int]:
        box = draw.textbbox(position, text, font=face)
        draw.text(position, text, fill=fill, font=face)
        boxes.append(box)
        return box

    margin = 60
    y = 30.0
    for line in _wrap(draw, figure.title, title_font, width - 2 * margin):
        box = put((margin, y), line, title_font, _INK)
        y = box[3] + 8
    source_box = draw.textbbox((0, 0), f"出典: {figure.source}", font=small_font)
    put(
        (margin, height - 20 - (source_box[3] - source_box[1])),
        f"出典: {figure.source}",
        small_font,
        _FAINT,
    )

    label_height = draw.textbbox((0, 0), "0123456789", font=label_font)
    label_height = label_height[3] - label_height[1]
    values = [value for _label, value in figure.rows]
    high = max(max(values), 0.0)
    low = min(min(values), 0.0)
    span = (high - low) or 1.0
    # 上は値の札のぶん、下は負の値の札と区分の札のぶんを空ける。
    top = y + label_height + 24
    categories = height - 90 - label_height
    bottom = categories - 16 - (label_height + 14 if low < 0 else 0)
    left, right = 80, width - 60

    def y_of(value: float) -> float:
        return top + (high - value) / span * (bottom - top)

    zero = y_of(0.0)
    # **件数は件数として書く。** 全部が 0 以上の整数なら、符号も小数も付けない。
    counts = all(value >= 0 and float(value).is_integer() for value in values)
    slot = (right - left) / len(figure.rows)
    bar = slot * 0.6
    for index, (label, value) in enumerate(figure.rows):
        middle = left + slot * (index + 0.5)
        level = y_of(value)
        colour = _POSITIVE if value >= 0 else _NEGATIVE
        if label == REFERENCE_LABEL:
            colour = _FAINT
        draw.rectangle(
            (middle - bar / 2, min(level, zero), middle + bar / 2, max(level, zero)),
            fill=colour,
        )
        text = f"{value:,.0f}" if counts else f"{value:+,.2f}"
        box = draw.textbbox((0, 0), text, font=label_font)
        offset = -(box[3] - box[1]) - 12 if value >= 0 else 8
        put((middle - (box[2] - box[0]) / 2, level + offset), text, label_font, _INK)
        box = draw.textbbox((0, 0), label, font=label_font)
        put((middle - (box[2] - box[0]) / 2, categories), label, label_font, _INK)
    draw.line((left, zero, right, zero), fill=_INK, width=3)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, format="PNG")
    return boxes


def build_all(
    articles: dict[str, Article],
    registry: Sequence[Hypothesis],
    docs: Path,
) -> list[str]:
    """全記事を検査する。**書く前に、全部の問題を並べる**（1つ目で止まらない）。"""
    known = {hypothesis.identifier for hypothesis in registry}
    problems: list[str] = []
    if INTRO not in articles:
        problems.append(f"「## {INTRO}」が無い")
    for identifier, article in articles.items():
        if identifier != INTRO and identifier not in known:
            problems.append(f"{identifier}: HYPOTHESES.md の登録に無い ID")
        problems += check_article(article, docs)
    return problems


def write_all(
    articles: dict[str, Article],
    registry: Sequence[Hypothesis],
    into: Path,
    font: Path,
) -> list[Path]:
    """記事と図を書く。**呼ぶ前に :func:`build_all` が空であること。**

    前書きの末尾に、記事の一覧（タイトル）を足す。
    """
    by_id = {hypothesis.identifier: hypothesis for hypothesis in registry}
    into.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    ordered = [INTRO] + [name for name in articles if name != INTRO]
    for number, identifier in enumerate(ordered):
        article = articles[identifier]
        text = render_note(article, by_id.get(identifier))
        if identifier == INTRO:
            listing = [f"- {articles[name].title}" for name in ordered[1:]]
            text = text.rstrip() + "\n\n## この連載の記事\n\n" + "\n".join(listing) + "\n"
        target = into / f"{number:02d}-{identifier}.md"
        target.write_text(text, encoding="utf-8")
        written.append(target)
        for index, figure in enumerate(article.figures, 1):
            picture = into / figure_name(identifier, index)
            draw_bars(figure, picture, font)
            written.append(picture)
    return written
