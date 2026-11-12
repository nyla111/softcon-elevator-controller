"""Part 1 Simulator & integration (Tech lead).

Scope: Simulated cars/doors/load, traffic generator, mock ACS, camera, fire panel, power room, notifier test inbox, heartbeat (FR-104), DB buffering (FR-107), failover (NFR-R2).
Provides ctx.services key(s): controllers, acs, notifier

Done so far: cars (controllers), traffic generator, scenario harness.
Passenger arrivals are produced by TrafficGenerator (traffic.py), started by the harness.
"""
from __future__ import annotations


def register(ctx) -> None:
    from .bridge import TemporaryBridge
    from .cars import Simulator
    from .population import Population

    ctx.services["controllers"] = Simulator(ctx)
    ctx.services["population"] = Population.build(ctx.rng)    # same people for the ACS
    TemporaryBridge(ctx)       # TEMPORARY until Part 3 merges (see bridge.py)
