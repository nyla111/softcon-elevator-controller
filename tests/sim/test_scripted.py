from pathlib import Path

import pytest

from ems.contracts.events import PassengerArrived
from ems.core.app import build
from ems.sim.baseline import install_dispatch_standin
from ems.sim.harness import run
from ems.sim.passengers import PassengerState
from ems.sim.scripted import ScriptedTraffic, ScriptedTrip, load, loads, run_list

DEMO = Path(__file__).resolve().parents[2] / "docs" / "examples" / "passengers_demo.csv"


def test_csv_and_json_give_the_same_trips():
    csv_text = "t,origin,destination,card,accessible,vip\n10,1,5,card-L5-001,,\n0,12,1,,true,no\n"
    json_text = ('[{"t": 10, "origin": 1, "destination": 5, "card": "card-L5-001"},'
                 ' {"t": 0, "origin": 12, "destination": 1, "accessible": true, "vip": false}]')
    assert loads(csv_text) == loads(json_text) == [
        ScriptedTrip(0.0, 12, 1, None, True, False),
        ScriptedTrip(10.0, 1, 5, "card-L5-001")]               # sorted by time


@pytest.mark.parametrize("text, message", [
    ("t,origin,destination\nx,1,5\n", "t must be a number"),
    ("t,origin,destination\n-1,1,5\n", ">= 0"),
    ("t,origin\n0,1\n", "needs origin and destination"),
    ("t,event\n0,earthquake\n", "unknown event"),
    ("t,origin,destination,expect\n0,1,5,arrived\n", "expect must be one of"),
    ("t,event,expect\n0,fire_alarm,delivered\n", "alert:<kind>"),
    ("t,origin,destination,vip\n0,1,5,maybe\n", "vip must be true or false"),
])
def test_bad_rows_are_rejected(text, message):
    with pytest.raises(ValueError, match=message):
        loads(text)


@pytest.mark.parametrize("origin, destination, message", [
    (1, 1, "same floor"),
    (1, 99, "not the lobby or a tenant floor"),
    (5, 20, "different groups"),
])
def test_trips_are_checked_against_the_building(origin, destination, message):
    with pytest.raises(ValueError, match=message):
        ScriptedTraffic(build(seed=1), [ScriptedTrip(0.0, origin, destination)])


def test_trips_arrive_on_time_with_their_defaults():
    ctx = build(seed=1)
    ScriptedTraffic(ctx, [ScriptedTrip(5.0, 1, 12), ScriptedTrip(9.0, 20, 1, vip=True,
                                                                  passenger_id="me")]).start()
    ctx.clock.run_until(10.0)
    a, b = ctx.bus.of_type(PassengerArrived)
    pop = ctx.services["population"]
    assert (a.t, a.passenger_id, a.group, a.panel_id) == (5.0, "S0001", "LOW", "panel-L1")
    assert a.credential_id == pop.on_floor(12)[0].credential_id     # default card
    assert (b.t, b.passenger_id, b.group, b.vip) == (9.0, "me", "HIGH", True)


def test_demo_list_runs_end_to_end():
    r = run("scripted", seed=1, script=load(DEMO), setup=install_dispatch_standin,
            warmup_s=0.0, measure_s=120.0, clear_s=900.0, out_dir=None, keep_ctx=True)
    people = r.ctx.services["controllers"].passengers
    assert people["refused-L18"].state is PassengerState.REFUSED    # card-L5-001 has no L18
    assert people["accepted-L5"].state is PassengerState.DONE
    assert r.metrics["n_delivered"] == 5 and r.metrics["calls_refused"] == 1
    assert r.metrics["calls_not_cleared"] == 0


def test_visitor_card_gets_a_pass_for_its_floor():
    ctx = build(seed=1)
    traffic = ScriptedTraffic(ctx, loads("t,origin,destination,card\n0,1,12,visitor:12\n"))
    card = traffic.arrivals[0]["card"]
    assert ctx.services["acs"].lookup(card).allowed_floors >= {12}
    with pytest.raises(ValueError, match="visitor:<floor>"):
        ScriptedTraffic(ctx, loads("t,origin,destination,card\n0,1,12,visitor:x\n"))


def test_failed_expectations_are_reported():
    text = ("t,origin,destination,card,event,car,expect\n"
            "0,1,18,card-L5-001,,,delivered\n"            # refused in reality
            "5,,,,check,,alert:entrapment\n")             # nothing happens
    _, checks = run_list(loads(text))
    assert [(c.expected, c.actual, c.ok) for c in checks] == [
        ("delivered", "refused", False), ("alert:entrapment", "no_alert:entrapment", False)]

