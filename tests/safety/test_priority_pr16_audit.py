"""Current PR #16 integration audit; PR #13 is deliberately not the target.

Enable with SAFETY_PR16_TREE pointing at an unmodified detached checkout of PRIORITY_COMMIT.
Runtime code is byte-verified against Git objects and loaded under a separate package name.
The Access fixture delegates to the real mock ACS; this is not a Part 3 integration claim.
"""
import ast
import importlib.util
import io
import os
import subprocess
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from ems.contracts import events as E
from ems.contracts.interfaces import PriorityService
from ems.contracts.types import Call, CallPriority, CallSource, Role
from ems.contracts.types import CarState as S
from ems.core.app import build
from ems.sim.building import BuildingSpec
from ems.sim.scenarios import _fire_goods_check

PRIORITY_COMMIT = "4a7b11348056083ada4f30187af708afa812a417"
SECURITY, FM, TECH, ADMIN = ("staff-security-1", "staff-fm-1", "staff-technician-1", "staff-admin-T01")


@pytest.fixture(scope="module")
def pr16_package():
    tree = os.environ.get("SAFETY_PR16_TREE")
    if not tree:
        pytest.skip("PR #16 audit requires SAFETY_PR16_TREE; see docs/safety/SYNC_AUDIT.md")
    root = Path(tree).resolve()
    repo = Path(__file__).resolve().parents[2]
    archive = subprocess.check_output(["git", "archive", PRIORITY_COMMIT, "ems/priority"], cwd=repo)
    with tarfile.open(fileobj=io.BytesIO(archive)) as members:
        for member in members:
            if member.isfile() and member.name.endswith(".py"):
                expected = members.extractfile(member).read().replace(b"\r\n", b"\n")
                if (root / member.name).read_bytes().replace(b"\r\n", b"\n") != expected:
                    pytest.fail(f"PR #16 source changed: {member.name}")
    name = "ems._priority_pr16_audit"
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
def environment(pr16_package):
    def make(building=None):
        ctx = build(parts=["sim", "safety"], services={"building": building} if building else None)
        # Explicit test adapter while real Access is absent. Staff roles come from merged ACS.
        ctx.services["access"] = SimpleNamespace(permission=ctx.services["acs"].lookup)
        pr16_package.register(ctx)
        return ctx, ctx.services["priority"], ctx.services["car_states"], ctx.services["controllers"]
    return make


def vip_call(call_id="vip", origin=1, destination=20):
    return Call(call_id, origin, destination, "HIGH", 0,
                CallSource.DESTINATION_PANEL, CallPriority.VIP)


def alarm(ctx, kind):
    if kind == "fire":
        ctx.services["devices"].fire.alarm()
    else:
        ctx.services["devices"].power.lose_mains(generator_failed=True)


def clear_physical(ctx, sim, kind):
    if kind == "fire":
        ctx.services["devices"].fire.clear()
        assert sim.reset_fire()
    else:
        ctx.services["devices"].power.restore_mains()
        ctx.clock.run_until(120)


def confirm(safety):
    safety.report_safety_clear(True)
    for car in safety.table.cars():
        safety.report_controller_normal(car, True)
    assert safety.confirm_recovery(Role.FM)


def active_goods(ctx, p, car="S", end=600):
    request = p.goods.request(ADMIN, ctx.clock.now(), end, ["test-mover"], car)
    assert request.ok and p.goods.approve(FM, request.ref).ok
    ctx.clock.run_until(ctx.clock.now())
    move = p.goods.moves[request.ref]
    assert move.status == "active"
    return move


def test_approved_priority_protocol_and_merged_staff_roles(environment):
    ctx, p, _safety, _sim = environment()
    for name in ("claim_call", "rank", "order", "car_ok_for", "door_dwell_s"):
        assert name in PriorityService.__dict__ and callable(getattr(p, name))
    roles = [(SECURITY, Role.SECURITY), (FM, Role.FM), (TECH, Role.TECHNICIAN), (ADMIN, Role.TENANT_ADMIN)]
    assert all(p.authorizer.actor(card).has(role) for card, role in roles)
    ordinary = vip_call("ordinary")
    ordinary.priority = CallPriority.NORMAL
    accessible = vip_call("accessible")
    accessible.priority = CallPriority.ACCESSIBLE
    assert p.order([ordinary, accessible]) == [accessible, ordinary]
    assert p.rank(accessible) < p.rank(ordinary) and p.door_dwell_s(accessible) >= 10
    assert p.car_ok_for(accessible, ctx.services["controllers"].status("E"))


@pytest.mark.parametrize("kind", ["fire", "power"])
def test_vip_goods_cancellation_and_goods_ended_are_command_free_and_terminal(environment, kind):
    ctx, p, safety, sim = environment()
    car = p.claim_call(vip_call())
    assert car is not None
    move = active_goods(ctx, p)
    mark = len(sim.command_log)
    alarm(ctx, kind)
    assert len(sim.command_log) == mark and not p.trips
    ended = ctx.bus.of_type(E.GoodsMoveEnded)
    assert [(e.move_id, e.car_id, e.cancelled, e.reason, e.t) for e in ended] == [
        (move.move_id, "S", True, "emergency:" + kind, 0)]
    assert move.status == "cancelled_emergency"
    assert len(ctx.bus.of_type(E.PriorityTripEnded)) == 1
    clear_physical(ctx, sim, kind)
    # Both the physical alarm and generator/mains locks can now be clear, but EMS is still held.
    assert safety.safety_active()
    assert not p.request_medical(SECURITY, 1, 20).ok
    assert p.claim_call(vip_call("new-during-recovery")) is None
    p.retry_reopen()
    assert len(sim.command_log) == mark
    confirm(safety)
    ctx.clock.run_until(650)
    assert not p.trips and move.status == "cancelled_emergency"
    assert ctx.bus.of_type(E.GoodsMoveEnded) == ended
    assert len(ctx.bus.of_type(E.PriorityTripStarted)) == 1


@pytest.mark.parametrize("kind", ["fire", "power"])
def test_car_state_changed_triggers_deferred_cleanup_only_after_fm(environment, kind):
    ctx, p, safety, sim = environment()
    car = p.claim_call(vip_call())
    assert car is not None
    move = active_goods(ctx, p)
    alarm(ctx, kind)
    clear_physical(ctx, sim, kind)
    assert p.reopen and sim.cars[car].closed_to_new_calls and sim.cars[move.car_id].independent
    mark = len(sim.command_log)
    confirm(safety)  # no manual retry: PR #16 consumes CarStateChanged
    assert not p.reopen
    assert not sim.cars[car].closed_to_new_calls and not sim.cars[move.car_id].independent
    assert all(c[4] for c in sim.command_log[mark:])


@pytest.mark.parametrize("kind", ["fire", "power"])
def test_safety_recovery_preserves_other_priority_and_operator_holds(environment, kind):
    ctx, p, safety, sim = environment()
    assert p.maintenance.request_maintenance(TECH, "B").ok
    assert safety.request("C", S.INDEPENDENT_SERVICE, "operator:independent")
    sim.inject_fault("A", "brake")
    alarm(ctx, kind)
    clear_physical(ctx, sim, kind)
    confirm(safety)
    assert safety.state("A") is S.FAULTED
    assert safety.table.holds["B"] == {(S.MAINTENANCE, "priority:maintenance")}
    assert safety.table.holds["C"] == {(S.INDEPENDENT_SERVICE, "operator:independent")}
    assert p.holds["B"].kind == "maintenance"
    assert safety.dispatchable("LOW") == ["D"]


def test_fresh_vip_and_medical_candidates_exclude_authoritative_isolation(environment):
    _ctx, p, safety, _sim = environment()
    assert safety.request("E", S.FAULTED, "audit:fault")
    assert safety.request("F", S.MAINTENANCE, "audit:maintenance")
    assert {s.car_id for s in p.candidate_statuses("HIGH", (1, 20))} == {"G", "H"}
    assert p.claim_call(vip_call()) in {"G", "H"}
    outcome = p.request_medical(SECURITY, 20, 25)
    assert outcome.ok and outcome.car_id not in {"E", "F"}


def test_completed_goods_move_publishes_one_terminal_event(environment):
    ctx, p, safety, _sim = environment()
    move = active_goods(ctx, p, end=10)
    ctx.clock.run_until(20)
    ended = ctx.bus.of_type(E.GoodsMoveEnded)
    assert [(e.move_id, e.cancelled, e.reason) for e in ended] == [(move.move_id, False, "completed")]
    assert safety.state("S") is S.GROUP_SERVICE


def test_sim08_goods_criterion_consumes_actual_pr16_terminal_evidence(environment):
    ctx, p, safety, sim = environment()
    active_goods(ctx, p)
    # Keep activation and alarm at different instants so the audit distinguishes start/restart.
    ctx.clock.run_until(1)
    alarm(ctx, "fire")
    clear_physical(ctx, sim, "fire")
    confirm(safety)
    ctx.clock.run_until(650)
    fire = ctx.bus.of_type(E.FireAlarm)[0]
    assert _fire_goods_check(ctx, fire)[1]
    # A repeated terminal event is not proof of exactly-once cancellation.
    ctx.bus.publish(ctx.bus.of_type(E.GoodsMoveEnded)[0])
    assert not _fire_goods_check(ctx, fire)[1]


def test_goods_end_event_cannot_clear_an_independent_safety_fault(environment):
    ctx, p, safety, sim = environment()
    active_goods(ctx, p, end=10)
    sim.inject_fault("S", "brake")
    ctx.clock.run_until(20)
    assert len(ctx.bus.of_type(E.GoodsMoveEnded)) == 1
    assert safety.state("S") is S.FAULTED and safety.dispatchable("SERVICE") == []


def test_production_priority_publishers_respect_approved_event_ownership(pr16_package):
    root = Path(pr16_package.__file__).parent
    allowed = {"PriorityTripStarted", "PriorityTripEnded", "GoodsMoveScheduled", "GoodsMoveEnded",
               "PanelMessage", "AlertRaised"}
    published = set()
    for filename in ("service.py", "vip.py", "medical.py", "maintenance.py", "goods.py"):
        tree = ast.parse((root / filename).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "publish"):
                value = node.args[0]
                assert isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                published.add(value.func.id)
    assert published <= allowed and "GoodsMoveEnded" in published


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="FAIL PR16-01: preemption owner transfer")
@pytest.mark.parametrize("replacement", ["medical", "send_to_lobby"])
def test_preemption_releases_the_owner_that_acquired_the_hold(environment, replacement):
    ctx, p, safety, _sim = environment()
    car = p.claim_call(vip_call(origin=20, destination=25))
    if car is None:
        pytest.fail("audit precondition: VIP failed to start")
    ctx.clock.run_until(3)
    outcome = (p.request_medical(SECURITY, 20, 25) if replacement == "medical"
               else p.send_to_lobby(SECURITY, car))
    if not outcome.ok or outcome.car_id != car:
        pytest.fail("audit precondition: replacement must reuse the VIP car")
    assert p.finish_trip(SECURITY, outcome.ref).ok
    assert safety.state(car) is S.GROUP_SERVICE


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="FAIL PR16-02: VIP-held medical state bypass")
@pytest.mark.parametrize("state", [S.FAULTED, S.MAINTENANCE])
def test_preemption_cannot_select_an_authoritatively_isolated_vip(environment, state):
    ctx, p, safety, _sim = environment()
    car = p.claim_call(vip_call(origin=20, destination=25))
    if car is None:
        pytest.fail("audit precondition: VIP failed to start")
    ctx.clock.run_until(3)
    assert safety.request(car, state, "audit:isolated")
    assert p.request_medical(SECURITY, 20, 25).car_id != car


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="FAIL PR16-03: cached claim bypasses safety")
@pytest.mark.parametrize("kind", ["fire", "power"])
def test_cached_claim_cannot_return_cancelled_car_during_emergency(environment, kind):
    ctx, p, _safety, _sim = environment()
    call = vip_call()
    assert p.claim_call(call) is not None
    alarm(ctx, kind)
    assert p.claim_call(call) is None


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="FAIL PR16-04: fire latch needs service group")
def test_priority_fire_recovery_works_without_a_service_group(environment):
    ctx, p, safety, sim = environment(BuildingSpec.design_limit())
    alarm(ctx, "fire")
    clear_physical(ctx, sim, "fire")
    confirm(safety)
    assert not p.emergency()
