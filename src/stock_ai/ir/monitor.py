"""Watchlist monitoring: fetch disclosures, judge them, alert on what matters.

One pass over the watchlist does four things per name: pull recent disclosures,
skip anything already reported, have the AI rate and summarize what is left,
and emit the items that clear that entry's threshold.

Three properties make this safe to run on a schedule:

- **Judged items are recorded**, so a feed that keeps returning the same
  week-old news does not re-alert every morning.
- **An item is recorded even when it falls below the threshold.** The
  judgement is what gets remembered, not the alert, so a quiet item is not
  re-classified (and re-billed to the AI provider) on every run.
- **A provider outage is not recorded.** Those items stay unseen and are
  retried next run. Remembering them would let a few minutes of downtime bury
  the affected filings for good, since a seen item is never revisited.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from stock_ai.ai.analysis import DEFAULT_SUMMARY_WORDS, classify_importance
from stock_ai.ai.analysis import summarize as ai_summarize
from stock_ai.ai.base import AIProvider
from stock_ai.core.exceptions import AIError
from stock_ai.core.logging import get_logger
from stock_ai.data.types import Disclosure, Importance, WatchEntry
from stock_ai.database.engine import Database
from stock_ai.database.repository import WatchlistRepository, get_profile
from stock_ai.ir.sources import DisclosureSource
from stock_ai.notification.base import Notifier

logger = get_logger(__name__)


class _Unjudged:
    """Marker for a disclosure the provider could not be asked about."""


#: Returned when the AI provider itself failed, as opposed to answering badly.
UNJUDGED = _Unjudged()


@dataclass(frozen=True)
class Alert:
    """A disclosure judged worth reporting, with its AI summary."""

    entry: WatchEntry
    disclosure: Disclosure
    importance: Importance
    summary: str

    def format(self) -> str:
        """Render the alert as a notification-ready block.

        The feed is named in the header because ``--feed all`` mixes two very
        different things: an EDINET filing is a statutory disclosure by the
        company, and a news item is a third party writing about it. The first
        live alert this system produced was a press summary about Toyota, and
        nothing on screen distinguished it from a filing - which is the sort of
        thing a reader would only notice after acting on it.
        """
        header = f"[{self.importance.value.upper()}] {self.entry.symbol}"
        if self.entry.note:
            header += f" ({self.entry.note})"
        if self.disclosure.source:
            header += f" - via {self.disclosure.source}"
        lines = [header, self.disclosure.title]
        if self.disclosure.published_on:
            lines.append(str(self.disclosure.published_on))
        if self.summary:
            lines.append("")
            lines.append(self.summary)
        if self.disclosure.url:
            lines.append(self.disclosure.url)
        return "\n".join(lines)


@dataclass(frozen=True)
class MonitorResult:
    """The outcome of one monitoring pass."""

    alerts: list[Alert]
    checked: int
    """Disclosures examined this run (new ones only)."""
    skipped: int
    """Disclosures ignored because they had already been reported."""
    unjudged: int = 0
    """Disclosures the AI provider could not be asked about.

    These are *not* recorded as seen, so they are retried on the next run once
    the provider recovers. A non-zero count means the pass was incomplete.
    """
    delivery_error: str | None = None
    """Why the alerts could not be sent, if a notifier was configured and failed.

    Recorded rather than raised. By the time delivery is attempted the items are
    already marked seen, so an exception here loses the alerts twice over: they
    never reach the channel, and they are never looked at again. Keeping the
    failure as data lets the caller still print what it found.
    """

    def format(self) -> str:
        """Render every alert as one message, most important first."""
        ordered = sorted(self.alerts, key=lambda a: a.importance.rank, reverse=True)
        return "\n\n".join(alert.format() for alert in ordered)


class WatchMonitor:
    """Check the watchlist for disclosures worth surfacing."""

    def __init__(
        self,
        database: Database,
        source: DisclosureSource,
        provider: AIProvider,
        notifier: Notifier | None = None,
        summary_words: int = DEFAULT_SUMMARY_WORDS,
    ) -> None:
        """Wire the monitor to its collaborators.

        Args:
            database: Holds the watchlist and the already-reported set.
            source: Where disclosures come from.
            provider: AI provider used to rate and summarize.
            notifier: Optional channel to deliver alerts to.
            summary_words: Length cap for each disclosure summary.
        """
        self.database = database
        self.source = source
        self.provider = provider
        self.notifier = notifier
        self.summary_words = summary_words

    def run(self, limit: int = 10, notify: bool = False) -> MonitorResult:
        """Make one pass over the watchlist.

        Args:
            limit: Maximum disclosures to pull per symbol.
            notify: Send the alerts through the configured notifier.

        Returns:
            The alerts raised, plus how much was examined and skipped.
        """
        with self.database.session() as session:
            entries = WatchlistRepository(session).list_entries()
        if not entries:
            logger.info("Watchlist is empty; nothing to monitor.")
            return MonitorResult(alerts=[], checked=0, skipped=0)

        subjects = self._subjects(entries)
        alerts: list[Alert] = []
        checked = skipped = unjudged = 0
        for entry in entries:
            fresh, already_seen = self._fresh_disclosures(entry, limit)
            skipped += already_seen
            for disclosure in fresh:
                checked += 1
                verdict = self._judge(entry, disclosure, about=subjects.get(entry.symbol))
                if isinstance(verdict, _Unjudged):
                    unjudged += 1
                elif verdict is not None:
                    alerts.append(verdict)

        result = MonitorResult(alerts=alerts, checked=checked, skipped=skipped, unjudged=unjudged)
        logger.info(
            "Monitored %d name(s): %d new, %d already seen, %d alert(s), %d unjudged",
            len(entries),
            checked,
            skipped,
            len(alerts),
            unjudged,
        )
        if unjudged:
            logger.warning(
                "%d disclosure(s) could not be classified; they will be retried "
                "(and billed) on the next run.",
                unjudged,
            )
        if checked and getattr(self.provider, "is_stub", False):
            # Said plainly, because everything else about this run looks real:
            # it lists alerts, it names symbols, and it costs nothing.
            logger.warning(
                "Provider %r only echoes prompts - these %d item(s) were not "
                "judged, and nothing was recorded as seen. Set AI_PROVIDER or "
                "pass --provider to run for real.",
                getattr(self.provider, "name", "?"),
                checked,
            )
        if notify and alerts:
            failure = self._deliver(result)
            if failure:
                result = replace(result, delivery_error=failure)
        return result

    def pending(self, limit: int = 10) -> list[tuple[WatchEntry, Disclosure]]:
        """Return exactly what the next :meth:`run` would send to the model.

        Same watchlist, same feed, same already-seen filter - but nothing is
        judged, so calling this costs no tokens. It exists so a run can be
        priced before it is paid for; anything that estimated from a different
        set than :meth:`run` actually processes would be an estimate of the
        wrong thing.
        """
        with self.database.session() as session:
            entries = WatchlistRepository(session).list_entries()

        work: list[tuple[WatchEntry, Disclosure]] = []
        for entry in entries:
            fresh, _seen = self._fresh_disclosures(entry, limit)
            work.extend((entry, disclosure) for disclosure in fresh)
        return work

    def pending_texts(self, limit: int = 10) -> list[tuple[str, str]]:
        """Return ``(company, text)`` for everything the next run would judge.

        Pairs the text with the company name the rating prompt will carry, so
        a cost estimate prices the request that will actually be sent. The
        name is part of that request; leaving it out would undercount every
        item and reintroduce the drift this shared path exists to prevent.
        """
        work = self.pending(limit=limit)
        subjects = self._subjects([entry for entry, _ in work])
        return [(subjects.get(entry.symbol, entry.symbol), d.as_text()) for entry, d in work]

    def _fresh_disclosures(self, entry: WatchEntry, limit: int) -> tuple[list[Disclosure], int]:
        """Return ``entry``'s unreported disclosures and how many were skipped."""
        try:
            items = self.source.fetch(entry.symbol, limit=limit)
        except Exception as exc:  # a dead feed must not abort the whole pass
            logger.warning("Disclosure fetch failed for %s: %s", entry.symbol, exc)
            return [], 0

        with self.database.session() as session:
            repo = WatchlistRepository(session)
            fresh = [item for item in items if not repo.is_seen(item.uid)]
        return fresh, len(items) - len(fresh)

    def _subjects(self, entries: Sequence[WatchEntry]) -> dict[str, str]:
        """Name each watched company, for the rating prompt to judge against.

        A bare code says nothing to a model: asked to rate an item's bearing on
        "7272" it cannot know whether an article about Autoliv concerns that
        company or not. The stored profile already holds the name, and one
        lookup per run is cheaper than the alerts it prevents.
        """
        subjects: dict[str, str] = {}
        with self.database.session() as session:
            for entry in entries:
                name = ""
                try:
                    profile = get_profile(session, entry.symbol)
                    name = (profile.name if profile else "") or ""
                except Exception:  # a missing profile must not stop the run
                    name = ""
                label = f"{name} ({entry.symbol})" if name else entry.symbol
                subjects[entry.symbol] = label
        return subjects

    def _judge(
        self, entry: WatchEntry, disclosure: Disclosure, *, about: str | None = None
    ) -> Alert | None | _Unjudged:
        """Rate and summarize one disclosure, returning an alert if it qualifies.

        Returns :data:`UNJUDGED` when the provider itself failed. That case is
        treated as transient and deliberately *not* remembered: marking it seen
        would let a few minutes of provider downtime bury those filings
        permanently, since a seen item is never looked at again. An unparseable
        answer is different - the model did reply, so ``UNKNOWN`` is a verdict
        and is recorded like any other.
        """
        text = disclosure.as_text()
        try:
            importance = classify_importance(self.provider, text, about=about)
        except AIError as exc:
            logger.warning("Importance check failed for %s: %s", entry.symbol, exc)
            return UNJUDGED

        # A stub provider judged nothing, so nothing is worth remembering. A
        # seen disclosure is never fetched again, so recording an echo would
        # hide these filings from every later real run - the damage outlasts
        # the test that caused it.
        if not getattr(self.provider, "is_stub", False):
            self._remember(entry, disclosure, importance)
        if importance.rank < entry.min_importance.rank:
            return None

        summary = self._summarize(entry, text)
        return Alert(entry=entry, disclosure=disclosure, importance=importance, summary=summary)

    def _summarize(self, entry: WatchEntry, text: str) -> str:
        """Summarize a disclosure, degrading to an empty summary on failure.

        An alert with no summary is still worth delivering - the headline and
        the rating carry most of the signal - so a summarization failure must
        not swallow the alert.
        """
        try:
            return ai_summarize(self.provider, text, max_words=self.summary_words)
        except AIError as exc:
            logger.warning("Summary failed for %s: %s", entry.symbol, exc)
            return ""

    def _remember(self, entry: WatchEntry, disclosure: Disclosure, importance: Importance) -> None:
        """Record the verdict so later runs neither re-alert nor re-classify."""
        with self.database.session() as session:
            WatchlistRepository(session).mark_seen(disclosure, importance, market=entry.market)

    def _deliver(self, result: MonitorResult) -> str | None:
        """Send the alerts through the notifier; return why it failed, if it did.

        The failure is returned rather than raised. Delivery is the last step,
        after every item has been judged, billed and marked seen - so an
        exception here throws away work that has already been paid for, and
        throws away the alerts with it, since a seen item is never re-examined.
        The run reports what it found either way.
        """
        if self.notifier is None:
            logger.warning("Alerts raised but no notifier is configured.")
            return None
        try:
            self.notifier.send(result.format())
        except Exception as exc:
            logger.error("Could not deliver %d alert(s): %s", len(result.alerts), exc)
            return str(exc)
        logger.info("Delivered %d alert(s) via %s", len(result.alerts), self.notifier.name)
        return None


def unseen_only(database: Database, disclosures: Sequence[Disclosure]) -> list[Disclosure]:
    """Filter ``disclosures`` down to those not yet reported."""
    with database.session() as session:
        repo = WatchlistRepository(session)
        return [d for d in disclosures if not repo.is_seen(d.uid)]
