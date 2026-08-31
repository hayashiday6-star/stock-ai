"""The 自動売買 運用 screen renders, driven by a fake canonical repository.

These run the real Streamlit script through ``AppTest`` - no browser, no WSL.
The point is not to check wording but to catch the failures that only appear at
render time: a widget called with an argument this Streamlit does not take, a
frame shaped differently from what the chart expects, or an unreachable
repository rendering as "no positions" instead of as an error.
"""

from __future__ import annotations

import itertools
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

from stock_ai.core.exceptions import OpsUnavailableError
from stock_ai.dashboard import ops_page
from stock_ai.ops.bridge import OpsTarget

_labels = itertools.count()

STATUS: dict[str, Any] = {
    "kill_switches": [],
    "cron": ["30 7 * * 1-5 bash $T/run_us_signals.sh"],
    "risk": {"broker": "paper", "capital": 20_000_000},
    "jp_positions": [
        {
            "code": "72720",
            "name": "ヤマハ発動機",
            "shares": 1300,
            "status": "pending",
            "signal_date": "2026-08-04",
            "exec_date": "2026-08-05",
            "entry_px": 1234.0,
        }
    ],
    "orders_next": {"exec_date": "2026-09-01", "sells": [], "buys": [{"code": "72720"}]},
    "us_positions": [
        {"code": "US.AAPL", "shares": 10, "status": "filled", "entry_date": "2026-07-01"}
    ],
    "us_momentum": {"positions": [{"code": "MSFT"}]},
    "logs": {"orders.log": "2026-08-28 ok\n"},
}

CURVES: dict[str, Any] = {
    "jp": {
        "label": "日本株トラックA",
        "currency": "円",
        "capital": 20_000_000.0,
        "asof": "2026-08-28",
        "equity": 19_535_630.0,
        "points": [["2026-08-27", 19_600_000.0], ["2026-08-28", 19_535_630.0]],
    },
    "us_momentum": {
        "label": "米国株モメンタム",
        "currency": "ドル",
        "capital": 100_000.0,
        "asof": "2026-08-27",
        "equity": 97_791.0,
        "points": [["2026-08-26", 98_000.0], ["2026-08-27", 97_791.0]],
        "marks": [["2026-08-26", 98_010.0], ["2026-08-27", 97_795.0]],
    },
    "us_swing": None,
}

HISTORY: dict[str, Any] = {
    "rows": [
        {
            "key": "JP|72720|2026-08-05|買",
            "date": "2026-08-05",
            "track": "日本株A",
            "code": "72720",
            "name": "ヤマハ発動機",
            "side": "買",
            "shares": 1300,
            "price": "1,234円",
            "status": "pending",
            "rationale": "60日高値ブレイク",
        }
    ],
    "notes": {},
}


class FakeBridge:
    """A canonical repository that answers instantly, or refuses."""

    def __init__(self, unavailable: bool = False) -> None:
        self.target = OpsTarget(
            distro="Ubuntu-24.04",
            repo_path=f"/home/u/test{next(_labels)}",  # キャッシュを試験ごとに分ける
            python=".venv/bin/python",
        )
        self.unavailable = unavailable
        self.saved_notes: dict[str, str] | None = None
        self.killed: tuple[str, str] | None = None

    def _guard(self) -> None:
        if self.unavailable:
            raise OpsUnavailableError("WSL が起動していません。")

    def ping(self) -> dict[str, Any]:
        self._guard()
        return {"cwd": self.target.repo_path}

    def status(self) -> dict[str, Any]:
        self._guard()
        return STATUS

    def equity(self) -> dict[str, Any]:
        self._guard()
        return CURVES

    def trade_history(self) -> dict[str, Any]:
        self._guard()
        return HISTORY

    def notify_config(self) -> dict[str, Any]:
        self._guard()
        return {"events": {"シグナル": True, "発注": False}, "all_events": ["シグナル", "発注"]}

    def jobs(self) -> list[str]:
        self._guard()
        return ["照合のみ", "発注チェック(ドライラン・発注しない)"]

    def save_notes(self, notes: dict[str, str]) -> int:
        self.saved_notes = notes
        return len(notes)

    def activate_kill_switch(self, reason: str, scope: str) -> list[str]:
        self.killed = (reason, scope)
        return ["/home/u/test/kill_switch.json"]


#: 実行中の偽リポジトリ。``AppTest`` は渡したスクリプトを別ファイルとして実行するので、
#: 引数では渡せない。スクリプト側からこのモジュールを import して受け取る。
CURRENT_BRIDGE: FakeBridge | None = None

_SCRIPT = """
import tests.test_ops_page as fixture
from stock_ai.dashboard import ops_page

ops_page.render(fixture.CURRENT_BRIDGE)
"""


def _run(bridge: FakeBridge, view: str | None = None) -> AppTest:
    global CURRENT_BRIDGE
    CURRENT_BRIDGE = bridge
    app = AppTest.from_string(_SCRIPT, default_timeout=30)
    app.run()
    if view is not None:
        app.segmented_control[0].set_value(view).run()
    return app


def _text(app: AppTest) -> str:
    return " ".join(str(element.value) for element in app.markdown) + " ".join(
        str(element.value) for element in app.caption
    )


@pytest.mark.parametrize("view", ops_page.VIEWS)
def test_every_view_renders(view: str) -> None:
    app = _run(FakeBridge(), view)
    assert not app.exception


def test_status_view_shows_the_broker_and_the_kill_switch_state() -> None:
    app = _run(FakeBridge(), "状態")
    body = _text(app)
    assert "paper" in body
    assert "なし" in body  # キルスイッチ
    assert any("ヤマハ発動機" in str(frame.value.to_dict()) for frame in app.dataframe)


def test_a_tripped_kill_switch_is_impossible_to_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(STATUS, "kill_switches", ["kill_switch.json"])
    app = _run(FakeBridge(), "状態")
    assert any("解除はこの画面からはできません" in str(e.value) for e in app.error)


def test_equity_view_charts_each_track_that_has_trades() -> None:
    app = _run(FakeBridge(), "資産推移")
    assert not app.exception
    assert len(app.get("vega_lite_chart")) == 2  # jp と us_momentum。us_swing は None
    assert any("約定済みポジションがまだありません" in str(e.value) for e in app.markdown)


def test_history_view_saves_notes_without_touching_the_ledger() -> None:
    bridge = FakeBridge()
    app = _run(bridge, "売買履歴")
    app.button(key="ops_save_notes").click().run()
    # 空のメモも売買キーごと送る。消したメモを消えたままにするには、
    # 「無かったこと」ではなく「空になったこと」を正典に伝える必要がある。
    assert bridge.saved_notes == {"JP|72720|2026-08-05|買": ""}
    assert any("帳簿は変更なし" in str(e.value) for e in app.success)


def test_kill_switch_needs_a_reason_before_the_button_works() -> None:
    bridge = FakeBridge()
    app = _run(bridge, "運用操作")
    assert app.button(key="ops_kill").disabled  # 理由が空のうちは押せない
    app.text_input(key="ops_kill_reason").set_value("帳簿と口座の残高が合わない").run()
    app.button(key="ops_kill").click().run()
    assert bridge.killed == ("帳簿と口座の残高が合わない", "日本株")
    assert any("発動しました" in str(e.value) for e in app.error)


def test_an_unreachable_repository_is_an_error_not_an_empty_dashboard() -> None:
    app = _run(FakeBridge(unavailable=True))
    assert any("参照できません" in str(e.value) for e in app.error)
    assert not app.dataframe
    assert not app.get("vega_lite_chart")
