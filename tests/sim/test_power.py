"""Mains loss: rescue device, rescue reports, generator, command lockout."""
from ems.contracts.events import DoorsOpened, GeneratorReady, RescueReport
from ems.contracts.types import DoorState

from .helpers import arrive, assign, make


def reports(ctx):
    return {e.car_id: e for e in ctx.bus.of_type(RescueReport)}


def test_every_car_is_rescued_20_to_40_s_after_mains_loss():
    ctx, sim = make()
    sim.register_stop("A", 12, "x")
    ctx.clock.run_until(5)
    ctx.services["devices"].power.lose_mains(generator_failed=True)
    ctx.clock.run_until(60)
    got = reports(ctx)
    assert set(got) == set(sim.cars)
    assert all(e.status == "rescued_at_floor" and 25.0 <= e.t <= 45.0 for e in got.values())
    for s in sim.all_status():
        assert s.doors is DoorState.OPEN and s.position == s.floor
    assert sim.status("A").floor in (4, 5)              # frozen near L4/L5 at t=5, rescued there


def test_riders_leave_at_the_rescue_floor():
    ctx, sim = make()
    arrive(ctx, "P1", 1, 12)
    assign(ctx, sim, "P1", "A")
    ctx.clock.run_until(12)
    ctx.services["devices"].power.lose_mains()
    ctx.clock.run_until(80)
    assert sim.passengers["P1"].evacuated and sim.status("A").passengers == 0


def test_generator_ready_after_delay_and_commands_refused_until_then():
    ctx, sim = make()
    ctx.clock.run_until(5)
    ctx.services["devices"].power.lose_mains(generator_delay=15)
    assert not sim.register_stop("A", 3, "c")
    ctx.clock.run_until(19.9)
    assert not sim.register_stop("A", 3, "c")
    ctx.clock.run_until(20)
    assert [e.t for e in ctx.bus.of_type(GeneratorReady)] == [20.0]
    assert sim.register_stop("A", 3, "c")
    refused = [e for e in sim.command_log if e[0] < 20 and e[4]]
    assert refused == []


def test_cars_resume_after_power_returns():
    ctx, sim = make()
    sim.register_stop("A", 9, "c")
    ctx.clock.run_until(4)
    ctx.services["devices"].power.lose_mains(generator_delay=10)
    ctx.clock.run_until(300)
    assert 9 in [e.floor for e in ctx.bus.of_type(DoorsOpened) if e.car_id == "A"]


def test_failed_generator_keeps_commands_refused_until_mains_restored():
    ctx, sim = make()
    power = ctx.services["devices"].power
    power.lose_mains(generator_failed=True)
    ctx.clock.run_until(500)
    assert not ctx.bus.of_type(GeneratorReady)
    assert not sim.register_stop("A", 3, "c")
    power.restore_mains()
    assert sim.register_stop("A", 3, "c")
    ctx.clock.run_until(600)
    assert sim.status("A").floor == 3


def test_failed_and_stuck_rescue_cars():
    ctx, sim = make()
    ctx.services["devices"].power.lose_mains(failed_rescue_cars=("B",), stuck_cars=("C",))
    ctx.clock.run_until(100)
    got = reports(ctx)
    assert "B" not in got and got["C"].status == "stuck"
    assert got["A"].status == "rescued_at_floor"
    assert sim.status("B").doors is DoorState.CLOSED
