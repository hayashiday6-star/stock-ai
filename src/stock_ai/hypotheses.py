"""`docs/HYPOTHESES.md` を読み、`reports/<ID>/` を組み立てる。

## 散文を解釈しない

**表だけを読む。** 見出しで区切られた散文は、**切り出すだけで中身に触らない。**

日本語の散文を解析して報告書を組み立てると、書き方が少し変わっただけで節が
黙って抜ける。**中身の無い報告書は、無い報告書より悪い**——出したことになって
しまう。表は構造があるので確実に読める。見出しも境界が明確である。

## 二重管理を作らない

報告書は**生成物**である。出典も判定も `docs/HYPOTHESES.md` にしか書かない。
`reports/` を手で直さない——直すと、どちらが本当か分からなくなる。

## 空欄を埋めない

出典が「未記載」なら、報告書にも「未記載」と出る。**もっともらしい文献名を
補わない。** `PREREG_MARGIN_JP.md` の戒め——「転記すれば『出典のある数字』の
見た目を作ってしまう」——がそのまま当てはまる。
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

from stock_ai.core.logging import get_logger

logger = get_logger(__name__)

#: 記録が無いことを表す語。**空欄と区別する。**
MISSING = "未記載"

#: 判定として認める語。**これ以外は状態である**（`docs/PURPOSE.md`）。
VERDICTS = ("合格", "不合格", "検出できず", "検証不能")

_ROW = re.compile(r"^\|(.+)\|\s*$")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _cells(line: str) -> list[str]:
    found = _ROW.match(line.strip())
    if not found:
        return []
    return [cell.strip() for cell in found.group(1).split("|")]


def _plain(cell: str) -> str:
    """飾りを外して中身だけにする。**リンクは文字のほうを残す。**"""
    text = _BOLD.sub(r"\1", cell)
    text = _LINK.sub(r"\1", text)
    return text.replace("`", "").strip()


def read_table(text: str, heading: str) -> list[dict[str, str]]:
    """``heading`` の直後にある最初の表を、辞書の列にする。

    **見出しで場所を決める。** 何番目の表か、で数えると、上に表が1つ増えた
    だけで別の表を読む。

    Args:
        text: `HYPOTHESES.md` の中身。
        heading: 見出しの文字列（`### 登録` など）。

    Returns:
        1行につき1つの辞書。表が無ければ空。
    """
    if heading not in text:
        return []
    after = text.split(heading, 1)[1]
    header: list[str] = []
    rows: list[dict[str, str]] = []
    for line in after.splitlines():
        cells = _cells(line)
        if not cells:
            if header:
                break  # 表が終わった
            continue
        if not header:
            header = [_plain(cell) for cell in cells]
            continue
        if all(set(cell) <= {"-", ":"} for cell in cells if cell):
            continue  # 区切りの行
        rows.append(dict(zip(header, cells, strict=False)))
    return rows


@dataclasses.dataclass
class Hypothesis:
    """1本の説。**登録の表と判定の表を、ID で突き合わせたもの。**"""

    identifier: str
    number: str
    title: str
    kind: str
    composition: str
    market: str
    source: str
    prereg: str
    verdict: str
    sealed_on: str
    one_line: str

    @property
    def judged(self) -> bool:
        """判定を消費したか。**「未判定（…）」は判定ではない。**"""
        return any(self.verdict.startswith(word) for word in VERDICTS)

    @property
    def source_recorded(self) -> bool:
        """出典が記録されているか。"""
        return self.source not in {"", MISSING, "—"}


def read_registry(path: Path) -> list[Hypothesis]:
    """登録の表と判定の表を読み、ID で突き合わせる。

    **片方にしか無い ID は落とさずに残す。** 落とすと、表がずれていることに
    気付けない。判定の表に無い説は、判定を空にして返す。
    """
    text = path.read_text(encoding="utf-8")
    registered = read_table(text, "### 登録")
    judged = {_plain(row.get("ID", "")): row for row in read_table(text, "### 判定")}

    found = []
    for row in registered:
        identifier = _plain(row.get("ID", ""))
        if not identifier:
            continue
        verdict_row = judged.get(identifier, {})
        found.append(
            Hypothesis(
                identifier=identifier,
                number=_plain(row.get("#", "")),
                title=_plain(row.get("説", "")),
                kind=_plain(row.get("種類", "")),
                composition=_plain(row.get("構成", "")),
                market=_plain(row.get("市場", "")),
                source=_plain(row.get("出典", "")),
                prereg=_plain(row.get("事前登録", "")),
                verdict=_plain(verdict_row.get("判定", "")),
                sealed_on=_plain(verdict_row.get("封印日", "")),
                one_line=_plain(verdict_row.get("ひとことで言うと", "")),
            )
        )
    return found


def section_for(text: str, number: str) -> str:
    """``## <number>. …`` の節を、**そのまま切り出す。**

    中身は解釈しない。次の `## ` までを返す。見つからなければ空文字。
    """
    marker = f"\n## {number}. "
    if marker not in text:
        return ""
    body = text.split(marker, 1)[1]
    head, _, rest = body.partition("\n")
    ended = rest.split("\n## ", 1)[0]
    return f"## {number}. {head}\n{ended}".rstrip()


def report_for(hypothesis: Hypothesis, section: str) -> str:
    """1本ぶんの公開用レポートを組み立てる。

    構造のある項目を上に置き、その下に `HYPOTHESES.md` の節をそのまま置く。
    **書き直さない**ので、元を直せばレポートも直る。
    """
    lines = [
        f"# {hypothesis.identifier} — {hypothesis.title}",
        "",
        "**この文書は生成物である。** 直すなら `docs/HYPOTHESES.md` のほうを直す。",
        "",
        "| | |",
        "|---|---|",
        f"| ID | `{hypothesis.identifier}` |",
        f"| 種類 | {hypothesis.kind} |",
        f"| 構成 | {hypothesis.composition} |",
        f"| 市場 | {hypothesis.market} |",
        f"| 出典 | {hypothesis.source or MISSING} |",
        f"| 事前登録 | {hypothesis.prereg} |",
        f"| 封印日 | {hypothesis.sealed_on or MISSING} |",
        f"| 判定 | {hypothesis.verdict or MISSING} |",
        "",
    ]
    if not hypothesis.source_recorded:
        lines += [
            "> **出典が記録されていない。** もっともらしい文献名を補っていない——",
            "> 転記すれば「出典のある数字」の見た目を作ってしまう。当たり直して埋める。",
            "",
        ]
    if not hypothesis.judged:
        lines += [
            f"> **まだ判定していない**（{hypothesis.verdict or MISSING}）。",
            "> 結果は載っていない。",
            "",
        ]
    lines += ["---", ""]
    lines.append(section if section else "_`docs/HYPOTHESES.md` に対応する節が無い。_")
    return "\n".join(lines).rstrip() + "\n"


def write_reports(path: Path, into: Path) -> list[tuple[str, Path]]:
    """説ごとに `reports/<ID>/README.md` を書く。

    Args:
        path: `docs/HYPOTHESES.md`。
        into: `reports/` の場所。

    Returns:
        （ID、書いたパス）の列。
    """
    text = path.read_text(encoding="utf-8")
    written = []
    for hypothesis in read_registry(path):
        section = section_for(text, hypothesis.number)
        folder = into / hypothesis.identifier
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / "README.md"
        target.write_text(report_for(hypothesis, section), encoding="utf-8")
        written.append((hypothesis.identifier, target))
    return written
