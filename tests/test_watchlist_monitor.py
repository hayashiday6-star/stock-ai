"""Tests for the watchlist, disclosure sources, monitoring, and the scheduler."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator

import pytest

from stock_ai.core.exceptions import AIError
from stock_ai.core.scheduler import DailyScheduler
from stock_ai.data.types import Disclosure, Importance, WatchEntry
from stock_ai.database.engine import Database
from stock_ai.database.repository import WatchlistRepository
from stock_ai.ir.monitor import Alert, WatchMonitor, unseen_only
from stock_ai.ir.sources import (
    CompositeDisclosureSource,
    NewsDisclosureSource,
    StaticDisclosureSource,
    from_callable,
)
from stock_ai.news.sources import NewsItem, StaticNewsSource


class _FakeAI:
    """Rates anything containing a keyword as high, everything else as low."""

    name = "fake"

    def __init__(self, high_keyword: str = "上方修正", summary: str = "SUMMARY") -> None:
        self.high_keyword = high_keyword
        self.summary = summary
        self.importance_calls = 0
        self.summary_calls = 0

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 1024,
        **_kwargs: object,
    ) -> str:
        if prompt.startswith("Rate the importance"):
            self.importance_calls += 1
            return "high" if self.high_keyword in prompt else "low"
        self.summary_calls += 1
        return self.summary


class _BrokenAI:
    """Fails every call."""

    name = "broken"

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 1024,
        **_kwargs: object,
    ) -> str:
        raise AIError("provider down")


class _CaptureNotifier:
    name = "capture"

    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, message: str) -> None:
        self.sent.append(message)


@pytest.fixture
def database() -> Iterator[Database]:
    db = Database("sqlite:///:memory:")
    db.create_all()
    yield db
    db.dispose()


def _disclosure(title: str, symbol: str = "4593.T", day: int = 1) -> Disclosure:
    return Disclosure(
        symbol=symbol, title=title, body=f"{title} の本文", published_on=dt.date(2026, 8, day)
    )


# --- disclosure identity ----------------------------------------------------


def test_the_same_item_has_a_stable_id_regardless_of_body() -> None:
    """Dedup keys on what makes a disclosure the same disclosure."""
    first = Disclosure(symbol="X", title="上方修正", body="a", published_on=dt.date(2026, 8, 1))
    second = Disclosure(symbol="X", title="上方修正", body="b", published_on=dt.date(2026, 8, 1))
    assert first.uid == second.uid


def test_different_symbols_or_dates_are_different_items() -> None:
    base = Disclosure(symbol="X", title="上方修正", published_on=dt.date(2026, 8, 1))
    assert (
        base.uid != Disclosure(symbol="Y", title="上方修正", published_on=dt.date(2026, 8, 1)).uid
    )
    assert (
        base.uid != Disclosure(symbol="X", title="上方修正", published_on=dt.date(2026, 8, 2)).uid
    )


def test_unknown_outranks_low() -> None:
    """An unjudgeable item deserves a glance more than a confidently routine one."""
    assert Importance.UNKNOWN.rank > Importance.LOW.rank
    assert Importance.HIGH.rank > Importance.MEDIUM.rank > Importance.UNKNOWN.rank


# --- watchlist persistence --------------------------------------------------


def test_watchlist_round_trips(database: Database) -> None:
    with database.session() as session:
        repo = WatchlistRepository(session)
        repo.add("4593.T", note="ヘリオス", min_importance=Importance.HIGH, market="JP")
        repo.add("AAPL")

    with database.session() as session:
        entries = WatchlistRepository(session).list_entries()
    assert [e.symbol for e in entries] == ["4593.T", "AAPL"]
    assert entries[0].min_importance is Importance.HIGH
    assert entries[0].note == "ヘリオス"
    assert entries[1].min_importance is Importance.MEDIUM


def test_re_adding_updates_rather_than_duplicates(database: Database) -> None:
    with database.session() as session:
        repo = WatchlistRepository(session)
        repo.add("AAPL", note="first", min_importance=Importance.LOW)
        repo.add("AAPL", note="second", min_importance=Importance.HIGH)

    with database.session() as session:
        entries = WatchlistRepository(session).list_entries()
    assert len(entries) == 1
    assert entries[0].note == "second"
    assert entries[0].min_importance is Importance.HIGH


def test_removing_reports_whether_it_was_there(database: Database) -> None:
    with database.session() as session:
        WatchlistRepository(session).add("AAPL")
    with database.session() as session:
        repo = WatchlistRepository(session)
        assert repo.remove("AAPL") is True
        assert repo.remove("AAPL") is False


def test_marking_seen_is_idempotent(database: Database) -> None:
    """Two runs racing on the same item must not raise."""
    item = _disclosure("上方修正")
    with database.session() as session:
        repo = WatchlistRepository(session)
        assert repo.is_seen(item.uid) is False
        repo.mark_seen(item, Importance.HIGH, market="JP")
        repo.mark_seen(item, Importance.HIGH, market="JP")

    with database.session() as session:
        assert WatchlistRepository(session).is_seen(item.uid) is True


def test_unseen_only_filters_reported_items(database: Database) -> None:
    old, new = _disclosure("旧", day=1), _disclosure("新", day=2)
    with database.session() as session:
        WatchlistRepository(session).mark_seen(old, Importance.LOW, market="JP")
    assert unseen_only(database, [old, new]) == [new]


# --- sources ----------------------------------------------------------------


def test_news_source_adapts_into_disclosures() -> None:
    news = StaticNewsSource({"AAPL": [NewsItem(title="Apple ships", summary="details")]})
    (item,) = NewsDisclosureSource(news).fetch("AAPL")
    assert item.title == "Apple ships"
    assert item.body == "details"


def test_composite_merges_and_dedupes_across_feeds() -> None:
    """A filing carried by two feeds must alert once."""
    shared = _disclosure("上方修正", day=2)
    only_b = _disclosure("本社移転", day=1)
    composite = CompositeDisclosureSource(
        StaticDisclosureSource({"4593.T": [shared]}),
        StaticDisclosureSource({"4593.T": [shared, only_b]}),
    )
    items = composite.fetch("4593.T")
    assert [i.title for i in items] == ["上方修正", "本社移転"]  # newest first


def test_composite_survives_a_dead_feed() -> None:
    class _Broken:
        name = "broken"

        def fetch(self, symbol: str, limit: int = 10) -> list[Disclosure]:
            raise RuntimeError("feed down")

    composite = CompositeDisclosureSource(
        _Broken(), StaticDisclosureSource({"X": [_disclosure("生きてる", symbol="X")]})
    )
    assert [i.title for i in composite.fetch("X")] == ["生きてる"]


def test_from_callable_builds_a_source_and_drops_untitled_items() -> None:
    source = from_callable(
        "tdnet-stub",
        lambda symbol, limit: [
            {"title": "上方修正", "body": "本文", "published_on": "2026/08/01"},
            {"body": "タイトルなし"},
        ],
    )
    (item,) = source.fetch("4593.T")
    assert item.title == "上方修正"
    assert item.published_on == dt.date(2026, 8, 1)
    assert item.source == "tdnet-stub"


# --- monitoring -------------------------------------------------------------


def _watch(
    database: Database, symbol: str, importance: Importance, note: str | None = None
) -> None:
    with database.session() as session:
        WatchlistRepository(session).add(symbol, note=note, min_importance=importance, market="JP")


def test_only_items_above_the_threshold_alert(database: Database) -> None:
    _watch(database, "4593.T", Importance.MEDIUM, note="ヘリオス")
    source = StaticDisclosureSource(
        {"4593.T": [_disclosure("通期業績予想の上方修正", day=2), _disclosure("本社移転", day=1)]}
    )

    result = WatchMonitor(database, source, _FakeAI()).run()

    assert result.checked == 2
    assert [a.disclosure.title for a in result.alerts] == ["通期業績予想の上方修正"]
    assert result.alerts[0].importance is Importance.HIGH


def test_a_per_name_threshold_silences_routine_news(database: Database) -> None:
    _watch(database, "4593.T", Importance.HIGH)
    source = StaticDisclosureSource({"4593.T": [_disclosure("本社移転")]})
    assert WatchMonitor(database, source, _FakeAI()).run().alerts == []


def test_a_second_run_does_not_re_alert(database: Database) -> None:
    """The whole point of recording what was reported."""
    _watch(database, "4593.T", Importance.MEDIUM)
    source = StaticDisclosureSource({"4593.T": [_disclosure("通期業績予想の上方修正")]})
    ai = _FakeAI()
    monitor = WatchMonitor(database, source, ai)

    first = monitor.run()
    second = monitor.run()

    assert len(first.alerts) == 1
    assert second.alerts == []
    assert (second.checked, second.skipped) == (0, 1)


def test_a_below_threshold_item_is_not_re_classified(database: Database) -> None:
    """Remembering the verdict, not the alert, keeps the AI bill down."""
    _watch(database, "4593.T", Importance.HIGH)
    source = StaticDisclosureSource({"4593.T": [_disclosure("本社移転")]})
    ai = _FakeAI()
    monitor = WatchMonitor(database, source, ai)

    monitor.run()
    monitor.run()
    assert ai.importance_calls == 1


def test_a_provider_outage_is_reported_and_retried(database: Database) -> None:
    """Downtime must not bury a filing: unjudged items stay unseen."""
    _watch(database, "4593.T", Importance.MEDIUM)
    source = StaticDisclosureSource({"4593.T": [_disclosure("通期業績予想の上方修正")]})

    outage = WatchMonitor(database, source, _BrokenAI()).run()
    assert outage.alerts == []
    assert outage.unjudged == 1

    # Once the provider recovers the same item is judged, not skipped as seen.
    recovered = WatchMonitor(database, source, _FakeAI()).run()
    assert recovered.unjudged == 0
    assert [a.disclosure.title for a in recovered.alerts] == ["通期業績予想の上方修正"]


def test_an_unparseable_answer_is_a_verdict_not_an_outage(database: Database) -> None:
    """The model replied; that is a judgement, so it is remembered."""

    class _Babbling:
        name = "babbling"

        def complete(
            self,
            prompt: str,
            *,
            system: str | None = None,
            max_tokens: int = 1024,
            **_kwargs: object,
        ) -> str:
            return "I am not sure about this one"

    _watch(database, "4593.T", Importance.LOW)
    source = StaticDisclosureSource({"4593.T": [_disclosure("上方修正")]})
    monitor = WatchMonitor(database, source, _Babbling())

    first = monitor.run()
    assert first.unjudged == 0
    assert first.alerts[0].importance is Importance.UNKNOWN

    assert monitor.run().checked == 0  # recorded, so not re-examined


def test_a_summary_failure_does_not_swallow_the_alert(database: Database) -> None:
    """The headline and rating carry most of the signal."""

    class _RatesButCannotSummarize:
        name = "partial"

        def complete(
            self,
            prompt: str,
            *,
            system: str | None = None,
            max_tokens: int = 1024,
            **_kwargs: object,
        ) -> str:
            if prompt.startswith("Rate the importance"):
                return "high"
            raise AIError("summarizer down")

    _watch(database, "4593.T", Importance.MEDIUM)
    source = StaticDisclosureSource({"4593.T": [_disclosure("上方修正")]})

    result = WatchMonitor(database, source, _RatesButCannotSummarize()).run()

    assert len(result.alerts) == 1
    assert result.alerts[0].importance is Importance.HIGH
    assert result.alerts[0].summary == ""


def test_a_dead_feed_does_not_abort_the_pass(database: Database) -> None:
    class _Broken:
        name = "broken"

        def fetch(self, symbol: str, limit: int = 10) -> list[Disclosure]:
            raise RuntimeError("feed down")

    _watch(database, "4593.T", Importance.MEDIUM)
    result = WatchMonitor(database, _Broken(), _FakeAI()).run()
    assert result.checked == 0
    assert result.alerts == []


def test_alerts_are_delivered_and_ordered_by_importance(database: Database) -> None:
    _watch(database, "4593.T", Importance.LOW)
    source = StaticDisclosureSource(
        {"4593.T": [_disclosure("本社移転", day=1), _disclosure("上方修正", day=2)]}
    )
    notifier = _CaptureNotifier()

    result = WatchMonitor(database, source, _FakeAI(), notifier=notifier).run(notify=True)

    assert len(notifier.sent) == 1
    assert result.format().index("上方修正") < result.format().index("本社移転")


def test_nothing_is_sent_when_no_alert_clears(database: Database) -> None:
    _watch(database, "4593.T", Importance.HIGH)
    source = StaticDisclosureSource({"4593.T": [_disclosure("本社移転")]})
    notifier = _CaptureNotifier()
    WatchMonitor(database, source, _FakeAI(), notifier=notifier).run(notify=True)
    assert notifier.sent == []


def test_an_empty_watchlist_does_no_work(database: Database) -> None:
    result = WatchMonitor(database, StaticDisclosureSource({}), _FakeAI()).run()
    assert (result.checked, result.skipped, result.alerts) == (0, 0, [])


def test_alert_formatting_includes_the_note_and_link() -> None:
    alert = Alert(
        entry=WatchEntry(symbol="4593.T", market="JP", note="ヘリオス"),
        disclosure=Disclosure(
            symbol="4593.T",
            title="上方修正",
            published_on=dt.date(2026, 8, 1),
            url="https://example.com/x",
        ),
        importance=Importance.HIGH,
        summary="営業利益を50%引き上げ。",
    )
    text = alert.format()
    assert text.startswith("[HIGH] 4593.T (ヘリオス)")
    assert "https://example.com/x" in text
    assert "営業利益を50%引き上げ。" in text


# --- scheduler --------------------------------------------------------------


def test_jobs_run_in_order() -> None:
    order: list[str] = []
    scheduler = DailyScheduler().add("a", lambda: order.append("a"))
    scheduler.add("b", lambda: order.append("b"))

    results = scheduler.run_once()

    assert order == ["a", "b"]
    assert all(r.ok for r in results)


def test_a_failing_job_does_not_stop_the_others() -> None:
    """A broken price fetch must not silence the watchlist monitor."""
    ran: list[str] = []

    def boom() -> None:
        raise RuntimeError("network down")

    results = (
        DailyScheduler()
        .add("prices", boom)
        .add("monitor", lambda: ran.append("monitor"))
        .run_once()
    )

    assert ran == ["monitor"]
    assert [(r.name, r.ok) for r in results] == [("prices", False), ("monitor", True)]
    assert results[0].error == "network down"


def test_scheduling_with_no_jobs_is_refused() -> None:
    with pytest.raises(ValueError, match="No jobs registered"):
        DailyScheduler().run_forever()


def test_an_alert_says_which_feed_it_came_from() -> None:
    """A statutory filing and a press article must not look identical.

    The first live alert was a news summary about Toyota; read as an EDINET
    filing it would carry a weight it has not earned.
    """
    from stock_ai.data.types import Disclosure, Importance, WatchEntry
    from stock_ai.ir.monitor import Alert

    entry = WatchEntry(symbol="7203", market="JP")
    filing = Alert(
        entry=entry,
        disclosure=Disclosure(symbol="7203", title="決算短信", source="edinet"),
        importance=Importance.HIGH,
        summary="...",
    )
    article = Alert(
        entry=entry,
        disclosure=Disclosure(symbol="7203", title="Toyota sharpens cost cuts", source="yfinance"),
        importance=Importance.MEDIUM,
        summary="...",
    )

    assert "via edinet" in filing.format()
    assert "via yfinance" in article.format()


def test_a_run_priced_over_the_cap_is_refused_before_anything_is_sent() -> None:
    """A scheduled run bills nightly with nobody watching.

    How many disclosures are filed on a given day is not something the schedule
    controls, so the cap is the only thing between a busy filing day and a bill
    nobody chose. Checking costs nothing - counting tokens is unbilled.
    """
    from stock_ai.cli import _within_budget

    class _Counter:
        model = "claude-opus-5"
        name = "anthropic"
        calls = 0

        def count_tokens(self, prompt: str, *, system: str | None = None) -> int:
            type(self).calls += 1
            return 5_000

        def complete(self, *args: object, **kwargs: object) -> str:  # pragma: no cover
            raise AssertionError("the run must not start when it is over budget")

    class _Item:
        def as_text(self) -> str:
            return "a filing"

    class _Pending:
        def pending(self, limit: int = 10) -> list[tuple[object, object]]:
            return [(object(), _Item())] * 3

    provider = _Counter()
    assert _within_budget(_Pending(), provider, max_cost=100.0, limit=10) is True
    assert _within_budget(_Pending(), provider, max_cost=0.0001, limit=10) is False
    assert _Counter.calls > 0  # it really priced the run rather than guessing


def test_a_provider_that_cannot_be_priced_still_runs() -> None:
    """Refusing because the guard could not be evaluated is an outage, not a cap."""
    from stock_ai.ai.dummy import DummyAIProvider
    from stock_ai.cli import _within_budget

    class _Pending:
        def pending(self, limit: int = 10) -> list[tuple[object, object]]:
            raise AssertionError("nothing should be fetched when pricing is impossible")

    assert _within_budget(_Pending(), DummyAIProvider(), max_cost=0.0, limit=10) is True


def test_a_stub_provider_does_not_consume_the_backlog(tmp_path) -> None:
    """A dummy run must leave the work for a real one.

    Everything else about such a run looks real - it lists alerts, names
    symbols and costs nothing - so the only thing separating it from a paid run
    is whether the ratings are read closely. If it also marked items seen, the
    filings would be hidden from every later run, and no re-run recovers them:
    a seen disclosure is never fetched again.
    """
    from stock_ai.ai.dummy import DummyAIProvider
    from stock_ai.data.types import Disclosure
    from stock_ai.database.repository import WatchlistRepository
    from stock_ai.ir.monitor import WatchMonitor

    class OneItem:
        def fetch(self, symbol: str, limit: int = 10) -> list[Disclosure]:
            return [
                Disclosure(
                    symbol=symbol,
                    title="臨時報告書",
                    body="body",
                    published_on=None,
                    url="https://example.invalid/1",
                    source="edinet",
                )
            ]

    database = Database(f"sqlite:///{tmp_path / 'stub.db'}")
    database.create_all()
    with database.session() as session:
        WatchlistRepository(session).add("2502", market="JP")

    monitor = WatchMonitor(database, source=OneItem(), provider=DummyAIProvider())
    first = monitor.run(limit=10)
    assert first.checked == 1

    # Nothing was recorded, so a real provider still sees the same item.
    with database.session() as session:
        assert WatchlistRepository(session).count_seen() == 0
    assert monitor.run(limit=10).checked == 1


def test_forgetting_seen_records_lets_a_real_run_see_them_again(tmp_path) -> None:
    """Recovery from a pass that recorded verdicts it should not have."""
    from stock_ai.ai.analysis import Importance
    from stock_ai.data.types import Disclosure
    from stock_ai.database.repository import WatchlistRepository

    database = Database(f"sqlite:///{tmp_path / 'forget.db'}")
    database.create_all()
    item = Disclosure(
        symbol="2502",
        title="臨時報告書",
        body="body",
        published_on=None,
        url="https://example.invalid/1",
        source="edinet",
    )
    other = Disclosure(
        symbol="7203",
        title="半期報告書",
        body="body",
        published_on=None,
        url="https://example.invalid/2",
        source="edinet",
    )
    with database.session() as session:
        repo = WatchlistRepository(session)
        repo.mark_seen(item, Importance.HIGH, market="JP")
        repo.mark_seen(other, Importance.HIGH, market="JP")

    with database.session() as session:
        repo = WatchlistRepository(session)
        assert repo.count_seen() == 2
        assert repo.forget_seen("2502") == 1

    with database.session() as session:
        repo = WatchlistRepository(session)
        assert not repo.is_seen(item.uid)
        assert repo.is_seen(other.uid)  # untouched
        assert repo.forget_seen() == 1
        assert repo.count_seen() == 0
