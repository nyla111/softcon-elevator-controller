# Part 5 integration guide

`ctx.services["priority"]` is a `PriorityService` (`ems/priority/service.py`) with two
sub-services: `.maintenance` (`maintenance.py`) and `.goods` (`goods.py`). Pure policy functions
are in `policy.py`. Look the service up lazily inside handlers (`ctx.get("priority")`).

The sections below are per part. (The mapping of teammates to parts is not in the repo, so this
file names parts, not people.)

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
| `claim_call(call) -> car or None` (Part 2) | call priority VIP (authorised by Part 3) | FR-71 car, FR-72 caps + `would_breach_ceiling_without`; `VIP_EXCLUSIVE`, close to new calls, `express(origin)`; `PriorityTripStarted("vip")`; `reassign_calls_of(car)` | same call id -> same answer, one start |
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

**Part 1 (simulator, tech lead).**
* `ems/sim/scenarios.py`: SIM-06/11/12/18 import their setup/check from `ems/priority/acceptance.py`
  (four registry entries changed, nothing else).
* Simulator behaviour found while testing: `express(floor, hold_doors_open=True)` sent while the
  car is in a door cycle at ANOTHER floor keeps the doors open at that other floor and the car
  never leaves (`SimCar._end_dwell` does not look at `express_floor`). Part 5 works around it
  (moves first, holds on arrival); a fix in `cars.py` would be `if (self.hold_doors and
  self.express_floor is None) or self.fire`.
* CCR-02 (door time), CCR-05 (staff cards).
* SIM-12 needs "replace 10 ordinary arrivals by VIPs"; Part 5 does it in its setup with
  `VipReplacementPopulation` (same rng draws, no traffic added). A first-class traffic hook would
  be cleaner.

**Part 2 (dispatch).** CCR-01. For every `CallRequested`:
1. `car = ctx.get("priority").claim_call(call)`; if `car`: publish `CallAssigned(call, car, eta)`
   and do NOT `register_stop` (Part 5 already sent `express`). Never assign other calls to a car
   whose state is not `GROUP_SERVICE` (`car_states.dispatchable`).
2. Otherwise order waiting calls with `priority.rank(call)` / `priority.order(calls)` (BR-10) and
   filter cars with `priority.car_ok_for(call, status)` (FR-08).
3. Implement `would_breach_ceiling_without(car)` truthfully (Part 5 asks it before every VIP start
   and every passenger-car goods move) and `reassign_calls_of(car, reason)` (Part 5 calls it when it
   takes a car; FR-04).
4. `current_mode(group)` drives FR-33 (Rush) and FR-36.
Reference: `standins.PriorityAwareNearestCar` (about 40 lines).

**Part 3 (access & panels).**
* Set `Call.priority` from the credential's roles (`ACCESSIBLE` > `VIP` > `NORMAL`); Part 5 trusts
  `CallPriority.VIP` on a `CallRequested` as authorised.
* FR-82: during an active move on a car, accept only `GoodsMoveScheduled.credential_ids` on that
  car's panels, or ask `priority.goods.service_car_accepts(cred, car)` (until CCR-04 exists).
* Part 5 voices accessible assignments (`PanelMessage(voice=True)` to `call.panel_id`): do not voice
  the same assignment again.
* CCR-05: return staff roles from `permission()`.

**Part 4 (safety, car states).**
* Requests come as `by="priority:<kind>"` with kind `vip`, `medical`, `send_to_lobby`,
  `maintenance`, `goods`. Part 5 releases only states it holds and never asks for recovery from
  `FAULTED` / `FIRE_RECALL` / `EMERGENCY_POWER`.
* `safety_active()` is read before every new trip / removal / goods start.
* On `FireAlarm` / `MainsLost` Part 5 ends trips and active goods moves and sends no command.
  CCR-04 gives SIM-08 its "goods move cancelled" evidence.
* CCR-03 (state for medical trips).

**Part 6 (dashboard, reports).**
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
