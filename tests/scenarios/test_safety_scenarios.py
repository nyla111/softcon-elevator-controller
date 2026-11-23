"""Seeds 1-10 exercise real simulator behavior, with acceptance blockers kept explicit.

Passing these tests proves only the named Safety subcriteria. Full SIM acceptance stays
BLOCKED in the registry, and metrics_only never returns passed=True.
"""
import pytest

from ems.contracts import events as E
from ems.contracts.types import Call, CallSource
from ems.core.app import build
from ems.safety.acceptance import _reassignment_check
from ems.sim.scenarios import SCENARIOS, run_scenario

SAFETY_CRITERIA = {
    "SIM-08": {"fire at 17:00 Rush transition", "zero EMS controller commands throughout fire handling",
               "isolated cars remain excluded"},
    "SIM-13": {"at least three overload bursts exercised", "both bulky items flagged by Monitor"},
    "SIM-14": {"failed generator and exactly one rescue report missing",
               "POSSIBLE ENTRAPMENT for every missing rescue by 60 s", "no automatic healthy recovery"},
    "SIM-15": {"occupied between-floor brake fault exercised",
               "urgent entrapment alert and contractor call-out within 10 s",
               "no EMS command to the faulted car"},
}


@pytest.mark.slow
@pytest.mark.parametrize("seed", range(1, 11))
@pytest.mark.parametrize("sid", SAFETY_CRITERIA)
def test_safety_subcriteria_on_real_scenarios(sid, seed):
    result = run_scenario(sid, seed, metrics_only=True)
    if sid == "SIM-13":
        assert result["ready"] is True and result["passed"] is True, result["criteria"]
        return
    assert result["ready"] is False and result["passed"] is None
    actual = {c["criterion"]: c for c in result["criteria"]}
    assert SAFETY_CRITERIA[sid] <= actual.keys()
    assert all(actual[c]["passed"] for c in SAFETY_CRITERIA[sid]), result["criteria"]
    # Honest negative evidence: cross-part acceptance is still incomplete. SIM-13 and SIM-15
    # only waited for Part 2 (re-assignment), which is real now: some seeds pass every
    # criterion, so the negative check stays for SIM-08 and SIM-14 only.
    if sid in ("SIM-08", "SIM-14"):
        assert any(not c["passed"] for c in actual.values())


def test_sim_17_is_blocked_without_fabricating_panel_behavior():
    scenario = SCENARIOS["SIM-17"]
    assert not scenario.ready
    criteria = scenario.check(None, None)
    assert len(criteria) == 1 and not criteria[0][1] and "BLOCKED" in criteria[0][2]
    assert run_scenario("SIM-17", 1)["passed"] is None


def test_reassignment_check_excludes_boarded_riders_and_requires_real_waiting_reassignment():
    ctx = build(parts=[])
    for call_id in ("waiting", "boarded"):
        call = Call(call_id, 1, 5, "LOW", 0, CallSource.DESTINATION_PANEL)
        ctx.bus.publish(E.CallRequested(0, call))
        ctx.bus.publish(E.CallAssigned(0, call_id, "A", 0))
    ctx.bus.publish(E.PassengerBoarded(1, "boarded", "A"))
    incident = E.ControllerFault(2, "A", "brake", True, False)
    ctx.bus.publish(incident)
    assert not _reassignment_check(ctx, [incident])[1]
    ctx.bus.publish(E.CallReassigned(2, "waiting", "A", "B", "fault"))
    assert _reassignment_check(ctx, [incident])[1]
    ctx.bus.publish(E.CallReassigned(3, "boarded", "A", "B", "fault"))
    assert not _reassignment_check(ctx, [incident])[1]
