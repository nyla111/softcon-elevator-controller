from ems.contracts.events import CallAssigned, PassengerArrived
from ems.core.app import build


def make():
    """Fresh context with only the simulator registered; returns (ctx, simulator)."""
    ctx = build(seed=1, parts=["sim"])
    return ctx, ctx.services["controllers"]


def arrive(ctx, pid, origin, destination, group="LOW", accessible=False, vip=False):
    ctx.bus.publish(PassengerArrived(ctx.clock.now(), pid, f"panel-L{origin}", origin,
                                     destination, group, None, accessible, vip))


def assign(ctx, sim, pid, car_id):
    """Do what the dispatcher would: name the car and register the origin stop."""
    origin = sim.passengers[pid].origin
    ctx.bus.publish(CallAssigned(ctx.clock.now(), pid, car_id, 0.0))
    assert sim.register_stop(car_id, origin, pid)


def times(ctx, event_type, **match):
    return [e.t for e in ctx.bus.of_type(event_type)
            if all(getattr(e, k) == v for k, v in match.items())]
