"""FR-113 counts independent mechanical episodes, not repeated reports."""
import pytest

from ems.contracts import events as E
from ems.core.app import build

DAY = 86400


def reviews(ctx):
    return [e for e in ctx.bus.of_type(E.AlertRaised) if e.kind == "fault_review"]


def episode(ctx, car="A", t=0):
    ctx.clock.run_until(t)
    ctx.bus.publish(E.ControllerFault(t, car, "brake", False, False))
    ctx.bus.publish(E.ControllerFault(t, car, "brake", False, False))
    ctx.bus.publish(E.ControllerFaultCleared(t, car))


def test_third_mechanical_episode_delivers_one_fm_notice_on_real_services():
    ctx = build()
    sim = ctx.get("controllers")
    for t in (0, 2, 4, 6):
        ctx.clock.run_until(t)
        sim.inject_fault("A", "brake")
        sim.inject_fault("A", "brake")
        if t < 4:
            assert reviews(ctx) == []
        sim.clear_fault("A")
    assert [(e.car_id, e.t, e.recipients) for e in reviews(ctx)] == [("A", 4, ["fm"])]
    inbox = [n for n in ctx.get("notifier").inbox if "fault_review" in n[2]]
    assert len(inbox) == 1 and inbox[0][0] == 4 and inbox[0][1] == ["fm"]
    assert "30" in inbox[0][3] and "review" in inbox[0][3].lower()
    # P5 still owns its per-episode mechanical notifications.
    assert len([e for e in ctx.bus.of_type(E.AlertRaised) if e.kind == "fault"]) == 4


@pytest.mark.parametrize("last,expected", [(30 * DAY, 1), (30 * DAY + 0.001, 0)])
def test_rolling_thirty_day_boundary_is_inclusive(last, expected):
    ctx = build(parts=["safety"])
    for t in (0, DAY, last):
        episode(ctx, t=t)
    assert len(reviews(ctx)) == expected


def test_new_car_link_loss_and_repeated_assertions_do_not_create_mechanical_episodes():
    ctx = build(parts=["safety"])
    for t in range(5):
        ctx.clock.run_until(t)
        ctx.bus.publish(E.ControllerFault(t, "A", "brake", False, False))
        ctx.bus.publish(E.HeartbeatLost(t, "B"))
        ctx.bus.publish(E.HeartbeatRestored(t, "B"))
    episode(ctx, car="B", t=5)
    episode(ctx, car="C", t=6)
    assert reviews(ctx) == []
    ctx.bus.publish(E.ControllerFaultCleared(6, "A"))
    episode(ctx, t=7)
    episode(ctx, t=8)
    assert len(reviews(ctx)) == 1 and reviews(ctx)[0].car_id == "A"


def test_threshold_can_notify_again_after_old_episodes_age_out():
    ctx = build(parts=["safety"])
    for t in (0, 1, 2, 31 * DAY, 31 * DAY + 1, 31 * DAY + 2):
        episode(ctx, t=t)
    assert [e.t for e in reviews(ctx)] == [2, 31 * DAY + 2]
