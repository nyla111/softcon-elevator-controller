"""Part 2 Dispatch & modes.

Scope: Call handling 4.1, modes 4.3, FR-100/101 overload re-assign, FR-105 fallback rule.
Provides ctx.services key(s): dispatcher

Modules:
  service  Dispatcher (protocol in contracts/interfaces.py): call flow, pending queue, FR-04
           re-assignment, FR-100 overload, FR-13 ceiling watch, FR-14 Eco step-up, parking and
           Eco standby, pauses for fire / power loss / EMS server loss
  engine   EtdEngine, the replaceable DispatchEngine plug-in (FR-06), always called through
           Part 1's FR-105 guard (ctx.services["dispatch_guard"])
  model    trip replay: predicted pick-up and arrival times per car
  modes    ModeManager: schedule (FR-11), manual switch (FR-12), step-up override (FR-14)
  acceptance  checks for SIM-01..05 and SIM-10 (used by ems/sim/scenarios.py)
"""
from __future__ import annotations


def register(ctx) -> None:
    from .service import Dispatcher

    dispatcher = Dispatcher(ctx)
    dispatcher.subscribe()
    ctx.services["dispatcher"] = dispatcher
