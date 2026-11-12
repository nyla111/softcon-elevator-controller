import pytest

from ems.contracts.events import (AlarmButtonPressed, AlertRaised, CameraEvent, FireAlarm,
                                  FireAlarmCleared, GeneratorReady, MainsLost, MainsRestored)
from ems.contracts.types import Role

from .helpers import make


def test_acs_lookup_of_an_employee_card():
    ctx, _ = make()
    acs, pop = ctx.services["acs"], ctx.services["population"]
    e = pop.lookup("card-L18-007")
    perm = acs.lookup("card-L18-007")
    assert perm.valid and perm.allowed_floors == e.allowed_floors
    assert perm.default_floor == 18 and Role.EMPLOYEE in perm.roles
    assert perm.tenant_id == e.tenant_id and not perm.from_cache
    acc = next(x for x in pop.employees if x.accessible)
    assert Role.ACCESSIBLE in acs.lookup(acc.credential_id).roles
    vip = next(x for x in pop.employees if x.vip)
    assert Role.VIP in acs.lookup(vip.credential_id).roles


def test_unknown_and_revoked_credentials_are_invalid():
    ctx, _ = make()
    acs = ctx.services["acs"]
    assert not acs.lookup("card-nobody").valid
    assert acs.lookup("card-L18-007").valid
    acs.revoke("card-L18-007")
    assert not acs.lookup("card-L18-007").valid


def test_acs_down_raises_timeout_and_logs_latency():
    ctx, _ = make()
    acs = ctx.services["acs"]
    acs.lookup("card-L18-007")
    acs.set_down(True)
    with pytest.raises(TimeoutError):
        acs.lookup("card-L18-007")
    acs.set_down(False)
    acs.lookup("card-L18-007")
    assert [(lat, out) for _, _, lat, out in acs.lookup_log] == [(0.1, "ok"), (1.0, "timeout"),
                                                                 (0.1, "ok")]


def test_visitor_pass_validity_window_and_revoke():
    ctx, _ = make()
    acs = ctx.services["acs"]
    qr = acs.issue_visitor_pass("T05", {7}, 100.0, 200.0, group_size=2)
    assert qr.startswith("QR-")
    assert not acs.lookup(qr).valid                    # t=0: not yet valid
    ctx.clock.run_until(150)
    perm = acs.lookup(qr)
    assert perm.valid and 7 in perm.allowed_floors and Role.VISITOR in perm.roles
    ctx.clock.run_until(200)
    assert not acs.lookup(qr).valid                    # expired
    qr2 = acs.issue_visitor_pass("T05", {7}, 0.0, 1000.0)
    assert acs.lookup(qr2).valid and qr2 != qr
    acs.revoke(qr2)
    assert not acs.lookup(qr2).valid


def test_acs_down_does_not_block_pass_issue():
    ctx, _ = make()
    ctx.services["acs"].set_down(True)
    assert ctx.services["acs"].issue_visitor_pass("T01", {2}, 0, 10)


def test_notifier_delivers_alerts_into_inbox():
    ctx, _ = make()
    ctx.clock.run_until(5)
    ctx.bus.publish(AlertRaised(5.0, "critical", "entrapment", ["security", "fm"],
                                "Car B stuck", car_id="B", floor=7))
    inbox = ctx.services["notifier"].inbox
    assert len(inbox) == 1
    t, recipients, subject, body = inbox[0]
    assert (t, recipients, body) == (5.0, ["security", "fm"], "Car B stuck")
    assert "critical" in subject and "entrapment" in subject
    ctx.services["notifier"].send(["fm"], "hello", "world")
    assert len(inbox) == 2


def test_building_devices_publish_their_events():
    ctx, _ = make()
    dev = ctx.services["devices"]
    dev.fire.alarm()
    dev.fire.alarm("io_signal_lost")
    dev.fire.clear()
    dev.power.lose_mains(generator_delay=15)
    ctx.clock.run_until(20)
    dev.power.restore_mains()
    dev.camera.event("C", "smoking", people_count=2)
    dev.alarm_button("D")
    log = ctx.bus.log
    assert [e.source for e in ctx.bus.of_type(FireAlarm)] == ["panel", "io_signal_lost"]
    assert len(ctx.bus.of_type(FireAlarmCleared)) == 1
    assert len(ctx.bus.of_type(MainsLost)) == 1 and len(ctx.bus.of_type(MainsRestored)) == 1
    assert [e.t for e in ctx.bus.of_type(GeneratorReady)] == [15.0]
    cam = ctx.bus.of_type(CameraEvent)[0]
    assert (cam.car_id, cam.kind, cam.people_count) == ("C", "smoking", 2)
    assert cam.clip_ref.startswith("clip://") and cam.event_id
    assert ctx.bus.of_type(AlarmButtonPressed)[0].car_id == "D"
    assert log
