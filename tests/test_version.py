"""実行しているコードがどれかを、出力に書く（`stock_ai.core.version`）。

**常に点く旗は、何も区別しない。**

`describe()` は `git status --porcelain` を**未追跡込み**で見ていた。この
リポジトリには `data/valuation_monthly.csv.gz` という**恒常的に未追跡の
生成物**が在るので、**コードを一行も触っていなくても必ず「+変更あり」が
出ていた**（2026-09-19 に発覚）。

`CLAUDE.md` の「広すぎる幅は、狭すぎる幅より悪い」の逆向きである——
**常に赤なので、誰も見なくなる。**

ここで押さえるのは2つ。**片方だけだと、常に点かない旗に倒しても緑になる。**

1. **未追跡ファイルだけが在るとき、旗が付かない**
2. **追跡ファイルを変えたとき、旗が付く**
"""

from __future__ import annotations

import pathlib
import subprocess

from stock_ai.core.version import CODE_PATHS, code_is_dirty, describe

_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}


def _repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """`src/` と `data/` を持つ、コミット済みのリポジトリ。"""
    import os

    where = tmp_path / "repo"
    (where / "src" / "stock_ai").mkdir(parents=True)
    (where / "data").mkdir()
    (where / "src" / "stock_ai" / "thing.py").write_text("x = 1\n", encoding="utf-8")
    (where / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    env = {**os.environ, **_ENV}
    subprocess.run(["git", "init", "-q", "-b", "main", "."], cwd=where, env=env, check=True)
    subprocess.run(["git", "add", "-A"], cwd=where, env=env, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "x"], cwd=where, env=env, check=True)
    return where


class TestTheFlagMeansSomething:
    def test_a_clean_tree_has_no_flag(self, tmp_path: pathlib.Path) -> None:
        assert not code_is_dirty(_repo(tmp_path))

    def test_an_untracked_data_file_does_not_raise_the_flag(self, tmp_path: pathlib.Path) -> None:
        """**恒常的に未追跡の生成物で、旗が点きっぱなしにならない。**

        `data/valuation_monthly.csv.gz` がそれである。
        """
        where = _repo(tmp_path)
        (where / "data" / "valuation_monthly.csv.gz").write_bytes(b"\x1f\x8b")

        assert not code_is_dirty(where)

    def test_a_changed_source_file_raises_the_flag(self, tmp_path: pathlib.Path) -> None:
        """**片方だけ置くと、常に点かない旗に倒しても緑になる。**"""
        where = _repo(tmp_path)
        (where / "src" / "stock_ai" / "thing.py").write_text("x = 2\n", encoding="utf-8")

        assert code_is_dirty(where)

    def test_a_new_untracked_source_file_raises_the_flag(self, tmp_path: pathlib.Path) -> None:
        """**`src/` に増えた未追跡の `.py` は、本物のコード変更である。**

        `--untracked-files=no` にすると、これを見逃す。**だから範囲で絞った。**
        """
        where = _repo(tmp_path)
        (where / "src" / "stock_ai" / "new.py").write_text("y = 1\n", encoding="utf-8")

        assert code_is_dirty(where)

    def test_a_changed_bat_raises_the_flag(self, tmp_path: pathlib.Path) -> None:
        """`.bat` も動くコードである。**どの深さに在っても見る。**"""
        import os

        where = _repo(tmp_path)
        (where / "research").mkdir()
        (where / "research" / "run.bat").write_text("@echo off\n", encoding="utf-8")
        env = {**os.environ, **_ENV}
        subprocess.run(["git", "add", "-A"], cwd=where, env=env, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "bat"], cwd=where, env=env, check=True)
        assert not code_is_dirty(where)

        (where / "research" / "run.bat").write_text("@echo on\n", encoding="utf-8")

        assert code_is_dirty(where)

    def test_a_changed_dependency_raises_the_flag(self, tmp_path: pathlib.Path) -> None:
        """**依存が変われば挙動が変わる。**"""
        where = _repo(tmp_path)
        (where / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")

        assert code_is_dirty(where)

    def test_a_changed_document_does_not_raise_the_flag(self, tmp_path: pathlib.Path) -> None:
        """**文書は動かない。** 版の行が答えるのは「どのコードが動いたか」。"""
        import os

        where = _repo(tmp_path)
        (where / "docs").mkdir()
        (where / "docs" / "x.md").write_text("a\n", encoding="utf-8")
        env = {**os.environ, **_ENV}
        subprocess.run(["git", "add", "-A"], cwd=where, env=env, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "doc"], cwd=where, env=env, check=True)

        (where / "docs" / "x.md").write_text("b\n", encoding="utf-8")

        assert not code_is_dirty(where)

    def test_a_directory_that_is_not_a_repo_says_nothing(self, tmp_path: pathlib.Path) -> None:
        """**分からないことを「変更あり」と言わない。**"""
        plain = tmp_path / "plain"
        plain.mkdir()

        assert not code_is_dirty(plain)


class TestTheLabelSaysWhatItLookedAt:
    """**意味を狭めたのに札が同じだと、「きれい」を作業ツリー全体と読まれる。**"""

    def test_the_paths_it_watches_are_written_down(self) -> None:
        assert "src" in CODE_PATHS
        assert "scripts" in CODE_PATHS
        assert "*.bat" in CODE_PATHS
        assert "pyproject.toml" in CODE_PATHS

    def test_it_does_not_watch_data(self) -> None:
        assert "data" not in CODE_PATHS

    def test_the_flag_names_the_narrowing(self, tmp_path: pathlib.Path) -> None:
        describe.cache_clear()
        body = describe()

        assert "変更あり" not in body or "コード変更あり" in body

    def test_describe_still_carries_the_commit(self) -> None:
        describe.cache_clear()

        assert "0.1.0" in describe()
