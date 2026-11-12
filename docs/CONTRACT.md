# EMS contract (read before coding)

## How the program works
One Python app. Six parts talk in two ways only:
- **Events** (`contracts/events.py`): "something happened". Publish with `ctx.bus.publish(...)`, listen with `ctx.bus.subscribe(EventType, handler)`.
- **Direct calls** (`contracts/interfaces.py`): only when you need an answer right now (e.g. "may this card go to floor 18?").

## Hard rules
1. **Time**: use `ctx.clock.now()` only. Never `time.time()`, `datetime.now()` or `sleep()`. Delays = `ctx.clock.call_later(sec, fn)`.
2. **Randomness**: use `ctx.rng` only (seeded runs must repeat exactly).
3. **One publisher per event** (see "pub:" in events.py). Don't publish other parts' events.
4. **Car state**: only Part 4 changes it. Others ask via `car_states.request(...)`; it may say no (BR-06).
5. **Controllers can refuse** any command (returns False). Always handle False.
6. **No personal data** in trip records or reports. Credential IDs only in the security log.
7. **Don't edit `contracts/` or `core/`**. Need a new event/field? Ask the tech lead.
8. **Missing dependency?** Use a stub that returns a fixed answer. Don't wait for others.

## Your package
Your package `ems/<your_part>/__init__.py` already exists. Fill in `register`:
```python
def register(ctx):
    service = MyService(ctx)
    ctx.bus.subscribe(SomeEvent, service.handler)
    ctx.services["<key>"] = service      # see keys in core/app.py
```
Anything not registered yet is filled with a stub (`contracts/stubs.py`), so the app always runs.
Test your part alone with `build(seed=1, parts=["<your_part>"])`.

| Part | Package | Service key | Implements |
|---|---|---|---|
| 1 Simulator & integration | `ems/sim` | controllers, acs, notifier, db | ControllerAdapter, AcsClient, Notifier, Database |
| 2 Dispatch & modes | `ems/dispatch` | dispatcher | Dispatcher, DispatchEngine |
| 3 Access & panels | `ems/access` | access | AccessService |
| 4 Safety & edge cases | `ems/safety` | car_states | CarStates |
| 5 Priority & service car | `ems/priority` | priority | (events only + own API) |
| 6 Dashboard, camera & admin | `ems/monitor` | reports | (subscribes to all events) |

## Conventions
- Floors are ints: B2 = -2, B1 = -1, L1 = 1 ... L30 = 30.
- Branch: `part<N>/<short-name>`; PR title starts with the requirement ID, e.g. `FR-72 VIP caps`.
- Tests in `tests/<your_part>/`, run with `pytest`.

## Contract changelog
Changes made to `contracts/` after the first skeleton, newest last.

| Change | Publisher | Consumer | Notes |
|---|---|---|---|
| `PassengerArrived` event (events.py) | Part 1 (traffic generator) | Part 1 simulator (owns the passenger), Part 3 access | Part 3 checks permissions and turns it into `CallRequested`. Carries `credential_id` (employee card or visitor QR), `accessible`, `vip`, `group`. |
| `ctx.services["population"]` | Part 1 | Part 1 mock ACS, Part 3 (tests) | Deterministic synthetic tenants and employees (`ems/sim/population.py`, about 2,500 people, 25 tenants). Same people for the same seed. |
| Temporary bridge `ems/sim/bridge.py` | Part 1 | none | While `ctx.services["access"]` is `StubAccess`, publishes `CallRequested` straight from `PassengerArrived`. Does nothing once Part 3 registers the real service. Delete when Part 3 merges. |
| `ctx.services["db"]` + `Database` protocol (interfaces.py), `StubDatabase` (stubs.py) | Part 1 (`TripDatabase`) | Part 6 (writes trips), anyone | `write()` never raises and buffers while the database is down; `records()` returns persisted records only (the real database flushes every 30 s); `available()`. |
| `EmsServerDown(node)`, `EmsServerUp(node, gap_s)` (events.py) | Part 1 (failover model) | Part 2 (stop / resume assigning), Part 4, Part 6 | The primary EMS server crashed / the standby took over (gap target <= 10 s). While down the controllers refuse EMS commands and serve passengers with their own nearest-car logic. |
| `AcsClient.revoke(credential_id)` (interfaces.py, `StubAcs.revoke`) | Part 1 (mock ACS) | Part 3, Part 6 | NFR-SE6: a revoked credential answers `valid=False`. |
| Extra service keys `devices`, `failover` | Part 1 | tests and the fault injector only | `devices` = fire panel, power room, camera; `failover` = crash/takeover model. Not a contract for other parts. |

