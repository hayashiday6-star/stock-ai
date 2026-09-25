"""`CLAUDE.md` と `docs/POSTMORTEMS.md` を引いている箇所が、実在する文を指しているか。

コードと docs は `CLAUDE.md`「見出し」の形で規則を引いている。**文書を組み替えると、
引用だけが古いまま残る**——例外は出ず、引いた先に何も無いだけである。事例を
`docs/POSTMORTEMS.md` へ移したとき（2026-09-25）、移す前から行き先の無い引用が
11 件あった（見出しを言い換えた、別の文書にある文を `CLAUDE.md` として引いた、など）。

あわせて2つ見る。

- **事例には必ず形の記号が付いている。** どれにも当たらない事例は、`CLAUDE.md` に
  形を足すまで置けない
- **`CLAUDE.md` の行数に上限を置く。** 毎ターン読み込まれる文書なので、事例が
  戻ってくると、その都度払うことになる
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CLAUDE = ROOT / "CLAUDE.md"
POSTMORTEMS = ROOT / "docs" / "POSTMORTEMS.md"

#: 毎ターン読み込まれるので、ここを超えたら事例が戻ってきている。
CLAUDE_MAX_LINES = 300

#: `CLAUDE.md` に残した規則や部品の経緯。形ではないので、形の一覧には載らない。
RULE_TAG = "規"

#: 引用の形。文書名のあとに（コードの書式の閉じを挟んで）かぎ括弧が続く。
#: 文書名とかぎ括弧のあいだで折り返していても拾う（コメントの続きの記号も挟める）。
_NAME = r"(CLAUDE|POSTMORTEMS)\.md`?"
_CITATION = re.compile(_NAME + r"\s*(?:#:?\s*)?" + "「([^」]+)」")


def _normalize(text: str) -> str:
    """強調・コードの書式・改行と、折り返しで挟まったコメント記号を落とす。"""
    text = re.sub(r"\n\s*(#:?)?\s*", "", text)
    text = re.sub(r"[*`\s]", "", text)
    return text.replace("『", "「").replace("』", "」")


def _sources() -> list[Path]:
    """引用を探す先。封印済みの事前登録は、封印時点の文書を指すので外す。"""
    paths = [CLAUDE, POSTMORTEMS, ROOT / "README.md"]
    paths += sorted((ROOT / "src").rglob("*.py"))
    paths += sorted(p for p in (ROOT / "tests").rglob("*.py") if p != Path(__file__).resolve())
    paths += sorted((ROOT / "scripts").rglob("*.ps1"))
    paths += sorted(p for p in (ROOT / "docs").glob("*.md") if not p.name.startswith("PREREG_"))
    return [p for p in paths if p.exists()]


def _citations() -> list[tuple[str, str, str]]:
    found = []
    for path in _sources():
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        for match in _CITATION.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            found.append((f"{path.relative_to(ROOT)}:{line}", match.group(1), match.group(2)))
    return found


def test_every_citation_points_at_text_that_exists() -> None:
    targets = {
        "CLAUDE": _normalize(CLAUDE.read_text(encoding="utf-8")),
        "POSTMORTEMS": _normalize(POSTMORTEMS.read_text(encoding="utf-8")),
    }
    missing = [
        f"{where}: {name}.md「{quoted}」"
        for where, name, quoted in _citations()
        if _normalize(quoted) not in targets[name]
    ]
    assert not missing, "引いた先に無い:\n" + "\n".join(missing)


def test_the_citation_check_can_fail() -> None:
    """無い文を引いたら落ちることを、検査そのものに対して見る。"""
    target = _normalize(CLAUDE.read_text(encoding="utf-8"))
    assert _normalize("落ちるものを置く") in target
    assert _normalize("この文はどこにも書いていない") not in target
    assert _CITATION.search("`CLAUDE.md`「落ちる」") is not None
    assert _CITATION.search("docs/POSTMORTEMS.md「落ちる」") is not None
    assert _CITATION.search("`CLAUDE.md`\n    # 「落ちる」") is not None


def _shapes() -> set[str]:
    """`CLAUDE.md`「繰り返し出る形」に載っている記号。"""
    return set(re.findall(r"^- 【(.)】", CLAUDE.read_text(encoding="utf-8"), re.M))


def test_the_shapes_are_listed() -> None:
    shapes = _shapes()
    assert len(shapes) >= 3, shapes
    assert RULE_TAG not in shapes


def test_every_postmortem_names_its_shape() -> None:
    allowed = _shapes() | {RULE_TAG}
    untagged = []
    for number, line in enumerate(POSTMORTEMS.read_text(encoding="utf-8").split("\n"), 1):
        if not re.match(r"^#{3,7} ", line):
            continue
        tag = re.search(r" 【(.)】$", line)
        if tag is None or tag.group(1) not in allowed:
            untagged.append(f"{number}: {line}")
    assert not untagged, "形の記号が無いか、CLAUDE.md に無い記号:\n" + "\n".join(untagged)


@pytest.mark.parametrize("path", [CLAUDE])
def test_the_always_loaded_document_stays_short(path: Path) -> None:
    lines = path.read_text(encoding="utf-8").count("\n") + 1
    assert lines <= CLAUDE_MAX_LINES, (
        f"{path.name} が {lines} 行ある（上限 {CLAUDE_MAX_LINES}）。"
        "事例は docs/POSTMORTEMS.md に置く"
    )
