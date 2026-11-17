"""Stage 1 invariants, including physical conditions masked by emergency precedence."""
from dataclasses import replace

import pytest

from ems.contracts import events as E
from ems.contracts.types import CarState as S
from ems.contracts.types import Role
from ems.core.app import build
from ems.sim.baseline import install_dispatch_standin
from ems.sim.building import BuildingSpec


def attest(safety):
    safety.report_safety_clear(True)
    for car in safety.table.cars():
        safety.report_controller_normal(car, True)


@pytest.mark.parametrize("emergency", ["fire", "power"])
def test_physical_fault_recorded_while_masked_and_survives_fm_recovery(emergency):
    ctx = build(parts=["sim", "safety"])
    sim, safety, devices = (ctx.services[k] for k in ("controllers", "car_states", "devices"))
    if emergency == "fire":
        devices.fire.alarm()
        dominant = S.FIRE_RECALL
    else:
        devices.power.lose_mains(generator_failed=True)
        dominant = S.EMERGENCY_POWER
    sim.inject_fault("A", "brake", between_floors=True)
    assert safety.state("A") is dominant
    assert (S.FAULTED, "safety:fault") in safety.table.holds["A"]
    if emergency == "fire":
        devices.fire.clear()
        assert sim.reset_fire()
    else:
        devices.power.restore_mains()
        ctx.clock.run_until(120)
    attest(safety)
    assert safety.confirm_recovery(Role.FM)
    assert safety.state("A") is S.FAULTED
    assert safety.dispatchable("LOW") == list("BCD")
    assert safety.dispatchable("HIGH") == list("EFGH")
    sim.clear_fault("A")
    assert safety.dispatchable("LOW") == list("ABCD")


def test_one_physical_fault_does_not_create_a_building_emergency():
    ctx = build(parts=["sim", "safety"])
    ctx.services["controllers"].inject_fault("A", "brake")
    safety = ctx.services["car_states"]
    assert not safety.safety_active()
    assert safety.dispatchable("LOW") == list("BCD")
    assert safety.dispatchable("HIGH") == list("EFGH")


def test_same_owner_different_states_and_other_owner_survive_releases():
    ctx = build(parts=["safety"])
    safety = ctx.services["car_states"]
    assert safety.request("A", S.INDEPENDENT_SERVICE, "operator")
    assert safety.request("A", S.MAINTENANCE, "operator")
    assert safety.request("A", S.MAINTENANCE, "other")
    assert safety.release("A", S.INDEPENDENT_SERVICE, "operator")
    assert safety.state("A") is S.MAINTENANCE
    assert safety.release("A", S.MAINTENANCE, "operator")
    assert safety.state("A") is S.MAINTENANCE
    assert safety.release("A", S.MAINTENANCE, "other")
    assert safety.state("A") is S.GROUP_SERVICE


def test_confirmed_previous_episode_does_not_confirm_a_new_emergency():
    ctx = build(parts=["sim", "safety"])
    safety = ctx.services["car_states"]
    fire, sim = ctx.services["devices"].fire, ctx.services["controllers"]
    fire.alarm()
    fire.clear()
    sim.reset_fire()
    attest(safety)
    assert safety.confirm_recovery(Role.FM)
    fire.alarm()
    assert safety.normal == set() and not safety.signals_clear
    fire.clear()
    sim.reset_fire()
    assert not safety.confirm_recovery(Role.FM)
    assert not safety.dispatchable("LOW")
    attest(safety)
    assert safety.recovery_eligible() and safety.safety_active()
    assert len(safety.recovery_log) == 1
    assert safety.confirm_recovery(Role.FM)
    assert len(safety.recovery_log) == 2


@pytest.mark.parametrize("active,clear", [
    (lambda: E.ControllerFault(10, "A", "brake", True, False),
     lambda: E.ControllerFaultCleared(9, "A")),
    (lambda: E.HeartbeatLost(10, "A"), lambda: E.HeartbeatRestored(9, "A")),
])
def test_stale_clear_cannot_erase_new_physical_condition(active, clear):
    ctx = build(parts=["safety"])
    ctx.clock.run_until(10)
    ctx.bus.publish(active())
    ctx.bus.publish(clear())
    safety = ctx.services["car_states"]
    assert safety.state("A") is S.FAULTED
    assert safety.dispatchable("LOW") == list("BCD")
    ctx.clock.run_until(11)
    ctx.bus.publish(replace(clear(), t=11))
    assert safety.state("A") is S.GROUP_SERVICE


@pytest.mark.parametrize("active,clear,attribute", [
    (E.FireAlarm(10), E.FireAlarmCleared(9), "fire"),
    (E.MainsLost(10), E.MainsRestored(9), "mains_lost"),
])
def test_stale_building_clear_cannot_make_recovery_eligible(active, clear, attribute):
    ctx = build(parts=["safety"])
    ctx.clock.run_until(10)
    ctx.bus.publish(active)
    ctx.bus.publish(clear)
    safety = ctx.services["car_states"]
    assert getattr(safety, attribute)
    ctx.clock.run_until(130)
    attest(safety)
    assert not safety.confirm_recovery(Role.FM)


def test_new_firefighter_episode_invalidates_all_attestations_under_existing_hold():
    ctx = build(parts=["safety"])
    safety = ctx.services["car_states"]
    ctx.bus.publish(E.FirefighterService(0, "S", True))
    ctx.clock.run_until(1)
    ctx.bus.publish(E.FirefighterService(1, "S", False))
    attest(safety)
    assert safety.recovery_eligible()
    ctx.clock.run_until(2)
    ctx.bus.publish(E.FirefighterService(2, "S", True))
    assert safety.normal == set() and not safety.signals_clear
    ctx.clock.run_until(3)
    ctx.bus.publish(E.FirefighterService(3, "S", False))
    safety.report_controller_normal("S", True)
    assert not safety.confirm_recovery(Role.FM)


def test_repeated_fault_does_not_downgrade_persistent_entrapment_evidence():
    ctx = build(parts=["safety"])
    ctx.bus.publish(E.ControllerFault(0, "A", "brake", True, True))
    ctx.bus.publish(E.ControllerFault(0, "A", "brake", False, False))
    assert ctx.services["car_states"].hazards["A"] == (True, True)


def test_fire_and_locked_recovery_block_ordinary_dispatch_commands_on_dynamic_layout():
    ctx = build(parts=["sim", "safety"], services={"building": BuildingSpec.design_limit()})
    install_dispatch_standin(ctx)
    fire, sim, safety = (ctx.services["devices"].fire, ctx.services["controllers"],
                         ctx.services["car_states"])
    fire.alarm()
    for call_id in ("during_fire", "after_reset", "after_attest"):
        if call_id == "after_reset":
            fire.clear()
            sim.reset_fire()
        if call_id == "after_attest":
            attest(safety)
        ctx.bus.publish(E.PassengerArrived(0, call_id, "test-panel", 1, 2, "G1", None, False, False))
    assert sim.command_log == []
    assert all(safety.dispatchable(group) == [] for group in safety.table.groups)
    assert safety.confirm_recovery(Role.FM)
    ctx.bus.publish(E.PassengerArrived(0, "after_fm", "test-panel", 1, 2, "G1", None, False, False))
    assert len(sim.command_log) == 1 and sim.command_log[0][4]


def test_safety_does_not_duplicate_part5_mechanical_fault_notification():
    ctx = build(parts=["sim", "safety"])
    ctx.services["controllers"].inject_fault("A", "brake")
    assert not [a for a in ctx.bus.of_type(E.AlertRaised) if a.kind == "fault"]


def test_stale_firefighter_off_and_generator_ready_cannot_clear_new_conditions():
    ctx = build(parts=["safety"])
    ctx.clock.run_until(10)
    ctx.bus.publish(E.FirefighterService(10, "S", True))
    ctx.bus.publish(E.FirefighterService(9, "S", False))
    ctx.bus.publish(E.MainsLost(10))
    ctx.bus.publish(E.GeneratorReady(9))
    safety = ctx.services["car_states"]
    assert safety.firefighters == {"S"} and not safety.generator_ready
    assert not safety.confirm_recovery(Role.FM)


def test_old_fault_telemetry_after_a_newer_clear_does_not_reassert_condition():
    ctx = build(parts=["safety"])
    ctx.clock.run_until(10)
    ctx.bus.publish(E.ControllerFault(10, "A", "brake", True, False))
    ctx.clock.run_until(11)
    ctx.bus.publish(E.ControllerFaultCleared(11, "A"))
    old_status = replace(ctx.services["controllers"].status("A"), fault_code="brake")
    ctx.bus.publish(E.CarStatusUpdated(10, old_status))
    assert ctx.services["car_states"].state("A") is S.GROUP_SERVICE
