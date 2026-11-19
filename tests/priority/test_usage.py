from datetime import datetime

from ems.contracts.events import CarStatusUpdated
from ems.core.app import build


def status(ctx, car, floor):
    original = ctx.get("controllers").status(car)
    ctx.bus.publish(CarStatusUpdated(ctx.clock.now(), original.__class__(
        original.car_id, original.group, floor, float(floor), original.direction,
        original.doors, original.load_pct, original.passengers)))


def test_distance_report_is_per_group_and_handles_missing_floor_zero():
    ctx = build(parts=["sim", "priority"])
    usage = ctx.get("usage")
    status(ctx, "A", -1)
    status(ctx, "A", 1)
    status(ctx, "B", 1)
    status(ctx, "B", 2)
    report = usage.wear_report()
    assert usage.distance("A") == 1 and usage.distance("B") == 1
    assert report["groups"]["LOW"]["cars"]["A"]["month_floors"] == 1
    assert report["groups"]["SERVICE"]["monthly_imbalance"] == 0


def test_six_month_rollover_does_not_reset_fr31_lifetime_counters():
    ctx = build(start=datetime(2026, 1, 15), parts=["sim", "priority"])
    usage = ctx.get("usage")
    status(ctx, "A", 1)
    status(ctx, "A", 5)
    lifetime = ctx.get("priority").maintenance.usage("A")["total"]["floors_travelled"]
    assert usage.distance("A") == 4 and lifetime == 4
    # Fast-forward the reporting boundary without executing six months of 1 s simulator ticks.
    ctx.clock._t = 181 * 86400
    report = usage.wear_report()
    assert usage.distance("A") == 0
    assert report["period_start"] == "2026-07-01"
    assert ctx.get("priority").maintenance.usage("A")["total"]["floors_travelled"] == 4


def test_wear_thresholds_use_strict_month_and_inclusive_period_limits():
    ctx = build(parts=["sim", "priority"])
    usage = ctx.get("usage")
    usage.period.update(A=100, B=98, C=99, D=100)
    usage.monthly[ctx.clock.now_dt().strftime("%Y-%m")] = dict(usage.period)
    low = usage.wear_report()["groups"]["LOW"]
    assert low["monthly_imbalance"] == 0.02 and low["monthly_pass"] is False
    assert low["period_imbalance"] == 0.02 and low["period_pass"] is True
