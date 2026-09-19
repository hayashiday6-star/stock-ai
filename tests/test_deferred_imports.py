"""関数の中に書いた `from ... import ...` が、実在する名前を指しているか。

**`import` が関数の中にあると、モジュールを import するだけでは捕まらない。**
その行が実行される経路を通って初めて `ImportError` になる。

2026-09-19 に `cli._universe_calendar` が
`from stock_ai.backtest.universe_benchmark import equal_weighted_daily` と
書いていて、**その名前は存在しなかった。** ユーザーの PC で `--universe` を
付けた実行が落ちるまで分からなかった。

- `tests/test_turn_of_month.py` は 332行あったが、**その経路を一度も通って
  いなかった**
- こちらの疎通確認は空の DB で叩いたので、**手前で終わって到達していなかった**

**`factor_panel` が `list_securities(session, market="JP")` という存在しない
引数を本番まで出した形と同じである**（`CLAUDE.md`）。**その間テストは全部
緑だった。**

## なぜ経路ごとの end-to-end ではなく、これを置くか

経路ごとに1本ずつ足す方式は、**次の1本を書き忘れた瞬間に同じことが起きる**
——現にそうなった。

ここは **`src/` 全体の関数内 import を機械的に拾って、名前の実在を確かめる。**
新しい経路を足しても、**登録も追加も要らずに守られる。**

## 見るのは `stock_ai.*` だけ

外部ライブラリは任意の extra で入っていないことがあり、**入っていないことを
「名前が無い」と読むと誤報になる。** そして繰り返しているのは**自分の
モジュールの名前を間違える**形である。
"""

from __future__ import annotations

import ast
import importlib
import pathlib

import pytest

_SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "stock_ai"


def _deferred(path: pathlib.Path) -> list[tuple[int, str, tuple[str, ...]]]:
    """関数の中にある ``from stock_ai... import ...`` を拾う。

    Args:
        path: `.py` ファイル。

    Returns:
        ``(行番号, モジュール, 名前の並び)``。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, str, tuple[str, ...]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.ImportFrom):
                continue
            # 相対 import はここでは扱わない（`src/` に無い）。
            if inner.level or not inner.module:
                continue
            if not inner.module.startswith("stock_ai"):
                continue
            names = tuple(alias.name for alias in inner.names if alias.name != "*")
            if names:
                found.append((inner.lineno, inner.module, names))
    return found


def _modules() -> list[pathlib.Path]:
    return sorted(_SRC.rglob("*.py"))


@pytest.mark.parametrize("path", _modules(), ids=lambda path: path.name)
def test_every_deferred_import_names_something_real(path: pathlib.Path) -> None:
    """**その行が実行される経路を通らなくても、名前の不在で落ちる。**"""
    missing: list[str] = []
    for lineno, module, names in _deferred(path):
        target = importlib.import_module(module)
        missing.extend(
            f"{path.name}:{lineno} {module}.{name}" for name in names if not hasattr(target, name)
        )

    assert not missing, "関数の中の import が、存在しない名前を指している:\n" + "\n".join(missing)


class TestTheCheckWouldNoticeATypo:
    """**この検査が落ちる条件を、実際に1つ作って、落ちることを見る。**"""

    def test_a_missing_name_is_found(self, tmp_path: pathlib.Path) -> None:
        source = tmp_path / "fake.py"
        source.write_text(
            "def go():\n"
            "    from stock_ai.backtest.universe_benchmark import no_such_name\n"
            "\n"
            "    return no_such_name\n",
            encoding="utf-8",
        )

        found = _deferred(source)

        assert found == [(2, "stock_ai.backtest.universe_benchmark", ("no_such_name",))]
        target = importlib.import_module(found[0][1])
        assert not hasattr(target, "no_such_name")

    def test_a_module_level_import_is_not_this_check(self, tmp_path: pathlib.Path) -> None:
        """**モジュールの先頭に在る import は、import しただけで落ちる。**

        ここが見るのは、**通らないと落ちない**ほうだけである。
        """
        source = tmp_path / "top.py"
        source.write_text("from stock_ai.backtest import tails\n", encoding="utf-8")

        assert _deferred(source) == []

    def test_an_alias_is_checked_by_its_real_name(self, tmp_path: pathlib.Path) -> None:
        """`build_series as turn_series` は `build_series` を見る。"""
        source = tmp_path / "alias.py"
        source.write_text(
            "def go():\n"
            "    from stock_ai.backtest.turn_of_month import build_series as x\n"
            "\n"
            "    return x\n",
            encoding="utf-8",
        )

        assert _deferred(source)[0][2] == ("build_series",)

    def test_a_third_party_import_is_left_alone(self, tmp_path: pathlib.Path) -> None:
        """**入っていない extra を「名前が無い」と読まない。**"""
        source = tmp_path / "third.py"
        source.write_text(
            "def go():\n    from anthropic import Anthropic\n\n    return Anthropic\n",
            encoding="utf-8",
        )

        assert _deferred(source) == []
