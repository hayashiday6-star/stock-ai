"""moomoo OpenD connectivity and account authentication, checked one link at a time.

moomoo's API is not an HTTP endpoint with a key in a header. It is a local
gateway - OpenD - that you install on your own machine, log into with your
moomoo securities account, and then talk to over a socket on ``127.0.0.1``.
Everything this project could do with a moomoo account therefore depends on a
chain of separate things being true at once, on a machine this code cannot see
until it is already running there.

When a link in that chain is missing, the Python side reports it in the least
useful way available: it blocks. Observed directly while building this -
constructing ``OpenQuoteContext`` against a port with nothing listening does
not raise. It retries in a background thread and the call never returns. So
"OpenD is not installed", "OpenD is running but nobody logged in", and "the
account has no permission for this market" all present identically at the
prompt: as nothing happening.

That is what this module exists to prevent. It walks the chain in order, stops
at the first break, and names *that link* rather than the symptom:

1. ``moomoo-api`` is importable
2. something is listening on the OpenD port (a plain socket, with a timeout,
   deliberately *before* the library gets a chance to hang)
3. OpenD is logged in - quotes and trading are reported separately, because
   they fail separately
4. the account is visible through it (``get_acc_list``)
5. the account answers a query (``accinfo_query``) - the only step that proves
   authentication reached the account rather than just the gateway
6. real-money trading is unlocked (``unlock_trade``), and only when asked

Nothing here places an order. Authentication and execution are deliberately
different steps: see :mod:`stock_ai.broker.ibkr` for the same stance about
Interactive Brokers, and use :class:`~stock_ai.broker.paper.PaperBroker` for
dry runs.
"""

from __future__ import annotations

import logging
import socket
import threading
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from stock_ai.config.constants import OPEND_HOST, OPEND_PORT
from stock_ai.core.exceptions import BrokerError
from stock_ai.data.markets import JAPAN, market_for_symbol

#: Re-exported so callers can talk about the gateway without reaching into
#: the config package for two integers.
DEFAULT_HOST = OPEND_HOST
DEFAULT_PORT = OPEND_PORT

#: Which moomoo entity holds the account. A Japanese moomoo証券 account is
#: ``FUTUJP``; the same login can hold accounts at more than one entity, and
#: asking the wrong one returns an empty account list rather than an error.
SECURITY_FIRMS = (
    "FUTUJP",
    "FUTUINC",
    "FUTUSECURITIES",
    "FUTUSG",
    "FUTUAU",
    "FUTUCA",
    "FUTUMY",
)

#: Markets an account can be filtered to. moomoo証券 (JP) supports JP and US.
TRD_MARKETS = ("JP", "US", "HK", "SG", "AU", "CA", "MY", "CN")

#: ``SIMULATE`` is the paper account. It is the default here for the same
#: reason PaperBroker is the default elsewhere: a check that reaches for the
#: real account by default is one keystroke away from being a live session.
TRD_ENVS = ("SIMULATE", "REAL")

#: Reporting currency per market, so a JP account is not asked for its balance
#: in the library's HKD default and answered with zeros.
_MARKET_CURRENCY = {
    "JP": "JPY",
    "US": "USD",
    "HK": "HKD",
    "SG": "SGD",
    "AU": "AUD",
    "CA": "CAD",
    "MY": "MYR",
    "CN": "CNH",
}

#: A plain ``uv sync`` is the right instruction rather than the extra's name:
#: ``moomoo`` is declared as an extra but pulled in through the default
#: ``runtime`` group, because an extra installed on its own does not survive the
#: next ``uv run`` - and every .bat launcher here goes through ``uv run``.
#: Granularities ``get_capital_flow`` accepts. ``INTRADAY`` ignores the date
#: range entirely and returns today's minute-by-minute flow; the others take a
#: range of at most 365 days.
PERIOD_TYPES = ("INTRADAY", "DAY", "WEEK", "MONTH")

#: Prefixes moomoo puts in front of a code. A symbol that already carries one is
#: passed through untouched - guessing a market for ``JP.9842`` would read the
#: prefix itself as the ticker and send ``US.JP`` to the gateway.
_MOOMOO_PREFIXES = tuple(f"{m}." for m in TRD_MARKETS)

_INSTALL_HINT = "moomoo-api is not installed. Run: uv sync   (outside uv: pip install moomoo-api)"


class StageStatus(StrEnum):
    """How one link in the chain came out."""

    OK = "ok"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class Stage:
    """One checked link, with the next step to take when it broke.

    ``hint`` carries the action, not the diagnosis. A failed check whose output
    is only "failed" sends the reader back to a search engine; naming the
    command or the screen to open is the whole value of running this.
    """

    name: str
    status: StageStatus
    detail: str
    hint: str = ""


@dataclass(frozen=True)
class MoomooConfig:
    """Where OpenD is and which account to look for behind it."""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    security_firm: str = "FUTUJP"
    trd_market: str = "JP"
    trd_env: str = "SIMULATE"

    def __post_init__(self) -> None:
        """Reject values the gateway would answer with silence instead of an error."""
        if self.security_firm not in SECURITY_FIRMS:
            raise BrokerError(
                f"Unknown MOOMOO_SECURITY_FIRM {self.security_firm!r}. "
                f"Expected one of: {', '.join(SECURITY_FIRMS)}."
            )
        if self.trd_market not in TRD_MARKETS:
            raise BrokerError(
                f"Unknown MOOMOO_TRD_MARKET {self.trd_market!r}. "
                f"Expected one of: {', '.join(TRD_MARKETS)}."
            )
        if self.trd_env not in TRD_ENVS:
            raise BrokerError(
                f"Unknown MOOMOO_TRD_ENV {self.trd_env!r}. Expected one of: {', '.join(TRD_ENVS)}."
            )
        if not 1 <= self.port <= 65535:
            raise BrokerError(f"MOOMOO_OPEND_PORT {self.port} is not a valid TCP port.")

    @property
    def currency(self) -> str:
        """Reporting currency for this market."""
        return _MARKET_CURRENCY.get(self.trd_market, "USD")

    @property
    def is_real(self) -> bool:
        """Whether this configuration points at the live-money account."""
        return self.trd_env == "REAL"


@dataclass(frozen=True)
class Account:
    """One account OpenD is willing to show us, as returned by ``get_acc_list``."""

    acc_id: str
    trd_env: str
    acc_type: str
    security_firm: str
    markets: tuple[str, ...]
    status: str

    @property
    def masked_id(self) -> str:
        """The account number with all but the last four digits hidden.

        The check writes a transcript people paste when asking for help, and a
        full brokerage account number in a pasted log is exactly the kind of
        thing that is impossible to take back.
        """
        text = str(self.acc_id)
        return text if len(text) <= 4 else f"****{text[-4:]}"


@dataclass
class Diagnosis:
    """The whole run: every link checked, and what was found behind them."""

    stages: list[Stage] = field(default_factory=list)
    accounts: list[Account] = field(default_factory=list)
    global_state: dict[str, Any] = field(default_factory=dict)
    account_summary: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """Whether every link that was actually checked held."""
        return all(s.status is not StageStatus.FAILED for s in self.stages)

    @property
    def first_failure(self) -> Stage | None:
        """The link that broke, which is the only one worth acting on."""
        return next((s for s in self.stages if s.status is StageStatus.FAILED), None)


def port_is_open(host: str, port: int, timeout: float = 2.0) -> bool:
    """Whether anything is accepting TCP connections at ``host:port``.

    Deliberately a bare socket rather than the moomoo client. The client's own
    failure mode for a closed port is to retry forever in a background thread,
    which turns a five-second diagnosis into a hang; this answers in ``timeout``
    seconds and lets the caller report "OpenD is not running" as a fact.
    """
    with suppress(OSError), socket.create_connection((host, port), timeout=timeout):
        return True
    return False


class GatewayTimeoutError(BrokerError):
    """A call to OpenD did not come back inside its deadline."""


def _with_deadline(label: str, func: Any, seconds: float) -> Any:
    """Run ``func`` in a daemon thread and give up after ``seconds``.

    The port probe rules out the common case - nothing listening at all - but
    it cannot rule out the awkward one: something *is* listening on 11111 and
    it is not OpenD, or it is an OpenD that never finishes its handshake. The
    client has no connect timeout of its own, so without a deadline here that
    case is a hang again, just one step later.

    The thread is a daemon and is abandoned rather than killed, which is safe
    for exactly the calls this wraps: they connect and read, they do not place
    orders.
    """
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["value"] = func()
        except BaseException as exc:  # reported to the caller, not swallowed
            box["error"] = exc

    thread = threading.Thread(target=target, name=f"moomoo-{label}", daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        raise GatewayTimeoutError(f"{label} did not respond within {seconds:.0f}s")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _quiet_moomoo_console() -> None:
    """Stop the moomoo client narrating over the report.

    The library logs connection chatter to its own console logger at INFO. It
    is useful when something is wrong and pure noise in the middle of a table,
    so the table is what stays.
    """
    logging.getLogger("FTConsoleLog").setLevel(logging.WARNING)


def _account_rows(frame: Any) -> list[Account]:
    """Turn the ``get_acc_list`` DataFrame into plain records."""
    accounts: list[Account] = []
    for row in frame.to_dict("records"):
        markets = row.get("trdmarket_auth") or []
        accounts.append(
            Account(
                acc_id=str(row.get("acc_id", "")),
                trd_env=str(row.get("trd_env", "")),
                acc_type=str(row.get("acc_type", "")),
                security_firm=str(row.get("security_firm", "")),
                markets=tuple(str(m) for m in markets),
                status=str(row.get("acc_status", "")),
            )
        )
    return accounts


def diagnose(
    config: MoomooConfig,
    *,
    unlock_password: str | None = None,
    timeout: float = 2.0,
    handshake_timeout: float = 20.0,
) -> Diagnosis:
    """Walk the OpenD chain and report where it breaks.

    Never raises for a broken link - a diagnosis that stops at an exception can
    only report one thing, and the point here is to say which of six things it
    is. The only errors that escape are configuration ones raised before any
    connection is attempted.

    Args:
        config: Where OpenD is, and which account to look for behind it.
        unlock_password: The 6-digit moomoo trading PIN (取引暗証番号). Only
            used when ``config`` names the ``REAL`` account; a paper account
            needs no unlocking and asking anyway just fails confusingly.
        timeout: Seconds to wait for the port probe.
        handshake_timeout: Seconds to wait for OpenD itself to answer once the
            port is known to be open. Generous, because a first connection after
            OpenD starts is genuinely slow; finite, because the client has no
            connect timeout of its own and an unanswered handshake is a hang.

    Returns:
        A :class:`Diagnosis`. Read ``first_failure`` for the one thing to fix.
    """
    result = Diagnosis()

    # --- 1. the client library ------------------------------------------
    try:
        import moomoo  # optional dependency: imported on use, not at startup
    except ImportError:
        result.stages.append(
            Stage(
                name="moomoo-api installed",
                status=StageStatus.FAILED,
                detail="the moomoo Python client is not importable",
                hint=_INSTALL_HINT,
            )
        )
        return result

    version = getattr(moomoo, "__version__", "unknown")
    result.stages.append(Stage("moomoo-api installed", StageStatus.OK, f"client version {version}"))

    # Connection threads are the ones that hang when OpenD goes away. As
    # daemons they cannot keep the interpreter alive after the report is
    # printed, so a failed check still exits.
    with suppress(Exception):
        moomoo.SysConfig.set_all_thread_daemon(True)
    _quiet_moomoo_console()

    # --- 2. is OpenD even there? ----------------------------------------
    endpoint = f"{config.host}:{config.port}"
    if not port_is_open(config.host, config.port, timeout=timeout):
        result.stages.append(
            Stage(
                name="OpenD reachable",
                status=StageStatus.FAILED,
                detail=f"nothing is listening on {endpoint}",
                hint=(
                    "Start moomoo OpenD and leave it running, then re-run this. "
                    "If OpenD is running on a different port, set MOOMOO_OPEND_PORT "
                    "in .env to match its settings screen."
                ),
            )
        )
        return result
    result.stages.append(Stage("OpenD reachable", StageStatus.OK, f"listening on {endpoint}"))

    quote_ctx = None
    trade_ctx = None
    try:
        # --- 3. is anybody logged in? -----------------------------------
        def open_quotes() -> Any:
            ctx = moomoo.OpenQuoteContext(host=config.host, port=config.port)
            return ctx, ctx.get_global_state()

        try:
            quote_ctx, (ret, state) = _with_deadline(
                "quote handshake", open_quotes, handshake_timeout
            )
        except GatewayTimeoutError as exc:
            result.stages.append(
                Stage(
                    name="OpenD logged in",
                    status=StageStatus.FAILED,
                    detail=str(exc),
                    hint=(
                        f"Something is listening on {endpoint} but it is not answering "
                        "as OpenD. Check that OpenD - not another program - owns that "
                        "port, and that its window is not sitting on a login or "
                        "verification-code prompt."
                    ),
                )
            )
            return result
        if ret != moomoo.RET_OK:
            result.stages.append(
                Stage(
                    name="OpenD logged in",
                    status=StageStatus.FAILED,
                    detail=f"OpenD refused the state query: {state}",
                    hint="Open the OpenD window and check it is not showing an error.",
                )
            )
            return result

        result.global_state = dict(state)
        qot = bool(state.get("qot_logined"))
        trd = bool(state.get("trd_logined"))
        if not qot and not trd:
            result.stages.append(
                Stage(
                    name="OpenD logged in",
                    status=StageStatus.FAILED,
                    detail="OpenD is running but no account is logged in",
                    hint=(
                        "Log into OpenD with your moomoo ID and password. First login "
                        "on a machine also needs the device verification code moomoo "
                        "sends to your phone or email."
                    ),
                )
            )
            return result
        result.stages.append(
            Stage(
                name="OpenD logged in",
                status=StageStatus.OK,
                detail=f"quotes: {'yes' if qot else 'NO'}, trading: {'yes' if trd else 'NO'}",
                hint=(
                    ""
                    if trd
                    else "Quotes are authenticated but trading is not - OpenD was started "
                    "without the trading login. Restart it and log in fully."
                ),
            )
        )

        # --- 4. can we see the account? ---------------------------------
        def open_trading() -> Any:
            ctx = moomoo.OpenSecTradeContext(
                filter_trdmarket=config.trd_market,
                host=config.host,
                port=config.port,
                security_firm=config.security_firm,
            )
            return ctx, ctx.get_acc_list()

        try:
            trade_ctx, (ret, acc_frame) = _with_deadline(
                "trade handshake", open_trading, handshake_timeout
            )
        except GatewayTimeoutError as exc:
            result.stages.append(
                Stage(
                    name="account visible",
                    status=StageStatus.FAILED,
                    detail=str(exc),
                    hint=(
                        "Quotes answered but the trading connection did not. Restart "
                        "OpenD and log in again - it can hold a quote session open "
                        "after the trading session has dropped."
                    ),
                )
            )
            return result
        if ret != moomoo.RET_OK:
            result.stages.append(
                Stage(
                    name="account visible",
                    status=StageStatus.FAILED,
                    detail=f"get_acc_list failed: {acc_frame}",
                    hint=(
                        f"Check MOOMOO_SECURITY_FIRM ({config.security_firm}) and "
                        f"MOOMOO_TRD_MARKET ({config.trd_market}) match the account "
                        "you actually hold."
                    ),
                )
            )
            return result

        result.accounts = _account_rows(acc_frame)
        wanted = [a for a in result.accounts if a.trd_env == config.trd_env]
        if not wanted:
            # Two very different situations end up here and they need opposite
            # advice, so they are told apart rather than sharing one message.
            others = sorted({a.trd_env for a in result.accounts})
            if others:
                # The list came back full, just of another kind of account. A
                # moomoo Japan login often has no paper side at all, so the
                # SIMULATE default lands here on a completely healthy setup -
                # and telling that user their entity is wrong sends them to
                # re-check settings that were right the whole time.
                other = others[0]
                choice = "2" if other == "REAL" else "1"
                detail = f"no {config.trd_env} account on this login; it has {other} instead"
                hint = (
                    "Nothing is wrong with the login or the settings. Re-run against "
                    f"the account you actually have: choose {choice} in "
                    f"moomoo接続確認.bat, or set MOOMOO_TRD_ENV={other} in .env. "
                    "Reading a REAL account never places an order."
                )
            else:
                detail = (
                    f"no account at all for {config.security_firm} "
                    f"with {config.trd_market} permission"
                )
                hint = (
                    "This is the failure that looks like success: the login worked "
                    "and the list came back empty. Either the entity or the market "
                    "is wrong for this account, or the account is not approved for "
                    "that market yet."
                )
            result.stages.append(
                Stage(
                    name="account visible",
                    status=StageStatus.FAILED,
                    detail=detail,
                    hint=hint,
                )
            )
            return result

        account = wanted[0]
        result.stages.append(
            Stage(
                name="account visible",
                status=StageStatus.OK,
                detail=(
                    f"{account.trd_env} {account.acc_type} {account.masked_id} "
                    f"({'/'.join(account.markets) or 'no markets listed'})"
                ),
            )
        )

        # --- 5. does the account answer? --------------------------------
        ret, info = trade_ctx.accinfo_query(
            trd_env=config.trd_env,
            acc_id=int(account.acc_id),
            currency=config.currency,
        )
        if ret != moomoo.RET_OK:
            result.stages.append(
                Stage(
                    name="account answers",
                    status=StageStatus.FAILED,
                    detail=f"accinfo_query failed: {info}",
                    hint=(
                        "The gateway is authenticated but the account query was "
                        "refused. Check the account is open and funded for this market."
                    ),
                )
            )
        else:
            row = info.to_dict("records")[0] if len(info) else {}
            result.account_summary = {
                "currency": row.get("currency", config.currency),
                "total_assets": row.get("total_assets"),
                "cash": row.get("cash"),
                "market_val": row.get("market_val"),
            }
            result.stages.append(
                Stage(
                    name="account answers",
                    status=StageStatus.OK,
                    detail=f"balances returned in {result.account_summary['currency']}",
                )
            )

        # --- 6. unlock, only when the live account is the one asked for --
        if not config.is_real:
            result.stages.append(
                Stage(
                    name="trading unlocked",
                    status=StageStatus.SKIPPED,
                    detail="paper account (SIMULATE) needs no unlock",
                )
            )
        elif not unlock_password:
            result.stages.append(
                Stage(
                    name="trading unlocked",
                    status=StageStatus.SKIPPED,
                    detail="no trading PIN given, so the live account was left locked",
                    hint=(
                        "Set MOOMOO_TRADE_PASSWORD in .env with "
                        "'powershell -File scripts/set-key.ps1 MOOMOO_TRADE_PASSWORD' "
                        "to check the PIN too. Leaving it out is a safe default."
                    ),
                )
            )
        else:
            ret, msg = trade_ctx.unlock_trade(password=unlock_password)
            if ret != moomoo.RET_OK:
                result.stages.append(
                    Stage(
                        name="trading unlocked",
                        status=StageStatus.FAILED,
                        detail=f"unlock_trade was refused: {msg}",
                        hint=(
                            "The 6-digit trading PIN (取引暗証番号) is not the password "
                            "you log into moomoo with. Re-enter it with "
                            "'powershell -File scripts/set-key.ps1 MOOMOO_TRADE_PASSWORD'."
                        ),
                    )
                )
            else:
                # Locking again immediately: this command proves the PIN, it does
                # not open a trading session. Leaving an unlocked live account
                # behind a diagnostic is not a state this project hands back.
                with suppress(Exception):
                    trade_ctx.unlock_trade(password=unlock_password, is_unlock=False)
                result.stages.append(
                    Stage(
                        name="trading unlocked",
                        status=StageStatus.OK,
                        detail="PIN accepted, then locked again",
                    )
                )
    finally:
        for ctx in (trade_ctx, quote_ctx):
            if ctx is not None:
                with suppress(Exception):
                    ctx.close()

    return result


def to_moomoo_code(symbol: str) -> str:
    """Render ``symbol`` the way OpenD names an instrument: ``JP.9842``.

    This project's symbols come in three shapes - a bare JP code (``9842``), a
    Yahoo-style suffixed one (``9842.T``), and a US ticker (``AAPL``) - and
    moomoo accepts none of them. Its own form is market-first, the reverse of
    every other identifier here, so the conversion is easy to get backwards and
    worth doing in one place.
    """
    text = symbol.strip().upper()
    if text.startswith(_MOOMOO_PREFIXES):
        return text  # already moomoo's form; do not re-guess its market
    head = text.split(".")[0]
    return f"{JAPAN}.{head}" if market_for_symbol(text) == JAPAN else f"US.{head}"


def capital_flow(
    config: MoomooConfig,
    symbol: str,
    *,
    period_type: str = "DAY",
    start: str | None = None,
    end: str | None = None,
    timeout: float = 2.0,
    handshake_timeout: float = 20.0,
) -> Any:
    """Return the capital in/out flow for ``symbol`` as a DataFrame.

    Read-only market data: this asks the gateway a question and never touches
    the account. It carries the same guards as :func:`diagnose` because it has
    the same failure mode - a closed or unresponsive port makes the client hang
    rather than raise, and a hang in a data command is indistinguishable from a
    slow one.

    Args:
        config: Where OpenD is. Only the connection fields matter; ``trd_env``
            is irrelevant to market data.
        symbol: Any form :func:`to_moomoo_code` accepts.
        period_type: One of :data:`PERIOD_TYPES`. ``INTRADAY`` ignores the dates.
        start: ``YYYY-MM-DD``. Ignored for ``INTRADAY``. Given without ``end``,
            the API reads it as a year forward from there; omitting both asks
            for the last 365 days.
        end: ``YYYY-MM-DD``. Ignored for ``INTRADAY``. Given without ``start``,
            the API reads it as a year back from there.
        timeout: Seconds to wait for the port probe.
        handshake_timeout: Seconds to wait for OpenD to answer once the port is
            known to be open.

    Note:
        Documented limits, none of which surface as errors: at most 30 calls per
        30 seconds; stocks, warrants, funds and crypto only; one year of history
        for the dated periods and one day for ``INTRADAY``; and regular-session
        data only, so pre- and post-market flow is absent rather than zero.
        Two returned fields are period-dependent - ``main_in_flow`` is valid only
        for the dated periods, ``last_valid_time`` only for ``INTRADAY``.

    Raises:
        BrokerError: If the client is missing, OpenD is unreachable or silent,
            or the gateway refuses the request.
    """
    period = period_type.upper()
    if period not in PERIOD_TYPES:
        raise BrokerError(
            f"Unknown period type {period_type!r}. Expected one of: {', '.join(PERIOD_TYPES)}."
        )

    try:
        import moomoo  # optional dependency: imported on use, not at startup
    except ImportError as exc:
        raise BrokerError(_INSTALL_HINT) from exc

    with suppress(Exception):
        moomoo.SysConfig.set_all_thread_daemon(True)
    _quiet_moomoo_console()

    endpoint = f"{config.host}:{config.port}"
    if not port_is_open(config.host, config.port, timeout=timeout):
        raise BrokerError(
            f"Nothing is listening on {endpoint}. Start moomoo OpenD and log in, "
            "then try again - moomoo接続確認.bat checks the whole chain."
        )

    code = to_moomoo_code(symbol)
    ctx = None
    try:

        def query() -> Any:
            # security_firm is documented as applying only to *crypto* quote
            # connections, so it is not what grants access to a JP stock. The
            # client forwards it on this request all the same, so the configured
            # entity is passed rather than the N/A default - harmless where it is
            # ignored, correct where it is not.
            inner = moomoo.OpenQuoteContext(
                host=config.host, port=config.port, security_firm=config.security_firm
            )
            return inner, inner.get_capital_flow(code, period_type=period, start=start, end=end)

        try:
            ctx, (ret, data) = _with_deadline("capital flow", query, handshake_timeout)
        except GatewayTimeoutError as exc:
            raise BrokerError(
                f"{exc}. Something holds {endpoint} but is not answering as OpenD - "
                "check that the OpenD window is logged in and not sitting on a prompt."
            ) from exc

        if ret != moomoo.RET_OK:
            raise BrokerError(f"OpenD refused the capital-flow request for {code}: {data}")
        return data
    finally:
        if ctx is not None:
            with suppress(Exception):
                ctx.close()
