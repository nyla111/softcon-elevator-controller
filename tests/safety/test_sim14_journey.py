"""SIM-14 real passenger-list journey and exact recovery/command windows."""
import json
import os
from dataclasses import asdict
from pathlib import Path

import pytest

from ems.contracts import events as E
from ems.sim.scripted import load, run_list

FIXTURE = Path(__file__).resolve().parents[2] / "docs/passenger_lists/sim14_power_loss.csv"


@pytest.mark.parametrize("seed", range(1, 11))
def test_sim14_fixture_has_trapped_rider_and_complete_authorized_recovery(seed, monkeypatch):
    from ems.safety.service import SafetyService
    original = SafetyService.confirm_recovery
    attempts = []

    def observe(service, role):
        result = original(service, role)
        attempts.append((service.ctx.clock.now(), result))
        return result

    monkeypatch.setattr(SafetyService, "confirm_recovery", observe)
    ctx, checks = run_list(load(FIXTURE), seed=seed)
    assert all(check.ok for check in checks), checks
    assert attempts == [(141, False), (259, False), (261, True)]
    assert ctx.bus.of_type(E.GeneratorReady) == []
    reports = ctx.bus.of_type(E.RescueReport)
    assert {r.car_id for r in reports if r.status == "rescued_at_floor"} == set("BCDEFGHS")
    boards = ctx.bus.of_type(E.PassengerBoarded)
    assert any(e.call_id == "sim14-onboard" and e.car_id == "A" and e.t < 14 for e in boards)
    assert not [e for e in ctx.bus.of_type(E.PassengerAlighted)
                if e.call_id == "sim14-onboard" and e.t < 140]
    last_status = [e.status for e in ctx.bus.of_type(E.CarStatusUpdated)
                   if e.status.car_id == "A" and e.t < 140][-1]
    assert last_status.passengers == 1
    assert [(e.car_id, e.t) for e in ctx.bus.of_type(E.AlertRaised)
            if e.kind == "entrapment"] == [("A", 74)]
    received = [n for n in ctx.get("notifier").inbox if "entrapment car A" in n[2]]
    assert len(received) == 1 and received[0][0] == 74
    assert all(term in received[0][3] for term in ("floor=", "load=", "telemetry_t="))
    assert not [c for c in ctx.get("controllers").command_log if 14 <= c[0] < 261]
    assert not [e for e in ctx.bus.of_type(E.CallAssigned) if 14 <= e.t < 261]
    assert not [e for e in boards if e.call_id == "sim14-during-outage" and e.t < 261]
    requested = {e.call.call_id: e.call.created_at for e in ctx.bus.of_type(E.CallRequested)}
    assert requested["sim14-during-outage"] == 15
    assert ctx.get("dispatcher").calls["sim14-during-outage"].call.created_at == 15
    assert ctx.get("car_states").recovery_log == [(261, "fm_confirmed")]
    assert ctx.get("controllers").undelivered() == 0


@pytest.mark.parametrize("seed", range(1, 11))
def test_sim14_registry_full_acceptance_each_seed(seed):
    from ems.sim.scenarios import SCENARIOS, run_setup
    scenario = SCENARIOS["SIM-14"]
    result = run_setup(scenario.runs[0], seed)
    criteria = scenario.check(result, result.ctx)
    evidence_dir = os.environ.get("EMS_SAFETY_EVIDENCE_DIR")
    if evidence_dir:
        def redact(value):
            if isinstance(value, dict):
                return {k: redact(v) for k, v in value.items()
                        if k not in ("credential_id", "credential_ids")}
            if isinstance(value, (tuple, list)):
                return [redact(v) for v in value]
            return value
        ctx = result.ctx
        payload = {"seed": seed, "scenario": "SIM-14", "criteria": criteria,
                   "config": result.params, "event_digest": result.event_digest,
                   "commands": ctx.get("controllers").command_log,
                   "notifications": ctx.get("notifier").inbox,
                   "observations": ctx.get("safety_acceptance"),
                   "events": [{"type": type(e).__name__, **redact(asdict(e))}
                              for e in ctx.bus.log]}
        path = Path(evidence_dir) / f"sim14-seed{seed}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, default=str), encoding="utf-8")
    assert criteria and all(passed for _, passed, _ in criteria), criteria
    assert result.metrics["calls_not_cleared"] == 0
