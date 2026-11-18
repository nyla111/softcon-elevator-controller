"""FR-55: the simulated controllers report to Part 4 when they are back to normal, so the
facility manager can confirm recovery after a fire alarm or a power loss (#26)."""
from ems.contracts import config
from ems.contracts.events import PassengerArrived
from ems.contracts.types import CarState, Role
from ems.core.app import build
from ems.sim.baseline import install_dispatch_standin


def make():
    ctx = build(seed=1)
    return (ctx, ctx.services["car_states"], ctx.services["controllers"],
            ctx.services["devices"])


def test_fire_recovery_opens_only_after_clear_and_reset():
    ctx, states, sim, devices = make()
    ctx.clock.run_until(30)
    devices.fire.alarm("panel")
    ctx.clock.run_until(120)
    assert not states.recovery_eligible()
    devices.fire.clear()
    ctx.clock.run_until(125)
    assert not states.recovery_eligible()            # controllers not reset yet
    assert sim.reset_fire()
    ctx.clock.run_until(130)
    assert states.recovery_eligible()
    assert not states.confirm_recovery(Role.SECURITY)
    assert states.confirm_recovery(Role.FM)
    assert not states.safety_active()
    assert {states.state(c) for c in sim.cars} == {CarState.GROUP_SERVICE}


def test_cars_serve_passengers_again_after_fm_confirms():
    ctx, states, sim, devices = make()
    install_dispatch_standin(ctx)
    devices.fire.alarm("panel")
    ctx.clock.run_until(120)
    devices.fire.clear()
    sim.reset_fire()
    ctx.clock.run_until(125)
    assert states.confirm_recovery(Role.FM)
    ctx.bus.publish(PassengerArrived(ctx.clock.now(), "after-fire", "panel-L1", 1, 5, "LOW",
                                     "card-L5-001", False, False))
    ctx.clock.run_until(ctx.clock.now() + 300)
    assert sim.undelivered() == 0


def test_power_recovery_waits_for_stable_mains():
    ctx, states, sim, devices = make()
    ctx.clock.run_until(30)
    devices.power.lose_mains()
    ctx.clock.run_until(90)
    assert not states.recovery_eligible()
    devices.power.restore_mains()
    ctx.clock.run_until(ctx.clock.now() + 5)
    assert not states.recovery_eligible()            # mains not stable long enough yet
    ctx.clock.run_until(ctx.clock.now() + config.MAINS_STABLE_S)
    assert states.recovery_eligible()
    assert states.confirm_recovery(Role.FM)


def test_a_faulted_car_does_not_block_recovery_and_stays_out_of_service():
    ctx, states, sim, devices = make()
    devices.fire.alarm("panel")
    ctx.clock.run_until(120)
    sim.inject_fault("B", "F-01")
    devices.fire.clear()
    sim.reset_fire()
    ctx.clock.run_until(125)
    assert states.confirm_recovery(Role.FM)
    assert states.state("B") is CarState.FAULTED
    assert states.state("A") is CarState.GROUP_SERVICE
    sim.clear_fault("B")
    ctx.clock.run_until(130)
    assert states.state("B") is CarState.GROUP_SERVICE
