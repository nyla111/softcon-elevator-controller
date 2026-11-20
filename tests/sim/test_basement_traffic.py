"""D-08 traffic model: basement trips and the change at the lobby (Part 1)."""
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


def test_low_rise_cars_serve_the_basements():
    assert {-2, -1} <= set(config.GROUPS["LOW"]["floors"])
    assert not {-2, -1} & set(config.GROUPS["HIGH"]["floors"])


def test_basement_share_of_up_trips_and_the_change_at_the_lobby():
    ctx, gen = _run("up_peak", 3600.0)
    arrivals = ctx.bus.of_type(PassengerArrived)
    origins = Counter(a.origin for a in arrivals)
    up_starts = origins[-2] + origins[-1] + origins[config.LOBBY] - gen.transfers
    assert 0.25 < origins[-2] / up_starts < 0.35
    assert origins[-1] > 0
    # basement rides only go to the lobby or to a low-rise floor, on the low-rise group
    for a in arrivals:
        if a.origin < config.LOBBY:
            assert a.group == "LOW" and (a.destination == config.LOBBY
                                        or a.destination in config.LOW_FLOORS)
    # every basement -> high-rise trip changes at the lobby and continues on a high-rise car
    to_lobby = [a for a in arrivals if a.origin < config.LOBBY and a.destination == config.LOBBY]
    assert gen.transfers == len(to_lobby) > 0
    sim = ctx.services["controllers"]
    assert all(p.state is PassengerState.DONE for p in sim.passengers.values())


def test_down_trips_to_the_car_park_change_at_the_lobby():
    ctx, gen = _run("evening", 1800.0)
    arrivals = ctx.bus.of_type(PassengerArrived)
    to_basement = [a for a in arrivals if a.destination < config.LOBBY]
    assert to_basement and all(a.group == "LOW" for a in to_basement)
    from_high = [a for a in arrivals if a.group == "HIGH" and a.destination == config.LOBBY]
    assert gen.transfers > 0 and gen.transfers <= len(from_high)


def test_appendix_b_profiles_stay_lobby_only_by_default():
    ctx, gen = _run("up_peak", 1200.0, basements=False)
    assert all(a.origin >= config.LOBBY and a.destination >= config.LOBBY
               for a in ctx.bus.of_type(PassengerArrived))
    assert gen.transfers == 0


def test_shares_are_documented_constants():
    assert traffic.PARKING_SHARE == 0.30 and traffic.DOCK_SHARE == 0.02

