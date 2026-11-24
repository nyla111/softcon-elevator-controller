"""FR-110 / VER-10: power availability is not per-car rescue evidence (#46)."""
import pytest

from ems.contracts import events as E
from ems.contracts.types import CarState, Role
from ems.core.app import build


def entrapments(ctx):
    return [event for event in ctx.bus.of_type(E.AlertRaised) if event.kind == "entrapment"]


def assert_held_without_commands(ctx, state=CarState.EMERGENCY_POWER, since=0):
    safety = ctx.get("car_states")
    assert safety.safety_active()
    assert all(safety.state(car) is state for car in safety.table.cars())
    assert all(safety.dispatchable(group) == [] for group in safety.table.groups)
    # Includes refused commands: controller rejection is not evidence of EMS silence.
    assert [entry for entry in ctx.get("controllers").command_log if entry[0] >= since] == []
    assert [event for event in ctx.bus.of_type(E.CallAssigned) if event.t >= since] == []


@pytest.mark.parametrize("seed", range(1, 11))
@pytest.mark.parametrize("generator_failed", [False, True])
def test_each_missing_report_alerts_at_60_even_with_ready_generator(seed, generator_failed):
    ctx = build(seed=seed)  # Real Safety, Access, Dispatcher, Priority and simulator.
    ctx.get("devices").power.lose_mains(
        generator_failed=generator_failed, generator_delay=15, failed_rescue_cars=("A", "B"),
    )
    # Exercise real admission/dispatch while held; no arrival-free command assertion.
    ctx.bus.publish(E.PassengerArrived(0, "waiting", "panel:1", 1, 2, "LOW",
                                     "staff-fm-1", False, False))
    assert len(ctx.bus.of_type(E.CallRequested)) == 1
    ctx.clock.run_until(59)
    assert ctx.get("car_states").generator_ready is (not generator_failed)
    assert {e.car_id for e in ctx.bus.of_type(E.RescueReport)} == set("CDEFGHS")
    assert entrapments(ctx) == []
    assert_held_without_commands(ctx)
    ctx.clock.run_until(59.999)
    assert entrapments(ctx) == []
    ctx.clock.run_until(60)
    alerts = entrapments(ctx)
    assert {e.car_id for e in alerts} == {"A", "B"}
    assert len(alerts) == 2
    assert all(e.t == 60 and e.severity == "critical" for e in alerts)
    assert all(set(e.recipients) == {"security", "fm", "contractor"} for e in alerts)
    delivered = [entry for entry in ctx.get("notifier").inbox if "entrapment" in entry[2]]
    assert len(delivered) == 2
    assert all(t == 60 and set(to) == {"security", "fm", "contractor"}
               and "POSSIBLE ENTRAPMENT" in body for t, to, _subject, body in delivered)
    assert {subject for _, _, subject, _ in delivered} == {
        "[critical] entrapment car A", "[critical] entrapment car B",
    }
    ctx.clock.run_until(61)
    assert entrapments(ctx) == alerts
    assert_held_without_commands(ctx)


@pytest.mark.parametrize("restore_at", [15, 59, 60, 61])
def test_mains_restoration_does_not_cancel_missing_rescue_deadline(restore_at):
    ctx = build()
    safety, power = ctx.get("car_states"), ctx.get("devices").power
    power.lose_mains(generator_failed=True, failed_rescue_cars=("A",))
    ctx.clock.run_until(restore_at)
    power.restore_mains()
    assert not safety.confirm_recovery(Role.FM)  # Early confirmation is not saved.
    ctx.clock.run_until(max(61, restore_at))
    assert [(e.car_id, e.t) for e in entrapments(ctx)] == [("A", 60)]
    ctx.clock.run_until(restore_at + 119.999)
    assert not safety.recovery_eligible()
    assert_held_without_commands(ctx)
    ctx.clock.run_until(restore_at + 120)
    assert safety.recovery_eligible() and safety.safety_active()
    assert ctx.get("controllers").command_log == []
    assert safety.confirm_recovery(Role.FM)
    assert not safety.safety_active()
    assert len(entrapments(ctx)) == 1


@pytest.mark.parametrize("report_at,expected_times", [(59, []), (60, [60]), (61, [60])])
def test_current_rescue_before_or_after_deadline(report_at, expected_times):
    ctx = build()
    ctx.get("devices").power.lose_mains(generator_delay=15, failed_rescue_cars=("A",))
    # At exactly60 the already queued deadline executes first. A later successful
    # report may update evidence but must not erase or repeat an already sent alert.
    ctx.clock.run_until(report_at)
    report = E.RescueReport(report_at, "A", "rescued_at_floor")
    ctx.bus.publish(report)
    ctx.bus.publish(report)
    ctx.clock.run_until(62)
    assert [e.t for e in entrapments(ctx)] == expected_times
    assert "A" in ctx.get("car_states").rescued
    assert_held_without_commands(ctx)


def test_report_scheduled_at_deadline_before_timer_is_valid_rescue_evidence():
    ctx = build()
    ctx.clock.call_at(60, lambda: ctx.bus.publish(E.RescueReport(60, "A", "rescued_at_floor")))
    ctx.get("devices").power.lose_mains(generator_delay=15, failed_rescue_cars=("A",))
    ctx.clock.run_until(61)
    assert entrapments(ctx) == []
    assert_held_without_commands(ctx)


def test_stale_future_unknown_and_other_car_reports_do_not_prove_rescue():
    ctx = build()
    ctx.clock.run_until(10)
    ctx.get("devices").power.lose_mains(generator_delay=15, failed_rescue_cars=("A", "B"))
    for report in [E.RescueReport(9, "A", "rescued_at_floor"),
                   E.RescueReport(11, "A", "rescued_at_floor"),
                   E.RescueReport(10, "A", "unknown"),
                   E.RescueReport(10, "not-a-car", "rescued_at_floor"),
                   E.RescueReport(10, "B", "rescued_at_floor")]:
        ctx.bus.publish(report)
    ctx.clock.run_until(69.999)
    assert entrapments(ctx) == []
    ctx.clock.run_until(70)
    assert [(e.car_id, e.t) for e in entrapments(ctx)] == [("A", 70)]
    # Normal Eco standby requests before the outage are outside its command window.
    assert_held_without_commands(ctx, since=10)


def test_unknown_occupancy_and_no_telemetry_never_suppress_alerts():
    ctx = build(parts=["safety"])
    ctx.bus.publish(E.MainsLost(0))
    ctx.clock.run_until(15)
    ctx.bus.publish(E.GeneratorReady(15))
    assert ctx.bus.of_type(E.CarStatusUpdated) == []
    ctx.clock.run_until(59)
    assert entrapments(ctx) == []
    ctx.clock.run_until(60)
    assert {e.car_id for e in entrapments(ctx)} == set(ctx.get("car_states").table.cars())
    assert all(e.t == 60 for e in entrapments(ctx))


def test_stuck_and_deadline_alerts_deduplicate_with_generator_ready():
    ctx = build()
    ctx.get("devices").power.lose_mains(
        generator_delay=15, failed_rescue_cars=("B",), stuck_cars=("A",),
    )
    ctx.clock.run_until(59)
    assert len(entrapments(ctx)) == 1
    first = entrapments(ctx)[0]
    assert first.car_id == "A" and first.t <= 40
    ctx.bus.publish(E.RescueReport(59, "A", "stuck"))
    ctx.clock.run_until(60)
    assert [(e.car_id, e.t) for e in entrapments(ctx)] == [("A", first.t), ("B", 60)]
    ctx.bus.publish(E.RescueReport(60, "B", "stuck"))
    ctx.clock.run_until(61)
    assert len(entrapments(ctx)) == 2
    assert_held_without_commands(ctx)


def test_fire_precedence_does_not_cancel_missing_rescue_alert():
    ctx = build()
    ctx.get("devices").power.lose_mains(generator_delay=15, failed_rescue_cars=("A",))
    ctx.clock.run_until(16)
    ctx.get("devices").fire.alarm()
    ctx.clock.run_until(60)
    assert [(e.car_id, e.t) for e in entrapments(ctx)] == [("A", 60)]
    assert_held_without_commands(ctx, CarState.FIRE_RECALL)


def test_repeated_outage_resets_alert_deduplication_and_rejects_old_rescue():
    ctx = build()
    power = ctx.get("devices").power
    power.lose_mains(generator_delay=15, failed_rescue_cars=("A",))
    ctx.clock.run_until(60)
    assert [(e.car_id, e.t) for e in entrapments(ctx)] == [("A", 60)]
    power.restore_mains()
    ctx.clock.run_until(70)
    power.lose_mains(generator_delay=15, failed_rescue_cars=("A",))
    ctx.bus.publish(E.RescueReport(40, "A", "rescued_at_floor"))
    ctx.clock.run_until(129)
    assert len(entrapments(ctx)) == 1
    ctx.clock.run_until(130)
    assert [(e.car_id, e.t) for e in entrapments(ctx)] == [("A", 60), ("A", 130)]
    ctx.clock.run_until(131)
    assert len(entrapments(ctx)) == 2
    assert_held_without_commands(ctx)


def test_superseded_timer_and_duplicate_loss_do_not_change_new_outage_deadline():
    ctx = build()
    power = ctx.get("devices").power
    power.lose_mains(generator_delay=15, failed_rescue_cars=("A",))
    ctx.clock.run_until(45)
    power.restore_mains()
    ctx.clock.run_until(50)
    power.lose_mains(generator_delay=15, failed_rescue_cars=("A",))
    ctx.clock.run_until(60)  # Previous outage timer must not inspect the new episode.
    assert entrapments(ctx) == []
    ctx.bus.publish(E.MainsLost(60))  # Duplicate must not move the current deadline.
    ctx.bus.publish(E.RescueReport(40, "A", "rescued_at_floor"))
    ctx.clock.run_until(109.999)
    assert entrapments(ctx) == []
    ctx.clock.run_until(110)
    assert [(e.car_id, e.t) for e in entrapments(ctx)] == [("A", 110)]
    assert_held_without_commands(ctx)


@pytest.mark.parametrize("generator_failed", [False, True])
def test_successful_rescues_do_not_alert_with_or_without_generator(generator_failed):
    ctx = build()
    ctx.get("devices").power.lose_mains(generator_failed=generator_failed)
    ctx.clock.run_until(61)
    assert len(ctx.get("car_states").rescued) == len(ctx.get("car_states").table.cars())
    assert entrapments(ctx) == []
    assert_held_without_commands(ctx)
