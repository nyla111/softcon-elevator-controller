# Part 4 Safety & recovery

Package `ems/safety`, service `ctx.services["car_states"]` (the unchanged `CarStates`
protocol). Safety supervises EMS state; controllers own physical recall, rescue and
movement interlocks. Safety sends no controller commands. Historical working notes
are preserved on `codex/backup-part4-safety-before-review-20261116`.

## States and ownership

BR-06 precedence, highest first: Fire Recall, Emergency Power, Faulted (including
Disconnected), Maintenance, Independent Service, VIP Exclusive, Standby, Group Service.
`states.py` tracks independent `(state, owner)` holds; releasing a hold cannot erase
another owner's hold. Lower-priority requests are refused without acquiring a hold.
Group Service is the default when no holds remain. The `safety:` namespace is internal;
Priority uses `priority:<kind>` for both acquisition and release. `VIP_EXCLUSIVE` covers
VIP, medical and send-to-lobby trips. `CarStateChanged` reports effective changes only.

`dispatchable(group)` excludes non-Group-Service cars, unhealthy telemetry and every
car during a building emergency or locked recovery. Dispatch must consult this and
`safety_active()` before all commands, including parking and flag changes.

## Fire, power and faults

| Input | Safety behaviour |
|---|---|
| Fire alarm, including lost fire I/O | Latch Fire Recall on all cars, invalidate interrupted exclusive holds, ask Dispatch to reassign waiting calls, show bilingual stair messages. Priority owns trip/goods cancellation and `GoodsMoveEnded`. |
| Firefighter service | Keep normal service and recovery blocked while active; controllers retain physical authority. |
| Mains loss | Latch Emergency Power, request waiting-call reassignment, show power messages. Fire Recall still wins. `GeneratorReady` does not reopen EMS dispatch. |
| Missing/stuck rescue report | One critical POSSIBLE ENTRAPMENT alert per affected car by 60 s, even after generator readiness or mains restoration. |
| Fault or heartbeat loss | Independent Faulted holds; only matching clear/restored events release them. Faults under emergency precedence survive recovery. |
| Occupied between-floor/stuck-door fault; alarm button | Critical entrapment/assistance alerts to security, FM and contractor. Part 5 owns mechanical-fault notifications. |

Timing uses the simulated clock. Older physical assertions/clears are rejected; equal
timestamps follow bus order because contracts have no episode/sequence identifier.

FR-110 rescue evidence is per car and power episode. Generator readiness and mains
restoration do not prove rescue. At the 60 s deadline, every car without a current
`rescued_at_floor` report alerts, including unknown occupancy; a `stuck` report alerts
immediately. A newer outage supersedes the old timer and resets alert deduplication.
Late rescue reports do not erase notifications already delivered. Regression evidence:
`tests/safety/test_rescue_deadline.py` (#46).

Car-specific notifications include the last timestamped, connected controller report's
floor, load, passenger estimate and telemetry time/age in delivered message text, plus
the structured floor when known (FR-53/110, #80). Cached values are scalar snapshots;
older/future reports and disconnected payloads cannot overwrite them. With no report,
context is explicitly unknown; no adapter timestamp or occupancy is invented. See
`tests/safety/test_alert_context.py` for notifier-inbox evidence.

## Recovery gate

Physical clear/reset alone never resumes EMS. Trusted adapters must call
`report_safety_clear(True)` and `report_controller_normal(car, True)` for healthy,
non-isolated cars after physical reset. After power loss, mains must remain stable
for 120 continuous seconds. Fire, firefighter service, mains loss and external emergency
holds must be clear. An authenticated FM caller must then invoke
`confirm_recovery(Role.FM)`; early confirmation is rejected, never saved.

A new emergency invalidates previous attestations; mains return invalidates them again.
Confirmation removes only Safety's building-emergency holds. Faulted, disconnected,
maintenance and independent cars stay excluded. Interrupted VIP/goods activity does not
restart. Current demo clear/reset controls therefore leave EMS held until adapters and
FM confirmation are connected. These internal methods are not authenticated UI endpoints.

## Integration dependencies and incomplete acceptance

FR-113 is implemented by the mechanical-fault hold owner: the third independent episode
in a trailing 30-day window sends the FM a review notice. The exact 30-day boundary is
included. Duplicate assertions/statuses and link loss do not count; a matching fault
clear is required before another episode. Further episodes while already at/above three
do not spam notices; the threshold may notify again after old episodes age out. P5 keeps
FR-30 per-episode alerts. See `tests/safety/test_fault_review.py`; the window convention
and cross-owner integration remain subject to owner review in #88.

Part 5 is merged and exercised directly in `tests/integration/test_safety_priority.py`.
Seven strict xfails track [issue #18](https://github.com/nyla111/softcon-elevator-controller/issues/18):
PR16-01 owner transfer (2), PR16-02 isolated VIP preemption (2), PR16-03 cached emergency
claim (2), PR16-04 fire recovery without a service group (1). These require Part 5 fixes.

| Owner / requirement | Still needed |
|---|---|
| Part 1 controller recovery | Explicit normal/reset-complete and safety-input-clear telemetry, correlated to the current emergency; ordinary `CarStatus` cannot prove these. |
| Part 6 FM recovery | Authenticated FM confirmation for the current episode, rejected on stale/replayed confirmation; connect trusted adapters before exposing recovery. |
| Part 2 Dispatch | Real waiting-call retention/reassignment excluding boarded riders; overload step-out/next-car messages within 1 s; guard all commands. |
| Part 3 Access | Pre-acceptance action ID, kind and exemption classification plus proven duplicate correlation and waiting/boarded status; optional lockdown suspend/resume. |
| FR-52 generator operation | Approved capacity/allocation, per-car enable acknowledgement and parking completion, readiness withdrawal/failure, and episode-correlated rescue reports. Limited operation/sequential parking is unimplemented; the simulator currently resumes all physical cars on `GeneratorReady`. |

`call_policy.py` independently allows five ordinary actions per rolling 60 s; the sixth
starts a fixed 60 s block. Denied repeats do not extend it. `evaluate()` returns an
immutable decision with bilingual refusal text and retry time; `allow()` remains a
boolean wrapper. The third episode per simulated calendar day invokes the security-log
owner's callback once. Midnight resets daily counts, not an active block. Exit/assistance/
fire actions remain exempt and consume no quota. Duplicate removal requires same-action
proof, waiting status, no boarding and no uncertainty. These policies await Access admission
integration; Safety does not filter already accepted `CallRequested` events.

`acceptance.py` loads only when a Part 4 scenario executes. SIM-08/13/14/15/17 remain
`ready=False`; metrics-only runs expose partial criteria with aggregate `passed=None`.
SIM-08 still needs an active goods setup and full recovery integration; SIM-13 overload
messages/reassignment; SIM-14 full recovery; SIM-15 reassignment; SIM-17 panel admission
and duplicate proof. The optional SIM-08 lockdown variant is not integrated. Unit fixtures
and passing subcriteria do not establish full Appendix B acceptance.

## Run

```bash
pytest tests/safety -q
pytest tests/integration/test_safety_priority.py -q
pytest tests/scenarios tests/sim/test_scenarios_registry.py -q
pytest -q
ruff check --select E9,F63,F7,F82 .
```
