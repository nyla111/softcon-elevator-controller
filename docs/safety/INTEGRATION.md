# Part 4 Safety integration and requirement limits

The [2026-11-15 sync audit](SYNC_AUDIT.md) is the current verification against main `6aab02e`
and production Priority PR #16. [AUDIT.md](AUDIT.md) is historical evidence against the earlier
archival Priority snapshot; its failures must not be treated as results for PR #16.

The foundation was built on `8b4afdc` and safely fast-forwarded to fetched main `6aab02e` on
2026-11-15, with all 15 pre-existing local files hash-verified unchanged during synchronization.
Simulator and Monitor are implemented; Dispatch, Access and Priority on main remain stubs.
PR #15's approved contracts are merged. The current Priority target is PR #16,
`origin/part5/minh-priority` at `4a7b113`, tested in an isolated worktree and not merged here.
PR #13 is archival. PR #14 Access still contains twelve zero-byte implementation modules
and a stub register; its latest CI tests fail, so no Access integration was attempted.
No contracts/core were manually edited; their changes came only from the authorized main sync.

## Registered interface

`ctx.services["car_states"]` is `SafetyService`, implementing the unchanged `CarStates`
protocol. Layout comes from `ctx.services["building"]`. Safety-only builds discover cars
through the controller adapter after core fills missing dependencies with stubs.

States use the BR-06 order defined by `CarState`. Each accepted `(state, by)` hold is tracked
independently. Higher states mask lower holds; releasing one owner cannot clear another.
Requests below the effective priority return False and acquire **no** hold. Repeated requests
by the same owner are idempotent. Equal-priority holds from different owners are independent.
Use a stable module owner token, such as `priority:vip`, for both request and release.
`GROUP_SERVICE` is the default, not an instruction to erase active holds. Unknown cars raise
KeyError. The `safety:` owner namespace is reserved for internal event-driven conditions.

Internal mechanical-fault and disconnected holds are independent even though both map to
`FAULTED`. Only their corresponding clear/restored event releases them. Maintenance and
Independent Service are never automatically released by Safety. Interrupted exclusive priority
state holds (VIP, medical, send-to-lobby under the approved VIP_EXCLUSIVE meaning) are
invalidated on a building emergency; Priority still owns cancelling the actual trip.
State-change events are emitted only when the effective state changes.

`dispatchable(group)` excludes every non-Group-Service car, bad controller telemetry, and
all cars while a building emergency or its recovery gate is active. The existing Part 1
DispatchGuard already consumes this list, so the nearest-car scenario stand-in stops sending
ordinary stop commands during fire/power handling. Part 2 must also consult `safety_active()`
before **all** commands, including mode parking and flag changes, and must filter candidates
through `dispatchable()`. Controllers remain the final physical authority.

## Events and ownership

Safety consumes existing fire, firefighter, power, rescue, controller-fault, heartbeat,
telemetry and passenger-alarm events. It publishes only Part 4's `CarStateChanged` and the
shared `AlertRaised` / `PanelMessage` outputs. It sends no ControllerAdapter commands.
All timing uses the simulated clock. No wall time or random draws were added.

- Fire, including `source="io_signal_lost"`, latches normal dispatch off. Firefighter service
  also blocks recovery. Simulator owns recall and firefighter physical authority.
- Mains loss immediately holds every car in Emergency Power; Fire Recall still outranks it.
  GeneratorReady is treated as the contract's combined generator/controller-readiness report,
  but does not unlock ordinary dispatch. No movement is attempted before or after it by Safety.
- Without a ready generator, unconfirmed rescue-at-floor reports produce one critical
  POSSIBLE ENTRAPMENT alert per affected car at 60 simulated seconds. A `stuck` report alerts
  immediately. Unknown statuses and reports timestamped before the current loss are ignored.
  A new outage invalidates the previous deadline and report set. Restoration ends the
  no-power deadline check; a qualified ongoing fault/alarm still alerts independently.
- A fault with reported occupants and between-floor / stuck-door evidence alerts immediately
  (within the 10 s deadline), including contractor recipients. Passenger alarm requests are
  urgent independently of occupancy. Empty faulted cars are unavailable but are not described
  as confirmed entrapments.
- Mechanical fault and link loss delegate waiting-call reassignment through
  `Dispatcher.reassign_calls_of(car_id, reason)`. Safety never publishes CallReassigned,
  cancels rider destinations, or decides which passengers are waiting.
- **FR-100 overload** belongs to Dispatch (`scripts/requirements.csv`): consume
  OverloadDetected, retain/reassign only waiting passengers, and issue the step-out message
  naming the next car within 1 s. Safety does not issue a competing message without knowing
  that next assignment. This remains blocked by the Dispatch stub.
- **FR-84 bulky items** already belong to Monitor, which flags incidents and issues the
  service-car message. Safety does not duplicate this logic or credential attribution.
- Priority consumes FireAlarm/MainsLost and owns cancelling trips and goods periods.
  Main now defines the approved `GoodsMoveEnded(move_id, car_id, cancelled, reason)` event.
  PR #16 component tests verify command-free cancellation and one terminal goods event, with
  no automatic restart. The Part 4 SIM-08 check consumes this event as cancellation evidence;
  Safety does not use it to erase any state owner. Main's StubPriority still cannot exercise
  an active goods move by itself. PR #16 also consumes CarStateChanged and correctly retries
  controller cleanup after FM recovery, resolving that earlier archival finding. Preemption
  owner-transfer, held-VIP state bypass, cached claims and layout-independent Priority fire
  recovery still fail executable checks. See SYNC_AUDIT.md.
- No lockdown service or shared lockdown-suspension interface is implemented on main.

## Recovery gate (FR-55 / FR-111)

Alarm clear, controller reset, generator readiness and mains return **never** restore normal
dispatch by themselves. Early confirmation is refused and is not saved for later.

Trusted internal integration entry points exist on `SafetyService`:

```python
safety.report_controller_normal(car_id, True)  # AFTER physical reset, for each healthy car
safety.report_safety_clear(True)               # ALL relevant physical safety inputs clear
if safety.recovery_eligible():
    safety.confirm_recovery(Role.FM)           # explicit authenticated FM action at this time
```

The caller must authenticate/authorize the actor. Passing an enum is **not authentication**;
these methods are internal hooks, not an HTTP endpoint or UI. No existing UI invokes them.
Do not derive either attestation from ordinary CarStatus fields: these fields do not expose
controller normal mode, recall-reset state, or all safety inputs. The hooks do not reset
controllers. A fresh emergency invalidates prior attestations. Mains return invalidates them
again; all non-isolated controllers must report normal after that return.

Eligibility requires cleared fire/power conditions, no firefighter service, clear safety
signals, explicit normal attestations and healthy current telemetry for all non-isolated
cars, and at least 120 continuous seconds of mains after a power outage. Another loss
restarts the interval; duplicate MainsRestored notifications do not restart it. Explicit FM
confirmation then clears only the internal building-emergency holds. Faulted, Disconnected,
Maintenance and Independent Service cars stay excluded and do not block healthy recovery.
Their own separate holds/clear events remain authoritative. No VIP or goods period restarts.

Existing demo fire-clear/reset controls therefore leave EMS in a safe recovery hold until
the missing recovery integration is supplied. This is an intentional behavior change.

## Approved additions and remaining contract requests

PR #15 approved `PriorityService` (`claim_call`, `rank`, `order`, `car_ok_for`, `door_dwell_s`),
`ControllerAdapter.register_stop(..., min_dwell_s=None)`, `GoodsMoveEnded`, mock-ACS staff cards,
and the shared meaning of VIP_EXCLUSIVE for all exclusive priority trips. It also redacts
credentials from `/api/state` event lines. The goods terminal-event request is resolved;
staff-role data does not by itself provide an FM recovery API. CarStates signatures and
BR-06 precedence are unchanged.

These are proposals only; **none was added to contracts/core**.

| Need | Proposed contract and responsible publisher/caller |
|---|---|
| Explicit FM confirmation | `RecoveryConfirmed(t, emergency_id, actor_role)` from an authenticated FM API, or an approved CarStates method invoked by that API. Role must come from server authorization; audit actor identity only in the security log. Confirmation must identify the current episode and cannot be replayed into a later emergency. |
| Controller normal / reset and safety inputs | Extend Part 1 telemetry or publish `ControllerSafetyStatus(t, car_id, emergency_id, normal, safety_clear, fire_reset_complete, ep_ready)`. Add building-wide safety-input status if inputs are not per car. Ordinary CarStatus currently has none of these fields. |
| FR-52 generator capacity and safe operation | Approved configured capacity (simultaneously powered cars), authorized operating car IDs, readiness epoch, per-car EP enable/disable acknowledgement, and a completion/at-parking-floor report. Define the permitted emergency operation and parking floor from the SRS. `GeneratorReady` alone has no capacity or allocation. |
| Generator failure / readiness withdrawal | Part 1 `GeneratorFailed(t, emergency_id)` or readiness-changed status. Today no event can retract GeneratorReady or distinguish delay from failure. |
| Rescue correlation | Add emergency/power-loss ID to RescueReport. Timestamp checks cannot reject an old delayed report that is re-stamped with a current time by an upstream adapter. |
| Lockdown suspension/resume | An optional Access lockdown suspension/resume interface correlated to the emergency remains absent on main. Goods cancellation observability is now supplied by the approved GoodsMoveEnded event and is no longer a missing-contract request. Safety must not publish another owner's terminal events. |
| FR-120/121/125 action admission | Approved Access hook before CallRequested, carrying action ID, credential, action kind and exemption classification (exit, accessible/assistance, alarm, medical, fire), plus owner-confirmed duplicate correlation and waiting/boarded status. CallRequested is already accepted and cannot safely be filtered afterward. |

FR-52 sequential parking and limited generator operation are **not implemented**. The current
simulator resumes **all** physical cars on GeneratorReady, without a capacity interlock.
Neither sequential EMS park commands nor a number invented by Safety can make that model
prove capacity compliance. Once the contract/model is approved, implement one accepted park
request at a time, wait for a positive completion, and stop/replan on refusal or readiness
withdrawal. A command returning False must never advance the sequence as if parked.

`ems.safety.call_policy.ActionLimiter` independently implements five ordinary admitted
actions per rolling 60 s, rejects the sixth, and exempts exits/assistance/fire without
consuming quota. Accessible, medical and alarm actions must be classified as assistance by
Access. `duplicate_removable` requires explicit same-action proof, waiting status, no
boarding, and no uncertainty. These are tested local policies, **not panel integration**.
Credential keys are short-lived in-memory counters and are never emitted in Safety events,
trip records, messages or reports. Access must not log these counters into trip data.

## Scenario evidence and commands

The five assigned scenarios have registered check functions but remain `ready=False` with
specific `waiting_for` reasons. `run_scenario(..., metrics_only=True)` exposes partial criteria
while leaving aggregate `passed=None`. SIM-08/13/14/15 exercise actual simulator faults;
SIM-17 returns a precise blocked result because there is no action-admission input contract.
No test turns a stub response into acceptance evidence.

`tests/scenarios/test_safety_scenarios.py` runs the Safety subcriteria for SIM-08/13/14/15
across seeds 1–10 at the registry's full run lengths. It also checks the remaining failed
integration criteria are visible. The unit suite proves explicit internal FM recovery,
precedence pairs, independent holds, deadline boundaries, dynamic layout, policy exemptions,
and no controller commands. These tests do not claim full Appendix B acceptance.

```bash
cd /mnt/d/Projects/softcon-elevator-controller
source .venv/bin/activate
pytest tests/safety/ -q
pytest tests/scenarios/test_safety_scenarios.py -q
pytest -q
git diff --check
git status
```
