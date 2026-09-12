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
        lines = [
            line
            for line in _text("archive-backup.ps1").splitlines()
            if line.strip().startswith("robocopy ")
        ]

        assert lines, "robocopy の行が見当たらない（名前が変わった？）"
        for line in lines:
            # **下見の行も見る。** 下見が /MIR で走れば、消えるものは同じである。
            assert "/MIR" not in line, line
            assert "/E" in line, line

    def test_the_copy_is_verified_against_the_manifest(self) -> None:
        """**写したつもりで写せていないのが、いちばん困る。**"""
        body = _text("archive-backup.ps1")

        assert "jquants-archive-verify" in body
        assert "--dir" in body


class TestTheBackupHoldsAtGigabyteScale:
    """265MB では起きないが、20年ぶんの 1GB 超では起きること。

    **どれも例外は出ない。** 遅いだけ、確かめていないだけ、という形で出る。
    """

    def test_the_free_space_check_is_not_swallowed_when_it_fails(self) -> None:
        """**空きが分からなかったことを、黙って通さない。**

        最初は `Get-PSDrive` だけを見ていて、失敗したら `catch {}` で握り
        潰していた。ネットワークパスでは `GetPathRoot` が `\\\\server\\share`
        を返すのでドライブ名として引けず、**そのまま容量を確かめずに写し
        始める。** 265MB なら入るので気付かない。
        """
        body = _text("archive-backup.ps1")

        assert "DriveInfo" in body, "引けなかったときの二の矢が無い"
        assert "Write-Warn" in body, "分からなかったことを言っていない"

    def test_there_is_a_way_to_look_before_leaping(self) -> None:
        """**いきなり GB を流す前に、何が写るかを見られること。**"""
        body = _text("archive-backup.ps1")

        assert "$DryRun" in body
        assert "/L" in body

    def test_the_dry_run_does_not_copy(self) -> None:
        """`/L` の付いた行だけが下見である。**本番の行に混ぜない。**"""
        lines = [
            line.strip()
            for line in _text("archive-backup.ps1").splitlines()
            if line.strip().startswith("robocopy ")
        ]

        assert len(lines) == 2, lines
        listing = [line for line in lines if "/L" in line.split()]
        assert len(listing) == 1, "下見の行が1つではない"

    def test_the_destination_is_counted_before_copying(self) -> None:
        """**「ぜんぶ写る」の意味が2通りに読めてしまうのを防ぐ。**

        写し先が空なら、ぜんぶ写ると出るのが正しい姿である。既に同じだけ
        あるのにぜんぶ写ると出るなら、写し先が元の更新時刻を保てていない
        ——毎回ぜんぶ上げ直すことになる。**robocopy の出力は、どちらも
        同じ形をしている。**
        """
        body = _text("archive-backup.ps1")

        assert "Show-CopyReading" in body
        assert body.count("Show-CopyReading") >= 3, "下見と本番の両方で読み解いていない"

    def test_the_reading_is_shown_on_the_real_copy_too(self) -> None:
        """下見でしか出ないと、**普段の実行では気付けない。**"""
        lines = _text("archive-backup.ps1").splitlines()
        real = next(
            index
            for index, line in enumerate(lines)
            if line.strip().startswith("robocopy ") and "/L" not in line.split()
        )

        assert any("Show-CopyReading" in line for line in lines[real : real + 8])

    def test_no_flag_is_carried_that_was_never_shown_to_help(self) -> None:
        """**外れた見立てから足した指定を残さない。**

        「写し先に 386本あるのに全部写ると出る」のを更新時刻のずれと読んで
        `/FFT` と `/DST` を足したが、**足してもスキップは 0 のままだった。**
        実際には前の写しが1段深いところに入っていただけで、**時刻は一度も
        問題になっていない。**

        根拠の無い指定を残すと、次に「スキップ」が出たときに、直ったのが
        置き場所なのか `/FFT` なのかが分からなくなる。**外せば1回で分かる。**
        """
        lines = [
            line
            for line in _text("archive-backup.ps1").splitlines()
            if line.strip().startswith("robocopy ")
        ]

        assert lines
        for line in lines:
            assert "/FFT" not in line.split(), line
            assert "/DST" not in line.split(), line

    def test_the_likely_destination_is_named_when_it_can_be(self) -> None:
        """**言い当てられるなら言い当てる。**

        「写し先を確かめてください」で止めると、打ち直す先を人が探すことに
        なり、また別の場所に入りうる。余りが1つのフォルダにまとまっていて、
        そこに目録があるなら、そこが前の写しである。
        """
        body = _text("archive-backup.ps1")

        assert "$nested" in body
        assert "manifest.csv" in body, "目録の有無で確かめていない"
        assert "$strayRoots.Count -eq 1" in body, "候補が複数でも言い当てている"

    def test_the_destination_is_matched_by_path_not_by_count(self) -> None:
        """**件数が合っていることは、同じ場所にあることではない。**

        実測（2026-09-12）: 写し先に 386本あり、元も 386本なので「揃って
        いる」と読んだが、robocopy は1本も一致していないと言っていた。
        前の写しが1段深いところに入っていたためである。

        **再帰で数えると、同じ場所にある386本と、1段深いところにある別の
        写し386本が、同じ数に見える。** 数えるべきは件数ではなく、元から見た
        相対パスが一致する本数である。
        """
        body = _text("archive-backup.ps1")

        assert "$sourceRel" in body, "相対パスで突き合わせていない"
        assert "Substring" in body, "相対パスを作っていない"
        assert "同じ場所" in body, "見出しが件数のままになっている"

    def test_files_at_the_destination_that_are_not_in_the_source_are_named(self) -> None:
        """**入れ子の写しは、余りとして現れる。** 数えるだけでなく、名前を出す。

        そのまま進めると同じものが2つ置かれ、以後ばらばらに古くなっていく。
        `/MIR` を使わないので消えもしない。
        """
        body = _text("archive-backup.ps1")

        assert "$strayFiles" in body
        assert "$strayShown" in body

    def test_the_destination_is_remembered_only_after_it_verified(self) -> None:
        """**打ち間違えた先を覚えると、次も同じ先に案内することになる。**

        写し先を打ち間違えて1段ずれた場所を指した（2026-09-12）。下見で
        止めたので二重にはならなかったが、**本番で打ち間違えれば 1GB が
        無駄になり、以後2つがばらばらに古くなる。**

        覚えるのは照合を通った先だけである。書く場所が、照合の判定より後に
        あることを当てる。
        """
        body = _text("archive-backup.ps1")
        lines = body.splitlines()

        assert "$memo" in body, "写し先を覚えていない"
        write = next(index for index, line in enumerate(lines) if "Set-Content -Path $memo" in line)
        verified = next(
            index for index, line in enumerate(lines) if "jquants-archive-verify" in line
        )
        assert write > verified, "照合より前に覚えている"
