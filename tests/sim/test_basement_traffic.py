"""D-08 / D-10 traffic model: basement trips ride straight to their floor (Part 1)."""
from collections import Counter

from ems.contracts import config
from ems.contracts.events import PassengerArrived
from ems.core.app import build
from ems.sim import traffic
from ems.sim.baseline import install_dispatch_standin
from ems.sim.passengers import PassengerState
from ems.sim.traffic import PROFILES, TrafficGenerator


def _run(profile, seconds, basements=True, seed=3):
    ctx = build(seed=seed, start=None)
    install_dispatch_standin(ctx)
    gen = TrafficGenerator.fixed(ctx, PROFILES[profile], 0.0, seconds, basements=basements)
    gen.start()
    ctx.clock.run_until(seconds + 1800.0)
    return ctx, gen


def test_both_passenger_groups_serve_the_basements():
    assert {-2, -1} <= set(config.GROUPS["LOW"]["floors"])
    assert {-2, -1} <= set(config.GROUPS["HIGH"]["floors"])


def test_basement_share_of_up_trips_and_the_change_at_the_lobby():
    ctx, gen = _run("up_peak", 3600.0)
    arrivals = ctx.bus.of_type(PassengerArrived)
    origins = Counter(a.origin for a in arrivals)
    up_starts = origins[-2] + origins[-1] + origins[config.LOBBY] - gen.transfers
    assert 0.25 < origins[-2] / up_starts < 0.35
    assert origins[-1] > 0
    # D-10: a basement ride goes straight to its floor, in that floor's group: no change at L1
    from_basement = [a for a in arrivals if a.origin < config.LOBBY]
    assert {a.group for a in from_basement} == {"LOW", "HIGH"}
    for a in from_basement:
        assert a.destination in config.GROUPS[a.group]["floors"] and a.destination != config.LOBBY
    assert gen.transfers == 0
    sim = ctx.services["controllers"]
    assert all(p.state is PassengerState.DONE for p in sim.passengers.values())


def test_down_trips_to_the_car_park_ride_straight_down():
    ctx, gen = _run("evening", 1800.0)
    arrivals = ctx.bus.of_type(PassengerArrived)
    to_basement = [a for a in arrivals if a.destination < config.LOBBY]
    assert {a.group for a in to_basement} == {"LOW", "HIGH"}
    assert all(a.origin > config.LOBBY for a in to_basement) and gen.transfers == 0


def test_appendix_b_profiles_stay_lobby_only_by_default():
    ctx, gen = _run("up_peak", 1200.0, basements=False)
    assert all(a.origin >= config.LOBBY and a.destination >= config.LOBBY
               for a in ctx.bus.of_type(PassengerArrived))
    assert gen.transfers == 0


def test_shares_are_documented_constants():
    assert traffic.PARKING_SHARE == 0.30 and traffic.DOCK_SHARE == 0.02

