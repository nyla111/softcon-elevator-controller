"""Part 1 Simulator & integration (Tech lead).

Scope: Simulated cars/doors/load, traffic generator, mock ACS, camera, fire panel, power room, notifier test inbox, heartbeat (FR-104), DB buffering (FR-107), failover (NFR-R2).
Provides ctx.services key(s): controllers, acs, notifier, building (layout of this run)

Modules: cars, traffic, population, harness, baseline, bridge (temporary), devices (mock ACS,
notifier, fire panel, power room, camera), faults, heartbeat, database, failover, building (BuildingSpec), workload (SIM-10 load hooks), dashboard_load, dispatch_guard (FR-105), scenarios (registry), live (demo server helpers).
Passenger arrivals are produced by TrafficGenerator (traffic.py), started by the harness.
"""
from __future__ import annotations


def register(ctx) -> None:
    from .bridge import TemporaryBridge
    from .cars import Simulator
    from .database import TripDatabase
    from .devices import Devices, MockAcs, MockNotifier
    from .dispatch_guard import DispatchGuard
    from .failover import Failover
    from .heartbeat import HeartbeatMonitor
    from .population import Population

    from .building import BuildingSpec, SpecCarStates

    building = ctx.services.setdefault("building", BuildingSpec.reference())
    if building.name != "reference":              # the contracts stub only knows config.GROUPS
        ctx.services.setdefault("car_states", SpecCarStates(building))
    sim = Simulator(ctx)
    population = Population.build(ctx.rng, building=building)
    db = TripDatabase(ctx)
    ctx.services["controllers"] = sim
    ctx.services["population"] = population      # the people the ACS knows
    ctx.services["acs"] = MockAcs(ctx, population)
    ctx.services["notifier"] = MockNotifier(ctx)
    ctx.services["db"] = db
    ctx.services["devices"] = Devices(ctx, sim)  # fire panel, power room, camera
    ctx.services["failover"] = Failover(ctx, sim, db)
    ctx.services["dispatch_guard"] = DispatchGuard(ctx)   # FR-105: Part 2 calls its engine here
    HeartbeatMonitor(ctx, sim)
    TemporaryBridge(ctx)       # TEMPORARY until Part 3 merges (see bridge.py)
