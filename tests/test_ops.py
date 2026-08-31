"""Tests for the bridge to the canonical trading repository (no WSL, no network)."""

from __future__ import annotations

import json
import shlex

import pandas as pd
import pytest

from stock_ai.core.exceptions import OpsError, OpsUnavailableError
from stock_ai.ops import _payload
from stock_ai.ops.bridge import DEFAULT_TIMEOUT, TIMEOUTS, OpsBridge, OpsTarget, RunResult, _decode

TARGET = OpsTarget(distro="Ubuntu-24.04", repo_path="/home/u/test", python=".venv/bin/python")


class FakeRunner:
    """Stand in for wsl.exe, recording what it was asked to run."""

    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.calls: list[tuple[list[str], str, float]] = []

    def __call__(self, argv: list[str], stdin_text: str, timeout: float) -> RunResult:
        self.calls.append((argv, stdin_text, timeout))
        return RunResult(returncode=self.returncode, stdout=self.stdout, stderr=self.stderr)


def _ok(data: object, stdout: str = "") -> str:
    return json.dumps({"ok": True, "data": data, "stdout": stdout}, ensure_ascii=False)


def _bridge(stdout: str = "", stderr: str = "") -> tuple[OpsBridge, FakeRunner]:
    runner = FakeRunner(stdout=stdout, stderr=stderr)
    return OpsBridge(TARGET, runner=runner), runner


# --- コマンド組み立て -------------------------------------------------------


def test_argv_runs_the_canonical_python_in_the_named_distro() -> None:
    bridge, _ = _bridge()
    argv = bridge.argv("status")
    assert argv[:5] == ["wsl.exe", "-d", "Ubuntu-24.04", "--", "bash"]
    assert argv[5] == "-c"
    assert "cd /home/u/test" in argv[6]
    assert ".venv/bin/python - status" in argv[6]


def test_argv_quotes_free_text_so_it_cannot_become_a_command() -> None:
    """発動理由もメモもユーザーの自由入力。シェルに素で載せない。"""
    bridge, _ = _bridge()
    payload = {"reason": "残高不一致'; rm -rf ~; echo '", "scope": "両方"}
    tokens = shlex.split(bridge.argv("kill", payload)[6])
    assert tokens[-2:] == ["kill", json.dumps(payload, ensure_ascii=False)]
    assert "rm" not in tokens  # 1つの引数のまま、コマンドにはならない


def test_argv_rejects_a_command_outside_the_whitelist() -> None:
    bridge, _ = _bridge()
    with pytest.raises(OpsError, match="許可されていない"):
        bridge.argv("rm")


def test_call_sends_the_payload_script_on_stdin() -> None:
    bridge, runner = _bridge(_ok({"cwd": "/home/u/test"}))
    bridge.ping()
    _, stdin_text, _ = runner.calls[0]
    assert "def cmd_ping" in stdin_text
    assert "COMMANDS" in stdin_text


def test_slow_commands_get_their_own_timeout() -> None:
    bridge, runner = _bridge(_ok({"asof": "2026-08-28"}))
    bridge.dry_run(60, 1.5, 20_000_000)
    assert runner.calls[0][2] == TIMEOUTS["dry-run"] > DEFAULT_TIMEOUT


# --- 応答の解釈 -------------------------------------------------------------


def test_call_returns_the_data_and_the_captured_stdout() -> None:
    bridge, _ = _bridge(_ok({"jobs": ["照合のみ"]}, stdout="読み込み中..."))
    reply = bridge.call("jobs")
    assert reply.data == {"jobs": ["照合のみ"]}
    assert reply.stdout == "読み込み中..."


def test_an_error_from_the_canonical_side_surfaces_its_message() -> None:
    bridge, _ = _bridge(json.dumps({"ok": False, "error": "FileNotFoundError: positions.json"}))
    with pytest.raises(OpsError, match="positions.json"):
        bridge.status()


def test_silence_is_reported_as_unreachable_not_as_empty_data() -> None:
    """WSLが止まっているのと「保有ゼロ」は別物。取り違えると気付けない。"""
    bridge, _ = _bridge(stdout="", stderr="There is no distribution with the supplied name.")
    with pytest.raises(OpsUnavailableError) as excinfo:
        bridge.status()
    assert "Ubuntu-24.04:/home/u/test" in str(excinfo.value)
    assert "no distribution" in str(excinfo.value)


def test_unparsable_output_is_an_error_with_the_output_quoted() -> None:
    bridge, _ = _bridge("Traceback (most recent call last): ...")
    with pytest.raises(OpsError, match="Traceback"):
        bridge.status()


def test_decode_reads_the_utf16_that_wsl_exe_uses_for_its_own_errors() -> None:
    raw = "指定された名前のディストリビューションはありません。".encode("utf-16-le")
    assert _decode(raw).startswith("指定された名前")
    assert chr(0) not in _decode(raw)


def test_decode_reads_utf8_json_from_the_canonical_side() -> None:
    assert (
        json.loads(_decode(_ok({"code": "７２０３"}).encode("utf-8")))["data"]["code"] == "７２０３"
    )


# --- 安全側の性質 -----------------------------------------------------------


def test_the_bridge_has_no_way_to_release_a_kill_switch() -> None:
    """解除はユーザーが正典側で行う(交渉不可)。口を作らないことをテストで留める。"""
    names = [n for n in dir(OpsBridge) if not n.startswith("_")]
    assert "activate_kill_switch" in names
    assert not [n for n in names if any(w in n for w in ("release", "deactivate", "clear_kill"))]
    assert not [c for c in _payload.COMMANDS if "release" in c or "unkill" in c]


def test_activate_kill_switch_returns_the_files_it_created() -> None:
    bridge, runner = _bridge(_ok({"created": ["/home/u/test/kill_switch.json"]}))
    assert bridge.activate_kill_switch("残高不一致", "日本株") == ["/home/u/test/kill_switch.json"]
    sent = json.loads(shlex.split(runner.calls[0][0][6])[-1])
    assert sent == {"reason": "残高不一致", "scope": "日本株"}


def test_save_notes_reports_how_many_were_kept() -> None:
    bridge, _ = _bridge(_ok({"saved": 2}))
    assert bridge.save_notes({"a": "x", "b": "y", "c": ""}) == 2


# --- 正典側で動くスクリプト ------------------------------------------------


def test_payload_serialises_a_curve_for_transport() -> None:
    series = pd.Series([100.0, 101.5], index=pd.to_datetime(["2026-08-27", "2026-08-28"]))
    curve = _payload._curve(
        {
            "label": "日本株トラックA",
            "currency": "円",
            "capital": 20_000_000,
            "asof": "2026-08-28",
            "equity": 101.5,
            "curve": series,
        }
    )
    assert curve["points"] == [["2026-08-27", 100.0], ["2026-08-28", 101.5]]
    assert "marks" not in curve
    assert json.dumps(curve)  # 転送できる形になっている


def test_payload_curve_of_nothing_is_none() -> None:
    assert _payload._curve(None) is None


def test_payload_wraps_the_result_in_an_envelope(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(_payload.COMMANDS, "ping", lambda args: {"echo": args})
    assert _payload.main(["-", "ping", '{"a": 1}']) == 0
    envelope = json.loads(capsys.readouterr().out)
    assert envelope == {"ok": True, "data": {"echo": {"a": 1}}, "stdout": ""}


def test_payload_keeps_stdout_clean_for_json(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """正典側のコードが進捗を print しても、標準出力はJSON1個のまま。"""

    def noisy(_args: dict) -> dict:
        print("読み込み中 60%")
        return {"done": True}

    monkeypatch.setitem(_payload.COMMANDS, "ping", noisy)
    assert _payload.main(["-", "ping", "{}"]) == 0
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["data"] == {"done": True}
    assert "読み込み中" in envelope["stdout"]


def test_payload_turns_an_exception_into_a_readable_envelope(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_args: dict) -> dict:
        raise FileNotFoundError("positions.json")

    monkeypatch.setitem(_payload.COMMANDS, "ping", boom)
    assert _payload.main(["-", "ping", "{}"]) == 0
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["ok"] is False
    assert "positions.json" in envelope["error"]


def test_payload_refuses_an_unknown_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert _payload.main(["-", "rm-rf", "{}"]) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_payload_kill_needs_a_reason_and_a_known_scope() -> None:
    with pytest.raises(ValueError, match="発動理由"):
        _payload.cmd_kill({"reason": "  ", "scope": "日本株"})
    with pytest.raises(ValueError, match="対象が不正"):
        _payload.cmd_kill({"reason": "残高不一致", "scope": "全部"})
