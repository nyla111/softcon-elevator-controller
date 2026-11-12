"""Part 1 Simulator & integration (Tech lead).

Scope: Simulated cars/doors/load, traffic generator, mock ACS, camera, fire panel, power room, notifier test inbox, heartbeat (FR-104), DB buffering (FR-107), failover (NFR-R2).
Provides ctx.services key(s): controllers, acs, notifier

Modules: cars, traffic, population, harness, baseline, bridge (temporary), devices (mock ACS,
notifier, fire panel, power room, camera), faults, heartbeat, database, failover.
Passenger arrivals are produced by TrafficGenerator (traffic.py), started by the harness.
"""
from __future__ import annotations


def register(ctx) -> None:
    from .bridge import TemporaryBridge
    from .cars import Simulator
    from .database import TripDatabase
    from .devices import Devices, MockAcs, MockNotifier
    from .failover import Failover
    from .heartbeat import HeartbeatMonitor
    from .population import Population

    sim = Simulator(ctx)
    population = Population.build(ctx.rng)
    db = TripDatabase(ctx)
    ctx.services["controllers"] = sim
    ctx.services["population"] = population      # the people the ACS knows
    ctx.services["acs"] = MockAcs(ctx, population)
    ctx.services["notifier"] = MockNotifier(ctx)
    ctx.services["db"] = db
    ctx.services["devices"] = Devices(ctx, sim)  # fire panel, power room, camera
    ctx.services["failover"] = Failover(ctx, sim, db)
    HeartbeatMonitor(ctx, sim)
    TemporaryBridge(ctx)       # TEMPORARY until Part 3 merges (see bridge.py)
