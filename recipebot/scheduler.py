"""The tiny loop: sleep until the next post time, run once, sleep again.

The loop is idempotent per rotation date. It consults the runs table before every run, so:
* a container that (re)starts after the post time catches up the missed post, within
  RECIPEBOT_CATCH_UP_HOURS of the post time;
* a restart after the day's post never posts a second time;
* a run that was killed half way is reported to the admin chat and not repeated, because the
  first half may already be in the channel.
"""

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
MAX_SKIP_DAYS = 7

NONE, DONE, UNFINISHED = "none", "done", "unfinished"


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


def day_status(pipeline: Pipeline, day: date) -> str:
    """NONE when nothing ran for the date, DONE when a run finished (whatever its outcome),
    UNFINISHED when the only rows are runs that started and never finished."""
    try:
        runs = pipeline.history.runs_for_day(day)
    except Exception:  # noqa: BLE001 - a broken database is reported by the run itself
        log.exception("could not read the runs table; assuming %s has not run", day)
        return NONE
    if not runs:
        return NONE
    if any(not r.unfinished for r in runs):
        return DONE
    return UNFINISHED


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
    reported: set[date] = set()

    def run_day(day: date) -> None:
        nonlocal runs
        _safe_run(pipeline, day)
        runs += 1

    def report_unfinished(day: date) -> None:
        if day in reported:
            return
        reported.add(day)
        text = (
            f"RecipeBot: the run for {day.isoformat()} started but never finished, most likely because the "
            "container was stopped mid run. Not running again today, because the recipe may already be in the "
            "channel. Check the channel and `recipebot history`."
        )
        log.warning(text)
        pipeline.notify_admin(text)

    try:
        if settings.run_on_start:
            today = now().date()
            status = day_status(pipeline, today)
            if status == NONE:
                log.info("run_on_start: running for %s", today)
                run_day(today)
            elif status == UNFINISHED:
                report_unfinished(today)
            else:
                log.info("run_on_start: %s already has a run, skipping", today)

        while max_runs is None or runs < max_runs:
            current = now()
            today = current.date()
            scheduled = datetime.combine(today, settings.post_time, tzinfo=current.tzinfo)
            window_end = scheduled + timedelta(hours=settings.catch_up_hours)
            if scheduled <= current < window_end:
                status = day_status(pipeline, today)
                if status == NONE:
                    log.info("post time %s has passed and %s has not run yet, catching up", settings.post_time, today)
                    run_day(today)
                    continue
                if status == UNFINISHED:
                    report_unfinished(today)

            target = next_run_at(current, settings.post_time)
            for _ in range(MAX_SKIP_DAYS):
                status = day_status(pipeline, target.date())
                if status == NONE:
                    break
                if status == UNFINISHED:
                    report_unfinished(target.date())
                log.info("%s already has a run, skipping to the next day", target.date())
                target = next_run_at(target, settings.post_time)
            log.info("next run at %s", target.isoformat(timespec="minutes"))
            wait_until(target, now=now, sleep=sleep)
            run_day(target.date())
    except Shutdown as stop:
        log.info("received %s, stopping the loop after %d run(s)", stop, runs)
    return runs


def _safe_run(pipeline: Pipeline, day: date) -> None:
    try:
        pipeline.run(day=day)
    except Exception:  # noqa: BLE001 - pipeline.run already catches, this is belt and braces
        log.exception("scheduled run crashed")
