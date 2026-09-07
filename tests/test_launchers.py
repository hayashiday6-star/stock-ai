"""`.ps1` が CLI の既定値を上書きしていないか。

**この型は 2026-09-07 の1日で2回起きた。**

1. `delisted-harvest.ps1` が `-Start '2021-09-01'` を常に渡していた。CLI 側を
   「プランから引く」に直しても、`.ps1` が上書きするので何も変わらない。
2. その2コミット後、`jquants-archive.ps1` で**同じことをした。**
   `-Throttle 0.5` を常に渡していて、CLI 側の「プランから引く」が効かず、
   Light の上限の2倍で叩き続けた。

**どちらも例外は出ない。** CLI を直したのに挙動が変わらないだけで、直した側
を見ているかぎり原因が見えない。CLAUDE.md の「CLI側が新しい設定を配線し忘れ
て、切り替えたはずが常に旧経路を叩き続けていた」と同じ形が、`.ps1` 側で
起きている。
"""

from __future__ import annotations

import pathlib
import re

import pytest

#: CLI が `.env`（設定）から既定値を引く引数。**`.ps1` は既定では渡さない。**
#:
#: 渡すのは、利用者が明示的に指定したときだけである。
DERIVED_FROM_SETTINGS = [
    ("jquants-archive.ps1", "--throttle"),
    ("delisted-harvest.ps1", "--start"),
]


def _scripts_dir() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent.parent / "scripts"


def _text(name: str) -> str:
    raw = (_scripts_dir() / name).read_bytes()
    return raw[3:].decode("utf-8") if raw.startswith(b"\xef\xbb\xbf") else raw.decode("utf-8")


@pytest.mark.parametrize(("script", "option"), DERIVED_FROM_SETTINGS)
def test_a_setting_derived_option_is_passed_only_when_asked_for(script: str, option: str) -> None:
    """**無条件に渡すと、CLI 側の既定が死ぬ。**"""
    lines = [line for line in _text(script).splitlines() if f"'{option}'" in line]

    assert lines, f"{script} に {option} が見当たらない（名前が変わった？）"
    for line in lines:
        assert line.strip().startswith("if ("), (
            f"{script} が {option} を無条件に渡している: {line.strip()!r}. "
            "CLI が設定から引く既定を、.ps1 が上書きしてしまう。"
        )


@pytest.mark.parametrize(("script", "option"), DERIVED_FROM_SETTINGS)
def test_the_parameter_default_is_empty_so_nothing_is_pinned(script: str, option: str) -> None:
    """引数の既定値そのものが空であること。

    条件を付けても、既定値が非空なら条件が常に真になる。**両方見る。**
    """
    name = "".join(word.capitalize() for word in option.lstrip("-").split("-"))
    found = re.search(rf"\$\{{?{name}}}?\s*=\s*(\S+)", _text(script))

    assert found, f"{script} に ${name} の既定が見当たらない"
    assert found.group(1).rstrip(",") in {"''", '""', "0", "$null"}, (
        f"{script} の ${name} に既定値 {found.group(1)!r} が入っている。"
        "CLI が設定から引く値を、ここで固定してしまう。"
    )


def test_every_launcher_script_is_readable() -> None:
    """一覧の名前が古くなっていないこと。**名前が変われば上の検査は空を通る。**"""
    for script, _option in DERIVED_FROM_SETTINGS:
        assert (_scripts_dir() / script).is_file(), script


class TestRobocopyExitCodes:
    """`robocopy` は**成功でも 0 を返さない。**

    0 = 写すものが無かった / 1 = 写した / 2 = 余分があった / 4 = 食い違い、
    8 以上が失敗である。`-ne 0` で見ると、**実際に写せた回が毎回「失敗」に
    なる。** よくある踏み方なので、判定の形を固定しておく。
    """

    def test_success_is_judged_by_being_under_eight(self) -> None:
        body = _text("archive-backup.ps1")

        assert "$robo -ge 8" in body
        assert "$robo -ne 0" not in body

    def test_the_mirror_switch_is_not_used(self) -> None:
        """`/MIR` は写し先の余分を消す。**打ち間違えた先のものを消してしまう。**

        見るのは**実行している行だけ**である。最初はファイル全体を見ていて、
        「/MIR は使わない」と書いた注釈そのものに引っ掛かった。注釈を消せば
        通るが、それでは検査が理由を消したことになる。
        """
        (line,) = [
            line
            for line in _text("archive-backup.ps1").splitlines()
            if line.strip().startswith("robocopy ")
        ]

        assert "/MIR" not in line
        assert "/E" in line

    def test_the_copy_is_verified_against_the_manifest(self) -> None:
        """**写したつもりで写せていないのが、いちばん困る。**"""
        body = _text("archive-backup.ps1")

        assert "jquants-archive-verify" in body
        assert "--dir" in body
