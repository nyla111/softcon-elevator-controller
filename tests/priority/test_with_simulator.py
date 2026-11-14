"""Part 5 against Part 1's REAL simulator (cars, passengers, controllers, mock ACS), with the
Part 2/3/4 contract stubs + Part 5 stand-ins (ems/priority/standins.py). This is integration
with the simulator only, not with Part 2/3/4 (still stubs at the base commit)."""
from ems.contracts.events import (CallAssigned, HeartbeatLost, PassengerArrived,
                                  PriorityTripEnded, PriorityTripStarted)
from ems.contracts.types import CarState
from ems.core.app import build
from ems.priority.standins import (VipReplacementPopulation, install_scenario_standins,
                                   integration_gaps)
from ems.sim.faults import measured
from ems.sim.harness import run
from ems.sim.traffic import PROFILES, TrafficGenerator


def _arrivals(ctx):
    return [(e.t, e.passenger_id, e.origin, e.destination, e.group, e.credential_id,
             e.accessible, e.vip) for e in ctx.bus.of_type(PassengerArrived)]


def _traffic(replace: bool):
    ctx = build(seed=4)
    if replace:
        ctx.services["population"] = VipReplacementPopulation(
            ctx, ctx.services["population"], "HIGH",
            [(measured(m), measured(m + 1)) for m in range(20, 30)])
    TrafficGenerator.fixed(ctx, PROFILES["up_peak"], 0.0, measured(31)).start()
    ctx.clock.run_until(measured(31))
    return ctx


def test_vip_replacement_adds_no_traffic_and_flips_exactly_ten_ordinary_arrivals():
    base, swapped = _traffic(False), _traffic(True)
    a, b = _arrivals(base), _arrivals(swapped)
    assert len(a) == len(b)
    diff = [(x, y) for x, y in zip(a, b) if x != y]
    assert len(diff) == 10
    for x, y in diff:
        assert x[:7] == y[:7] and (x[6], x[7]) == (False, False) and y[7] is True
        assert y[4] == "HIGH"
    pop = swapped.services["population"]
    assert pop.replaced == [y[1] for _, y in diff]
    minutes = sorted(int((y[0] - measured(0)) // 60) for _, y in diff)
    assert minutes == list(range(20, 30))


def test_up_peak_run_is_deterministic_with_part5_and_leaves_nothing_held():
    r1 = run("up_peak", 2, setup=install_scenario_standins, keep_ctx=True, out_dir=None,
             start_hhmm="07:00", measure_s=1200.0)
    r2 = run("up_peak", 2, setup=install_scenario_standins, keep_ctx=True, out_dir=None,
             start_hhmm="07:00", measure_s=1200.0)
    assert r1.event_digest == r2.event_digest
    p = r1.ctx.services["priority"]
    started = r1.ctx.bus.of_type(PriorityTripStarted)
    assert started and len(r1.ctx.bus.of_type(PriorityTripEnded)) == len(started)
    assert p.holds == {} and p.trips == {} and p.reopen == {}
    assert r1.metrics["calls_not_cleared"] == 0
    # each exclusive VIP call was assigned to the car Part 5 sent (by the Part 2 stand-in)
    assigned = {e.call_id: e.car_id for e in r1.ctx.bus.of_type(CallAssigned)}
    for d in p.decisions:
        if d["ok"]:
            assert assigned[d["call_id"]] == d["car_id"]
    sim = r1.ctx.services["controllers"]
    assert all(not c.closed_to_new_calls and not c.independent for c in sim.cars.values())


def test_heartbeat_loss_ends_a_vip_trip_and_the_car_is_reopened_later():
    ctx = build(seed=1, start=None)
    install_scenario_standins(ctx)
    sim, p = ctx.services["controllers"], ctx.services["priority"]
    TrafficGenerator.fixed(ctx, PROFILES["up_peak"], 0.0, 900.0).start()
    trip = None
    while trip is None and ctx.clock.now() < 900:
        ctx.clock.run_until(ctx.clock.now() + 1)
        trip = next(iter(p.trips.values()), None)
    assert trip is not None
    sim.set_disconnected(trip.car_id, True)
    ctx.clock.run_until(ctx.clock.now() + 5)
    assert any(e.car_id == trip.car_id for e in ctx.bus.of_type(HeartbeatLost))
    assert trip.trip_id not in p.trips and trip.car_id in p.reopen
    sim.set_disconnected(trip.car_id, False)
    ctx.clock.run_until(ctx.clock.now() + 3)
    assert trip.car_id not in p.reopen and not sim.cars[trip.car_id].closed_to_new_calls


def test_integration_gaps_name_every_stub():
    ctx = build(seed=1)
    install_scenario_standins(ctx)
    gaps = " ".join(integration_gaps(ctx))
    assert "Part 2" in gaps and "Part 3" in gaps and "Part 4" in gaps and "StaffDirectory" in gaps
    assert ctx.services["car_states"].state("A") is CarState.GROUP_SERVICE


def test_vip_call_is_not_lost_when_its_car_is_taken_for_medical_before_boarding():
    """Regression A1 reproduced on the real simulator: before the fix the VIP stayed WAITING
    for ever on a car that had no stop for them."""
    ctx = build(seed=1)
    install_scenario_standins(ctx)
    sim, p = ctx.services["controllers"], ctx.services["priority"]
    for c in ("F", "G", "H", "S"):
        sim.inject_fault(c, "F-X")
    ctx.clock.run_until(5)
    ctx.bus.publish(PassengerArrived(5, "VIP1", "panel-L20", 20, 1, "HIGH", "card-L20-001",
                                     False, True))
    assert p.decisions[-1]["car_id"] == "E"
    ctx.clock.run_until(8)
    out = p.request_medical("staff-security-1", 25, 1)
    assert out.car_id == "E"
    ctx.clock.run_until(60)
    assert p.medical_depart("staff-security-1", out.ref).ok
    ctx.clock.run_until(200)
    assert p.finish_trip("staff-security-1", out.ref).reason == "ok"
    ctx.clock.run_until(600)
    vip = sim.passengers["VIP1"]
    assert vip.alighted_at is not None and vip.destination == 1 and not vip.evacuated


def test_send_to_lobby_on_the_real_simulator_keeps_riders_and_their_destinations():
    """FR-128 end to end on Part 1's simulator: a car with riders goes to the lobby with its
    doors held; riders are not counted as delivered there; after security finishes, every
    rider reaches their own floor."""
    from ems.contracts.events import DoorsOpened, PassengerAlighted
    ctx = build(seed=3)
    install_scenario_standins(ctx)
    sim, p = ctx.services["controllers"], ctx.services["priority"]
    TrafficGenerator.fixed(ctx, PROFILES["up_peak"], 0.0, 900.0).start()
    car = None
    while car is None:
        ctx.clock.run_until(ctx.clock.now() + 1)
        car = next((c for c in sim.cars.values() if c.group == "HIGH" and len(c.riders) >= 3
                    and c.floor >= 20 and c.car_id not in p.holds), None)
    riders = [r.passenger_id for r in car.riders]
    t0 = ctx.clock.now()
    out = p.send_to_lobby("staff-security-1", car.car_id)
    assert out.ok
    ctx.clock.run_until(t0 + 200)
    lobby = [e for e in ctx.bus.of_type(DoorsOpened) if e.car_id == car.car_id and e.t > t0
             and e.floor == 1]
    assert lobby and car.doors.name == "OPEN" and car.held          # doors held at L1
    early = [e for e in ctx.bus.of_type(PassengerAlighted) if e.call_id in riders
             and e.t > t0 and e.floor != sim.passengers[e.call_id].destination]
    assert not early                                                 # nobody dropped at L1
    assert p.finish_trip("staff-security-1", out.ref).reason == "ok"
    ctx.clock.run_until(t0 + 1200)
    for rid in riders:
        r = sim.passengers[rid]
        assert r.alighted_at is not None and not r.evacuated, rid
    assert p.holds == {} and not car.closed_to_new_calls
