from dataclasses import replace

import pytest

from ems.contracts import events as E
from ems.contracts.types import CarState as S
from ems.contracts.types import Role
from ems.core.app import build


def make():
    ctx = build(parts=["sim", "safety", "monitor"])
    return ctx, ctx.services["car_states"], ctx.services["controllers"]


def attest(service):
    service.report_safety_clear(True)
    for car in service.table.cars():
        service.report_controller_normal(car, True)


@pytest.mark.parametrize("source", ["panel", "io_signal_lost"])
def test_fire_latches_until_physical_reset_attestations_and_fm(source):
    ctx, safety, sim = make()
    safety.request("A", S.MAINTENANCE, "maintenance")
    safety.request("B", S.INDEPENDENT_SERVICE, "independent")
    safety.request("C", S.VIP_EXCLUSIVE, "priority:vip")
    ctx.services["devices"].fire.alarm(source)
    assert safety.safety_active() and not safety.dispatchable("LOW")
    assert all(safety.state(c) is S.FIRE_RECALL for c in sim.cars)
    assert not safety.confirm_recovery(Role.FM)
    ctx.services["devices"].fire.clear()
    attest(safety)
    assert not safety.confirm_recovery(Role.FM)  # firefighter service still active
    assert sim.reset_fire()
    attest(safety)
    assert not safety.confirm_recovery(Role.SECURITY)
    assert safety.safety_active()  # alarm clear and reset do NOT automatically unlock
    assert safety.confirm_recovery(Role.FM)
    assert not safety.safety_active()
    assert safety.state("A") is S.MAINTENANCE
    assert safety.state("B") is S.INDEPENDENT_SERVICE
    assert safety.state("C") is S.GROUP_SERVICE  # interrupted VIP hold is not restored
    assert safety.dispatchable("LOW") == list("CD")
    assert sim.command_log == []


def test_normal_telemetry_cannot_replace_missing_controller_and_safety_reports():
    ctx, safety, sim = make()
    sim.reports_to_safety = False  # controllers that have not reported yet
    ctx.services["devices"].fire.alarm()
    ctx.services["devices"].fire.clear()
    sim.reset_fire()
    ctx.clock.run_until(20)
    assert not safety.recovery_eligible()
    safety.report_safety_clear(True)
    assert not safety.recovery_eligible()
    attest(safety)
    safety.report_controller_normal("D", False)
    assert not safety.recovery_eligible()
    safety.report_controller_normal("D", True)
    assert safety.recovery_eligible() and safety.safety_active()
    safety.report_safety_clear(False)
    assert not safety.confirm_recovery(Role.FM)


def test_fault_and_link_conditions_do_not_clear_each_other():
    ctx, safety, sim = make()
    sim.inject_fault("A", "brake", between_floors=True)
    ctx.bus.publish(E.HeartbeatLost(0, "A"))
    sim.clear_fault("A")
    assert safety.state("A") is S.FAULTED
    ctx.bus.publish(E.HeartbeatRestored(0, "A"))
    assert safety.state("A") is S.GROUP_SERVICE
    sim.inject_fault("A", "brake")
    ctx.bus.publish(E.HeartbeatLost(0, "A"))
    ctx.bus.publish(E.HeartbeatRestored(0, "A"))
    assert safety.state("A") is S.FAULTED


def test_isolated_cars_do_not_block_healthy_recovery_and_stay_excluded():
    ctx, safety, sim = make()
    sim.inject_fault("A", "brake")
    ctx.bus.publish(E.HeartbeatLost(0, "B"))
    safety.request("C", S.MAINTENANCE, "maintenance")
    ctx.services["devices"].power.lose_mains(generator_failed=True)
    ctx.services["devices"].power.restore_mains()
    ctx.clock.run_until(120)
    safety.report_safety_clear(True)
    for car in "DEFGHS":
        safety.report_controller_normal(car, True)
    assert safety.confirm_recovery(Role.FM)
    assert safety.dispatchable("LOW") == ["D"]


def test_continuous_mains_timer_restarts_and_early_confirmation_is_not_saved():
    ctx, safety, sim = make()
    power = ctx.services["devices"].power
    power.lose_mains(generator_failed=True)
    power.restore_mains()
    attest(safety)
    assert not safety.confirm_recovery(Role.FM)
    ctx.clock.run_until(119)
    power.lose_mains(generator_failed=True)
    power.restore_mains()
    attest(safety)
    ctx.clock.run_until(120)
    assert not safety.recovery_eligible()
    ctx.clock.run_until(238.999)
    assert not safety.recovery_eligible()
    ctx.clock.run_until(239)
    assert safety.recovery_eligible() and safety.safety_active()
    power.restore_mains()  # duplicate must not reset the timer
    assert safety.confirm_recovery(Role.FM)
    assert sim.command_log == []


def test_fire_and_power_latches_do_not_clear_each_other():
    ctx, safety, sim = make()
    ctx.services["devices"].power.lose_mains(generator_failed=True)
    ctx.services["devices"].fire.alarm()
    ctx.services["devices"].fire.clear()
    sim.reset_fire()
    attest(safety)
    assert not safety.confirm_recovery(Role.FM)
    ctx.services["devices"].power.restore_mains()
    ctx.clock.run_until(120)
    attest(safety)
    assert safety.confirm_recovery(Role.FM)


def test_failed_rescue_deadline_is_60_seconds_and_stale_reports_do_not_count():
    ctx, _safety, _sim = make()
    ctx.clock.run_until(10)
    ctx.services["devices"].power.lose_mains(generator_failed=True,
                                            failed_rescue_cars=("A", "B"))
    ctx.bus.publish(E.RescueReport(0, "A", "rescued_at_floor"))
    ctx.bus.publish(E.RescueReport(10, "B", "unknown"))
    ctx.clock.run_until(69.999)
    assert not ctx.bus.of_type(E.AlertRaised)
    ctx.clock.run_until(70)
    alerts = ctx.bus.of_type(E.AlertRaised)
    assert {a.car_id for a in alerts} == {"A", "B"}
    assert all(a.t == 70 and a.severity == "critical" and
               "POSSIBLE ENTRAPMENT" in a.message for a in alerts)
    ctx.clock.run_until(100)
    assert ctx.bus.of_type(E.AlertRaised) == alerts


def test_generator_readiness_never_unlocks_ordinary_dispatch_or_sends_commands():
    ctx, safety, sim = make()
    ctx.services["devices"].power.lose_mains(generator_delay=15)
    assert not safety.generator_ready
    ctx.clock.run_until(60)
    assert safety.generator_ready and safety.safety_active()
    assert safety.dispatchable("LOW") == [] and sim.command_log == []
    assert not ctx.bus.of_type(E.AlertRaised)


def test_stuck_rescue_alerts_immediately_and_repeated_reports_are_deduplicated():
    ctx, _safety, _sim = make()
    ctx.services["devices"].power.lose_mains(generator_failed=True, stuck_cars=("A",))
    ctx.clock.run_until(60)
    reports = [e for e in ctx.bus.of_type(E.RescueReport) if e.car_id == "A"]
    alerts = ctx.bus.of_type(E.AlertRaised)
    assert len(alerts) == 1 and alerts[0].t == reports[0].t <= 40


@pytest.mark.parametrize("between,doors", [(True, False), (False, True)])
def test_occupied_fault_entrapment_within_10_seconds_and_no_command(between, doors):
    ctx, safety, sim = make()
    # Adapter telemetry, not simulator private passenger lists, qualifies occupancy.
    status = replace(sim.status("A"), passengers=2)
    sim.status = lambda car: status if car == "A" else sim.cars[car].status()
    ctx.bus.publish(E.ControllerFault(0, "A", "brake", between, doors))
    ctx.clock.run_until(10)
    alerts = [e for e in ctx.bus.of_type(E.AlertRaised) if e.kind == "entrapment"]
    assert len(alerts) == 1 and alerts[0].t <= 10
    assert alerts[0].severity == "critical" and "contractor" in alerts[0].recipients
    assert safety.state("A") is S.FAULTED and "A" not in safety.dispatchable("LOW")
    assert sim.command_log == []


def test_unoccupied_fault_is_not_claimed_to_be_entrapment_and_alarm_is_urgent():
    ctx, _safety, sim = make()
    sim.inject_fault("A", "brake", between_floors=True)
    assert not [a for a in ctx.bus.of_type(E.AlertRaised) if a.kind == "entrapment"]
    ctx.services["devices"].alarm_button("A")
    alarm = ctx.bus.of_type(E.AlertRaised)[-1]
    assert alarm.kind == "passenger_alarm" and alarm.severity == "critical" and alarm.t == 0


def test_fault_handoff_delegates_waiting_call_selection_to_dispatch():
    ctx, _safety, sim = make()
    requests = []
    class Dispatcher:
        def reassign_calls_of(self, car_id, reason):
            requests.append((car_id, reason))
            return []
    ctx.services["dispatcher"] = Dispatcher()
    sim.inject_fault("A", "brake")
    ctx.clock.run_until(3)
    assert requests == [("A", "safety:fault")]
    assert not ctx.bus.of_type(E.CallReassigned)  # Safety must never publish Dispatch events


def test_telemetry_can_qualify_existing_occupied_between_floor_fault():
    ctx, safety, sim = make()
    status = replace(sim.status("A"), passengers=1, fault_code="brake", position=1.5)
    ctx.bus.publish(E.CarStatusUpdated(0, status))
    assert safety.state("A") is S.FAULTED
    alerts = [a for a in ctx.bus.of_type(E.AlertRaised) if a.kind == "entrapment"]
    assert len(alerts) == 1 and alerts[0].t == 0


def test_fault_during_fire_is_retained_and_firefighter_event_does_not_repeat_handoffs():
    ctx, safety, sim = make()
    requests = []
    class Dispatcher:
        def reassign_calls_of(self, car_id, reason):
            requests.append((car_id, reason))
            return []
    ctx.services["dispatcher"] = Dispatcher()
    ctx.services["devices"].fire.alarm()
    assert len(requests) == 9  # FirefighterService(active=True) does not duplicate the fire handoff
    sim.inject_fault("A", "brake")
    ctx.services["devices"].fire.clear()
    sim.reset_fire()
    attest(safety)
    assert safety.confirm_recovery(Role.FM)
    assert safety.state("A") is S.FAULTED and "A" not in safety.dispatchable("LOW")


def test_earlier_outage_deadline_cannot_alert_or_accept_reports_for_new_outage():
    ctx, _safety, _sim = make()
    power = ctx.services["devices"].power
    power.lose_mains(generator_failed=True, failed_rescue_cars=("A",))
    ctx.clock.run_until(45)
    power.restore_mains()
    ctx.clock.run_until(50)
    power.lose_mains(generator_failed=True, failed_rescue_cars=("A",))
    ctx.bus.publish(E.RescueReport(40, "A", "rescued_at_floor"))
    ctx.clock.run_until(60)
    assert not [a for a in ctx.bus.of_type(E.AlertRaised) if a.kind == "entrapment"]
    ctx.clock.run_until(109.999)
    assert not [a for a in ctx.bus.of_type(E.AlertRaised) if a.kind == "entrapment"]
    ctx.clock.run_until(110)
    alerts = [a for a in ctx.bus.of_type(E.AlertRaised) if a.kind == "entrapment"]
    assert len(alerts) == 1 and alerts[0].car_id == "A" and alerts[0].t == 110
