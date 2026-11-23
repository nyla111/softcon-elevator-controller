"""SIM-15 needs actual occupied motion and nonempty waiting-call reassignment."""
from pathlib import Path

import pytest

from ems.contracts import events as E
from ems.safety.acceptance import _waiting_on
from ems.sim.scripted import load, run_list

FIXTURE = Path(__file__).resolve().parents[2] / "docs/passenger_lists/sim15_brake_fault.csv"


@pytest.mark.parametrize("seed", range(1, 11))
def test_sim15_fixture_explicit_brake_and_real_waiting_reassignment(seed):
    ctx, checks = run_list(load(FIXTURE), seed=seed)
    faults = [e for e in ctx.bus.of_type(E.ControllerFault) if e.car_id == "A"]
    assert len(faults) == 1 and faults[0].code == "brake" and faults[0].between_floors
    fault = faults[0]
    assert all(c.ok for c in checks), checks
    waiting = _waiting_on(ctx, fault)
    assert "sim15-already-waiting" in waiting
    before = [e.status for e in ctx.bus.of_type(E.CarStatusUpdated)
              if e.status.car_id == "A" and e.t < fault.t][-1]
    assert before.passengers > 0 and before.position != before.floor
    reassigned = {e.call_id for e in ctx.bus.of_type(E.CallReassigned)
                  if fault.t <= e.t and e.from_car == "A" and e.to_car != "A"}
    assert waiting <= reassigned
    assert ctx.get("dispatcher").calls["sim15-already-waiting"].call.created_at == 10
    assert not [e for e in ctx.bus.of_type(E.CallReassigned) if e.call_id == "sim15-onboard"]
    assert not [c for c in ctx.get("controllers").command_log if c[1] == "A"
                and fault.t <= c[0] < fault.t + 120]
    alerts = [e for e in ctx.bus.of_type(E.AlertRaised)
              if e.car_id == "A" and e.kind == "entrapment"]
    assert len(alerts) == 1 and fault.t <= alerts[0].t <= fault.t + 10
    inbox = [n for n in ctx.get("notifier").inbox if "entrapment car A" in n[2]]
    assert len(inbox) == 1 and fault.t <= inbox[0][0] <= fault.t + 10
    assert {"security", "contractor"} <= set(inbox[0][1])
