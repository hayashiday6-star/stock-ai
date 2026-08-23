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


def _git(*args: str) -> str | None:
    """リポジトリに対して git を1回叩く。失敗したら ``None``。

    git が無い、チェックアウトでない、応答しない――どれも「情報が少し減る」
    だけであって、実行を止める理由にはならない。
    """
    root = pathlib.Path(__file__).resolve().parents[3]
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


@functools.cache
def describe() -> str:
    """``0.1.0 (a86331e 2026-08-24)`` のような1行。

    手元に未コミットの変更があれば ``+変更あり`` を付ける。同じコミットでも
    中身が違う可能性がある、というのは出力を読む側が知りたいこと。
    """
    parts = [__version__]
    commit = _git("rev-parse", "--short", "HEAD")
    if commit:
        when = _git("log", "-1", "--format=%cd", "--date=format:%Y-%m-%d")
        stamp = f"{commit} {when}" if when else commit
        dirty = _git("status", "--porcelain")
        parts.append(f"({stamp}{'+変更あり' if dirty else ''})")
    else:
        parts.append("(git 情報なし)")
    return " ".join(parts)
