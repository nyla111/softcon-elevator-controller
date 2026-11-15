"""Optional cross-branch audit against an UNMODIFIED detached Priority worktree.

Set SAFETY_PRIORITY_AUDIT_TREE to the checkout at PRIORITY_COMMIT. Load its package under
an isolated module name, using this working tree's actual Safety/Simulator/contracts.
Only staff identity is a fixture; no Part 5 state or controller implementation is mocked.
Strict xfails identify confirmed defects outside Part 4's editing scope, not acceptance passes.
"""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ems.contracts import events as E
from ems.contracts.types import Call, CallPriority, CallSource, Role
from ems.contracts.types import CarState as S
from ems.core.app import build
from ems.sim.building import BuildingSpec

PRIORITY_COMMIT = "59c5d918887313e8fe182f5afd1c295cddf93bbe"


@pytest.fixture(scope="module")
def priority_package():
    tree = os.environ.get("SAFETY_PRIORITY_AUDIT_TREE")
    if not tree:
        pytest.skip("cross-branch audit requires SAFETY_PRIORITY_AUDIT_TREE; see docs/safety/AUDIT.md")
    root = Path(tree).resolve()
    # Windows-created worktree metadata contains D:/ paths, which Linux git cannot follow.
    # Verify every loaded file against the pinned Git object through the main checkout.
    repo = Path(__file__).resolve().parents[2]
    for filename in ("__init__", "service", "goods", "maintenance", "auth", "policy", "standins"):
        relative = f"ems/priority/{filename}.py"
        expected = subprocess.check_output(["git", "show", f"{PRIORITY_COMMIT}:{relative}"], cwd=repo)
        if (root / relative).read_bytes().replace(b"\r\n", b"\n") != expected.replace(b"\r\n", b"\n"):
            pytest.fail(f"Priority audit source differs from pinned commit: {relative}")
    name = "ems._priority_safety_audit"
    spec = importlib.util.spec_from_file_location(
        name, root / "ems/priority/__init__.py", submodule_search_locations=[str(root / "ems/priority")])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    yield module
    for key in list(sys.modules):
        if key == name or key.startswith(name + "."):
            del sys.modules[key]


@pytest.fixture
def environment(priority_package):
    def make(building=None):
        ctx = build(parts=["sim", "safety"], services={"building": building} if building else None)
        priority_package.register(ctx)
        p = ctx.services["priority"]
        auth = sys.modules[priority_package.__name__ + ".auth"]

        class Staff:
            def actor(self, credential_id):
                roles = {"security": Role.SECURITY, "fm": Role.FM, "tech": Role.TECHNICIAN,
                         "admin": Role.TENANT_ADMIN}
                return auth.Actor(credential_id, frozenset({roles[credential_id]}), "test-tenant")

        p.use_authorizer(Staff())
        return ctx, p, ctx.services["car_states"], ctx.services["controllers"]
    return make


def vip_call(call_id="vip", group="HIGH", destination=20, origin=1):
    return Call(call_id, origin, destination, group, 0, CallSource.DESTINATION_PANEL, CallPriority.VIP)


def recover(ctx, safety, sim, kind):
    if kind == "fire":
        ctx.services["devices"].fire.clear()
        assert sim.reset_fire()
    else:
        ctx.services["devices"].power.restore_mains()
        ctx.clock.run_until(120)
    safety.report_safety_clear(True)
    for car in safety.table.cars():
        safety.report_controller_normal(car, True)
    assert safety.confirm_recovery(Role.FM)


def emergency(ctx, kind):
    if kind == "fire":
        ctx.services["devices"].fire.alarm()
    else:
        ctx.services["devices"].power.lose_mains(generator_failed=True)


@pytest.mark.parametrize("kind", ["fire", "power"])
def test_active_vip_goods_cancel_without_commands_or_automatic_restart(environment, kind):
    ctx, p, safety, sim = environment()
    car = p.claim_call(vip_call())
    assert car is not None
    request = p.goods.request("admin", 0, 600, ["mover"], "S")
    assert request.ok and p.goods.approve("fm", request.ref).ok
    ctx.clock.run_until(0)
    move = p.goods.moves[request.ref]
    assert move.status == "active"
    mark = len(sim.command_log)
    emergency(ctx, kind)
    assert not p.trips and move.status == "cancelled_emergency"
    assert len(p.finished) == 1 and p.finished[0].end_reason == "emergency:" + kind
    assert len(ctx.bus.of_type(E.PriorityTripEnded)) == 1
    assert len(sim.command_log) == mark
    # Start APIs and explicit retry remain blocked throughout fire / locked recovery.
    assert not p.request_medical("security", 1, 20).ok
    assert p.claim_call(vip_call("new")) is None
    p.retry_reopen()
    assert len(sim.command_log) == mark
    recover(ctx, safety, sim, kind)
    # Existing Priority retry hook cleans flags, but a recovery event connection is still missing.
    p.retry_reopen()
    ctx.clock.run_until(650)
    assert not p.trips and move.status == "cancelled_emergency"
    assert not p.holds
    assert safety.state(car) is S.GROUP_SERVICE and safety.state("S") is S.GROUP_SERVICE
    assert len(ctx.bus.of_type(E.PriorityTripStarted)) == 1


def test_faulted_maintenance_cars_excluded_from_fresh_vip_and_medical_candidates(environment):
    _ctx, p, safety, _sim = environment()
    assert safety.request("E", S.FAULTED, "audit:physical")
    assert safety.request("F", S.MAINTENANCE, "audit:maintenance")
    assert {s.car_id for s in p.candidate_statuses("HIGH", (1, 20))} == {"G", "H"}
    assert p.claim_call(vip_call()) in {"G", "H"}
    medical = p.request_medical("security", 20, 25)
    assert medical.ok and medical.car_id not in {"E", "F"}


def test_normal_owner_release_does_not_erase_another_owner_maintenance(environment):
    _ctx, p, safety, _sim = environment()
    car = p.claim_call(vip_call())
    assert (S.VIP_EXCLUSIVE, "priority:vip") in safety.table.holds[car]
    assert safety.request(car, S.MAINTENANCE, "audit:maintenance")
    p.end_trip(p.trip_of_car(car), True, "audit", commands=False)
    assert safety.state(car) is S.MAINTENANCE
    assert safety.table.holds[car] == {(S.MAINTENANCE, "audit:maintenance")}


@pytest.mark.parametrize("kind", ["fire", "power"])
def test_accessible_dispatch_standin_respects_real_safety_guard(environment, priority_package, kind):
    ctx, _p, safety, sim = environment()
    spec = importlib.util.spec_from_file_location(
        priority_package.__name__ + ".standins", Path(priority_package.__file__).parent / "standins.py")
    standins = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = standins
    spec.loader.exec_module(standins)
    standins.PriorityAwareNearestCar(ctx)
    emergency(ctx, kind)
    ctx.bus.publish(E.PassengerArrived(0, "accessible", "panel", 1, 20, "HIGH", None, True, False))
    assert not ctx.bus.of_type(E.CallAssigned) and sim.command_log == []
    assert not safety.dispatchable("HIGH")


def test_single_mechanical_alert_owner_and_separate_safety_entrapment(environment):
    ctx, _p, _safety, sim = environment()
    sim.inject_fault("A", "brake")
    alerts = [a for a in ctx.bus.of_type(E.AlertRaised) if a.kind == "fault"]
    assert len(alerts) == 1 and alerts[0].recipients == ["contractor", "fm"]


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="BLOCKED P5-01: preemption changes Hold.kind without owner transfer")
@pytest.mark.parametrize("replacement", ["medical", "send_to_lobby"])
def test_preempted_vip_releases_its_recorded_owner(environment, replacement):
    ctx, p, safety, _sim = environment()
    car = p.claim_call(vip_call(origin=20, destination=25))
    if car is None:
        pytest.fail("audit setup failed to start the VIP trip")
    ctx.clock.run_until(3)  # E is closer to L20 than every other eligible car.
    outcome = (p.request_medical("security", 20, 25) if replacement == "medical"
               else p.send_to_lobby("security", car))
    assert outcome.ok and outcome.car_id == car
    assert p.finish_trip("security", outcome.ref).ok
    assert safety.state(car) is S.GROUP_SERVICE


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="BLOCKED P5-02: VIP-held medical candidates bypass effective Safety state")
@pytest.mark.parametrize("state", [S.FAULTED, S.MAINTENANCE])
def test_vip_preemption_rechecks_authoritative_state(environment, state):
    ctx, p, safety, sim = environment()
    car = p.claim_call(vip_call(origin=20, destination=25))
    if car is None:
        pytest.fail("audit setup failed to start the VIP trip")
    ctx.clock.run_until(3)
    assert safety.request(car, state, "audit:isolated")
    mark = len(sim.command_log)
    outcome = p.request_medical("security", 20, 25)
    assert outcome.car_id != car
    assert not [c for c in sim.command_log[mark:] if c[1] == car]


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="BLOCKED P5-03: cancelled VIP claim cache bypasses emergency check")
@pytest.mark.parametrize("kind", ["fire", "power"])
def test_replayed_vip_claim_is_not_assigned_during_emergency(environment, kind):
    ctx, p, _safety, _sim = environment()
    call = vip_call()
    assert p.claim_call(call) is not None
    emergency(ctx, kind)
    assert p.claim_call(call) is None


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="BLOCKED P5-04: no Safety recovery event retries deferred controller cleanup")
@pytest.mark.parametrize("kind", ["fire", "power"])
def test_deferred_priority_flags_clear_after_fm_confirmation(environment, kind):
    ctx, p, safety, sim = environment()
    car = p.claim_call(vip_call())
    emergency(ctx, kind)
    recover(ctx, safety, sim, kind)
    assert not p.reopen and not sim.cars[car].closed_to_new_calls


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="BLOCKED P5-05: fire latch reset depends on a service-group firefighter-off event")
def test_priority_recovers_after_fire_in_layout_without_service_group(environment):
    ctx, p, safety, sim = environment(BuildingSpec.design_limit())
    emergency(ctx, "fire")
    recover(ctx, safety, sim, "fire")
    assert not p.emergency()
