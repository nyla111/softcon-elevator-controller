"""CR-01 / NFR-W1 support in Part 1: long runs and the per-month wear rows."""
from datetime import datetime

from ems.contracts import config
from ems.core.app import build
from ems.sim.long_run import run_long, wear_rows
from ems.sim.traffic import schedule_segments

SATURDAY = datetime(2026, 11, 21)


def _report(month: str, low: dict, high: dict) -> dict:
    def group(cars):
        hi = max(cars.values())
        imb = (hi - min(cars.values())) / hi
        return {"cars": {c: {"month_floors": n, "period_floors": n} for c, n in cars.items()},
                "monthly_imbalance": imb, "period_imbalance": imb,
                "monthly_pass": imb < 0.02, "period_pass": imb <= 0.05}
    return {"month": month, "monthly_limit": 0.02, "period_limit": 0.05,
            "groups": {"LOW": group(low), "HIGH": group(high),
                       "SERVICE": group({"S": 1})}}


def test_wear_rows_cover_each_month_and_the_period_per_group():
    low = {"A": 100, "B": 99, "C": 99.5, "D": 98.5}
    high = {"E": 100, "F": 90, "G": 100, "H": 100}
    final = _report("2026-12", low, high)
    rows = {(r["group"], r["window"]): r for r in wear_rows({"2026-12": final}, final)}
    assert set(rows) == {("LOW", "2026-12"), ("LOW", "period"),
                         ("HIGH", "2026-12"), ("HIGH", "period")}   # S is alone: no row
    assert rows[("LOW", "2026-12")]["ok"] and rows[("LOW", "period")]["limit"] == 0.05
    assert not rows[("HIGH", "2026-12")]["ok"] and not rows[("HIGH", "period")]["ok"]


def test_no_rows_without_a_usage_report():
    assert wear_rows({}, {}) == []


def test_weekends_follow_the_weekend_schedule():
    ctx = build(start=datetime(2026, 11, 20))                  # Friday
    segments = schedule_segments(ctx, day_start=0.0, until=3 * 86400.0, weekends=True)
    saturday = [s for s in segments if 86400.0 <= s.start < 2 * 86400.0]
    friday = [s for s in segments if s.start < 86400.0]
    assert len(saturday) == len(config.WEEKEND_SCHEDULE) == 1
    assert len(friday) == len(config.WEEKDAY_SCHEDULE)
    assert len(schedule_segments(ctx, day_start=0.0, until=3 * 86400.0)) == 3 * len(friday)


def test_long_run_keeps_memory_flat_and_reports_wear(tmp_path):
    result = run_long(1, 1, start=SATURDAY, out_dir=tmp_path, keep_ctx=True)
    ctx, day = result.ctx, result.summaries[0]
    assert day.weekend and day.passengers > 0 and day.delivered > 0
    assert ctx.bus.log == []
    assert len(ctx.services["controllers"].passengers) < day.passengers      # finished ones dropped
    assert {r["group"] for r in result.imbalance} == {"LOW", "HIGH"}
    assert list(result.months) == ["2026-11"]
    assert result.manifest_path and tmp_path.joinpath(result.manifest_path).exists()
