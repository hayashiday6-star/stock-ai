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


#:  の中で、実際に PowerShell を起動している行。**echo の中は拾わない。**
_INVOCATION = re.compile(r'^\s*powershell\b.*?-File\s+"([^"]+)"(.*)$', re.IGNORECASE)

#: その行で固定している引数（`-Foo`）。`%*` や `%VAR%` は引数ではない。
_SWITCH = re.compile(r'(?<![\w"])-([A-Za-z][A-Za-z0-9]*)')

#: `.ps1` の param() が宣言している引数名。
_PARAMETER = re.compile(
    r"^\s*\[?[A-Za-z\[\]]*\]?\s*\$([A-Za-z][A-Za-z0-9]*)\s*(?:=|,|\))", re.MULTILINE
)


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


class TestTakingTheIrreplaceableOnesFirst:
    """**途中で止まったとき、何が手元にあるか。**

    2026-09-15 の下見で、Premium では 3,556本・3.65GB と分かった。そのうち
    **1.74GB（48%）がデリバティブで、読み口も説も無い。** そして取得順では
    `/markets/margin-alert`（説#8 が要る唯一の経路）がその後ろにある。

    途中で止まれば、**重いだけで使わないものを取り終えて、軽くて使うものが
    無い**状態になる。回線が切れても電源が落ちても、そうなる。
    """

    def test_the_critical_set_is_a_subset_of_what_is_archived(self) -> None:
        """**取らないものを「先に取る」に入れない。**"""
        from stock_ai.data.jquants_bulk import ARCHIVE_ENDPOINTS, CRITICAL_ENDPOINTS

        assert set(CRITICAL_ENDPOINTS) <= set(ARCHIVE_ENDPOINTS)

    def test_the_one_path_hypothesis_8_needs_is_in_it(self) -> None:
        """`margin-alert` が無ければ、説#8 は封印すらできない。"""
        from stock_ai.data.jquants_bulk import CRITICAL_ENDPOINTS

        assert "/markets/margin-alert" in CRITICAL_ENDPOINTS

    def test_the_roster_comes_first(self) -> None:
        """**生存バイアスを直せる唯一のもの。** 欠ければ他が全部「生存者のみ」になる。"""
        from stock_ai.data.jquants_bulk import CRITICAL_ENDPOINTS

        assert CRITICAL_ENDPOINTS[0] == "/equities/master"

    def test_the_heavy_unused_ones_are_left_for_the_second_pass(self) -> None:
        """デリバティブは 1.74GB あって、読み口も説も無い。**先に取らない。**"""
        from stock_ai.data.jquants_bulk import CRITICAL_ENDPOINTS

        assert not [name for name in CRITICAL_ENDPOINTS if name.startswith("/derivatives/")]

    def test_the_launcher_runs_two_passes(self) -> None:
        body = _text("jquants-archive.ps1")

        assert "CRITICAL_ENDPOINTS" in body, "先に取る一覧を読んでいない"
        assert "$passes" in body

    def test_a_failed_first_pass_does_not_start_the_second(self) -> None:
        """**進むと、失敗の理由が2回ぶん混ざる。** どちらの話か分からなくなる。"""
        body = _text("jquants-archive.ps1")
        lines = body.splitlines()
        check = next(
            index
            for index, line in enumerate(lines)
            if line.strip() == "if ($code -ne 0) {" and "break" in "".join(lines[index : index + 6])
        )

        assert any("break" in line for line in lines[check : check + 6])

    def test_a_dry_run_is_not_split(self) -> None:
        """下見は1バイトも落とさない。**2周に分ける意味が無い。**"""
        body = _text("jquants-archive.ps1")

        assert "if ($DryRun -or $Endpoint -ne '')" in body


class TestGitOutputIsReadAsUtf8:
    """**PowerShell は外部プログラムの出力を `[Console]::OutputEncoding` で復号する。**

    日本語 Windows ではそれが cp932 で、git は UTF-8 で出すので食い違う。
    2026-09-15 に報告された形:

        20蟷ｴ縺ｶ繧薙・蜷咲ｰｿ縺後〒縺阪◆

    **スクリプト自身の日本語は化けていない**ので、化けているのは git が返した
    文字列だけだと分かる。記録そのものは正しく入っている（履歴で確認した）。
    """

    def test_the_helper_switches_and_puts_it_back(self) -> None:
        """**戻すのが要点である。** 切り替えたままだと、こちらの日本語が化ける。"""
        body = _text("_common.ps1")

        assert "function Use-Utf8Git" in body
        assert "UTF8Encoding" in body
        assert "$previousEncoding" in body
        assert "finally" in body

    def test_the_helper_does_not_pass_arguments_through(self) -> None:
        """**引数を受け取って渡し直さない。**

        最初は `Invoke-Git add -- $targets` の形にしたが、**Windows
        PowerShell 5.1 の `ValueFromRemainingArguments` は配列を1つの文字列に
        潰す。** 実際に落ちた（2026-09-15）。

            fatal: pathspec 'data/universe_snapshots data/tachibana_snapshots'

        **git の呼び出しそのものに触らなければ、その種の壊れ方は起きない。**
        """
        body = _text("_common.ps1")

        # **見るのは `param` の行だけである。** 「使わない」と書いた説明文に
        # 引っ掛かっては、検査が理由を消したことになる——`/MIR` のときと同じ。
        declarations = [line for line in body.splitlines() if "[Parameter(" in line]

        assert declarations
        for line in declarations:
            assert "ValueFromRemainingArguments" not in line, line
        assert "[scriptblock]$Body" in body

    def test_paths_are_not_octal_escaped(self) -> None:
        """既定では非 ASCII のファイル名が 8進に化ける。**`.bat` が日本語である。**"""
        assert "core.quotepath=false" in _text("_common.ps1")

    def test_the_update_script_reads_commit_subjects_through_it(self) -> None:
        """コミットの件名は日本語である。**ここが報告された箇所そのもの。**"""
        lines = _text("0-update.ps1").splitlines()

        shown = [line for line in lines if "log --oneline" in line]
        assert shown, "件名を出す行が見当たらない（名前が変わった？）"
        for line in shown:
            assert "Use-Utf8Git" in line, line

    def test_the_merge_output_goes_through_it_too(self) -> None:
        """変わったファイルの名前も日本語でありうる（`checks\\*.bat`）。"""
        lines = [line for line in _text("0-update.ps1").splitlines() if "merge --ff-only" in line]

        assert lines
        for line in lines:
            assert "Use-Utf8Git" in line, line

    def test_the_snapshot_script_uses_it_as_well(self) -> None:
        body = _text("commit-snapshots.ps1")
        bare = [
            line.strip()
            for line in body.splitlines()
            if line.strip().startswith("git ") and "Get-Command" not in line
        ]

        assert bare == [], f"素の git 呼び出しが残っている: {bare}"

    def test_a_shared_script_says_which_job_it_is_doing(self) -> None:
        """**同じ .ps1 を2つの .bat から使い回している。**

        切り替えないと、`この商品区分は何か` を回したのに「絞り込みは20年でも
        持つか」と出る（2026-09-15 に報告）。**名前と見出しが食い違うと、
        違うものを実行したかと思う。**
        """
        body = _text("filter-census.ps1")
        sections = [line for line in body.splitlines() if "Write-Section" in line]

        assert len(sections) >= 2, f"見出しが1つしかない: {sections}"
        assert any("商品区分" in line for line in sections)


# ---------------------------------------------------------------------------
# .bat が固定している引数を、機械に数えさせる
# ---------------------------------------------------------------------------
#
# **上の検査は、こちらが名前を書いたスクリプトしか見ていない。** 書き忘れた
# ものは、検査があること自体が理由になって「見たつもり」になる。
#
# 2026-09-15 にそれが出た。`checks\この原本の列を全部見る.bat` は
# `-NoShapes -Columns` を渡すが、CLI 側の `--columns` の処理が `-NoShapes` の
# 早期 return の**後ろ**にあり、**列が1行も出なかった。** `.bat` を書いたのも
# CLI を書いたのも同じ側（こちら）なのに、**その組み合わせを一度も実行して
# いなかった。**
#
# 一覧を手で持つのをやめる。`.bat` を全部読んで、固定している引数を数える。


def _invocation(body: str) -> tuple[str, list[str]] | None:
    """`.bat` が呼んでいる `.ps1` と、固定している引数。呼んでいなければ None。

    **`echo` の中の行を拾わない。** `.bat` は使い方の説明として同じ形の行を
    印字することがある。実行しているのは行頭から始まる行だけである。
    """
    for line in body.splitlines():
        found = _INVOCATION.match(line)
        if found:
            script = pathlib.PurePath(found.group(1).replace("\\", "/")).name
            switches = _SWITCH.findall(found.group(2))
            return script, switches
    return None


def _launchers() -> dict[str, tuple[str, list[str]]]:
    """`.bat` の名前 → (呼んでいる `.ps1`, 固定している引数)。

    **再帰的に見る。** 直下だけを見る glob に戻すと、`checks/` と `research/`
    の `.bat` が黙って検査対象から外れる。
    """
    root = pathlib.Path(__file__).resolve().parent.parent
    found = {}
    for path in sorted(root.glob("*.bat")) + sorted(root.glob("*/*.bat")):
        invocation = _invocation(path.read_text(encoding="ascii", errors="replace"))
        if invocation is not None:
            found[path.relative_to(root).as_posix()] = invocation
    return found


#: 引数を2つ以上固定している `.bat` と、その**組み合わせを実際に動かしている**
#: テストの名前。
#:
#: **ここが仕組みの本体である。** 引数を2つ以上固定した `.bat` を足すと、この
#: 表に書くまでテストが落ちる。書くには、動かすテストを先に用意することになる。
#:
#: 引数が1つなら要らない。**効き目が消えるのは、片方がもう片方を打ち消すとき
#: だけ**だからである。
COMBINATIONS_EXERCISED: dict[str, str] = {
    "3-データ取得.bat": "TestLoadingASegmentWithALimit",
    "checks/この原本の列を全部見る.bat": "TestAskingForColumnsIsNotSilencedByNoShapes",
    "checks/名簿を同じ規則で揃える.bat": "TestRefetchingExactlyWhatIsStored",
    "checks/貸借区分を取り直す.bat": "TestRefetchingOnlyTheRostersMissingLending",
    "research/SUE検証(IS).bat": "TestRunningPeadWithASurpriseMeasure",
}


class TestEveryLauncherIsWiredToSomethingThatExists:
    """**呼び先が無い `.bat` は、押した人にしか分からない。**"""

    def test_every_bat_names_a_script_that_is_there(self) -> None:
        missing = {
            bat: script
            for bat, (script, _) in _launchers().items()
            if not (_scripts_dir() / script).is_file()
        }
        assert not missing, missing

    def test_a_bat_in_a_subfolder_climbs_out_first(self) -> None:
        """直下用のパスのまま移すと動かない。**押すまで分からない。**"""
        root = pathlib.Path(__file__).resolve().parent.parent
        for bat in _launchers():
            if "/" not in bat:
                continue
            body = (root / bat).read_text(encoding="ascii", errors="replace")
            assert 'cd /d "%~dp0.."' in body, f"{bat} が親へ移っていない"
            assert "%~dp0..\\scripts\\" in body, f"{bat} のパスが直下用のまま"

    def test_every_pinned_switch_is_a_real_parameter(self) -> None:
        """`.ps1` が受け取らない引数を渡していないこと。

        PowerShell は知らない引数で止まるので**これは大声で落ちる**側だが、
        引数の綴りを変えたときに `.bat` を直し忘れるのは静かに起きる。
        """
        wrong = {}
        for bat, (script, switches) in _launchers().items():
            declared = {name.lower() for name in _PARAMETER.findall(_text(script))}
            for switch in switches:
                if switch.lower() not in declared:
                    wrong[f"{bat} -> {script}"] = switch
        assert not wrong, wrong


class TestACombinationIsNeverShippedUnexercised:
    """**引数を2つ固定したなら、その2つを一緒に動かしたテストが要る。**

    `-NoShapes` と `-Columns` は、片方ずつなら両方とも正しく動いていた。
    **一緒に渡したときだけ、後から来たほうが消えた。**
    """

    def _multi(self) -> set[str]:
        return {bat for bat, (_, switches) in _launchers().items() if len(switches) >= 2}

    def test_the_registry_matches_what_the_bats_actually_pin(self) -> None:
        """一覧と実物がずれていないこと。**ずれたら、どちらかが嘘である。**"""
        assert self._multi() == set(COMBINATIONS_EXERCISED), {
            "登録が無い": sorted(self._multi() - set(COMBINATIONS_EXERCISED)),
            "実物が無い": sorted(set(COMBINATIONS_EXERCISED) - self._multi()),
        }

    def test_each_named_test_exists(self) -> None:
        """名前を書くだけでは足りない。**そのテストが在ること。**"""
        bodies = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(pathlib.Path(__file__).resolve().parent.glob("test_*.py"))
        )
        missing = [
            f"{bat} -> {name}"
            for bat, name in COMBINATIONS_EXERCISED.items()
            if f"class {name}" not in bodies
        ]
        assert not missing, missing
