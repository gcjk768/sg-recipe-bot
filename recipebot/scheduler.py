"""The tiny loop: sleep until the next post time, run once, sleep again.

The loop makes at most one attempt per rotation date per process, and consults the runs table
before every attempt, so:
* a container that (re)starts after the post time catches up the missed post, within
  RECIPEBOT_CATCH_UP_HOURS of the post time, even across midnight;
* a date that already has a run (scheduled or manual) is never posted a second time;
* a run that was killed half way is reported to the admin chat and not repeated, because the
  first half may already be in the channel;
* a run that fails before it can record itself is not retried in a loop; the next attempt is
  tomorrow's;
* every date whose post time passed without a run (the bot was down, the NAS was asleep) is
  reported to the admin chat once, however many days the gap spans.
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
MAX_REPORTED_DAYS = 14
LATE_GRACE = timedelta(minutes=15)
"""With catch up disabled, how late a wake from sleep may still post the day it was waiting for."""
FRESH_MIN_GAP = timedelta(hours=12)
"""On a brand new database, a catch-up post is skipped when the next scheduled post is closer than this."""

NONE, DONE, UNFINISHED = "none", "done", "unfinished"


class Shutdown(BaseException):
    """Raised inside the loop when the container is asked to stop. A BaseException, so the
    pipeline's catch all cannot swallow it and a stop request always ends the process."""


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


def last_scheduled_at(now: datetime, post_time: dtime) -> datetime:
    """The most recent occurrence of post_time at or before now (yesterday's when today's is still ahead)."""
    candidate = datetime.combine(now.date(), post_time, tzinfo=now.tzinfo)
    if candidate > now:
        candidate = datetime.combine(now.date() - timedelta(days=1), post_time, tzinfo=now.tzinfo)
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


def _format_days(days: list[date]) -> str:
    text = [d.isoformat() for d in days]
    if len(text) == 1:
        return text[0]
    return ", ".join(text[:-1]) + " and " + text[-1]


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
    process_start = now()
    first_slot = next_run_at(process_start - timedelta(microseconds=1), settings.post_time)
    """The first post time at or after this process started."""
    attempted: set[date] = set()  # one attempt per rotation date per process, whatever the database says
    reported_unfinished: set[date] = set()
    reported_missed: set[date] = set()
    window = timedelta(hours=settings.catch_up_hours)
    late_after = window if window > timedelta(0) else LATE_GRACE

    def status(day: date) -> str:
        if day in attempted:
            return DONE
        return day_status(pipeline, day)

    def run_day(day: date) -> None:
        nonlocal runs
        attempted.add(day)
        _safe_run(pipeline, day)
        runs += 1

    def span() -> tuple[date | None, date | None]:
        try:
            return pipeline.history.run_day_span()
        except Exception:  # noqa: BLE001
            log.exception("could not read the runs table")
            return None, None

    def established() -> bool:
        first, _last = span()
        return first is not None

    def report_unfinished(day: date) -> None:
        if day in reported_unfinished:
            return
        reported_unfinished.add(day)
        text = (
            f"RecipeBot: the run for {day.isoformat()} started but never finished, most likely because the "
            "container was stopped mid run. Not running again for that day, because the recipe may already be "
            "in the channel. Check the channel and `recipebot history`."
        )
        log.warning(text)
        _safe_notify(pipeline, text)

    def report_gap(upto: date) -> None:
        """Reports, once each, every date up to `upto` whose post time passed without a run. The
        gap starts after the last finished run in the database, or at this process's first post
        time on a new database, so a fresh install never reports days before it existed."""
        first, last_finished = span()
        if last_finished is not None:
            start = last_finished + timedelta(days=1)
        elif first is not None:
            start = min(first, first_slot.date())
        else:
            start = first_slot.date()
        if start > upto:
            return
        if (upto - start).days > 366:
            start = upto - timedelta(days=366)
        missed: list[date] = []
        day = start
        while day <= upto:
            if day not in reported_missed and day not in attempted:
                state = status(day)
                if state == NONE:
                    missed.append(day)
                elif state == UNFINISHED:
                    report_unfinished(day)
            day += timedelta(days=1)
        if not missed:
            return
        reported_missed.update(missed)
        shown = missed[-MAX_REPORTED_DAYS:]
        extra = len(missed) - len(shown)
        days_text = _format_days(shown) + (f" (and {extra} earlier day(s))" if extra else "")
        why = (
            f"and the {settings.catch_up_hours:g} hour catch up window has closed"
            if window > timedelta(0)
            else "and catch up is disabled"
        )
        text = (
            f"RecipeBot missed the post for {days_text}: the post time {settings.post_time:%H:%M} passed while "
            f"the bot was not running {why}. Nothing was posted for "
            f"{'that day' if len(missed) == 1 else 'those days'}. Run `recipebot run` by hand if you still want one."
        )
        log.warning(text)
        _safe_notify(pipeline, text)

    def consider(day: date, why: str) -> bool:
        """Runs `day` unless it was already attempted or recorded. Returns True when it ran."""
        current = status(day)
        if current == NONE:
            log.info("%s: running for %s", why, day)
            run_day(day)
            return True
        if current == UNFINISHED:
            report_unfinished(day)
        else:
            log.info("%s: %s already has a run, skipping", why, day)
        return False

    try:
        if settings.run_on_start:
            start = now()
            last = last_scheduled_at(start, settings.post_time)
            pending = last.date() if start < last + window else start.date()
            consider(pending, "run_on_start")

        while max_runs is None or runs < max_runs:
            current = now()
            last = last_scheduled_at(current, settings.post_time)
            if current < last + window:
                report_gap(last.date() - timedelta(days=1))
                if not established() and next_run_at(current, settings.post_time) - current < FRESH_MIN_GAP and last < process_start:
                    log.info("new database: not catching up %s, the next post is only hours away", last.date())
                elif consider(last.date(), f"post time {settings.post_time:%H:%M} has passed"):
                    continue
            else:
                report_gap(last.date())

            target = next_run_at(current, settings.post_time)
            for _ in range(MAX_SKIP_DAYS):
                day_state = status(target.date())
                if day_state == NONE:
                    break
                if day_state == UNFINISHED:
                    report_unfinished(target.date())
                log.info("%s already has a run, skipping to the next day", target.date())
                target = next_run_at(target, settings.post_time)
            log.info("next run at %s", target.isoformat(timespec="minutes"))
            wait_until(target, now=now, sleep=sleep)
            woke = now()
            if woke >= target + late_after:
                # Slept far past the post time (a suspended NAS). The top of the loop reports every
                # missed day and catches up the latest one when it is still inside its window.
                log.warning("woke at %s, %s after the post time; not posting this late", woke.isoformat(timespec="minutes"), woke - target)
                continue
            consider(target.date(), "scheduled post time")
    except Shutdown as stop:
        log.info("received %s, stopping the loop after %d run(s)", stop, runs)
    return runs


def _safe_run(pipeline: Pipeline, day: date) -> None:
    try:
        # ponytail: a restart mid-batch skips the rest of that day (the date already has runs); per-slot tracking if that bites.
        pipeline.run_daily(day)
    except Exception:  # noqa: BLE001 - pipeline.run already catches, this is belt and braces
        log.exception("scheduled run crashed")


def _safe_notify(pipeline: Pipeline, text: str) -> None:
    try:
        pipeline.notify_admin(text)
    except Exception:  # noqa: BLE001
        log.exception("could not send admin alert")
