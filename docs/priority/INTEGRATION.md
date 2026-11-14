# Part 5 integration guide

`ctx.services["priority"]` is a `PriorityService` (`ems/priority/service.py`) with two
sub-services: `.maintenance` (`maintenance.py`) and `.goods` (`goods.py`). Pure policy functions
are in `policy.py`. Look the service up lazily inside handlers (`ctx.get("priority")`).

Owners (given by Minh; the repo's CODEOWNERS still has placeholders, GitHub accounts not
verified): Part 1 simulator / core / contracts - Nhi; Part 2 dispatch - Nam; Part 3 access - Bao Vy;
Part 4 safety - Thao Vy; Part 6 monitor - Mai. Who does what next: [HANDOFF.md](HANDOFF.md).

> **Status (2026-11-14):** Part 2, 3 and 4 are still contract stubs on `main`. Nothing in
> `ems/dispatch` calls Part 5. The VIP handshake below is a **proposal** (issue #9), implemented
> on Part 5's side and exercised only by the scenario stand-in
> `ems/priority/standins.py::PriorityAwareNearestCar`. It is not an approved shared contract.

## 1. Rules Part 5 follows (and expects others to rely on)

| Topic | Part 5 behaviour |
|---|---|
| Events Part 5 publishes | `PriorityTripStarted`, `PriorityTripEnded`, `GoodsMoveScheduled`; `AlertRaised`, `PanelMessage` (any-module events). Never `CallRequested`, `CallAssigned`, `CallReassigned`, `CarStateChanged`. |
| Car state | Only `car_states.request(car, state, "priority:<kind>")` and `release(car, state, ...)` for states Part 5 holds: `VIP_EXCLUSIVE` (vip, medical, send_to_lobby; CCR-03), `MAINTENANCE`, `INDEPENDENT_SERVICE` (goods). A refusal is final for that car. Part 5 never releases a state it does not hold. |
| Controller commands | `close_to_new_calls`, `express`, `set_independent_service`. Every `False` is handled: the state is released and nothing is counted. No command while the car is faulted / disconnected, during fire or power loss, or while the EMS server is down; "re-open" commands are retried after recovery. |
| Identity | Credential id -> roles via `access.permission()` (`auth.AccessAuthorizer`). A caller cannot pass a role. Unknown / invalid / ACS timeout = no roles. |
| Personal data | Credential ids stay in memory (authorisation, VIP quota, goods-move credential list). Not in trip records, alerts, audit (`priority.audit` stores roles only) or `PriorityTrip*` events. `GoodsMoveScheduled.credential_ids` is defined by the contract for Part 3. |
| Time / randomness | `ctx.clock` only (`call_later`/`call_at` with epoch guards so late callbacks are no-ops). No randomness. |
| Building | Groups, cars, floors, lobby from `ctx.services["building"]`; no car id in production code. |

## 2. API reference

Every method returns `Outcome(ok, reason, ref, car_id)`; `ok=False` always has a reason
(`not_authorized`, `emergency`, `ems_down`, `state_refused`, `controller_refused`, ...).

| Method | Role | Effect / events | Idempotency |
|---|---|---|---|
| `claim_call(call) -> car or None` (Part 2; **proposed**, section 4) | call priority VIP (authorised by Part 3) | FR-71 car, FR-72 caps + `would_breach_ceiling_without`; `VIP_EXCLUSIVE`, close to new calls, `express(origin)`; `PriorityTripStarted("vip")`; `reassign_calls_of(car)` | same call id -> same answer, one start |
| `rank(call)`, `order(calls)`, `car_ok_for(call, status)`, `door_dwell_s(call)` | - | pure (FR-76, FR-08) | - |
| `set_vip_quota(cred, vip_cred, n)`, `revoke_vip(cred, vip_cred)` | FM | FR-70 per-day exclusive-trip quota / revoke | last call wins |
| `request_medical(cred, origin, dest)` | SECURITY | fastest healthy car (empty first), VIP trips of the served groups yield; `express(origin)`, doors held on arrival; `PriorityTripStarted("medical")`; critical alert if no car | new trip per call |
| `medical_depart(cred, trip_id)` | SECURITY | only at origin; `express(dest)`, doors held on arrival | `not_at_origin` otherwise |
| `send_to_lobby(cred, car_id)` | SECURITY | FR-128: `express(lobby)`, doors held; riders keep their stops | `car_held:<kind>` if held |
| `finish_trip(cred, trip_id)` | SECURITY | doors close, car back to service; `PriorityTripEnded(cancelled = not at destination)` | `unknown_trip` the 2nd time |
| `maintenance.request_maintenance(cred, car)` | TECHNICIAN | FR-32 entry, or FR-33 `approval_required` + FM alert, 600 s timeout | duplicate -> same pending request |
| `maintenance.approve_removal / deny_removal(cred, rid)` | FM | late (expired) / repeated answers refused | status as reason |
| `maintenance.exit_maintenance(cred, car)` | TECHNICIAN | release `MAINTENANCE`, re-open, reset "since service" counters | `not_in_maintenance` |
| `maintenance.set_service_threshold(cred, counter, n)` | FM, TECHNICIAN | FR-31 reminder threshold (no default) | last call wins |
| `maintenance.usage(car)` | - | `{"total", "since_service"}` counters | - |
| `goods.request(cred, start, end, creds, car=None)` | TENANT_ADMIN | FR-81 request (default car: first service-group car); FM info alert | new move per call |
| `goods.approve / reject(cred, move_id)` | FM | approve publishes `GoodsMoveScheduled`, books start/end | status as reason |
| `goods.cancel(cred, move_id)` | FM, or TENANT_ADMIN of the same tenant | ends now; late callbacks ignored | status as reason |
| `goods.service_car_accepts(cred, car)` | - (Part 3) | FR-82 live answer | - |
| `goods.show_house_rules(cred, limits, targets=None)` | FM | FR-80 `PanelMessage` (EN + VI) to service-car displays (or targets); shown again at move start | - |
| `snapshot()` | - | plain dict for a page / API | - |

## 3. Per part

**Part 1 (simulator, core, contracts - Nhi).**
* `ems/sim/scenarios.py`: SIM-06/11/12/18 import their setup/check from `ems/priority/acceptance.py`
  (four registry entries changed, nothing else).
* Simulator behaviour found while testing: `express(floor, hold_doors_open=True)` sent while the
  car is in a door cycle at ANOTHER floor keeps the doors open at that other floor and the car
  never leaves (`SimCar._end_dwell` does not look at `express_floor`). Part 5 works around it
  (moves first, holds on arrival); a fix in `cars.py` would be `if (self.hold_doors and
  self.express_floor is None) or self.fire`.
* CCR-02 (door time), CCR-05 (staff cards).
* Privacy (on main already): `/api/state` event lines print every event field, so the
  building page shows `PassengerArrived.credential_id` and `GoodsMoveScheduled.credential_ids`;
  `ems/web/server.py::_event_line` should drop `credential_id(s)` (NFR-SE2).
* SIM-12 needs "replace 10 ordinary arrivals by VIPs"; Part 5 does it in its setup with
  `VipReplacementPopulation` (same rng draws, no traffic added). A first-class traffic hook would
  be cleaner.

**Part 2 (dispatch, Nam).** See section 4 (proposed handshake), issue #9. In short: ask
`claim_call` for VIP calls before choosing a car; order queues with `order()`; filter cars for
accessible calls with `car_ok_for()`; answer `would_breach_ceiling_without()` and
`reassign_calls_of()` truthfully (Part 5 relies on the returned list); `current_mode()` drives
FR-33 and FR-36.

**Part 3 (access & panels - Bao Vy).**
* Set `Call.priority` from the credential's roles (`ACCESSIBLE` > `VIP` > `NORMAL`); Part 5 trusts
  `CallPriority.VIP` on a `CallRequested` as authorised.
* FR-82: during an active move on a car, accept only `GoodsMoveScheduled.credential_ids` on that
  car's panels, or ask `priority.goods.service_car_accepts(cred, car)` (until CCR-04 exists).
* Part 5 voices accessible assignments (`PanelMessage(voice=True)` to `call.panel_id`): do not voice
  the same assignment again.
* CCR-05: return staff roles from `permission()`.

**Part 4 (safety, car states - Thao Vy).**
* Requests come as `by="priority:<kind>"` with kind `vip`, `medical`, `send_to_lobby`,
  `maintenance`, `goods`. Part 5 releases only states it holds and never asks for recovery from
  `FAULTED` / `FIRE_RECALL` / `EMERGENCY_POWER`.
* `safety_active()` is read before every new trip / removal / goods start.
* On `FireAlarm` / `MainsLost` Part 5 ends trips and active goods moves and sends no command.
  CCR-04 gives SIM-08 its "goods move cancelled" evidence.
* CCR-03 (state for medical trips).

**Part 6 (dashboard, reports - Mai).**
* `PriorityTripStarted/Ended` already go to the security log. Part 5 alert kinds: `fault`,
  `fault_cleared`, `service_due`, `removal_approval`, `removal_expired`, `goods_move_request`,
  `goods_move_refused`, `medical_no_car`, `medical_car_lost`, `send_to_lobby_car_lost`,
  `<kind>_doors_not_held`.
* FR-80 limit values live in Part 6's `house_rule_limits`; the FM shows them with
  `goods.show_house_rules(cred, limits)`.
* FR-128 is started by security after reviewing a `camera_safety` alert; Part 5 does not start it
  from the camera event.
* SIM-06 "degraded waits logged": Part 6 marks `degraded` only on `CallReassigned`; calls that
  waited on a faulted car without being re-assigned are not marked.

## 4. Proposed VIP handshake (`claim_call`) - PROPOSAL, not an approved contract

**Where:** `PriorityService.claim_call(call: Call) -> Optional[CarId]`,
`ems/priority/service.py`. **Real caller today: none.** Only the scenario stand-in calls it.

**Caller and timing.** Part 2, synchronously, inside its own `CallRequested` handler, before it
chooses a car for a call with `call.priority is CallPriority.VIP`. It is a direct call, not an
event, so it does not depend on bus subscription order (Part 2 registers before Part 5). Part 5
never changes the `Call` object and never acts on a `CallRequested` after Part 2 (tested:
`test_integration_main_services::test_calls_are_never_mutated...`).

**Return semantics.**

| Answer | Meaning | What Part 2 does |
|---|---|---|
| a car id | HANDLED: Part 5 has started an exclusive trip; the car is in `VIP_EXCLUSIVE` (via `car_states`), closed to new calls, and an `express(origin)` was accepted; one quota unit is used | publish `CallAssigned(call, car, eta)`; do **not** `register_stop` (it would be refused: closed to new calls) |
| `None` | DECLINED or REJECTED: no exclusive trip (reasons in `priority.decisions`: `hourly_cap`, `group_exclusive_cap`, `ceiling`, `calls_not_moved`, `no_car`, `refused`, `vip_quota`, `vip_revoked`, `emergency`, `ems_down`, `no_destination`). Nothing is held, no quota used | dispatch the call normally, with its VIP rank |

There is no "deferred" answer: Part 5 decides at once. A decline is final for that call id.

**Idempotency.** Answers are cached per `call_id`: a second call returns the same answer and
starts nothing (tested: `test_vip::test_duplicate_claim_is_idempotent`).

**What Part 5 needs from Part 2 during the call.** `would_breach_ceiling_without(car)` for each
candidate (FR-72), and, when the chosen car has calls waiting for it,
`reassign_calls_of(car, "priority_vip")`. Part 5 compares the returned list with the calls it
saw assigned to that car (`CallAssigned` / `CallReassigned`, not yet boarded); if any was not
moved the car cannot be exclusive: Part 5 releases it and tries the next car (`calls_not_moved`).

**Ownership.** Part 2 owns the assignment and publishes `CallAssigned` / `CallReassigned`.
Part 5 owns the car state request/release and the trip events. Part 4 decides states.

**Failure handling.**
* Any refusal inside `claim_call` (state, controller) is rolled back before it returns `None`;
  the call is still Part 2's to dispatch (no pending state in Part 5).
* After a HANDLED answer the trip may end before the VIP boards (car fault / heartbeat loss,
  emergency, medical pre-emption, VIP not on board after one door cycle). Part 5 then releases the
  car and registers the pick-up stop on that same car (`register_stop(car, origin, call_id)`), so
  the `CallAssigned` stays true and the call is not lost; if the car cannot take commands yet, the
  stop is retried after recovery. Part 2 may instead re-assign the call (`CallReassigned`); Part 5
  then does not register the stop.
* `claim_call` does not raise in normal operation. Part 2 should still treat an exception as
  `None` and dispatch normally; it must not fall back to a stub.

**Example (Part 2 side):**
```python
def on_call_requested(self, ev):
    call = ev.call
    car = None
    if call.priority is CallPriority.VIP:
        car = self.ctx.get("priority").claim_call(call)
    if car is not None:
        status = self.ctx.get("controllers").status(car)
        self.ctx.bus.publish(CallAssigned(ev.t, call.call_id, car, eta(status, call.origin)))
        return
    self.queue.append(call)                     # normal path; order with priority.order(...)
```

## 5. Accessible capacity (`car_ok_for`) - what it counts

`car_ok_for(call, status)` is True when the party fits when the car opens at the call's floor:
`rated - committed_places(car, floor) - unseen riders >= party_size`, where committed places =
riders (tracked from `PassengerBoarded` / `PassengerAlighted` with the destination from
`CallRequested`) who do not leave at that floor + every call assigned to the car (any floor) that
does not end there. Conservative: it can refuse a car that would in fact have room. If no car
fits, Part 2 decides (the stand-in then falls back to the nearest car).
