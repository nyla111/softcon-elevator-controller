"""Session D: time of day, weekday blocks, Eco surge, BuildingSpec, design-limit hooks."""
from datetime import datetime

import pytest

from ems.contracts import config
from ems.core.app import build
from ems.sim import scenarios as S
from ems.sim.baseline import install_dispatch_standin
from ems.sim.building import BuildingSpec
from ems.sim.harness import run
from ems.sim.population import Population
from ems.sim.traffic import PROFILES, TrafficGenerator, schedule_segments

HOUR = 3600.0


def test_build_start_default_and_custom():
    assert build(seed=1, parts=[]).clock.now_dt() == datetime(2026, 11, 16, 0, 0)
    ctx = build(seed=1, parts=[], start=datetime(2026, 11, 16, 16, 30))
    assert ctx.clock.now_dt() == datetime(2026, 11, 16, 16, 30)
    ctx.clock.run_until(1800)
    assert ctx.clock.now_dt() == datetime(2026, 11, 16, 17, 0)


def test_run_starts_at_the_requested_time_of_day():
    r = run("up_peak", 1, warmup_s=0, measure_s=60, clear_s=60, start_hhmm="16:30",
            setup=install_dispatch_standin, out_dir=None, keep_ctx=True)
    assert r.start == "2026-11-16T16:30:00" and r.params["start"] == r.start


def test_schedule_segments_follow_the_clock_not_t0():
    ctx = build(seed=1, parts=[], start=datetime(2026, 11, 16, 21, 0))
    segs = schedule_segments(ctx, until=3 * HOUR)
    assert [s.profile.name for s in segs] == ["eco_quiet"]               # the 20:00-24:00 block
    assert segs[0].start == -HOUR and segs[0].end == 3 * HOUR
    longer = schedule_segments(ctx, until=5 * HOUR)                      # wraps over midnight
    assert [s.label for s in longer] == ["20:00 eco", "00:00 eco"] and longer[1].start == 3 * HOUR
    ctx = build(seed=1, parts=[], start=datetime(2026, 11, 16, 6, 30))
    first = TrafficGenerator.weekday(ctx, until=2 * HOUR).segments
    assert [s.profile.name for s in first] == ["eco_quiet", "up_peak"]
    assert first[1].start == 0.5 * HOUR                                  # Rush begins at 07:00


@pytest.mark.slow
def test_weekday_run_blocks_stranded_and_determinism():
    results = [run("weekday", seed, traffic="weekday", warmup_s=0, measure_s=86400,
                   setup=install_dispatch_standin, out_dir=None) for seed in (1, 2)]
    again = run("weekday", 1, traffic="weekday", warmup_s=0, measure_s=86400,
                setup=install_dispatch_standin, out_dir=None)
    assert again.event_digest == results[0].event_digest and again.blocks == results[0].blocks
    for r in results:
        assert r.passed
        assert [b["label"] for b in r.blocks] == [
            "00:00 eco", "07:00 rush", "09:30 balanced", "11:30 rush", "13:30 balanced",
            "17:00 rush", "18:30 balanced", "20:00 eco"]
        assert [b["profile"] for b in r.blocks] == [
            "eco_quiet", "up_peak", "balanced", "lunch", "balanced", "evening", "balanced",
            "eco_quiet"]
        assert r.blocks[0]["start_s"] == 0 and r.blocks[-1]["end_s"] == 86400
        assert all(b["calls"] > 0 and b["stranded"] == 0 for b in r.blocks)
        assert sum(b["calls"] for b in r.blocks) == r.metrics["passengers_measured"]


def test_surge_run_starts_in_an_eco_block(tmp_path):
    (_, _, surge) = S.SCENARIOS["SIM-04"].runs
    r = S.run_setup(surge.__class__(**{**surge.__dict__, "measure_s": 120.0, "clear_s": 120.0}),
                    1, out_dir=tmp_path)
    assert r.start == "2026-11-16T21:00:00"
    assert config.WEEKDAY_SCHEDULE[-1][0] == "20:00"                  # 20:00-24:00 is the Eco block
    arrivals = [e for e in r.ctx.bus.log if type(e).__name__ == "PassengerArrived"]
    quiet = [e for e in arrivals if e.t < 600]
    surge_arrivals = [e for e in arrivals if 600 <= e.t < 720]
    assert len(quiet) < 40 and len(surge_arrivals) > 100             # 2/min, then 75/min
    import json
    manifest = json.loads(open(r.manifest_path).read())
    assert "outside" in manifest["outside_normal_envelope"] and manifest["rate_per_min"] == 75


def test_reference_building_equals_config():
    b = BuildingSpec.reference()
    assert {g.name: (list(g.cars), list(g.floors)) for g in b.groups} == {
        n: (i["cars"], i["floors"]) for n, i in config.GROUPS.items()}
    assert b.passenger_groups == ["LOW", "HIGH"] and b.service_groups == {"SERVICE"}
    assert b.n_cars == 9 and b.all_tenant_floors == list(range(2, 31))
    ctx = build(seed=1)
    assert ctx.services["building"].name == "reference"
    assert set(ctx.services["controllers"].cars) == set("ABCDEFGHS")


def test_design_limit_layout_counts():
    b = BuildingSpec.design_limit()
    assert (len(b.groups), b.n_cars, b.n_stops, b.n_tenants) == (8, 64, 128, 200)
    assert all(len(g.cars) == 8 and 1 in g.floors for g in b.groups)
    assert [len([f for f in g.floors if f > 1]) for g in b.groups] == [16, 16, 16, 16, 16, 15, 15, 15]
    ctx = build(seed=1, parts=["sim"], services={"building": b})
    assert len(ctx.services["controllers"].cars) == 64
    pop = ctx.services["population"]
    assert len(pop.tenants) == 200 and len(pop.employees) == 20000
    assert len({e.tenant_id for e in pop.employees}) == 200
    assert pop.params["building"] == "design_limit"
    assert ctx.services["car_states"].dispatchable("G8") == [f"G8-{n}" for n in range(1, 9)]


def test_design_limit_latency_hooks_are_populated():
    (r0,) = S.SCENARIOS["SIM-10"].runs
    short = r0.__class__(**{**r0.__dict__, "warmup_s": 30.0, "measure_s": 90.0, "clear_s": 30.0,
                            "workload": {**r0.workload, "dashboard_users": 5,
                                         "dashboard_requests": 2, "camera_per_day": 200000.0}})
    res = S.run_setup(short, 1)
    lat = res.latency
    assert lat["assignment_s"]["n"] > 400 and lat["assignment_s"]["p99"] is not None
    assert lat["lookup_s"]["n"] >= 400 * 2 - 5 and lat["lookup_s"]["p95"] == 0.1
    assert lat["camera_events"] > 0
    assert lat["dashboard"]["requests"] == 10 and lat["dashboard"]["errors"] == 0
    assert lat["dashboard"]["response_s"]["max"] > 0
    assert res.params["building"]["n_cars"] == 64


def test_pop_default_building_is_unchanged():
    assert Population.build(build(seed=1, parts=[]).rng).params["total_employees"] == 2500


def test_eco_quiet_is_an_alias_of_eco():
    a, b = PROFILES["eco"], PROFILES["eco_quiet"]
    assert (a.rate_per_min, a.up, a.down, a.inter) == (b.rate_per_min, b.up, b.down, b.inter)
