from datetime import date, datetime, time, timedelta

from recipebot.scheduler import next_run_at, run_forever, wait_until
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
    def __init__(self):
        self.days = []
        self.now = lambda: datetime(2026, 9, 29, 12, 0, tzinfo=SGT)

    def run(self, *, day=None, **kwargs):
        self.days.append(day)


def test_run_forever_runs_for_the_scheduled_date(settings):
    clock = {"now": datetime(2026, 9, 29, 15, 59, 0, tzinfo=SGT)}

    def sleep(seconds):
        clock["now"] += timedelta(seconds=seconds)

    pipeline = StubPipeline()
    runs = run_forever(pipeline, settings, sleep=sleep, now=lambda: clock["now"], max_runs=2)
    assert runs == 2
    assert pipeline.days == [date(2026, 9, 29), date(2026, 9, 30)]


def test_run_on_start(settings):
    settings.run_on_start = True
    clock = {"now": datetime(2026, 9, 29, 18, 0, 0, tzinfo=SGT)}

    def sleep(seconds):
        clock["now"] += timedelta(seconds=seconds)

    pipeline = StubPipeline()
    runs = run_forever(pipeline, settings, sleep=sleep, now=lambda: clock["now"], max_runs=2)
    assert runs == 2 and pipeline.days == [date(2026, 9, 29), date(2026, 9, 30)]


def test_crashing_run_does_not_stop_the_loop(settings):
    clock = {"now": datetime(2026, 9, 29, 15, 59, 0, tzinfo=SGT)}

    def sleep(seconds):
        clock["now"] += timedelta(seconds=seconds)

    class Boom(StubPipeline):
        def run(self, *, day=None, **kwargs):
            super().run(day=day)
            raise RuntimeError("boom")

    pipeline = Boom()
    assert run_forever(pipeline, settings, sleep=sleep, now=lambda: clock["now"], max_runs=2) == 2
    assert len(pipeline.days) == 2
