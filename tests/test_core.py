"""Contract tests: must always pass. Owned by: Tech lead."""
from fastapi.testclient import TestClient

from ems.contracts.events import CallAssigned, CallRequested
from ems.contracts.types import Call, CallPriority, CallSource, CarState
from ems.core.app import build
from ems.web.server import app


def test_clock_and_bus_are_ordered_and_logged():
    ctx = build(seed=1)
    seen = []

    def on_request(e):
        seen.append("request")
        ctx.bus.publish(CallAssigned(ctx.clock.now(), e.call.call_id, "F", 12.0))
        seen.append("handler done")          # assigned is delivered after this

    ctx.bus.subscribe(CallRequested, on_request)
    ctx.bus.subscribe(CallAssigned, lambda e: seen.append("assigned"))
    call = Call("c1", 1, 18, "HIGH", 5.0, CallSource.DESTINATION_PANEL)
    ctx.clock.call_at(5, lambda: ctx.bus.publish(CallRequested(ctx.clock.now(), call)))
    ctx.clock.run_until(10)

    assert seen == ["request", "handler done", "assigned"]
    assert ctx.clock.now() == 10
    assert [type(e) for e in ctx.bus.log] == [CallRequested, CallAssigned]


def test_same_seed_same_randomness():
    assert build(seed=3).rng.random() == build(seed=3).rng.random()


def test_precedence_orders():
    assert CarState.FIRE_RECALL.outranks(CarState.VIP_EXCLUSIVE)
    assert not CarState.GROUP_SERVICE.outranks(CarState.STANDBY)
    assert sorted([CallPriority.VIP, CallPriority.MEDICAL, CallPriority.ACCESSIBLE]) == \
        [CallPriority.MEDICAL, CallPriority.ACCESSIBLE, CallPriority.VIP]


def test_web_state_endpoint():
    client = TestClient(app)
    data = client.get("/api/state").json()
    assert len(data["cars"]) == 9
    assert client.post("/api/advance?seconds=60").json()["t"] == 60
