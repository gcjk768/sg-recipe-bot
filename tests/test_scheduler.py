import os
import signal
from datetime import date, datetime, time, timedelta

import pytest

from recipebot.history import History
from recipebot.scheduler import DONE, NONE, UNFINISHED, Shutdown, day_status, install_signal_handlers, next_run_at, run_forever, wait_until
from tests.conftest import SGT


def test_next_run_at():
    post = time(16, 0)
    assert next_run_at(datetime(2026, 9, 29, 9, 0, tzinfo=SGT), post) == datetime(2026, 9, 29, 16, 0, tzinfo=SGT)
    assert next_run_at(datetime(2026, 9, 29, 16, 0, tzinfo=SGT), post) == datetime(2026, 9, 30, 16, 0, tzinfo=SGT)
    assert next_run_at(datetime(2026, 9, 29, 23, 59, tzinfo=SGT), post) == datetime(2026, 9, 30, 16, 0, tzinfo=SGT)


def test_wait_until_sleeps_in_chunks():
    clock = {"now": datetime(2026, 9, 29, 15, 57, 30, tzinfo=SGT)}
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        clock["now"] += timedelta(seconds=seconds)

    wait_until(datetime(2026, 9, 29, 16, 0, tzinfo=SGT), now=lambda: clock["now"], sleep=sleep)
    assert sleeps == [60.0, 60.0, 30.0]


class StubPipeline:
    """Records the days it was asked to run and writes real run rows, like the real pipeline."""

    def __init__(self, history: History, fail: bool = False):
        self.history = history
        self.days = []
        self.alerts = []
        self.fail = fail
        self.now = lambda: datetime(2026, 9, 29, 12, 0, tzinfo=SGT)

    def run(self, *, day=None, **kwargs):
        self.days.append(day)
        row = self.history.start_run(f"run-{len(self.days)}", "soups", None, run_day=day)
        self.history.finish_run(row, "posted", 1, "ok")
        if self.fail:
            raise RuntimeError("boom")

    def run_daily(self, day):
        self.run(day=day)

    def notify_admin(self, blocks):
        self.alerts.append("\n\n".join(blocks))


@pytest.fixture
def history(tmp_path):
    with History(tmp_path / "h.sqlite") as h:
        yield h


def _clock(start: datetime):
    clock = {"now": start}

    def sleep(seconds):
        clock["now"] += timedelta(seconds=seconds)

    return clock, sleep


def test_run_forever_runs_for_the_scheduled_date(settings, history):
    clock, sleep = _clock(datetime(2026, 9, 29, 15, 59, 0, tzinfo=SGT))
    pipeline = StubPipeline(history)
    assert run_forever(pipeline, settings, sleep=sleep, now=lambda: clock["now"], max_runs=2) == 2
    assert pipeline.days == [date(2026, 9, 29), date(2026, 9, 30)]


def test_restart_after_post_time_catches_up_the_missed_day(settings, history):
    clock, sleep = _clock(datetime(2026, 9, 29, 16, 1, 0, tzinfo=SGT))
    pipeline = StubPipeline(history)
    assert run_forever(pipeline, settings, sleep=sleep, now=lambda: clock["now"], max_runs=2) == 2
    assert pipeline.days == [date(2026, 9, 29), date(2026, 9, 30)]
    assert pipeline.alerts == []


def test_restart_after_a_completed_run_does_not_post_twice(settings, history):
    row = history.start_run("earlier", "soups", None, run_day=date(2026, 9, 29))
    history.finish_run(row, "posted", 1, "ok")
    clock, sleep = _clock(datetime(2026, 9, 29, 16, 1, 0, tzinfo=SGT))
    pipeline = StubPipeline(history)
    assert run_forever(pipeline, settings, sleep=sleep, now=lambda: clock["now"], max_runs=1) == 1
    assert pipeline.days == [date(2026, 9, 30)]


def test_failed_run_earlier_today_is_not_repeated(settings, history):
    row = history.start_run("earlier", "soups", None, run_day=date(2026, 9, 29))
    history.finish_run(row, "failed", 0, "bad json twice")
    clock, sleep = _clock(datetime(2026, 9, 29, 16, 30, 0, tzinfo=SGT))
    pipeline = StubPipeline(history)
    run_forever(pipeline, settings, sleep=sleep, now=lambda: clock["now"], max_runs=1)
    assert pipeline.days == [date(2026, 9, 30)]


def test_unfinished_run_is_reported_once_and_not_repeated(settings, history):
    history.start_run("killed", "soups", None, run_day=date(2026, 9, 29))
    clock, sleep = _clock(datetime(2026, 9, 29, 16, 1, 0, tzinfo=SGT))
    pipeline = StubPipeline(history)
    run_forever(pipeline, settings, sleep=sleep, now=lambda: clock["now"], max_runs=1)
    assert pipeline.days == [date(2026, 9, 30)]
    assert len(pipeline.alerts) == 1 and "never finished" in pipeline.alerts[0] and "2026-09-29" in pipeline.alerts[0]


def test_catch_up_window_expires(settings, history):
    clock, sleep = _clock(datetime(2026, 9, 29, 23, 30, 0, tzinfo=SGT))
    pipeline = StubPipeline(history)
    run_forever(pipeline, settings, sleep=sleep, now=lambda: clock["now"], max_runs=1)
    assert pipeline.days == [date(2026, 9, 30)]


def test_catch_up_can_be_disabled(settings, history):
    settings.catch_up_hours = 0
    clock, sleep = _clock(datetime(2026, 9, 29, 16, 1, 0, tzinfo=SGT))
    pipeline = StubPipeline(history)
    run_forever(pipeline, settings, sleep=sleep, now=lambda: clock["now"], max_runs=1)
    assert pipeline.days == [date(2026, 9, 30)]


def test_dry_runs_do_not_count_as_the_day_having_run(settings, history):
    row = history.start_run("dry", "soups", None, run_day=date(2026, 9, 29))
    history.finish_run(row, "dry_run", 1, "preview")
    clock, sleep = _clock(datetime(2026, 9, 29, 16, 1, 0, tzinfo=SGT))
    pipeline = StubPipeline(history)
    run_forever(pipeline, settings, sleep=sleep, now=lambda: clock["now"], max_runs=1)
    assert pipeline.days == [date(2026, 9, 29)]


def test_run_on_start_before_post_time_does_not_double_post(settings, history):
    settings.run_on_start = True
    clock, sleep = _clock(datetime(2026, 9, 29, 15, 50, 0, tzinfo=SGT))
    pipeline = StubPipeline(history)
    assert run_forever(pipeline, settings, sleep=sleep, now=lambda: clock["now"], max_runs=2) == 2
    assert pipeline.days == [date(2026, 9, 29), date(2026, 9, 30)]


def test_run_on_start_after_post_time(settings, history):
    settings.run_on_start = True
    clock, sleep = _clock(datetime(2026, 9, 29, 18, 0, 0, tzinfo=SGT))
    pipeline = StubPipeline(history)
    assert run_forever(pipeline, settings, sleep=sleep, now=lambda: clock["now"], max_runs=2) == 2
    assert pipeline.days == [date(2026, 9, 29), date(2026, 9, 30)]


def test_run_on_start_skips_when_today_already_ran(settings, history):
    settings.run_on_start = True
    row = history.start_run("earlier", "soups", None, run_day=date(2026, 9, 29))
    history.finish_run(row, "posted", 1, "ok")
    clock, sleep = _clock(datetime(2026, 9, 29, 18, 0, 0, tzinfo=SGT))
    pipeline = StubPipeline(history)
    run_forever(pipeline, settings, sleep=sleep, now=lambda: clock["now"], max_runs=1)
    assert pipeline.days == [date(2026, 9, 30)]


def test_day_status(history):
    pipeline = StubPipeline(history)
    assert day_status(pipeline, date(2026, 9, 29)) == NONE
    history.start_run("k", "soups", None, run_day=date(2026, 9, 29))
    assert day_status(pipeline, date(2026, 9, 29)) == UNFINISHED
    row = history.start_run("k2", "soups", None, run_day=date(2026, 9, 29))
    history.finish_run(row, "error", 0, "x")
    assert day_status(pipeline, date(2026, 9, 29)) == DONE


def test_crashing_run_does_not_stop_the_loop(settings, history):
    clock, sleep = _clock(datetime(2026, 9, 29, 15, 59, 0, tzinfo=SGT))
    pipeline = StubPipeline(history, fail=True)
    assert run_forever(pipeline, settings, sleep=sleep, now=lambda: clock["now"], max_runs=2) == 2
    assert len(pipeline.days) == 2


@pytest.mark.skipif(os.name == "nt", reason="os.kill(SIGTERM) terminates the process on Windows")
def test_sigterm_stops_the_loop_cleanly(settings, history):
    clock = {"now": datetime(2026, 9, 29, 15, 59, 0, tzinfo=SGT)}
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    install_signal_handlers()
    try:
        def sleep(seconds):
            clock["now"] += timedelta(seconds=seconds)
            os.kill(os.getpid(), signal.SIGTERM)

        pipeline = StubPipeline(history)
        assert run_forever(pipeline, settings, sleep=sleep, now=lambda: clock["now"], max_runs=5) == 0
        assert pipeline.days == []
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def test_shutdown_after_a_run_is_counted(settings, history):
    clock = {"now": datetime(2026, 9, 29, 15, 59, 30, tzinfo=SGT)}
    calls = {"n": 0}

    def sleep(seconds):
        calls["n"] += 1
        clock["now"] += timedelta(seconds=seconds)
        if calls["n"] > 1:
            raise Shutdown("SIGTERM")

    pipeline = StubPipeline(history)
    assert run_forever(pipeline, settings, sleep=sleep, now=lambda: clock["now"], max_runs=5) == 1
    assert pipeline.days == [date(2026, 9, 29)]


class NoRowPipeline(StubPipeline):
    """A pipeline whose runs fail before they can record themselves (broken rotation.json, unwritable db)."""

    def run(self, *, day=None, **kwargs):
        self.days.append(day)
        self.alerts.append(f"posted nothing for {day}")


def test_run_that_records_no_row_is_not_repeated_in_a_loop(settings, history):
    clock, sleep = _clock(datetime(2026, 9, 29, 16, 1, 0, tzinfo=SGT))
    pipeline = NoRowPipeline(history)
    assert run_forever(pipeline, settings, sleep=sleep, now=lambda: clock["now"], max_runs=3) == 3
    assert pipeline.days == [date(2026, 9, 29), date(2026, 9, 30), date(2026, 10, 1)]
    assert clock["now"] >= datetime(2026, 10, 1, 16, 0, tzinfo=SGT)


def test_run_that_records_no_row_via_the_wait_path_is_not_repeated(settings, history):
    clock, sleep = _clock(datetime(2026, 9, 29, 15, 59, 0, tzinfo=SGT))
    pipeline = NoRowPipeline(history)
    assert run_forever(pipeline, settings, sleep=sleep, now=lambda: clock["now"], max_runs=2) == 2
    assert pipeline.days == [date(2026, 9, 29), date(2026, 9, 30)]


def test_unreadable_history_does_not_spin(settings, history):
    class BrokenHistory:
        def runs_for_day(self, day, **kwargs):
            raise RuntimeError("unable to open database file")

    clock, sleep = _clock(datetime(2026, 9, 29, 16, 1, 0, tzinfo=SGT))
    pipeline = NoRowPipeline(history)
    pipeline.history = BrokenHistory()
    assert run_forever(pipeline, settings, sleep=sleep, now=lambda: clock["now"], max_runs=2) == 2
    assert pipeline.days == [date(2026, 9, 29), date(2026, 9, 30)]


def test_catch_up_window_crosses_midnight(settings, history):
    settings.post_time = time(23, 0)
    clock, sleep = _clock(datetime(2026, 9, 30, 1, 0, 0, tzinfo=SGT))
    pipeline = StubPipeline(history)
    run_forever(pipeline, settings, sleep=sleep, now=lambda: clock["now"], max_runs=2)
    assert pipeline.days == [date(2026, 9, 29), date(2026, 9, 30)]


def test_manual_run_during_the_wait_is_respected(settings, history):
    clock = {"now": datetime(2026, 9, 29, 15, 58, 0, tzinfo=SGT)}

    def sleep(seconds):
        clock["now"] += timedelta(seconds=seconds)
        if not history.runs_for_day(date(2026, 9, 29)):
            row = history.start_run("manual", "soups", None, run_day=date(2026, 9, 29))
            history.finish_run(row, "posted", 1, "by hand")

    pipeline = StubPipeline(history)
    run_forever(pipeline, settings, sleep=sleep, now=lambda: clock["now"], max_runs=1)
    assert pipeline.days == [date(2026, 9, 30)]


def test_long_suspend_past_the_window_is_reported_not_posted(settings, history):
    clock = {"now": datetime(2026, 9, 29, 15, 59, 30, tzinfo=SGT)}
    calls = {"n": 0}

    def sleep(seconds):
        calls["n"] += 1
        clock["now"] += timedelta(hours=9) if calls["n"] == 1 else timedelta(seconds=seconds)

    pipeline = StubPipeline(history)
    run_forever(pipeline, settings, sleep=sleep, now=lambda: clock["now"], max_runs=1)
    assert pipeline.days == [date(2026, 9, 30)]
    assert len(pipeline.alerts) == 1 and "missed the post for 2026-09-29" in pipeline.alerts[0]


def _seed(history, day, status="posted"):
    row = history.start_run(f"seed-{day}", "soups", None, run_day=day)
    if status != "running":
        history.finish_run(row, status, 1 if status == "posted" else 0, "seed")


def test_restart_after_the_window_on_the_same_day_is_reported_once(settings, history):
    _seed(history, date(2026, 9, 28))
    clock, sleep = _clock(datetime(2026, 9, 29, 23, 30, 0, tzinfo=SGT))
    pipeline = StubPipeline(history)
    run_forever(pipeline, settings, sleep=sleep, now=lambda: clock["now"], max_runs=2)
    assert pipeline.days == [date(2026, 9, 30), date(2026, 10, 1)]
    assert len(pipeline.alerts) == 1 and "2026-09-29" in pipeline.alerts[0]


def test_fresh_install_after_the_window_reports_nothing(settings, history):
    clock, sleep = _clock(datetime(2026, 9, 29, 23, 30, 0, tzinfo=SGT))
    pipeline = StubPipeline(history)
    run_forever(pipeline, settings, sleep=sleep, now=lambda: clock["now"], max_runs=1)
    assert pipeline.days == [date(2026, 9, 30)] and pipeline.alerts == []


def test_outage_restart_after_midnight_is_reported(settings, history):
    _seed(history, date(2026, 9, 28))
    clock, sleep = _clock(datetime(2026, 9, 30, 1, 0, 0, tzinfo=SGT))
    pipeline = StubPipeline(history)
    run_forever(pipeline, settings, sleep=sleep, now=lambda: clock["now"], max_runs=1)
    assert pipeline.days == [date(2026, 9, 30)]
    assert len(pipeline.alerts) == 1 and "missed the post for 2026-09-29" in pipeline.alerts[0]


def test_multi_day_outage_lists_every_missed_day_in_one_alert(settings, history):
    _seed(history, date(2026, 9, 26))
    clock, sleep = _clock(datetime(2026, 9, 29, 23, 0, 0, tzinfo=SGT))
    pipeline = StubPipeline(history)
    run_forever(pipeline, settings, sleep=sleep, now=lambda: clock["now"], max_runs=1)
    assert pipeline.days == [date(2026, 9, 30)]
    [alert] = pipeline.alerts
    assert "2026-09-27, 2026-09-28 and 2026-09-29" in alert and "those days" in alert


@pytest.mark.parametrize("hours,lost", [(25, ["2026-09-29"]), (30, ["2026-09-29"]), (47, ["2026-09-29", "2026-09-30"])])
def test_suspend_across_post_times_reports_every_lost_day(settings, history, hours, lost):
    _seed(history, date(2026, 9, 28))
    clock = {"now": datetime(2026, 9, 29, 15, 59, 30, tzinfo=SGT)}
    calls = {"n": 0}

    def sleep(seconds):
        calls["n"] += 1
        clock["now"] += timedelta(hours=hours) if calls["n"] == 1 else timedelta(seconds=seconds)

    pipeline = StubPipeline(history)
    run_forever(pipeline, settings, sleep=sleep, now=lambda: clock["now"], max_runs=1)
    joined = "\n".join(pipeline.alerts)
    assert len(pipeline.alerts) == 1
    for day in lost:
        assert day in joined
    for day in pipeline.days:
        assert day.isoformat() not in joined


def test_unfinished_run_outside_the_window_is_reported(settings, history):
    _seed(history, date(2026, 9, 28))
    _seed(history, date(2026, 9, 29), status="running")
    clock, sleep = _clock(datetime(2026, 9, 30, 9, 0, 0, tzinfo=SGT))
    pipeline = StubPipeline(history)
    run_forever(pipeline, settings, sleep=sleep, now=lambda: clock["now"], max_runs=1)
    assert pipeline.days == [date(2026, 9, 30)]
    assert len(pipeline.alerts) == 1 and "never finished" in pipeline.alerts[0] and "2026-09-29" in pipeline.alerts[0]


def test_zero_window_late_wake_does_not_post_and_reports(settings, history):
    settings.catch_up_hours = 0
    _seed(history, date(2026, 9, 28))
    clock = {"now": datetime(2026, 9, 29, 15, 59, 30, tzinfo=SGT)}
    calls = {"n": 0}

    def sleep(seconds):
        calls["n"] += 1
        clock["now"] += timedelta(hours=9) if calls["n"] == 1 else timedelta(seconds=seconds)

    pipeline = StubPipeline(history)
    run_forever(pipeline, settings, sleep=sleep, now=lambda: clock["now"], max_runs=1)
    assert pipeline.days == [date(2026, 9, 30)]
    assert len(pipeline.alerts) == 1 and "2026-09-29" in pipeline.alerts[0] and "catch up is disabled" in pipeline.alerts[0]


def test_zero_window_slightly_late_wake_still_posts(settings, history):
    settings.catch_up_hours = 0
    clock = {"now": datetime(2026, 9, 29, 15, 59, 30, tzinfo=SGT)}
    calls = {"n": 0}

    def sleep(seconds):
        calls["n"] += 1
        clock["now"] += timedelta(minutes=5) if calls["n"] == 1 else timedelta(seconds=seconds)

    pipeline = StubPipeline(history)
    run_forever(pipeline, settings, sleep=sleep, now=lambda: clock["now"], max_runs=1)
    assert pipeline.days == [date(2026, 9, 29)] and pipeline.alerts == []


def test_fresh_database_does_not_catch_up_yesterday_just_before_todays_post(settings, history):
    settings.catch_up_hours = 23
    clock, sleep = _clock(datetime(2026, 9, 29, 14, 30, 0, tzinfo=SGT))
    pipeline = StubPipeline(history)
    run_forever(pipeline, settings, sleep=sleep, now=lambda: clock["now"], max_runs=1)
    assert pipeline.days == [date(2026, 9, 29)] and pipeline.alerts == []


def test_established_database_does_catch_up_yesterday_with_a_long_window(settings, history):
    settings.catch_up_hours = 23
    _seed(history, date(2026, 9, 27))
    clock, sleep = _clock(datetime(2026, 9, 29, 14, 30, 0, tzinfo=SGT))
    pipeline = StubPipeline(history)
    run_forever(pipeline, settings, sleep=sleep, now=lambda: clock["now"], max_runs=2)
    assert pipeline.days == [date(2026, 9, 28), date(2026, 9, 29)]


def test_run_on_start_uses_the_pending_slot_after_midnight(settings, history):
    settings.run_on_start = True
    settings.post_time = time(23, 50)
    clock, sleep = _clock(datetime(2026, 9, 30, 0, 30, 0, tzinfo=SGT))
    pipeline = StubPipeline(history)
    run_forever(pipeline, settings, sleep=sleep, now=lambda: clock["now"], max_runs=2)
    assert pipeline.days == [date(2026, 9, 29), date(2026, 9, 30)]
    assert clock["now"] >= datetime(2026, 9, 30, 23, 50, tzinfo=SGT)


def test_first_start_before_post_time_does_not_report_yesterday(settings, history):
    clock, sleep = _clock(datetime(2026, 9, 29, 9, 0, 0, tzinfo=SGT))
    pipeline = StubPipeline(history)
    run_forever(pipeline, settings, sleep=sleep, now=lambda: clock["now"], max_runs=1)
    assert pipeline.days == [date(2026, 9, 29)] and pipeline.alerts == []


def test_catch_up_zero_still_runs_at_the_post_time(settings, history):
    settings.catch_up_hours = 0
    clock, sleep = _clock(datetime(2026, 9, 29, 15, 59, 0, tzinfo=SGT))
    pipeline = StubPipeline(history)
    run_forever(pipeline, settings, sleep=sleep, now=lambda: clock["now"], max_runs=2)
    assert pipeline.days == [date(2026, 9, 29), date(2026, 9, 30)]
    assert pipeline.alerts == []


def test_shutdown_raised_by_the_pipeline_stops_the_loop(settings, history):
    class Stopping(StubPipeline):
        def run(self, *, day=None, **kwargs):
            self.days.append(day)
            self.history.start_run("killed", "soups", None, run_day=day)
            raise Shutdown("SIGTERM")

    clock, sleep = _clock(datetime(2026, 9, 29, 15, 59, 0, tzinfo=SGT))
    pipeline = Stopping(history)
    assert run_forever(pipeline, settings, sleep=sleep, now=lambda: clock["now"], max_runs=5) == 0
    assert pipeline.days == [date(2026, 9, 29)]
    assert history.runs_for_day(date(2026, 9, 29))[0].unfinished
