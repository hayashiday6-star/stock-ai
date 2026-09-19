"""実行しているコードがどれかを、出力そのものに書けるようにする。

押されたけれど取り込まれていない修正は、動かない修正と見分けが付かない。同じ
行から同じ結果が出る。実際、EDINET の確認出力を3回貼ってもらったうち2回は更新
前のコードで、それを**出力の形から推測して**判定していた――候補一覧に新しい
要素名が無い、表の桁がずれている、といった間接的な手掛かりで。推測が要る時点で
間違える余地がある。

コミットを出力に混ぜておけば、貼られたものを見るだけで確定する。
"""

from __future__ import annotations

import functools
import pathlib
import subprocess

from stock_ai import __version__

#: 版に関わる場所。**データの増減は版ではない。**
#:
#: `git status --porcelain` を**未追跡込み**で見ていたので、
#: `data/valuation_monthly.csv.gz`（恒常的に未追跡の生成物）があるだけで
#: **コードを一行も触っていなくても必ず「変更あり」が出ていた**
#: （2026-09-19 に発覚）。**常に点く旗は、何も区別しない。**
#:
#: **`--untracked-files=no` にはしない。** それだと `src/` に増えた未追跡の
#: `.py` ——**本物のコード変更**——まで見逃す。**見る範囲のほうを絞る。**
#:
#: `docs/` は入れない。文書は動かない。この行が答えるのは「**どのコードが
#: 動いたか**」だけである。`.claude/` も入れない——セッションの道具であって、
#: stock-ai の実行コードではない。
CODE_PATHS = ("src", "scripts", "*.bat", "*.ps1", "pyproject.toml")


def _repo_root() -> pathlib.Path:
    """このパッケージが入っているリポジトリの根。"""
    return pathlib.Path(__file__).resolve().parents[3]


def _git(*args: str, root: pathlib.Path | None = None) -> str | None:
    """リポジトリに対して git を1回叩く。失敗したら ``None``。

    git が無い、チェックアウトでない、応答しない――どれも「情報が少し減る」
    だけであって、実行を止める理由にはならない。
    """
    root = root or _repo_root()
    try:
        result = subprocess.run(  # noqa: S603 - 引数は固定、シェルを経由しない
            ["git", "-C", str(root), *args],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def code_is_dirty(root: pathlib.Path | None = None) -> bool:
    """`CODE_PATHS` の中に、コミットされていない変更があるか。

    **未追跡も見る**——`src/` に増えた `.py` は本物のコード変更である。
    見ないのは範囲の外（`data/` や `docs/`）だけ。

    Args:
        root: リポジトリの根。省くとこのパッケージが入っている場所。

    Returns:
        変更があれば ``True``。**git が答えないときは `False`**
        ——分からないことを「変更あり」と言わない。
    """
    found = _git("status", "--porcelain", "--", *CODE_PATHS, root=root)
    return bool(found)


@functools.cache
def describe() -> str:
    """``0.1.0 (a86331e 2026-08-24)`` のような1行。

    **`CODE_PATHS` の中に**未コミットの変更があれば ``+コード変更あり`` を
    付ける。同じコミットでも中身が違う可能性がある、というのは出力を読む側が
    知りたいこと。

    **札に「コード」と書くのは、見る範囲を狭めたからである。** 狭めたのに札が
    同じだと、**「きれい」と出たときに作業ツリー全体がきれいだと読まれる。**
    """
    parts = [__version__]
    commit = _git("rev-parse", "--short", "HEAD")
    if commit:
        when = _git("log", "-1", "--format=%cd", "--date=format:%Y-%m-%d")
        stamp = f"{commit} {when}" if when else commit
        parts.append(f"({stamp}{'+コード変更あり' if code_is_dirty() else ''})")
    else:
        parts.append("(git 情報なし)")
    return " ".join(parts)
