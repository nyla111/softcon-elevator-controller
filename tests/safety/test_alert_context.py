"""Assert the notification actually delivered, not just optional event metadata."""
from dataclasses import replace

import pytest

from ems.contracts import events as E
from ems.core.app import build


def notification(ctx, kind):
    return [entry for entry in ctx.get("notifier").inbox if kind in entry[2]][-1]


def test_alarm_delivers_car_and_floor_within_ten_seconds():
    ctx = build()
    ctx.clock.run_until(1)
    ctx.get("devices").alarm_button("A")
    alert = ctx.bus.of_type(E.AlertRaised)[-1]
    t, recipients, subject, body = notification(ctx, "passenger_alarm")
    assert t == alert.t == 1 and t <= 11
    assert alert.car_id == "A" and alert.floor == 1
    assert "car A" in subject and "floor=1" in body
    assert {"security", "contractor"} <= set(recipients)


@pytest.mark.parametrize("generator_failed", [False, True])
def test_missing_rescue_delivers_last_floor_load_and_telemetry_time(generator_failed):
    ctx = build()
    ctx.get("devices").power.lose_mains(
        generator_failed=generator_failed, failed_rescue_cars=("A",),
    )
    ctx.clock.run_until(60)
    t, recipients, subject, body = notification(ctx, "entrapment")
    assert t == 60 and set(recipients) == {"security", "fm", "contractor"}
    assert "car A" in subject and "POSSIBLE ENTRAPMENT" in body
    assert "floor=1" in body and "load=0%" in body
    # The deadline callback precedes the same-time status tick: use the real last report.
    assert "telemetry_t=59s" in body and "age=1s" in body
    assert ctx.get("controllers").command_log == []


def test_disconnected_car_retains_last_report_with_age_and_ignores_stale_future_reports():
    ctx = build(parts=["sim", "safety"])
    sim = ctx.get("controllers")
    ctx.clock.run_until(10)
    sim.cars["A"].link_lost = True
    status = replace(sim.status("A"), floor=7, load_pct=42, passengers=3)
    ctx.bus.publish(E.CarStatusUpdated(10, status))
    status.floor = 99  # The event object is mutable; cached evidence must be a snapshot.
    ctx.clock.run_until(20)
    for report in [E.CarStatusUpdated(9, replace(status, floor=8)),
                   E.CarStatusUpdated(21, replace(status, floor=9)),
                   E.CarStatusUpdated(20, replace(status, floor=10, disconnected=True))]:
        ctx.bus.publish(report)
    ctx.get("devices").alarm_button("A")
    alert = ctx.bus.of_type(E.AlertRaised)[-1]
    _t, _recipients, _subject, body = notification(ctx, "passenger_alarm")
    assert alert.floor == 7
    assert "floor=7" in body and "load=42%" in body and "passengers=3" in body
    assert "telemetry_t=10s" in body and "age=10s" in body and "last reported" in body


def test_no_telemetry_is_explicitly_unknown_and_does_not_suppress_alarm():
    ctx = build(parts=["sim", "safety"])
    ctx.get("devices").alarm_button("A")
    alert = ctx.bus.of_type(E.AlertRaised)[-1]
    _t, _recipients, _subject, body = notification(ctx, "passenger_alarm")
    assert alert.floor is None
    assert "floor=unknown" in body and "load=unknown" in body
    assert "telemetry_t=unknown" in body and "passengers=unknown" in body


def test_stuck_report_delivers_context_and_does_not_duplicate_at_deadline():
    ctx = build()
    ctx.get("devices").power.lose_mains(stuck_cars=("A",))
    ctx.clock.run_until(61)
    entries = [entry for entry in ctx.get("notifier").inbox if "entrapment" in entry[2]]
    assert len(entries) == 1 and entries[0][0] <= 40
    assert "floor=1" in entries[0][3] and "telemetry_t=" in entries[0][3]
    assert ctx.get("controllers").command_log == []
