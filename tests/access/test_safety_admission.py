"""Real entry-point FR-120/121/122/125 and BR-07 integration."""
from datetime import datetime

from ems.contracts import events as E
from ems.core.app import build


def test_real_card_admission_blocks_sixth_and_delivers_refusal_before_publication():
    ctx = build()
    access = ctx.get("access")
    calls = [access.tap_card("card-L5-001", "panel:1", 1, "LOW") for _ in range(20)]
    assert all(calls[:5]) and calls[5:] == [None] * 15
    assert len(ctx.bus.of_type(E.CallRequested)) == 5
    refused = [e for e in ctx.bus.of_type(E.PanelMessage) if "Too many" in e.text_en]
    assert len(refused) == 15 and all(e.t == 0 and e.text_vi for e in refused)


def test_staggered_real_actions_and_third_daily_episode_reach_security():
    ctx = build()
    access = ctx.get("access")
    for t in range(5):
        ctx.clock.run_until(t)
        assert access.tap_card("card-L5-001", "p", 1, "LOW")
    ctx.clock.run_until(59)
    assert access.tap_card("card-L5-001", "p", 1, "LOW") is None
    ctx.clock.run_until(60)
    assert access.tap_card("card-L5-001", "p", 1, "LOW") is None
    for start in (119, 179):
        ctx.clock.run_until(start)
        for _ in range(5):
            assert access.tap_card("card-L5-001", "p", 1, "LOW")
        assert access.tap_card("card-L5-001", "p", 1, "LOW") is None
    notices = [n for n in ctx.get("notifier").inbox if "action_limit_review" in n[2]]
    assert len(notices) == 1 and notices[0][0] == 179 and notices[0][1] == ["security"]
    assert "card-L5-001" not in notices[0][3]
    logs = access.get_access_logs("staff-security-1")
    assert any(l["action"] == "ACTION_LIMIT_REVIEW" and l["credential_id"] == "card-L5-001"
               for l in logs)


def test_accessible_exit_alarm_medical_and_fire_real_entry_points_ignore_block():
    ctx = build(start=datetime(2026, 11, 16, 7))
    access = ctx.get("access")
    policy = access.actions
    for card in ("card-L5-001", "staff-security-1"):
        assert [policy.limiter.allow(card) for _ in range(6)] == [True] * 5 + [False]
    ctx.bus.publish(E.PassengerArrived(0, "accessible", "p", 1, 5, "LOW",
                                     "card-L5-001", True, False))
    ctx.bus.publish(E.PassengerArrived(0, "exit", "p", 5, 1, "LOW",
                                     "card-L5-001", False, False))
    assert {e.call.call_id for e in ctx.bus.of_type(E.CallRequested)} == {"accessible", "exit"}
    ctx.get("devices").alarm_button("A")
    assert any("passenger_alarm" in n[2] and n[0] <= 10 for n in ctx.get("notifier").inbox)
    assert ctx.get("priority").request_medical("staff-security-1", 8, 1).ok
    ctx.get("devices").fire.alarm()
    assert ctx.get("car_states").fire
    assert not policy.limiter.allow("card-L5-001")


def test_uncertain_in_car_calls_retained_and_only_owner_cancelled_or_proven_replay_removed():
    ctx = build()
    access = ctx.get("access")
    assert access.tap_car_card("A", "staff-fm-1")
    for destination in (5, 6, 7):
        assert access.press_car_floor("A", destination)
    assert any(e.kind == "uncertain_car_calls" for e in ctx.bus.of_type(E.AlertRaised))
    stops = list(access.actions.car_calls)
    assert len(stops) == 3
    assert not access.cancel_action("staff-security-1", stops[0])
    assert access.cancel_action("staff-fm-1", stops[0])
    assert len(access.actions.car_calls) == 2
    token = access.begin_car_action("A")
    assert access.press_car_floor("A", 8, action_id=token)
    count = len(access.actions.car_calls)
    # Use an exempt credential role after five ordinary actions are exhausted.
    ctx.clock.run_until(61)
    assert access.tap_car_card("A", "staff-fm-1")
    # Earlier stops have physically completed; replay must not create a fresh stop.
    count = len(access.actions.car_calls)
    commands = len(ctx.get("controllers").command_log)
    assert access.press_car_floor("A", 8, action_id=token)
    assert len(access.actions.car_calls) == count
    assert len(ctx.get("controllers").command_log) == commands


def test_owned_hall_call_no_show_cancel_after_actual_door_cycle_counts_as_action():
    ctx = build()
    access = ctx.get("access")
    ref = access.request_hall_call("card-L5-001", "hall:1", 1, 5, "LOW")
    assert ref
    ctx.clock.run_until(30)
    cancels = [e for e in ctx.bus.of_type(E.CallCancelled) if e.call_id == ref]
    assert len(cancels) == 1 and cancels[0].reason == "no_show"
    assert ctx.get("dispatcher").calls[ref].state == "cancelled"
    assert len(access.actions.limiter.actions["card-L5-001"]) == 2  # call + proven no-show


def test_boarded_or_uncertain_hall_call_never_cancelled_as_no_show():
    ctx = build()
    access = ctx.get("access")
    ref = access.request_hall_call("card-L5-001", "hall:1", 1, 5, "LOW")
    access.actions.mark_uncertain(ref)
    ctx.clock.run_until(30)
    assert not [e for e in ctx.bus.of_type(E.CallCancelled) if e.call_id == ref]
    assert ctx.get("dispatcher").calls[ref].state != "cancelled"


def test_fire_blocks_in_car_controller_attempts_even_with_an_exempt_exit():
    ctx = build()
    access = ctx.get("access")
    assert access.tap_car_card("A", "staff-fm-1")
    ctx.get("devices").fire.alarm()
    assert not access.press_car_floor("A", 1)
    assert ctx.get("controllers").command_log == []
