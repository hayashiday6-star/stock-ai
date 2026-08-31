"""正典(自動売買リポジトリ)への橋渡し。

自動売買の実運用は WSL の別リポジトリ(既定 ``Ubuntu-24.04:~/test``)で動いていて、
帳簿・シグナル・キルスイッチはそこが正典。stock-ai はそれを **読みに行くだけ** で、
売買ルールや帳簿の解釈をこちら側に複製しない。画面に出る数字を出しているのが
実運用と同じコードであることを、構造として保証するため。

やり方は単純で、``_payload.py`` の中身を ``wsl.exe`` 経由で正典の Python に標準入力
から流し込み、標準出力のJSONを1個読む。渡す引数はコマンド名(固定のホワイトリスト)
とJSON文字列だけで、シェルには :func:`shlex.quote` を通してから載せる — UIの自由入力
(発動理由やメモ)がコマンドとして解釈される余地をなくすため。
"""

from __future__ import annotations

import json
import shlex
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from stock_ai.config.settings import Settings, get_settings
from stock_ai.core.exceptions import OpsError, OpsUnavailableError
from stock_ai.core.logging import get_logger

logger = get_logger(__name__)

#: 通せるコマンド。UI から任意の文字列が来ても、ここにないものは実行しない。
COMMANDS = frozenset(
    {
        "ping",
        "status",
        "history",
        "save-notes",
        "equity",
        "notify-load",
        "notify-save",
        "dry-run",
        "kill",
        "jobs",
        "run-job",
    }
)

#: コマンドごとの既定タイムアウト(秒)。市場データの読み込みと夜間チェーンは長い。
TIMEOUTS = {"dry-run": 600.0, "run-job": 900.0, "equity": 300.0}
DEFAULT_TIMEOUT = 120.0


@dataclass(frozen=True)
class RunResult:
    """外部コマンド1回ぶんの結果(テストで差し替えられるよう最小限)。"""

    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class Reply:
    """橋渡しの戻り値。``stdout`` は正典側が処理中に print したもの。"""

    data: Any
    stdout: str


#: ``(argv, stdin_text, timeout) -> RunResult``。既定は wsl.exe を叩く実装。
Runner = Callable[[list[str], str, float], RunResult]


def _decode(raw: bytes) -> str:
    """wsl.exe の出力を文字列にする。

    正典側が書くJSONはUTF-8だが、``wsl.exe`` 自身のエラー(「指定された名前の
    ディストリビューションはありません」など)はUTF-16LEで返る。ここを取り違えると、
    一番知りたいメッセージだけが化けた文字列になり、原因が読めなくなる。

    判定を「先頭がヌル文字か」で済ませないのは、日本語のWindowsでは wsl.exe の
    メッセージ自体が日本語で、UTF-16LEにしても先頭にヌル文字が現れないため。
    UTF-8として読めないか、読めてもヌル文字が混じる(ASCIIをUTF-16LEにした形)なら、
    UTF-16LEとして読み直す。
    """
    if not raw:
        return ""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("utf-16-le", errors="replace")
    if chr(0) in text:  # ASCII を UTF-16LE にすると1文字おきにヌルが挟まる
        return raw.decode("utf-16-le", errors="replace")
    return text


def _wsl_runner(argv: list[str], stdin_text: str, timeout: float) -> RunResult:
    """既定の実行系。バイトで受けて自前でデコードする(理由は :func:`_decode`)。"""
    try:
        completed = subprocess.run(
            argv,
            input=stdin_text.encode("utf-8"),
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:  # wsl.exe が無い(WSL未導入のWindows)
        raise OpsUnavailableError(
            "wsl.exe が見つかりません。自動売買の正典は WSL 上にあるため、"
            "WSL が入っていない環境からは参照できません。"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise OpsError(f"正典への問い合わせが {timeout:.0f} 秒で時間切れになりました。") from exc
    return RunResult(
        returncode=completed.returncode,
        stdout=_decode(completed.stdout),
        stderr=_decode(completed.stderr),
    )


def _payload_source() -> str:
    """正典側で実行するスクリプトの中身。"""
    return Path(__file__).with_name("_payload.py").read_text(encoding="utf-8")


@dataclass(frozen=True)
class OpsTarget:
    """正典リポジトリの居場所。"""

    distro: str
    repo_path: str
    python: str

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> OpsTarget:
        """``.env`` の設定から作る。"""
        settings = settings or get_settings()
        return cls(
            distro=settings.ops_wsl_distro,
            repo_path=settings.ops_repo_path,
            python=settings.ops_repo_python,
        )

    @property
    def label(self) -> str:
        """画面やログに出す短い表記。"""
        return f"{self.distro}:{self.repo_path}"


class OpsBridge:
    """正典リポジトリのコードを呼ぶ窓口。"""

    def __init__(self, target: OpsTarget, runner: Runner | None = None) -> None:
        """実行先と、外部コマンドの実行方法(テストでは差し替える)を受け取る。"""
        self.target = target
        self._runner = runner or _wsl_runner

    # --- 低レベル ---------------------------------------------------------

    def argv(self, command: str, payload: dict[str, Any] | None = None) -> list[str]:
        """実際に起動するコマンド列。組み立てだけを切り出してテストできるように。"""
        if command not in COMMANDS:
            raise OpsError(f"許可されていないコマンドです: {command!r}")
        args = [command, json.dumps(payload or {}, ensure_ascii=False)]
        quoted = " ".join(shlex.quote(a) for a in args)
        script = (
            f"cd {shlex.quote(self.target.repo_path)} && "
            f"exec {shlex.quote(self.target.python)} - {quoted}"
        )
        return ["wsl.exe", "-d", self.target.distro, "--", "bash", "-c", script]

    def call(
        self,
        command: str,
        payload: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Reply:
        """コマンドを1回実行し、正典側のJSONを返す。"""
        argv = self.argv(command, payload)
        limit = timeout if timeout is not None else TIMEOUTS.get(command, DEFAULT_TIMEOUT)
        logger.debug("ops bridge: %s (timeout=%.0fs)", command, limit)
        result = self._runner(argv, _payload_source(), limit)

        text = result.stdout.strip()
        if not text:
            detail = result.stderr.strip()
            raise OpsUnavailableError(
                "正典リポジトリから応答がありませんでした。WSL が起動しているか、"
                f"`{self.target.label}` があるか確認してください。"
                + (f"\n{detail}" if detail else "")
            )
        try:
            envelope = json.loads(text)
        except json.JSONDecodeError as exc:
            raise OpsError(f"正典からの応答を読めませんでした(先頭200字): {text[:200]}") from exc
        if not envelope.get("ok"):
            raise OpsError(str(envelope.get("error", "原因不明のエラー")))
        return Reply(data=envelope.get("data"), stdout=envelope.get("stdout", ""))

    # --- 読み取り ---------------------------------------------------------

    def ping(self) -> dict[str, Any]:
        """正典がそこにあるかだけを確かめる(帳簿は読まない)。"""
        return self.call("ping").data

    def status(self) -> dict[str, Any]:
        """キルスイッチ・保有・翌営業日の注文・cron・ログ末尾。"""
        return self.call("status").data

    def trade_history(self) -> dict[str, Any]:
        """売買履歴(自動根拠つき)と手動メモ。"""
        return self.call("history").data

    def equity(self) -> dict[str, Any]:
        """3トラックの日次時価評価カーブ。"""
        return self.call("equity").data

    def notify_config(self) -> dict[str, Any]:
        """通知イベントのON/OFF。"""
        return self.call("notify-load").data

    def jobs(self) -> list[str]:
        """手動実行できるジョブ名。"""
        return list(self.call("jobs").data["jobs"])

    def dry_run(self, high_window: int, vol_mult: float, capital: float) -> dict[str, Any]:
        """日本株トラックAのドライラン(送信も書き込みもしない)。"""
        return self.call(
            "dry-run",
            {"high_window": high_window, "vol_mult": vol_mult, "capital": capital},
        ).data

    # --- 書き込み(正典が元々もっている3つだけ) ---------------------------

    def save_notes(self, notes: dict[str, str]) -> int:
        """売買メモを正典の trade_notes.json に保存する。帳簿は変えない。"""
        return int(self.call("save-notes", {"notes": notes}).data["saved"])

    def save_notify_config(self, events: dict[str, bool]) -> None:
        """通知イベントのON/OFFを正典に保存する。"""
        self.call("notify-save", {"events": events})

    def activate_kill_switch(self, reason: str, scope: str) -> list[str]:
        """キルスイッチを発動する。

        **解除する口はこのクラスに存在しない。** 原因を特定したうえで、ユーザー自身が
        正典側でファイルを消すのが唯一の解除手順(正典 CLAUDE.md の交渉不可ルール)。
        """
        return list(self.call("kill", {"reason": reason, "scope": scope}).data["created"])

    def run_job(self, name: str) -> str:
        """正典の MANUAL_JOBS にあるジョブを1本実行し、出力を返す。"""
        return str(self.call("run-job", {"name": name}).data["output"])


def get_bridge(settings: Settings | None = None, runner: Runner | None = None) -> OpsBridge:
    """設定から橋渡しを作る。"""
    return OpsBridge(OpsTarget.from_settings(settings), runner=runner)
