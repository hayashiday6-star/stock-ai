"""SessionStart フック（`.claude/hooks/session-start-branch-check.sh`）。

**このフックは黙って死んでいた。** ユーザーの Windows に `jq` が無く、
`set -eu` の下で `jq -n` が exit 127 で落ち、**何も出力しなかった**
（2026-09-19 に報告された）。

止めるはずだった事故——`main` で直接作業する、push 忘れが残る——を
`CLAUDE.md` は「3回起きている」と書いている。**その見張りが、いつからか
鳴っていなかった。いつからかは分からない。**

## なぜ pytest に置くか

**このリポジトリの関門は `uv run pytest -q` 1本**で、CI も同じものを見る。
シェルスクリプトでも subprocess で叩けるので、**忘れられない場所はここ**である。

## Windows で skip されるテストを、頼りにしない

下の `TestItRunsWithoutJq` などは `sh` が要るので、Windows では skip される。
**壊れていたのは、その Windows のほうである。**

**skip したテストは「緑」に見える**——`CLAUDE.md` の「緑であることは、見た
ことを意味しない」。だから `TestItCallsNothingItCannotAssume` を別に置く。
**ファイルを読むだけなので、どこでも走る。**
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_HOOK = _ROOT / ".claude" / "hooks" / "session-start-branch-check.sh"
_SETTINGS = _ROOT / ".claude" / "settings.json"

#: 呼んでよい外部コマンド。**`git` だけ。**
#:
#: `jq` はここに無い。**足し戻したら落ちる。** 依存を1つ増やすということは、
#: 「その環境に在る」と賭けるということである。フックは**利用者の PC で走る**
#: ので、こちらの手元に在ることは何の保証にもならない。
_ALLOWED_COMMANDS = frozenset({"git"})

#: シェルが自前で持っているもの。外部コマンドではない。
_SHELL_WORDS = frozenset(
    {
        "if",
        "then",
        "else",
        "elif",
        "fi",
        "for",
        "while",
        "do",
        "done",
        "case",
        "esac",
        "in",
        "function",
        "return",
        "break",
        "continue",
        "set",
        "unset",
        "exit",
        "cd",
        "trap",
        "shift",
        "read",
        "local",
        "export",
        "true",
        "false",
        "test",
        "eval",
        "exec",
        "printf",
        "echo",
        "command",
    }
)


def _external_commands(text: str) -> set[str]:
    """スクリプトが呼んでいる外部コマンドらしきもの。

    **多めに拾う。** 拾いすぎれば落ちて気付けるが、**拾い落とせば黙って
    通る**——止めたいのはそちらである。

    Args:
        text: スクリプトの中身。

    Returns:
        コマンド名らしき語。
    """
    stripped = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    # **自分で定義した関数は外部コマンドではない。** 最初これを引かずに書いて
    # 落ちた——拾いすぎる側に倒してあるので、落ちて気付けた（2026-09-19）。
    defined = set(re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(\)", stripped, re.MULTILINE))
    found = set()
    for match in re.finditer(
        r"(?:^|[;|&(]|\$\(|\bthen\b|\belse\b|\bdo\b)\s*([A-Za-z_][A-Za-z0-9_.-]*)",
        stripped,
        re.MULTILINE,
    ):
        word = match.group(1)
        after = stripped[match.end() : match.end() + 1]
        if after == "=":  # name=value は代入であって、コマンドではない
            continue
        if word in _SHELL_WORDS or word in defined:
            continue
        found.add(word)
    return found


def _bin_dir(tmp_path: pathlib.Path, tools: tuple[str, ...]) -> pathlib.Path | None:
    """``tools`` だけを置いた PATH 用のディレクトリ。**無い道具があれば `None`。**"""
    where = tmp_path / "bin"
    where.mkdir(exist_ok=True)
    for tool in tools:
        found = shutil.which(tool)
        if found is None:
            return None
        link = where / tool
        if not link.exists():
            os.symlink(found, link)
    return where


def _repo(tmp_path: pathlib.Path, branch: str = "main") -> pathlib.Path:
    """``branch`` に乗った、コミットが1つある git リポジトリ。"""
    where = tmp_path / "repo"
    where.mkdir()
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
    }
    subprocess.run(["git", "init", "-q", "-b", "main", "."], cwd=where, env=env, check=True)
    subprocess.run(
        ["git", "commit", "-q", "--allow-empty", "-m", "x"], cwd=where, env=env, check=True
    )
    if branch != "main":
        subprocess.run(["git", "checkout", "-q", "-b", branch], cwd=where, env=env, check=True)
    return where


def _run(cwd: pathlib.Path, path: pathlib.Path | None) -> subprocess.CompletedProcess[str]:
    """フックを走らせる。``path`` を渡すと、その中の道具しか見えない。"""
    env = dict(os.environ)
    if path is not None:
        env["PATH"] = str(path)
    # **`sh` は PATH を絞る前に見つけておく。** 絞ったあとで探すと、
    # 「jq が無いから落ちた」のか「テストが動いていない」のか区別できない
    # ——最初それで落ちた（2026-09-19）。
    shell = shutil.which("sh")
    assert shell is not None, "sh が無い環境では、このテストは skip されるはず"
    return subprocess.run(
        [shell, str(_HOOK)],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


_NEEDS_SH = pytest.mark.skipif(shutil.which("sh") is None, reason="sh が無い（Windows）")

#: `jq` の有無を両方走らせる。**片方だけだと、壊れているほうを素通りさせる。**
_PATHS = ("jq あり", "jq なし")


@pytest.fixture(params=_PATHS)
def path_dir(request, tmp_path: pathlib.Path) -> pathlib.Path | None:
    """PATH。``jq なし`` は `git` だけを置いたディレクトリを返す。"""
    if request.param == "jq あり":
        return None
    where = _bin_dir(tmp_path, ("git",))
    if where is None:
        pytest.skip("git が見つからない")
    return where


class TestItCallsNothingItCannotAssume:
    """**どこでも走る検査。** ファイルを読むだけなので Windows でも走る。

    **これが要である。** 下のテストは `sh` が無ければ skip され、
    **skip は緑に見える。** 壊れていたのは、その skip される側の環境だった。
    """

    def test_the_hook_exists_and_is_wired(self) -> None:
        assert _HOOK.exists()
        assert _HOOK.name in _SETTINGS.read_text(encoding="utf-8")

    def test_it_calls_only_what_the_allowlist_permits(self) -> None:
        """**`jq` はここに無い。足し戻したら落ちる。**

        依存を1つ増やすとは、「その環境に在る」と賭けること。フックは
        **利用者の PC で走る**ので、こちらの手元に在ることは保証にならない。
        """
        called = _external_commands(_HOOK.read_text(encoding="utf-8"))
        outside = called - _ALLOWED_COMMANDS - _SHELL_WORDS

        assert not outside, f"許可していない外部コマンドを呼んでいる: {sorted(outside)}"

    def test_the_allowlist_would_notice_jq(self) -> None:
        """**この検査が落ちる条件を、実際に1つ作る。**"""
        assert "jq" in _external_commands("foo=1\njq -n '{}'\n")
        assert "jq" not in _ALLOWED_COMMANDS


@_NEEDS_SH
class TestItSurvivesWithoutJq:
    """**jq が無い環境で、黙って死なない。**

    ユーザーの PC はこれだった。`set -eu` の下で exit 127、出力なし。
    """

    def test_it_exits_cleanly(self, tmp_path: pathlib.Path, path_dir) -> None:
        found = _run(_repo(tmp_path, branch="work"), path_dir)

        assert found.returncode == 0, found.stderr

    def test_it_prints_json(self, tmp_path: pathlib.Path, path_dir) -> None:
        found = _run(_repo(tmp_path, branch="work"), path_dir)

        assert found.stdout.strip(), "何も出力していない"
        json.loads(found.stdout)

    def test_the_json_carries_the_branch(self, tmp_path: pathlib.Path, path_dir) -> None:
        found = _run(_repo(tmp_path, branch="work"), path_dir)
        payload = json.loads(found.stdout)

        assert "work" in payload["systemMessage"]
        assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"
        assert "work" in payload["hookSpecificOutput"]["additionalContext"]


@_NEEDS_SH
class TestTheWarningsActuallyFire:
    """**見張りが鳴ることを、実際に鳴らして確かめる。**

    鳴らないなら、その見張りは何も守っていない。
    """

    def test_main_is_called_out(self, tmp_path: pathlib.Path, path_dir) -> None:
        found = _run(_repo(tmp_path, branch="main"), path_dir)
        payload = json.loads(found.stdout)

        assert "main で直接作業" in payload["systemMessage"]

    def test_a_working_branch_is_not_called_out(self, tmp_path: pathlib.Path, path_dir) -> None:
        """**鳴りっぱなしの警告は読まれない。**"""
        found = _run(_repo(tmp_path, branch="work"), path_dir)
        payload = json.loads(found.stdout)

        assert "main で直接作業" not in payload["systemMessage"]

    def test_uncommitted_changes_are_called_out(self, tmp_path: pathlib.Path, path_dir) -> None:
        where = _repo(tmp_path, branch="work")
        (where / "dirty.txt").write_text("x", encoding="utf-8")

        payload = json.loads(_run(where, path_dir).stdout)

        assert "コミットされていない変更" in payload["systemMessage"]


@_NEEDS_SH
class TestAQuoteInTheBranchNameDoesNotBreakTheJson:
    """**枝名は外から来る値である。**

    `"` は git の枝名として通る（`\\` は git が弾く）。固定の文面から `"` を
    抜くだけでは足りない——**枝名にそれが入れば、同じ「黙って死ぬ」が起きる。**
    """

    def test_the_json_still_parses(self, tmp_path: pathlib.Path, path_dir) -> None:
        where = _repo(tmp_path, branch='feat/say"hi"')

        found = _run(where, path_dir)

        assert found.returncode == 0, found.stderr
        payload = json.loads(found.stdout)
        assert 'feat/say"hi"' in payload["systemMessage"]


@_NEEDS_SH
class TestItDoesNotDieSilently:
    """**沈黙が「異常なし」に見える形を、構造で潰す。**

    今回いつから鳴っていなかったか分からないのは、落ちても何も出さなかった
    ためである。
    """

    def test_a_missing_git_is_said_out_loud(self, tmp_path: pathlib.Path) -> None:
        """**`git` が無いことを、黙って飲み込まない。**

        `jq` が無いのを黙って飲み込んだのが今回である。PATH に `git` が無い
        のは同じ形の事故で、Windows で起こりうる。
        """
        where = _repo(tmp_path, branch="work")
        empty = _bin_dir(tmp_path, ())
        if empty is None:
            pytest.skip("道具を置けない")

        found = _run(where, empty)

        assert found.returncode == 0, found.stderr
        assert found.stdout.strip(), "**何も出力せずに終わっている。**"
        assert "git が見つかりません" in json.loads(found.stdout)["systemMessage"]

    def test_a_directory_that_is_not_a_repo_stays_quiet(self, tmp_path: pathlib.Path) -> None:
        """**鳴りっぱなしにしない。** リポジトリの外では、何も言わなくてよい。

        `git` が無いこととは別である——そちらは事故、こちらは通常。
        """
        outside = tmp_path / "plain"
        outside.mkdir()

        found = _run(outside, None)

        assert found.returncode == 0
        assert not found.stdout.strip()

    def test_the_net_is_installed_and_cleared(self) -> None:
        """**次に何が壊れても黙らない仕掛けが、在ること。**

        いまのスクリプトは git 呼び出しを全部 `||` で守っているので、外から
        途中死を作れない。**作れないことと、網が要らないことは別である**
        ——網は「次の変更」のために在る。
        """
        body = _HOOK.read_text(encoding="utf-8")

        assert "trap 'printf" in body, "落ちたときに出す網が無い"
        assert body.count("trap - EXIT") >= 2, "成功する経路で網を外していない"
