"""A small daily scheduler for the recurring jobs (prices, screens, monitoring).

Deliberately thin: it wraps the ``schedule`` package so jobs are declared in one
place and a job that raises cannot kill the loop. Anything more — retries with
backoff, catch-up after downtime, distributed locking — belongs to a real
scheduler (cron, Task Scheduler, systemd timers), and this is designed to be
run *under* one of those rather than to replace it.

Failure policy: a job that raises is logged and the run continues. A daily
pipeline where the price fetch failing also silences the watchlist monitor is
worse than one where each job stands or falls on its own.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from stock_ai.core.logging import get_logger

logger = get_logger(__name__)

Job = Callable[[], None]


@dataclass
class JobResult:
    """The outcome of one job invocation."""

    name: str
    ok: bool
    error: str | None = None


@dataclass
class DailyScheduler:
    """Run named jobs once a day at a fixed local time."""

    at: str = "18:00"
    """Local wall-clock time, ``HH:MM``, the jobs run at."""
    jobs: list[tuple[str, Job]] = field(default_factory=list)

    def add(self, name: str, job: Job) -> DailyScheduler:
        """Register ``job`` under ``name``; returns self so calls can chain."""
        self.jobs.append((name, job))
        return self

    def run_once(self) -> list[JobResult]:
        """Run every job now, in registration order, isolating failures."""
        results: list[JobResult] = []
        for name, job in self.jobs:
            try:
                job()
                logger.info("Job %s completed", name)
                results.append(JobResult(name, ok=True))
            except Exception as exc:  # one bad job must not stop the others
                logger.exception("Job %s failed", name)
                results.append(JobResult(name, ok=False, error=str(exc)))
        return results

    def run_forever(self, poll_seconds: float = 30.0) -> None:
        """Block, running the jobs once a day at :attr:`at`.

        Args:
            poll_seconds: How often the pending-job check runs. The default
                keeps the fire time accurate to well under a minute without
                busy-waiting.

        Raises:
            ImportError: If the optional ``schedule`` dependency is missing.
        """
        import schedule

        if not self.jobs:
            raise ValueError("No jobs registered; nothing to schedule.")

        schedule.every().day.at(self.at).do(self.run_once)
        logger.info(
            "Scheduled %d job(s) daily at %s: %s",
            len(self.jobs),
            self.at,
            ", ".join(name for name, _job in self.jobs),
        )
        while True:
            schedule.run_pending()
            time.sleep(poll_seconds)
