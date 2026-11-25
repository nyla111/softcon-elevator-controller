"""NFR-SF3: loss of fire I/O is both fail-safe and notified."""
import pytest

from ems.contracts import events as E
from ems.contracts.types import CarState, Role
from ems.core.app import build


def io_alerts(ctx):
    return [e for e in ctx.bus.of_type(E.AlertRaised) if e.kind == "fire_io_loss"]


@pytest.mark.parametrize("panel_first", [False, True])
def test_io_loss_notifies_once_even_if_panel_already_latched_fire(panel_first):
    ctx = build()
    fire = ctx.get("devices").fire
    if panel_first:
        fire.alarm()
    fire.alarm("io_signal_lost")
    fire.alarm("io_signal_lost")
    ctx.clock.run_until(2)
    assert len(io_alerts(ctx)) == 1
    alert = io_alerts(ctx)[0]
    assert alert.t == 0 and alert.severity == "high"
    assert {"security", "fm", "contractor"} <= set(alert.recipients)
    inbox = [e for e in ctx.get("notifier").inbox if "fire_io_loss" in e[2]]
    assert len(inbox) == 1 and inbox[0][0] == 0
    assert "I/O" in inbox[0][3] and "lost" in inbox[0][3]
    safety = ctx.get("car_states")
    assert all(safety.state(c) is CarState.FIRE_RECALL for c in safety.table.cars())
    assert all(safety.dispatchable(g) == [] for g in safety.table.groups)
    assert any("stairs" in e.text_en for e in ctx.bus.of_type(E.PanelMessage))
    assert ctx.get("controllers").command_log == []


def test_io_alert_episode_ends_only_after_full_fm_recovery():
    ctx = build()
    fire, safety = ctx.get("devices").fire, ctx.get("car_states")
    fire.alarm("io_signal_lost")
    fire.clear()
    assert not safety.confirm_recovery(Role.FM)
    fire.alarm("io_signal_lost")
    assert len(io_alerts(ctx)) == 1
    fire.clear()
    assert ctx.get("controllers").reset_fire()
    ctx.clock.run_until(1)  # Real controller-normal and safety-clear reports.
    assert safety.confirm_recovery(Role.FM)
    fire.alarm("io_signal_lost")
    assert len(io_alerts(ctx)) == 2
    assert ctx.get("controllers").command_log == []


def test_stale_io_loss_does_not_alert_after_recovery():
    ctx = build(parts=["sim", "safety"])
    ctx.clock.run_until(10)
    fire = ctx.get("devices").fire
    fire.alarm()
    ctx.clock.run_until(11)  # Let the real adapter observe the active fire before reset.
    fire.clear()
    assert ctx.get("controllers").reset_fire()
    ctx.clock.run_until(12)
    assert ctx.get("car_states").confirm_recovery(Role.FM)
    ctx.bus.publish(E.FireAlarm(9, "io_signal_lost"))
    assert io_alerts(ctx) == []
    assert not ctx.get("car_states").fire
    assert ctx.get("controllers").command_log == []
