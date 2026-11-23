from datetime import datetime

from ems.contracts import events as E
from ems.core.app import build
from ems.sim.scripted import ScriptedTraffic, loads


def test_overload_reassigns_unboarded_vip_after_priority_releases_trip():
    ctx = build(start=datetime(2026, 11, 16, 7))
    sim = ctx.get("controllers")
    sim.overload_burst("A")
    ScriptedTraffic(ctx, loads("t,origin,destination,card,vip,passenger_id\n"
                              "0,1,15,card-L15-001,true,waiting-vip\n")).start()
    ctx.clock.run_until(1)
    assert ctx.get("dispatcher").calls["waiting-vip"].state == "vip"
    assert sim.passengers["waiting-vip"].car_id == "A"
    ctx.clock.run_until(100)
    overloaded = ctx.bus.of_type(E.OverloadDetected)[0]
    messages = [e for e in ctx.bus.of_type(E.PanelMessage)
                if e.target == "car:A" and overloaded.t <= e.t <= overloaded.t + 1]
    assert messages and all(e.voice and e.text_vi and "Next car:" in e.text_en for e in messages)
    reassigned = [e for e in ctx.bus.of_type(E.CallReassigned) if e.call_id == "waiting-vip"]
    assert len(reassigned) == 1 and reassigned[0].from_car == "A" and reassigned[0].to_car != "A"
    rider = sim.passengers["waiting-vip"]
    assert rider.alighted_at is not None and not rider.evacuated and rider.car_id != "A"
    assert ctx.get("dispatcher").calls["waiting-vip"].call.created_at == 0
    board = next(e for e in ctx.bus.of_type(E.PassengerBoarded) if e.call_id == "waiting-vip")
    assert reassigned[0].t < board.t
