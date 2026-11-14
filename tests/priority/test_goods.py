"""FR-36 / FR-80 / FR-81 / FR-82 goods moves, service car and house rules."""
from datetime import datetime

from ems.contracts.events import (AlertRaised, FireAlarm, FirefighterService, GoodsMoveScheduled,
                                  PanelMessage)
from ems.contracts.types import CarState, Mode
from ems.core.app import build

from .conftest import ADMIN_T01, ADMIN_T02, BUILDING, EMPLOYEE, FM, Env

CREDS = ["mover-1", "mover-2"]


def test_request_validation(env):
    g = env.p.goods
    assert g.request(EMPLOYEE, 100, 200, CREDS).reason == "not_authorized"
    assert g.request(ADMIN_T01, 200, 100, CREDS).reason == "invalid_window"
    env.run_to(50)
    assert g.request(ADMIN_T01, 10, 100, CREDS).reason == "invalid_window"   # in the past
    assert g.request(ADMIN_T01, 100, 200, []).reason == "no_credentials"
    assert g.request(ADMIN_T01, 100, 200, CREDS, car_id="Z").reason == "no_service_car"
    out = g.request(ADMIN_T01, 100, 200, CREDS)
    assert out.ok and out.car_id == "S" and g.moves[out.ref].tenant_id == "T01"
    assert any(a.kind == "goods_move_request" and a.recipients == ["fm"]
               for a in env.events(AlertRaised))


def test_overlap_on_the_same_car_is_refused(env):
    g = env.p.goods
    first = g.request(ADMIN_T01, 100, 200, CREDS)
    assert g.approve(FM, first.ref).ok
    assert g.request(ADMIN_T02, 150, 250, ["x"]).reason == "overlap"
    assert g.request(ADMIN_T02, 200, 300, ["x"]).ok                     # touching is fine


def test_passenger_car_periods_not_in_rush_but_service_car_any_time():
    rush = Env.__new__(Env)                       # same doubles, run starting at 07:00 (Rush)
    Env.__init__(rush)
    rush.ctx = build(seed=1, parts=["priority"], start=datetime(2026, 11, 16, 7, 0), services={
        "building": BUILDING, "controllers": rush.controllers, "car_states": rush.states,
        "dispatcher": rush.dispatcher, "access": rush.access})
    g = rush.ctx.services["priority"].goods
    assert g.request(ADMIN_T01, 0, 600, CREDS, car_id="A").reason == "rush"
    assert g.request(ADMIN_T01, 3 * 3600, 4 * 3600, CREDS, car_id="A").ok   # 10:00-11:00
    assert g.request(ADMIN_T01, 0, 600, CREDS).ok                           # service car S


def test_approved_move_runs_on_schedule_and_restricts_the_car(env):
    g = env.p.goods
    out = g.request(ADMIN_T01, 100, 200, CREDS)
    assert g.approve(EMPLOYEE, out.ref).reason == "not_authorized"
    assert g.approve(FM, out.ref).ok
    ev = env.events(GoodsMoveScheduled)[-1]
    assert (ev.move_id, ev.car_id, ev.start, ev.end, ev.credential_ids) == (
        out.ref, "S", 100, 200, CREDS)
    assert g.service_car_accepts("someone", "S")                        # not started yet
    env.run_to(100)
    assert g.moves[out.ref].status == "active"
    assert env.states.states["S"] is CarState.INDEPENDENT_SERVICE
    assert env.controllers.sent("set_independent_service", "S")[-1][2] == (True,)
    assert g.service_car_accepts("mover-2", "S") and not g.service_car_accepts("someone", "S")
    env.run_to(200)
    assert g.moves[out.ref].status == "completed"
    assert env.states.states["S"] is CarState.GROUP_SERVICE
    assert env.controllers.sent("set_independent_service", "S")[-1][2] == (False,)
    assert g.service_car_accepts("someone", "S")


def test_cancel_before_start_makes_late_callbacks_no_ops(env):
    g = env.p.goods
    out = g.request(ADMIN_T01, 100, 200, CREDS)
    g.approve(FM, out.ref)
    assert g.cancel(ADMIN_T02, out.ref).reason == "not_authorized"      # other tenant
    assert g.cancel(ADMIN_T01, out.ref).reason == "cancelled"
    env.run_to(300)
    assert env.states.log == [] and env.controllers.log == []


def test_cancel_an_active_move(env):
    g = env.p.goods
    out = g.request(ADMIN_T01, 0, 200, CREDS)
    g.approve(FM, out.ref)
    env.run_to(10)
    assert g.cancel(FM, out.ref).ok
    assert env.states.states["S"] is CarState.GROUP_SERVICE
    env.run_to(300)
    assert len(env.controllers.sent("set_independent_service", "S")) == 2   # on, off


def test_start_refused_leaves_nothing_held(env):
    g = env.p.goods
    env.states.refuse.add(("S", CarState.INDEPENDENT_SERVICE))
    out = g.request(ADMIN_T01, 10, 200, CREDS)
    g.approve(FM, out.ref)
    env.run_to(10)
    assert g.moves[out.ref].status == "start_refused" and env.p.holds == {}
    assert any(a.kind == "goods_move_refused" for a in env.events(AlertRaised))


def test_passenger_car_start_checks_rush_and_the_ceiling(env):
    g = env.p.goods
    a = g.request(ADMIN_T01, 10, 200, CREDS, car_id="A")
    b = g.request(ADMIN_T01, 10, 200, CREDS, car_id="B")
    g.approve(FM, a.ref)
    g.approve(FM, b.ref)
    env.dispatcher.breach = {"A"}
    env.run_to(5)
    env.dispatcher.mode = Mode.BALANCED
    env.run_to(10)
    assert g.moves[a.ref].reason == "ceiling" and g.moves[b.ref].status == "active"
    assert ("B", "goods_move") in env.dispatcher.reassigned


def test_fire_cancels_the_active_move_without_commands(env):
    g = env.p.goods
    out = g.request(ADMIN_T01, 0, 500, CREDS)
    g.approve(FM, out.ref)
    env.run_to(10)
    n = len(env.controllers.log)
    env.publish(FireAlarm(env.now()))
    assert g.moves[out.ref].status == "cancelled_emergency"
    assert env.controllers.log[n:] == [] and env.p.holds == {}
    env.run_to(500)
    assert env.controllers.log[n:] == []                                # end callback: no-op
    env.publish(FirefighterService(env.now(), "S", False))              # fire reset done
    assert env.controllers.log[n:] == [("set_independent_service", "S", (False,), True)]


def test_house_rules_need_the_fm_and_configured_values(env):
    g = env.p.goods
    assert g.show_house_rules(EMPLOYEE, {"item_kg": 25}).reason == "not_authorized"
    assert g.show_house_rules(FM, {}).reason == "no_limits"
    out = g.show_house_rules(FM, {"item_kg": 25, "cart_width_m": 0.6})
    assert out.ok
    msg = env.events(PanelMessage)[-1]
    assert msg.target == "car:S" and "25 kg" in msg.text_en and "0.6 m" in msg.text_vi
    move = g.request(ADMIN_T01, 0, 100, CREDS)
    g.approve(FM, move.ref)
    env.run_to(1)
    assert env.events(PanelMessage)[-1].target == "car:S"


def test_approval_after_the_window_has_passed_is_refused(env):
    g = env.p.goods
    out = g.request(ADMIN_T01, 10, 20, CREDS)
    env.run_to(25)
    assert g.approve(FM, out.ref).reason == "expired"
    assert env.events(GoodsMoveScheduled) == [] and env.states.log == []
