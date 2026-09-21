"""原本の**列そのもの**を数える（どのエンドポイントでも）。

## なぜ、読み口ごとに書かないか

**読み口が捨てている列は、読み口からは見えない。** #5 で `FS` の中の鍵を
いくら並べても答えが出なかったのと、配当の `DeemDiv` / `NetAssetDecRatio`
を読み口が落としていたのと、同じ形である。

そして**経路ごとに検査を足す方式は、次の1本を書き忘れた瞬間に同じことが
起きる**（`tests/test_deferred_imports.py` と同じ理由）。だからここは
**エンドポイントを受け取って、原本の列を全部数える。**

## 列が在ることと、値が埋まっていることは別

**候補6 の `IV` で踏んだ。** 2008-05 の原本には列が在るのに、9列が全行で
空だった。**「在る」で先に進むと、IS が 359 日しかない設計になる。**

だから :class:`ColumnCensus` は列ごとに

- 空でなかった行の数（**割合で見る**。件数だけでは何も言えない）
- **いちばん古い年**と**いちばん新しい年**（`IV` は 2016-07 から）
- **その列を持っていたファイルの数**（原本の形はファイルごとに違いうる）

を返す。**1ファイルで確かめて終わりにしない**——`identity_check` が
1,588万行で落ちたのと同じ形である。

## 年は、どの列で数えたかを言う

エンドポイントごとに日付の列名が違う（`Date` / `DisclosedDate` / `PubDate`
/ `EnDate` …）。:data:`DATE_COLUMNS` を上から順に当て、**実際に使った列名を
返して出力に出す。** 1つも見つからなければ **`None` を返して、年では
数えない**——**黙って全部を1年に押し込めない。**
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from pathlib import Path

from stock_ai.core.logging import get_logger
from stock_ai.data.jquants_bulk import records_from_csv
from stock_ai.data.jquants_margin import parse_date

logger = get_logger(__name__)

#: 年を数えるのに使う列。**上から順に、最初に見つかったものを使う。**
#:
#: **「いつ時点か」を優先する。** 公表日（`PubDate` / `DisclosedDate`）は
#: 「いつ知れたか」で、材料が何年から在るかを見るときは前者が要る
#: （`jquants_markets` の「日付の意味が列ごとに違う」）。**どちらを使ったかは
#: 出力に出す。**
DATE_COLUMNS: tuple[str, ...] = (
    "Date",
    "ExDate",
    "CalcDate",
    "EnDate",
    "CurPerEnd",
    "CurrentPeriodEndDate",
    "DisclosedDate",
    "DiscDate",
    "PubDate",
)

#: 標本に出す行数の上限。**貼られる前提で作る**（`CLAUDE.md`）。
MAX_SAMPLE_ROWS = 3


@dataclasses.dataclass(frozen=True)
class Column:
    """1つの列について分かったこと。"""

    name: str

    filled: int
    """空でなかった行。"""

    files: int
    """その列を**持っていた**ファイルの数。**在ることと埋まることは別。**"""

    first_year: int | None
    """空でない値が出たいちばん古い年。年が数えられなければ ``None``。"""

    last_year: int | None
    """同じく、いちばん新しい年。"""

    def share(self, rows: int) -> float:
        """埋まっていた割合。**件数ではなく割合を見る**（`CLAUDE.md`）。"""
        return self.filled / rows if rows else 0.0


@dataclasses.dataclass(frozen=True)
class ColumnCensus:
    """あるエンドポイントの原本を、**列ごとに**数えたもの。"""

    endpoint: str
    files: int
    rows: int

    columns: tuple[Column, ...]
    """**原本に出てきた順。** 読み口が採る列だけではない。"""

    dated_by: str | None
    """年を数えるのに使った列名。**使えなかったら ``None``。**"""

    years: dict[int, int] = dataclasses.field(default_factory=dict)
    """``年 -> 行数``。:attr:`dated_by` が ``None`` なら空。"""

    sample: tuple[dict[str, str], ...] = ()
    """原本の行そのもの。**桁や書式を目で見るため。**"""

    @property
    def empty_columns(self) -> tuple[str, ...]:
        """**1行も埋まっていなかった列。** 在るのに使えない列である。"""
        return tuple(column.name for column in self.columns if not column.filled)

    def find(self, name: str) -> Column | None:
        """列を1つ引く。**無ければ ``None``**——在らないことも答えである。"""
        return next((column for column in self.columns if column.name == name), None)

    def summary(self) -> str:
        """1行のまとめ。"""
        if not self.rows:
            return f"{self.endpoint}: 原本が1行も読めなかった。**材料が無い。**"
        span = ""
        if self.years:
            span = f"、{min(self.years)}〜{max(self.years)} 年"
        return (
            f"{self.endpoint}: {self.files:,} ファイル、{self.rows:,} 行、"
            f"{len(self.columns)} 列{span}。"
        )

    def warnings(self) -> list[str]:
        """気付かなくても目に入るべきこと。**早期 return しない。**"""
        found: list[str] = []
        if not self.rows:
            return [f"**{self.endpoint} の原本が1行も読めなかった。**"]
        if self.dated_by is None:
            found.append(
                "**年で数えていない。** 日付の列が見つからなかった"
                f"（探したのは {'、'.join(DATE_COLUMNS)}）。"
                "**列が在るのに見つからないなら、`DATE_COLUMNS` に足すこと。**"
            )
        empty = self.empty_columns
        if empty:
            found.append(
                f"**{len(empty)} 列は1行も埋まっていない**（{'、'.join(empty)}）。"
                "**列が在ることと、値が埋まっていることは別である。**"
            )
        ragged = [column.name for column in self.columns if 0 < column.files < self.files]
        if ragged:
            found.append(
                f"**{len(ragged)} 列は一部のファイルにしか無い**（{'、'.join(ragged[:8])}"
                + ("…" if len(ragged) > 8 else "")  # noqa: PLR2004 - 8 は表示の都合
                + "）。**原本の形が途中で変わっている。**"
            )
        return found


def column_census(
    directory: Path,
    endpoint: str,
    sample_rows: int = MAX_SAMPLE_ROWS,
) -> ColumnCensus:
    """保存済みの原本を読んで、**列ごとに数える。取りには行かない。**

    Args:
        directory: 原本の置き場所。
        endpoint: 数えるエンドポイント（``/fins/summary`` など）。
        sample_rows: 中身を目で見るために持って返る行数。

    Returns:
        :class:`ColumnCensus`。
    """
    order: list[str] = []
    filled: dict[str, int] = {}
    files_with: dict[str, int] = {}
    first_year: dict[str, int] = {}
    last_year: dict[str, int] = {}
    years: dict[int, int] = {}
    sample: list[dict[str, str]] = []
    dated_by: str | None = None
    files = rows = 0

    for key, payload in _files(directory, endpoint):
        try:
            found = records_from_csv(payload)
        except Exception as exc:  # noqa: BLE001 - どこで読めないかが記録に値する
            logger.warning("原本を読めなかった: %s: %s", key, exc)
            continue
        files += 1
        seen_here: set[str] = set()
        for row in found:
            rows += 1
            if len(sample) < sample_rows:
                sample.append(dict(row))
            if dated_by is None:
                dated_by = next((name for name in DATE_COLUMNS if name in row), None)
            year = _year_of(row, dated_by)
            if year is not None:
                years[year] = years.get(year, 0) + 1
            for name, value in row.items():
                if name not in filled:
                    order.append(name)
                    filled[name] = 0
                if name not in seen_here:
                    seen_here.add(name)
                    files_with[name] = files_with.get(name, 0) + 1
                if not (value or "").strip():
                    continue
                filled[name] += 1
                if year is None:
                    continue
                # **いちばん古い年と新しい年。** `IV` が 2016-07 からなのは、
                # ここにしか出ない。
                first_year[name] = min(first_year.get(name, year), year)
                last_year[name] = max(last_year.get(name, year), year)

    return ColumnCensus(
        endpoint=endpoint,
        files=files,
        rows=rows,
        columns=tuple(
            Column(
                name=name,
                filled=filled[name],
                files=files_with.get(name, 0),
                first_year=first_year.get(name),
                last_year=last_year.get(name),
            )
            for name in order
        ),
        dated_by=dated_by,
        years=dict(sorted(years.items())),
        sample=tuple(sample),
    )


def _year_of(row: dict[str, str], column: str | None) -> int | None:
    """その行の年。**列が無い・読めないなら ``None``。**"""
    if column is None:
        return None
    parsed = parse_date(row.get(column))
    return None if parsed is None else parsed.year


def _files(directory: Path, endpoint: str) -> Iterable[tuple[str, bytes]]:
    """そのエンドポイントの原本を ``(鍵, 中身)`` で。**取りには行かない。**"""
    from stock_ai.data.jquants_archive import path_for, read_manifest
    from stock_ai.data.jquants_read import endpoint_of, read_archived

    for key in sorted(read_manifest(directory)):
        if endpoint_of(key) != endpoint:
            continue
        try:
            yield key, read_archived(path_for(directory, key))
        except Exception as exc:  # noqa: BLE001 - どこで開けないかが記録に値する
            logger.warning("原本を開けなかった: %s: %s", key, exc)
