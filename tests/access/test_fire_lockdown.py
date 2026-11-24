"""FR-50 proposal: suspend effective locks through the FR-55 fire recovery hold."""
from ems.contracts import events as E
from ems.contracts.types import Role
from ems.core.app import build


def test_effective_fire_suspension_preserves_configuration_and_tenant_permissions():
    ctx = build()
    access, safety = ctx.get("access"), ctx.get("car_states")
    access.lock_floors("staff-security-1", [5])
    lockdown = access.floor_access.lockdown
    permission = access.permission("card-L5-001")
    assert not lockdown.can_visit_floor(5, permission)
    ctx.get("devices").fire.alarm()
    assert lockdown.locked_floors == {5}
    assert lockdown.can_visit_floor(5, permission)
    ctx.bus.publish(E.PassengerArrived(0, "allowed", "panel:1", 1, 5, "LOW",
                                     "card-L5-001", False, False))
    ctx.bus.publish(E.PassengerArrived(0, "unauthorized", "panel:2", 1, 12, "LOW",
                                     "card-L5-001", False, False))
    assert [e.call.call_id for e in ctx.bus.of_type(E.CallRequested)] == ["allowed"]
    assert ctx.bus.of_type(E.AccessRefused)
    ctx.get("devices").fire.clear()
    assert lockdown.can_visit_floor(5, permission)
    assert ctx.get("controllers").reset_fire()
    ctx.clock.run_until(1)
    assert lockdown.can_visit_floor(5, permission) and safety.safety_active()
    assert not safety.confirm_recovery(Role.SECURITY)
    assert ctx.get("controllers").command_log == []
    assert safety.confirm_recovery(Role.FM)
    assert lockdown.locked_floors == {5}
    assert not lockdown.can_visit_floor(5, permission)
    assert lockdown.can_visit_floor(1, None)


def test_power_loss_alone_does_not_suspend_lockdown():
    ctx = build()
    access = ctx.get("access")
    access.lock_floors("staff-security-1", [5])
    ctx.get("devices").power.lose_mains(generator_failed=True)
    assert not access.floor_access.lockdown.can_visit_floor(5, access.permission("card-L5-001"))


def test_stale_fire_event_cannot_suspend_lockdown_after_recovery():
    ctx = build()
    ctx.clock.run_until(10)
    access = ctx.get("access")
    access.lock_floors("staff-security-1", [5])
    fire = ctx.get("devices").fire
    fire.alarm()
    ctx.clock.run_until(11)
    fire.clear()
    assert ctx.get("controllers").reset_fire()
    ctx.clock.run_until(12)
    assert ctx.get("car_states").confirm_recovery(Role.FM)
    ctx.bus.publish(E.FireAlarm(9))
    assert not access.floor_access.lockdown.can_visit_floor(5, access.permission("card-L5-001"))
