"""Internal recovery snapshots preserve the FR-55 gate and have no side effects."""
from copy import deepcopy

import pytest

from ems.contracts.types import CarState, Role
from ems.core.app import build
from ems.sim.building import BuildingSpec


@pytest.fixture
def environment():
    ctx = build(parts=["sim", "safety"])
    return ctx, ctx.get("car_states"), ctx.get("controllers"), ctx.get("devices")


def clear_fire(ctx, sim, devices):
    devices.fire.alarm()
    ctx.clock.run_until(60)
    devices.fire.clear()
    assert sim.reset_fire()


def attest(safety, cars):
    safety.report_safety_clear(True)
    for car in cars:
        safety.report_controller_normal(car, True)


def observed_state(ctx, safety, sim):
    return deepcopy({
        "holds": safety.table.holds,
        "safety": {key: value for key, value in vars(safety).items()
                   if key not in {"ctx", "table"}},
        "clock": (ctx.clock.now(), ctx.clock.now_dt(), tuple(ctx.clock._queue),
                  repr(ctx.clock._seq)),
        "events": ctx.bus.log,
        "commands": sim.command_log,
        "cars": sim.all_status(),
    })


def test_initial_normal_state_does_not_need_recovery(environment):
    ctx, safety, sim, _devices = environment
    status = safety.recovery_status()
    assert status["recovery_required"] is False
    assert status["fire_active"] is False
    assert status["safety_inputs_clear"] is False
    assert status["required_controllers"] == tuple(sim.cars)
    assert status["missing_controller_reports"] == tuple(sim.cars)
    assert status["unhealthy_controllers"] == ()
    assert status["mains_stable"] is True
    assert status["eligible"] is safety.recovery_eligible() is False
    assert "recovery_not_required" in status["blockers"]
    assert safety.dispatchable("LOW") == list("ABCD")
    assert ctx.clock.now() == 0


def test_active_fire_reports_alarm_and_firefighter_blockers(environment):
    _ctx, safety, sim, devices = environment
    devices.fire.alarm()
    status = safety.recovery_status()
    assert status["recovery_required"] is True
    assert status["fire_active"] is True
    assert status["firefighter_cars"] == ("S",)
    assert status["missing_controller_reports"] == tuple(sim.cars)
    assert {"fire_active", "firefighter_service_active", "safety_inputs_not_clear",
            "controller_reports_missing"} <= set(status["blockers"])
    assert status["eligible"] is safety.recovery_eligible() is False


def test_clear_and_reset_do_not_infer_attestations_from_telemetry(environment):
    ctx, safety, sim, devices = environment
    sim.reports_to_safety = False
    clear_fire(ctx, sim, devices)
    ctx.clock.run_until(65)
    status = safety.recovery_status()
    assert status["fire_active"] is False
    assert status["firefighter_cars"] == ()
    assert status["safety_inputs_clear"] is False
    assert status["missing_controller_reports"] == tuple(sim.cars)
    assert status["blockers"] == ("safety_inputs_not_clear", "controller_reports_missing")
    assert status["eligible"] is False
    assert safety.dispatchable("LOW") == []


def test_missing_normal_reports_are_named_even_after_safety_clear(environment):
    ctx, safety, sim, devices = environment
    clear_fire(ctx, sim, devices)
    attest(safety, ("A", "B"))
    status = safety.recovery_status()
    assert status["safety_inputs_clear"] is True
    assert status["missing_controller_reports"] == tuple("CDEFGHS")
    assert status["blockers"] == ("controller_reports_missing",)
    assert status["eligible"] is safety.recovery_eligible() is False


def test_eligible_snapshot_still_requires_explicit_fm_confirmation(environment):
    ctx, safety, sim, devices = environment
    clear_fire(ctx, sim, devices)
    attest(safety, sim.cars)
    status = safety.recovery_status()
    assert status["missing_controller_reports"] == status["unhealthy_controllers"] == ()
    assert status["blockers"] == ()
    assert status["eligible"] is safety.recovery_eligible() is True
    assert status["recovery_required"] is True
    assert safety.recovery_log == []
    assert safety.safety_active() and safety.dispatchable("LOW") == []
    assert not safety.confirm_recovery(Role.SECURITY)
    assert safety.confirm_recovery(Role.FM)
    confirmed = safety.recovery_status()
    assert confirmed["recovery_required"] is False
    assert confirmed["eligible"] is False
    assert "recovery_not_required" in confirmed["blockers"]


@pytest.mark.parametrize("isolation", ["fault", "disconnected", "maintenance", "independent"])
def test_isolated_car_is_exempt_from_reports_but_keeps_service_restriction(environment, isolation):
    ctx, safety, sim, devices = environment
    if isolation == "fault":
        sim.inject_fault("A", "F-01")
        expected = CarState.FAULTED
    elif isolation == "disconnected":
        sim.set_disconnected("A", True)
        ctx.clock.run_until(4)
        expected = CarState.FAULTED
    else:
        expected = {"maintenance": CarState.MAINTENANCE,
                    "independent": CarState.INDEPENDENT_SERVICE}[isolation]
        assert safety.request("A", expected, "operator")
    clear_fire(ctx, sim, devices)
    attest(safety, tuple(car for car in sim.cars if car != "A"))
    status = safety.recovery_status()
    assert "A" not in status["required_controllers"]
    assert status["missing_controller_reports"] == ()
    assert status["eligible"] is safety.recovery_eligible() is True
    assert safety.confirm_recovery(Role.FM)
    assert safety.state("A") is expected
    assert "A" not in safety.dispatchable("LOW")


@pytest.mark.parametrize("emergency", ["fire", "power"])
def test_new_emergency_invalidates_old_eligible_snapshot(environment, emergency):
    ctx, safety, sim, devices = environment
    clear_fire(ctx, sim, devices)
    attest(safety, sim.cars)
    previous = safety.recovery_status()
    assert previous["eligible"] is True
    if emergency == "fire":
        devices.fire.alarm()
    else:
        devices.power.lose_mains(generator_failed=True)
    current = safety.recovery_status()
    assert previous["eligible"] is True
    assert current["eligible"] is safety.recovery_eligible() is False
    assert current["safety_inputs_clear"] is False
    assert current["missing_controller_reports"] == tuple(sim.cars)


def test_power_recovery_keeps_120_second_continuous_mains_gate(environment):
    ctx, safety, sim, devices = environment
    devices.power.lose_mains(generator_failed=True)
    lost = safety.recovery_status()
    assert lost["mains_lost"] is True
    assert lost["mains_stable"] is False
    assert {"mains_lost", "mains_not_stable"} <= set(lost["blockers"])
    ctx.clock.run_until(60)
    devices.power.restore_mains()
    attest(safety, sim.cars)
    ctx.clock.run_until(179.999)
    waiting = safety.recovery_status()
    assert waiting["blockers"] == ("mains_not_stable",)
    assert waiting["eligible"] is safety.recovery_eligible() is False
    ctx.clock.run_until(180)
    ready = safety.recovery_status()
    assert ready["mains_stable"] is True
    assert ready["eligible"] is safety.recovery_eligible() is True
    assert safety.recovery_log == [] and safety.safety_active()


def test_external_emergency_hold_remains_a_named_blocker(environment):
    ctx, safety, sim, devices = environment
    assert safety.request("A", CarState.EMERGENCY_POWER, "external:power")
    clear_fire(ctx, sim, devices)
    attest(safety, sim.cars)
    status = safety.recovery_status()
    assert status["external_emergency_holds"] == (("A", "EMERGENCY_POWER", "external:power"),)
    assert status["blockers"] == ("external_emergency_holds",)
    assert status["eligible"] is safety.recovery_eligible() is False
    assert not safety.confirm_recovery(Role.FM)


def test_normal_attestation_does_not_hide_unhealthy_controller_status(environment):
    ctx, safety, sim, devices = environment
    clear_fire(ctx, sim, devices)
    attest(safety, sim.cars)
    sim.set_heartbeat_lost("A", True)
    status = safety.recovery_status()
    assert status["missing_controller_reports"] == ()
    assert status["unhealthy_controllers"] == ("A",)
    assert status["blockers"] == ("controllers_unhealthy",)
    assert status["eligible"] is safety.recovery_eligible() is False


@pytest.mark.parametrize("stage", ["normal", "fire", "reset", "eligible", "power"])
def test_repeated_reads_leave_all_observable_state_unchanged(environment, stage):
    ctx, safety, sim, devices = environment
    if stage == "fire":
        devices.fire.alarm()
    elif stage in {"reset", "eligible"}:
        clear_fire(ctx, sim, devices)
        if stage == "eligible":
            attest(safety, sim.cars)
    elif stage == "power":
        devices.power.lose_mains(generator_failed=True)
    before = observed_state(ctx, safety, sim)
    first = safety.recovery_status()
    for _ in range(5):
        assert safety.recovery_status() == first
    assert observed_state(ctx, safety, sim) == before


def test_returned_dictionary_and_tuple_values_do_not_alias_safety_state(environment):
    ctx, safety, sim, devices = environment
    clear_fire(ctx, sim, devices)
    attest(safety, ("A",))
    before = safety.recovery_status()
    returned = safety.recovery_status()
    with pytest.raises(TypeError):
        returned["missing_controller_reports"][0] = "A"
    returned["missing_controller_reports"] += ("A",)
    returned["eligible"] = True
    returned.clear()
    assert safety.recovery_status() == before
    assert safety.normal == {"A"}


def test_required_controllers_follow_dynamic_building_layout():
    ctx = build(parts=["sim", "safety"], services={"building": BuildingSpec.design_limit()})
    safety = ctx.get("car_states")
    assert safety.request("G8-8", CarState.MAINTENANCE, "operator")
    status = safety.recovery_status()
    assert len(status["required_controllers"]) == 63
    assert "G8-8" not in status["required_controllers"]
    assert status["missing_controller_reports"] == status["required_controllers"]
