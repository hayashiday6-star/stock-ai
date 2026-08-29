"""Tests for the moomoo OpenD check (no gateway, no network).

The whole point of :mod:`stock_ai.broker.moomoo` is what it does when OpenD is
*not* cooperating, so almost everything here is a fake gateway refusing in a
different way. The one thing the fakes cannot cover - that a real closed port
returns quickly instead of hanging - is covered with a real socket.
"""

from __future__ import annotations

import socket
import sys
import threading
from collections.abc import Iterator

import pandas as pd
import pytest
from typer.testing import CliRunner

from stock_ai import cli
from stock_ai.broker.moomoo import (
    Account,
    MoomooConfig,
    StageStatus,
    diagnose,
    port_is_open,
)
from stock_ai.core.exceptions import BrokerError

runner = CliRunner()

RET_OK = 0
RET_ERROR = -1

ACC_COLUMNS = [
    "acc_id",
    "trd_env",
    "acc_type",
    "uni_card_num",
    "card_num",
    "security_firm",
    "sim_acc_type",
    "competition_acc_name",
    "trdmarket_auth",
    "acc_status",
    "acc_role",
    "jp_acc_type",
]


def _acc_frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=ACC_COLUMNS)


def _account_row(acc_id: str = "12345678", trd_env: str = "SIMULATE") -> dict:
    return {
        "acc_id": acc_id,
        "trd_env": trd_env,
        "acc_type": "CASH",
        "uni_card_num": "",
        "card_num": "",
        "security_firm": "FUTUJP",
        "sim_acc_type": "",
        "competition_acc_name": "",
        "trdmarket_auth": ["JP", "US"],
        "acc_status": "ACTIVE",
        "acc_role": "",
        "jp_acc_type": "GENERAL",
    }


class FakeQuoteContext:
    """Stands in for ``OpenQuoteContext``, answering ``get_global_state`` only."""

    def __init__(self, state: dict, ret: int = RET_OK) -> None:
        self._state = state
        self._ret = ret
        self.closed = False

    def get_global_state(self) -> tuple[int, object]:
        return self._ret, self._state if self._ret == RET_OK else "refused"

    def close(self, *args: object, **kwargs: object) -> None:
        self.closed = True


class FakeTradeContext:
    """Stands in for ``OpenSecTradeContext``: accounts, balances, unlocking."""

    def __init__(
        self,
        accounts: pd.DataFrame,
        *,
        acc_ret: int = RET_OK,
        info_ret: int = RET_OK,
        unlock_ret: int = RET_OK,
    ) -> None:
        self._accounts = accounts
        self._acc_ret = acc_ret
        self._info_ret = info_ret
        self._unlock_ret = unlock_ret
        self.unlock_calls: list[tuple[str | None, bool]] = []
        self.closed = False

    def get_acc_list(self) -> tuple[int, object]:
        if self._acc_ret != RET_OK:
            return self._acc_ret, "get_acc_list refused"
        return RET_OK, self._accounts

    def accinfo_query(self, **kwargs: object) -> tuple[int, object]:
        if self._info_ret != RET_OK:
            return self._info_ret, "accinfo refused"
        return RET_OK, pd.DataFrame(
            [
                {
                    "currency": "JPY",
                    "total_assets": 1_000_000.0,
                    "cash": 400_000.0,
                    "market_val": 600_000.0,
                }
            ]
        )

    def unlock_trade(
        self, password: str | None = None, password_md5: str | None = None, is_unlock: bool = True
    ) -> tuple[int, object]:
        self.unlock_calls.append((password, is_unlock))
        if self._unlock_ret != RET_OK:
            return self._unlock_ret, "wrong PIN"
        return RET_OK, None

    def close(self, *args: object, **kwargs: object) -> None:
        self.closed = True


class FakeSysConfig:
    """The one moomoo global the check touches."""

    @classmethod
    def set_all_thread_daemon(cls, value: bool) -> None:
        return None


class FakeMoomoo:
    """A stand-in for the ``moomoo`` package, injected through ``sys.modules``."""

    __version__ = "10.10.7008"
    RET_OK = RET_OK
    SysConfig = FakeSysConfig

    def __init__(self, quote: FakeQuoteContext, trade: FakeTradeContext | None) -> None:
        self._quote = quote
        self._trade = trade

    def OpenQuoteContext(self, **kwargs: object) -> FakeQuoteContext:  # noqa: N802
        return self._quote

    def OpenSecTradeContext(self, **kwargs: object) -> FakeTradeContext:  # noqa: N802
        assert self._trade is not None
        return self._trade


@pytest.fixture
def open_port() -> Iterator[int]:
    """A port with a real listener, so the socket probe genuinely succeeds."""
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        yield server.getsockname()[1]


@pytest.fixture
def closed_port() -> int:
    """A port that was bound and released, so nothing is listening on it."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _install(monkeypatch: pytest.MonkeyPatch, fake: FakeMoomoo) -> None:
    monkeypatch.setitem(sys.modules, "moomoo", fake)


def _stage(result: object, name: str):
    return next(s for s in result.stages if s.name == name)


# --- configuration ------------------------------------------------------


def test_config_rejects_unknown_security_firm() -> None:
    with pytest.raises(BrokerError, match="MOOMOO_SECURITY_FIRM"):
        MoomooConfig(security_firm="NOMURA")


def test_config_rejects_unknown_market_and_env() -> None:
    with pytest.raises(BrokerError, match="MOOMOO_TRD_MARKET"):
        MoomooConfig(trd_market="TSE")
    with pytest.raises(BrokerError, match="MOOMOO_TRD_ENV"):
        MoomooConfig(trd_env="PAPER")


def test_config_rejects_impossible_port() -> None:
    with pytest.raises(BrokerError, match="valid TCP port"):
        MoomooConfig(port=0)


def test_jp_account_is_priced_in_yen() -> None:
    assert MoomooConfig(trd_market="JP").currency == "JPY"
    assert MoomooConfig(trd_market="US").currency == "USD"


def test_account_id_is_masked() -> None:
    assert Account("81234567", "REAL", "CASH", "FUTUJP", ("JP",), "ACTIVE").masked_id == "****4567"


# --- the port probe -----------------------------------------------------


def test_port_probe_sees_a_listener(open_port: int) -> None:
    assert port_is_open("127.0.0.1", open_port, timeout=2.0) is True


def test_port_probe_returns_quickly_on_a_closed_port(closed_port: int) -> None:
    assert port_is_open("127.0.0.1", closed_port, timeout=2.0) is False


# --- the chain ----------------------------------------------------------


def test_missing_client_names_the_install_command(
    monkeypatch: pytest.MonkeyPatch, closed_port: int
) -> None:
    """A missing client must not be reported as an unreachable gateway."""
    monkeypatch.setitem(sys.modules, "moomoo", None)  # import moomoo -> ImportError
    result = diagnose(MoomooConfig(port=closed_port))

    stage = _stage(result, "moomoo-api installed")
    assert stage.status is StageStatus.FAILED
    assert "uv sync" in stage.hint
    assert len(result.stages) == 1


def test_opend_not_running_stops_before_the_client_can_hang(
    monkeypatch: pytest.MonkeyPatch, closed_port: int
) -> None:
    _install(monkeypatch, FakeMoomoo(FakeQuoteContext({}), None))
    result = diagnose(MoomooConfig(port=closed_port), timeout=1.0)

    assert not result.ok
    failure = result.first_failure
    assert failure is not None
    assert failure.name == "OpenD reachable"
    assert "Start moomoo OpenD" in failure.hint


def test_running_but_nobody_logged_in(monkeypatch: pytest.MonkeyPatch, open_port: int) -> None:
    quote = FakeQuoteContext({"qot_logined": False, "trd_logined": False})
    _install(monkeypatch, FakeMoomoo(quote, None))

    result = diagnose(MoomooConfig(port=open_port))

    failure = result.first_failure
    assert failure is not None
    assert failure.name == "OpenD logged in"
    assert "verification code" in failure.hint


def test_quotes_logged_in_but_trading_is_not_is_flagged_not_hidden(
    monkeypatch: pytest.MonkeyPatch, open_port: int
) -> None:
    quote = FakeQuoteContext({"qot_logined": True, "trd_logined": False})
    trade = FakeTradeContext(_acc_frame([_account_row()]))
    _install(monkeypatch, FakeMoomoo(quote, trade))

    result = diagnose(MoomooConfig(port=open_port))

    stage = _stage(result, "OpenD logged in")
    assert stage.status is StageStatus.OK
    assert "trading: NO" in stage.detail
    assert "log in fully" in stage.hint


def test_empty_account_list_is_reported_as_a_mismatch_not_a_success(
    monkeypatch: pytest.MonkeyPatch, open_port: int
) -> None:
    """The login worked and the list came back empty - the confusing case."""
    quote = FakeQuoteContext({"qot_logined": True, "trd_logined": True})
    trade = FakeTradeContext(_acc_frame([]))
    _install(monkeypatch, FakeMoomoo(quote, trade))

    result = diagnose(MoomooConfig(port=open_port))

    failure = result.first_failure
    assert failure is not None
    assert failure.name == "account visible"
    assert "no account at all" in failure.detail


def test_asking_for_real_when_only_paper_exists_says_what_was_found(
    monkeypatch: pytest.MonkeyPatch, open_port: int
) -> None:
    quote = FakeQuoteContext({"qot_logined": True, "trd_logined": True})
    trade = FakeTradeContext(_acc_frame([_account_row(trd_env="SIMULATE")]))
    _install(monkeypatch, FakeMoomoo(quote, trade))

    result = diagnose(MoomooConfig(port=open_port, trd_env="REAL"))

    failure = result.first_failure
    assert failure is not None
    assert failure.name == "account visible"
    assert "SIMULATE" in failure.detail
    # The advice must be "switch to the account you have", not "your entity or
    # market is wrong" - the settings were right, and re-checking them is a
    # wasted trip.
    assert "MOOMOO_TRD_ENV=SIMULATE" in failure.hint
    assert "entity" not in failure.hint


def test_a_paperless_login_is_told_to_switch_not_to_recheck_its_settings(
    monkeypatch: pytest.MonkeyPatch, open_port: int
) -> None:
    """The real shape of a moomoo Japan account: REAL only, no paper side.

    The SIMULATE default lands here on a healthy setup, so the message has to
    say "use the account you have" rather than "your entity is wrong".
    """
    quote = FakeQuoteContext({"qot_logined": True, "trd_logined": True})
    trade = FakeTradeContext(_acc_frame([_account_row(trd_env="REAL")]))
    _install(monkeypatch, FakeMoomoo(quote, trade))

    result = diagnose(MoomooConfig(port=open_port, trd_env="SIMULATE"))

    failure = result.first_failure
    assert failure is not None
    assert failure.name == "account visible"
    assert "it has REAL instead" in failure.detail
    assert "MOOMOO_TRD_ENV=REAL" in failure.hint
    assert "choose 2" in failure.hint
    assert "never places an order" in failure.hint


def test_paper_account_passes_and_skips_the_unlock(
    monkeypatch: pytest.MonkeyPatch, open_port: int
) -> None:
    quote = FakeQuoteContext({"qot_logined": True, "trd_logined": True})
    trade = FakeTradeContext(_acc_frame([_account_row()]))
    _install(monkeypatch, FakeMoomoo(quote, trade))

    result = diagnose(MoomooConfig(port=open_port))

    assert result.ok
    assert _stage(result, "account answers").status is StageStatus.OK
    assert result.account_summary["currency"] == "JPY"
    assert _stage(result, "trading unlocked").status is StageStatus.SKIPPED
    assert trade.unlock_calls == []
    assert trade.closed and quote.closed


def test_live_account_without_a_pin_is_left_locked(
    monkeypatch: pytest.MonkeyPatch, open_port: int
) -> None:
    quote = FakeQuoteContext({"qot_logined": True, "trd_logined": True})
    trade = FakeTradeContext(_acc_frame([_account_row(trd_env="REAL")]))
    _install(monkeypatch, FakeMoomoo(quote, trade))

    result = diagnose(MoomooConfig(port=open_port, trd_env="REAL"))

    assert result.ok
    assert _stage(result, "trading unlocked").status is StageStatus.SKIPPED
    assert trade.unlock_calls == []


def test_a_tested_pin_is_locked_again_immediately(
    monkeypatch: pytest.MonkeyPatch, open_port: int
) -> None:
    """Proving the PIN must not leave a live account unlocked behind us."""
    quote = FakeQuoteContext({"qot_logined": True, "trd_logined": True})
    trade = FakeTradeContext(_acc_frame([_account_row(trd_env="REAL")]))
    _install(monkeypatch, FakeMoomoo(quote, trade))

    result = diagnose(MoomooConfig(port=open_port, trd_env="REAL"), unlock_password="123456")

    assert _stage(result, "trading unlocked").status is StageStatus.OK
    assert trade.unlock_calls == [("123456", True), ("123456", False)]


def test_a_refused_pin_names_the_right_password(
    monkeypatch: pytest.MonkeyPatch, open_port: int
) -> None:
    quote = FakeQuoteContext({"qot_logined": True, "trd_logined": True})
    trade = FakeTradeContext(_acc_frame([_account_row(trd_env="REAL")]), unlock_ret=RET_ERROR)
    _install(monkeypatch, FakeMoomoo(quote, trade))

    result = diagnose(MoomooConfig(port=open_port, trd_env="REAL"), unlock_password="000000")

    failure = result.first_failure
    assert failure is not None
    assert failure.name == "trading unlocked"
    assert "取引暗証番号" in failure.hint


def test_a_hanging_gateway_is_a_finding_not_a_hang(
    monkeypatch: pytest.MonkeyPatch, open_port: int
) -> None:
    """Something listening on the port that never answers must still report."""

    threading_event = threading.Event()

    class Hanging(FakeMoomoo):
        def OpenQuoteContext(self, **kwargs: object) -> FakeQuoteContext:  # noqa: N802
            threading_event.wait()  # never set
            raise AssertionError("unreachable")

    _install(monkeypatch, Hanging(FakeQuoteContext({}), None))

    result = diagnose(MoomooConfig(port=open_port), handshake_timeout=0.5)

    failure = result.first_failure
    assert failure is not None
    assert failure.name == "OpenD logged in"
    assert "not answering" in failure.hint


def test_a_refused_state_query_is_not_read_as_a_logged_out_gateway(
    monkeypatch: pytest.MonkeyPatch, open_port: int
) -> None:
    quote = FakeQuoteContext({}, ret=RET_ERROR)
    _install(monkeypatch, FakeMoomoo(quote, None))

    result = diagnose(MoomooConfig(port=open_port))

    failure = result.first_failure
    assert failure is not None
    assert failure.name == "OpenD logged in"
    assert "refused the state query" in failure.detail


def test_a_refused_account_list_names_the_two_settings_that_cause_it(
    monkeypatch: pytest.MonkeyPatch, open_port: int
) -> None:
    quote = FakeQuoteContext({"qot_logined": True, "trd_logined": True})
    trade = FakeTradeContext(_acc_frame([]), acc_ret=RET_ERROR)
    _install(monkeypatch, FakeMoomoo(quote, trade))

    result = diagnose(MoomooConfig(port=open_port))

    failure = result.first_failure
    assert failure is not None
    assert failure.name == "account visible"
    assert "MOOMOO_SECURITY_FIRM" in failure.hint
    assert "MOOMOO_TRD_MARKET" in failure.hint


def test_an_account_that_is_listed_but_will_not_answer_is_a_separate_finding(
    monkeypatch: pytest.MonkeyPatch, open_port: int
) -> None:
    """Seeing the account and reaching it are different things, reported apart."""
    quote = FakeQuoteContext({"qot_logined": True, "trd_logined": True})
    trade = FakeTradeContext(_acc_frame([_account_row()]), info_ret=RET_ERROR)
    _install(monkeypatch, FakeMoomoo(quote, trade))

    result = diagnose(MoomooConfig(port=open_port))

    assert _stage(result, "account visible").status is StageStatus.OK
    failure = result.first_failure
    assert failure is not None
    assert failure.name == "account answers"
    assert result.account_summary == {}


def test_a_dropped_trading_session_times_out_rather_than_hanging(
    monkeypatch: pytest.MonkeyPatch, open_port: int
) -> None:
    """OpenD can hold quotes open after trading has dropped; that must not hang."""
    forever = threading.Event()

    class HangingTrade(FakeMoomoo):
        def OpenSecTradeContext(self, **kwargs: object) -> FakeTradeContext:  # noqa: N802
            forever.wait()
            raise AssertionError("unreachable")

    quote = FakeQuoteContext({"qot_logined": True, "trd_logined": True})
    _install(monkeypatch, HangingTrade(quote, None))

    result = diagnose(MoomooConfig(port=open_port), handshake_timeout=0.5)

    failure = result.first_failure
    assert failure is not None
    assert failure.name == "account visible"
    assert "Restart" in failure.hint


# --- the CLI ------------------------------------------------------------


def test_cli_reports_a_missing_gateway_and_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, closed_port: int
) -> None:
    _install(monkeypatch, FakeMoomoo(FakeQuoteContext({}), None))
    result = runner.invoke(cli.app, ["moomoo-check", "--port", str(closed_port)])

    assert result.exit_code == 1
    assert "OpenD reachable" in result.output
    assert "nothing is listening" in result.output


def test_cli_rejects_an_unknown_entity_before_connecting() -> None:
    result = runner.invoke(cli.app, ["moomoo-check", "--firm", "NOMURA"])

    assert result.exit_code == 2
    assert "MOOMOO_SECURITY_FIRM" in result.output


def test_cli_masks_account_numbers_and_hides_balances_by_default(
    monkeypatch: pytest.MonkeyPatch, open_port: int
) -> None:
    quote = FakeQuoteContext({"qot_logined": True, "trd_logined": True})
    trade = FakeTradeContext(_acc_frame([_account_row(acc_id="81234567")]))
    _install(monkeypatch, FakeMoomoo(quote, trade))

    result = runner.invoke(cli.app, ["moomoo-check", "--port", str(open_port)])

    assert result.exit_code == 0
    assert "81234567" not in result.output
    assert "****4567" in result.output
    assert "1000000" not in result.output.replace(",", "")


def test_cli_shows_balances_when_asked(monkeypatch: pytest.MonkeyPatch, open_port: int) -> None:
    quote = FakeQuoteContext({"qot_logined": True, "trd_logined": True})
    trade = FakeTradeContext(_acc_frame([_account_row()]))
    _install(monkeypatch, FakeMoomoo(quote, trade))

    result = runner.invoke(cli.app, ["moomoo-check", "--port", str(open_port), "--show-assets"])

    assert result.exit_code == 0
    assert "1000000" in result.output.replace(",", "").replace(".0", "")
