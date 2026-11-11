"""Traffic generator: mixes, rates, floors, groups, 24 h schedule."""
from collections import Counter

from ems.contracts import config
from ems.contracts.events import PassengerArrived
from ems.core.app import build
from ems.sim.traffic import PROFILES, TENANT_FLOORS, TrafficGenerator

HOUR = 3600.0


def generate(profile, seed=1, hours=2.0, **kw):
    ctx = build(seed=seed, parts=["sim"])
    TrafficGenerator.fixed(ctx, PROFILES[profile], 0.0, hours * HOUR, **kw).start()
    ctx.clock.run_until(hours * HOUR)
    return ctx.bus.of_type(PassengerArrived)


def shares(arrivals):
    n = len(arrivals)
    up = sum(a.origin == 1 for a in arrivals) / n
    down = sum(a.destination == 1 for a in arrivals) / n
    return up, down, 1 - up - down


def test_profile_table():
    assert {k: v.rate_per_min for k, v in PROFILES.items()} == {
        "up_peak": 75, "lunch": 75, "evening": 75, "balanced": 20, "eco": 2}


def test_up_peak_rate_and_mix():
    arrivals = generate("up_peak")
    assert abs(len(arrivals) / 120 - 75) < 4               # per minute
    up, down, inter = shares(arrivals)
    assert abs(up - 0.90) < 0.02 and down == 0 and abs(inter - 0.10) < 0.02


def test_lunch_and_evening_mix():
    up, down, inter = shares(generate("lunch"))
    assert abs(up - 0.45) < 0.03 and abs(down - 0.45) < 0.03 and abs(inter - 0.10) < 0.02
    up, down, inter = shares(generate("evening"))
    assert up == 0 and abs(down - 0.90) < 0.02 and abs(inter - 0.10) < 0.02


def test_balanced_and_eco_mix():
    for name, rate in (("balanced", 20), ("eco", 2)):
        arrivals = generate(name, hours=20 if name == "eco" else 4)
        hours = 20 if name == "eco" else 4
        assert abs(len(arrivals) / (hours * 60) - rate) < rate * 0.12
        up, down, inter = shares(arrivals)
        assert abs(up - 0.4) < 0.07 and abs(down - 0.4) < 0.07 and abs(inter - 0.2) < 0.07


def test_exponential_gaps():
    ts = [a.t for a in generate("up_peak")]
    gaps = [b - a for a, b in zip(ts, ts[1:])]
    mean = sum(gaps) / len(gaps)
    assert abs(mean - 0.8) < 0.05
    var = sum((g - mean) ** 2 for g in gaps) / len(gaps)
    assert abs(var ** 0.5 / mean - 1) < 0.08               # exponential: std == mean


def test_floors_stay_inside_the_passengers_group():
    arrivals = generate("lunch")
    for a in arrivals:
        allowed = set(config.GROUPS[a.group]["floors"])
        assert a.origin in allowed and a.destination in allowed and a.origin != a.destination
    used = Counter(a.destination for a in generate("up_peak") if a.origin == 1)
    assert set(used) == set(TENANT_FLOORS["LOW"]) | set(TENANT_FLOORS["HIGH"])   # uniform cover
    low_share = sum(v for f, v in used.items() if f <= 15) / sum(used.values())
    assert abs(low_share - 14 / 29) < 0.03


def test_accessible_and_vip_shares_configurable():
    arrivals = generate("up_peak", hours=4)
    n = len(arrivals)
    assert abs(sum(a.accessible for a in arrivals) / n - 0.05) < 0.01
    assert abs(sum(a.vip for a in arrivals) / n - 0.01) < 0.005
    none = generate("balanced", accessible_share=0.0, vip_share=0.0)
    assert not any(a.accessible or a.vip for a in none)
    all_acc = generate("balanced", accessible_share=1.0)
    assert all(a.accessible for a in all_acc)


def test_weekday_schedule_segments():
    ctx = build(seed=1, parts=["sim"])
    gen = TrafficGenerator.weekday(ctx)
    expected = ["eco", "up_peak", "balanced", "lunch", "balanced", "evening", "balanced", "eco"]
    assert [s.profile.name for s in gen.segments] == expected
    assert gen.segments[0].start == 0 and gen.segments[-1].end == 24 * HOUR
    assert [s.start for s in gen.segments][1] == 7 * HOUR


def test_weekday_rate_changes_with_block():
    ctx = build(seed=3, parts=["sim"])
    TrafficGenerator.weekday(ctx).start()
    ctx.clock.run_until(24 * HOUR)
    per_hour = Counter(int(a.t // HOUR) for a in ctx.bus.of_type(PassengerArrived))
    assert per_hour[3] < 400 and per_hour[8] > 3500        # eco night vs up-peak morning
    assert 800 < per_hour[10] < 1600                       # balanced 20/min


def test_unique_passenger_ids():
    ids = [a.passenger_id for a in generate("balanced")]
    assert len(ids) == len(set(ids))
