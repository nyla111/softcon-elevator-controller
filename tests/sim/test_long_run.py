"""CR-01 / NFR-W1 support in Part 1: long runs, the wear check and SIM-19."""
from datetime import datetime

from ems.contracts import config
from ems.contracts.events import CarStatusUpdated
from ems.contracts.types import WearReport
from ems.core.app import build
from ems.sim import scenarios
from ems.sim.baseline import install_dispatch_standin
from ems.sim.long_run import imbalance, run_long, wear_imbalance
from ems.sim.traffic import schedule_segments

SATURDAY = datetime(2026, 11, 21)


class CountingUsage:
    """Test stand-in for Part 5: floors travelled from CarStatusUpdated, one counting month."""
    def __init__(self, ctx) -> None:
        self.floor: dict = {}
        self.months: list[dict] = [{}]
        ctx.bus.subscribe(CarStatusUpdated, self.on_status)

    def on_status(self, ev) -> None:
        car, floor = ev.status.car_id, ev.status.floor
        last = self.floor.get(car, floor)
        self.floor[car] = floor
        self.months[-1][car] = self.months[-1].get(car, 0.0) + abs(floor - last)

    def wear_report(self) -> WearReport:
        period: dict = {}
        for month in self.months:
            for car, n in month.items():
                period[car] = period.get(car, 0.0) + n
        return WearReport(0.0, config.WEAR_MONTH_S, [dict(m) for m in self.months], period)


def with_counting_usage(ctx) -> None:
    install_dispatch_standin(ctx)
    ctx.services["usage"] = CountingUsage(ctx)


def test_imbalance_is_max_minus_min_over_max():
    assert imbalance([100, 98, 99]) == 0.02
    assert imbalance([0, 0]) == 0.0
    assert imbalance([5]) == 0.0


def test_wear_imbalance_checks_each_month_and_the_period_per_group():
    building = build().services["building"]
    low = {"A": 100.0, "B": 99.0, "C": 99.5, "D": 98.5}
    high = {"E": 100.0, "F": 90.0, "G": 100.0, "H": 100.0}
    report = WearReport(0.0, config.WEAR_MONTH_S, [low | high], low | high)
    rows = {(r["group"], r["window"]): r for r in wear_imbalance(report, building)}
    assert set(rows) == {("LOW", "month 1"), ("LOW", "period"),
                         ("HIGH", "month 1"), ("HIGH", "period")}   # S is alone: no row
    assert rows[("LOW", "month 1")]["ok"] and rows[("LOW", "period")]["limit"] == 0.05
    assert not rows[("HIGH", "month 1")]["ok"] and not rows[("HIGH", "period")]["ok"]


def test_stub_usage_gives_no_figures():
    assert wear_imbalance(build().services["usage"].wear_report(), build().services["building"]) == []


def test_weekends_follow_the_weekend_schedule():
    ctx = build(start=datetime(2026, 11, 20))                  # Friday
    segments = schedule_segments(ctx, day_start=0.0, until=3 * 86400.0, weekends=True)
    saturday = [s for s in segments if 86400.0 <= s.start < 2 * 86400.0]
    friday = [s for s in segments if s.start < 86400.0]
    assert len(saturday) == len(config.WEEKEND_SCHEDULE) == 1
    assert len(friday) == len(config.WEEKDAY_SCHEDULE)
    assert len(schedule_segments(ctx, day_start=0.0, until=3 * 86400.0)) == 3 * len(friday)


def test_long_run_keeps_memory_flat_and_reports_wear(tmp_path):
    result = run_long(1, 1, start=SATURDAY, setup=with_counting_usage, out_dir=tmp_path,
                      keep_ctx=True)
    ctx, day = result.ctx, result.summaries[0]
    assert day.weekend and day.passengers > 0 and day.delivered > 0
    assert ctx.bus.log == []
    assert len(ctx.services["controllers"].passengers) < day.passengers      # finished ones dropped
    assert {r["group"] for r in result.imbalance} == {"LOW", "HIGH"}
    assert sum(result.wear.period.values()) > 0
    assert result.manifest_path and tmp_path.joinpath(result.manifest_path).exists()


def test_sim_19_waits_for_part_5_and_checks_every_row():
    assert scenarios.run_scenario("SIM-19", 1)["message"].startswith("not ready")
    building = build().services["building"]
    even = {car: 100.0 for car in "ABCDEFGH"}
    report = WearReport(0.0, config.WEAR_MONTH_S, [even] * 6, {c: 600.0 for c in even})

    class Result:
        imbalance = wear_imbalance(report, building)

    criteria = scenarios.check_sim_19(Result, None)
    assert len(criteria) == 2 * 7 and all(ok for _, ok, _ in criteria)
