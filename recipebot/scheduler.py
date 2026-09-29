"""The tiny loop: sleep until the next post time, run once, sleep again."""

from __future__ import annotations

import logging
import signal
import time
from datetime import date, datetime, time as dtime, timedelta
from typing import Callable

from recipebot.config import Settings
from recipebot.pipeline import Pipeline

log = logging.getLogger(__name__)

MAX_SLEEP_CHUNK = 60.0


class Shutdown(Exception):
    """Raised inside the loop when the container is asked to stop."""


def install_signal_handlers() -> None:
    """Turn SIGTERM (docker stop) and SIGINT into a Shutdown exception so the loop exits cleanly."""

    def _handler(signum, _frame):
        raise Shutdown(signal.Signals(signum).name)

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _handler)


def next_run_at(now: datetime, post_time: dtime) -> datetime:
    """The next occurrence of post_time in now's timezone, strictly after now."""
    tz = now.tzinfo
    candidate = datetime.combine(now.date(), post_time, tzinfo=tz)
    if candidate <= now:
        candidate = datetime.combine(now.date() + timedelta(days=1), post_time, tzinfo=tz)
    return candidate


def wait_until(target: datetime, *, now: Callable[[], datetime], sleep: Callable[[float], None]) -> None:
    """Sleeps in short chunks and re-checks the clock, so suspends and clock changes cannot skip a day."""
    while True:
        remaining = (target - now()).total_seconds()
        if remaining <= 0:
            return
        sleep(min(remaining, MAX_SLEEP_CHUNK))


def run_forever(
    pipeline: Pipeline,
    settings: Settings,
    *,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] | None = None,
    max_runs: int | None = None,
) -> int:
    """Runs the daily loop. max_runs is for tests; None means forever. Returns the number of runs."""
    now = now or pipeline.now
    runs = 0
    try:
        if settings.run_on_start:
            _safe_run(pipeline, now().date())
            runs += 1
        while max_runs is None or runs < max_runs:
            target = next_run_at(now(), settings.post_time)
            log.info("next run at %s", target.isoformat(timespec="minutes"))
            wait_until(target, now=now, sleep=sleep)
            _safe_run(pipeline, target.date())
            runs += 1
    except Shutdown as stop:
        log.info("received %s, stopping the loop after %d run(s)", stop, runs)
    return runs


def _safe_run(pipeline: Pipeline, day: date) -> None:
    try:
        pipeline.run(day=day)
    except Exception:  # noqa: BLE001 - pipeline.run already catches, this is belt and braces
        log.exception("scheduled run crashed")
