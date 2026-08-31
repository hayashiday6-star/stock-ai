"""正典(自動売買リポジトリ)の中で実行される橋渡しスクリプト。

このファイルは stock-ai 側からは **import されない**。中身がそのまま標準入力で
正典リポジトリの Python(``~/test/.venv/bin/python``)へ渡され、その作業ディレクトリで
実行される。したがってここで使えるのは標準ライブラリと正典の ``app_lib`` だけで、
``stock_ai`` のモジュールは一切使えない。

なぜ正典のコードを呼ぶのか: シグナル条件(60日高値・出来高1.5倍)や帳簿の読み方を
stock-ai 側に書き写すと、片方だけ直したときに「もっともらしいが違う値」が黙って
出る。実運用の判断に使う数字は、実運用が使っているコードそのものに出させる。

安全設計(正典 CLAUDE.md の交渉不可ルールをそのまま引き継ぐ):
  - 書き込むのは ``app_lib`` が元々書く3つだけ — 通知設定・売買メモ・
    キルスイッチの**発動**。解除するコマンドはここに存在しない。
  - 発注に触れるのは ``app_lib.MANUAL_JOBS`` に載っているものだけ(発注は
    ``--dry-run`` 固定)。任意のコマンド実行口は開けない。
  - 標準出力にはJSONを1個だけ書く。処理中の print は stderr に逃がす。
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import traceback

LOG_FILES = (
    "signals.log",
    "orders.log",
    "us_signals.log",
    "us_orders.log",
    "cron_daily_signals.log",
    "cron_place_orders.log",
    "cron_us_signals.log",
    "cron_us_orders.log",
    "cron_us_momentum.log",
)


def _app_lib():
    """正典の app_lib を遅延importする(WSLは動くが正典が壊れている場合を切り分けるため)。"""
    import app_lib

    return app_lib


def _series_points(series) -> list[list]:
    """pandas.Series を [[ISO日付, 値], ...] に落とす。"""
    points = []
    for key, value in series.items():
        stamp = key.date() if hasattr(key, "date") else key
        points.append([str(stamp), float(value)])
    return points


def _curve(result) -> dict | None:
    """app_lib の資産推移の戻り値をJSONにできる形へ。"""
    if result is None:
        return None
    out = {
        "label": result["label"],
        "currency": result["currency"],
        "capital": float(result["capital"]),
        "asof": result["asof"],
        "equity": float(result["equity"]),
        "points": _series_points(result["curve"]),
    }
    marks = result.get("marks")
    if marks is not None and len(marks) > 0:
        out["marks"] = _series_points(marks)
    return out


# --- コマンド ---------------------------------------------------------------


def cmd_ping(_args: dict) -> dict:
    """正典リポジトリが期待どおりそこにあるかだけを見る(app_libはimportしない)。"""
    return {
        "cwd": os.getcwd(),
        "python": sys.version.split()[0],
        "has_app_lib": os.path.exists("app_lib.py"),
        "has_positions": os.path.exists("positions.json"),
    }


def cmd_status(_args: dict) -> dict:
    """状態一式。launchd(Mac)ではなく cron(WSL)を見る点だけ app_lib と違う。"""
    app_lib = _app_lib()
    status = app_lib.get_status()
    status.pop("launchd_jobs", None)
    status["cron"] = _cron_lines()
    status["logs"] = {name: _tail(name) for name in LOG_FILES}
    return status


def _cron_lines() -> list[str]:
    """正典の crontab にある実ジョブ行(コメント・環境変数行は除く)。"""
    try:
        out = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, timeout=10, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ["(crontab 取得失敗)"]
    lines = [ln.strip() for ln in out.splitlines()]
    return [ln for ln in lines if ln and not ln.startswith("#") and "=" not in ln.split()[0]]


def _tail(name: str, lines: int = 12) -> str:
    try:
        with open(name, encoding="utf-8", errors="replace") as handle:
            return "".join(handle.readlines()[-lines:])
    except OSError:
        return "(なし)"


def cmd_history(_args: dict) -> dict:
    """売買履歴(自動根拠つき)と、手動メモ。どちらも帳簿は読むだけ。"""
    app_lib = _app_lib()
    return {"rows": app_lib.trade_history(), "notes": app_lib.load_trade_notes()}


def cmd_save_notes(args: dict) -> dict:
    """手動メモを trade_notes.json に保存する。帳簿には触れない。"""
    app_lib = _app_lib()
    notes = args.get("notes") or {}
    app_lib.save_trade_notes(notes)
    return {"saved": len([v for v in notes.values() if str(v).strip()])}


def cmd_equity(_args: dict) -> dict:
    """3トラックの日次時価評価カーブ。"""
    app_lib = _app_lib()
    return {
        "jp": _curve(app_lib.jp_equity_curve()),
        "us_momentum": _curve(app_lib.us_momentum_equity_curve()),
        "us_swing": _curve(app_lib.us_swing_equity_curve()),
    }


def cmd_notify_load(_args: dict) -> dict:
    """通知イベントのON/OFF。"""
    app_lib = _app_lib()
    return {"events": app_lib.load_notify_config(), "all_events": list(app_lib.NOTIFY_EVENTS)}


def cmd_notify_save(args: dict) -> dict:
    """通知イベントのON/OFFを保存する。"""
    app_lib = _app_lib()
    app_lib.save_notify_config(args.get("events") or {})
    return {"saved": True}


def cmd_dry_run(args: dict) -> dict:
    """日本株トラックAのドライラン。読むだけ・送らない・書かない。"""
    app_lib = _app_lib()
    panel, universe = app_lib.load_market()
    return app_lib.dry_run_jp(
        panel,
        universe,
        capital=float(args["capital"]),
        high_window=int(args["high_window"]),
        vol_mult=float(args["vol_mult"]),
    )


def cmd_kill(args: dict) -> dict:
    """キルスイッチを発動する(停止方向のみ。解除コマンドは存在しない)。"""
    # 検証を import より先にやる。理由や対象が不正なときに「app_lib が無い」といった
    # 無関係なエラーが返ると、発動できなかった本当の理由が読めなくなる。
    reason = str(args.get("reason", "")).strip()
    scope = args.get("scope")
    if not reason:
        raise ValueError("発動理由が空です。")
    if scope not in ("日本株", "米国株", "両方"):
        raise ValueError(f"対象が不正です: {scope!r}")
    return {"created": _app_lib().activate_kill_switch(reason, scope)}


def cmd_jobs(_args: dict) -> dict:
    """手動実行できるジョブ名(正典が定義しているものだけ)。"""
    app_lib = _app_lib()
    return {"jobs": list(app_lib.MANUAL_JOBS.keys())}


def cmd_run_job(args: dict) -> dict:
    """ジョブを1本実行する。名前は正典の MANUAL_JOBS にあるものに限る。"""
    app_lib = _app_lib()
    name = args.get("name")
    if name not in app_lib.MANUAL_JOBS:
        raise ValueError(f"未知のジョブです: {name!r}")
    return {"output": app_lib.run_manual_job(name)}


COMMANDS = {
    "ping": cmd_ping,
    "status": cmd_status,
    "history": cmd_history,
    "save-notes": cmd_save_notes,
    "equity": cmd_equity,
    "notify-load": cmd_notify_load,
    "notify-save": cmd_notify_save,
    "dry-run": cmd_dry_run,
    "kill": cmd_kill,
    "jobs": cmd_jobs,
    "run-job": cmd_run_job,
}


def main(argv: list[str]) -> int:
    """1コマンド実行し、JSONのエンベロープを標準出力に1個だけ書く。"""
    name = argv[1] if len(argv) > 1 else ""
    raw_args = argv[2] if len(argv) > 2 else "{}"
    handler = COMMANDS.get(name)
    if handler is None:
        json.dump(
            {"ok": False, "error": f"未知のコマンド: {name!r}"}, sys.stdout, ensure_ascii=False
        )
        return 2

    # 正典側のコードが進捗を print することがある。標準出力はJSON専用にしたいので、
    # 実行中の print はすべて捕まえて、結果と一緒に返す(呼び出し側で表示できる)。
    captured = io.StringIO()
    try:
        with contextlib.redirect_stdout(captured):
            data = handler(json.loads(raw_args))
    except Exception as exc:  # noqa: BLE001 - 呼び出し側に文字列で返すのが仕事
        json.dump(
            {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc()[-4000:],
                "stdout": captured.getvalue()[-4000:],
            },
            sys.stdout,
            ensure_ascii=False,
            default=str,
        )
        return 0
    json.dump(
        {"ok": True, "data": data, "stdout": captured.getvalue()[-4000:]},
        sys.stdout,
        ensure_ascii=False,
        default=str,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
