from ems.contracts.events import AlertRaised
from ems.sim.database import DEFAULT_CAPACITY, TripDatabase

from .helpers import make


def test_default_capacity_is_24h_of_75_per_min_plus_20_percent():
    assert DEFAULT_CAPACITY == 129_600


def test_records_are_flushed_every_30_seconds_in_order():
    ctx, _ = make()
    db = ctx.services["db"]
    assert db.available()
    db.write({"n": 1})
    ctx.clock.run_until(20)
    db.write({"n": 2})
    assert db.records() == []                             # still in the buffer
    ctx.clock.run_until(30)
    assert db.records() == [{"n": 1}, {"n": 2}]
    db.write({"n": 3})
    ctx.clock.run_until(60)
    assert [r["n"] for r in db.records()] == [1, 2, 3]


def test_outage_buffers_alerts_once_at_80_percent_and_flushes_in_order():
    ctx, _ = make()
    db = TripDatabase(ctx, capacity=10)
    db.set_down(True)
    assert not db.available()
    for n in range(8):
        db.write({"n": n})
    assert not ctx.bus.of_type(AlertRaised)               # 8 of 10 is exactly 80%: not past it
    db.write({"n": 8})
    alerts = ctx.bus.of_type(AlertRaised)
    assert len(alerts) == 1
    assert (alerts[0].severity, alerts[0].kind, alerts[0].recipients) == (
        "high", "db_buffer", ["fm"])
    db.write({"n": 9})
    assert len(ctx.bus.of_type(AlertRaised)) == 1         # only once
    ctx.clock.run_until(100)
    assert db.records() == []                             # nothing persisted while down
    db.set_down(False)
    assert [r["n"] for r in db.records()] == list(range(10))
    assert ctx.services["notifier"].inbox[-1][1] == ["fm"]


def test_buffer_overflow_drops_oldest_and_write_never_raises():
    ctx, _ = make()
    db = TripDatabase(ctx, capacity=3)
    db.set_down(True)
    for n in range(5):
        db.write({"n": n})
    db.write(object())                                    # not even a dict: still no exception
    db.set_down(False)
    assert db.records() == [{"n": 2}, {"n": 3}, {"n": 4}]      # oldest two dropped
    assert db.dropped == 3                                    # 2 overflowed + the bad record


def test_lose_unflushed_empties_the_buffer():
    ctx, _ = make()
    db = ctx.services["db"]
    db.write({"n": 1})
    assert db.lose_unflushed() == [{"n": 1}]
    ctx.clock.run_until(60)
    assert db.records() == []
