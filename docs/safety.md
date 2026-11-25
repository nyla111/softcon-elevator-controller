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
| Fire alarm, including lost fire I/O | Latch Fire Recall on all cars, invalidate interrupted exclusive holds, ask Dispatch to reassign waiting calls, show bilingual stair messages. I/O loss also sends one `fire_io_loss` notification per recovery episode, including when a panel alarm came first. Priority owns trip/goods cancellation and `GoodsMoveEnded`. |
| Firefighter service | Keep normal service and recovery blocked while active; controllers retain physical authority. |
| Mains loss | Latch Emergency Power, request waiting-call reassignment, show power messages. Fire Recall still wins. `GeneratorReady` does not reopen EMS dispatch. |
| Missing/stuck rescue report | One critical POSSIBLE ENTRAPMENT alert per affected car by 60 s, even after generator readiness or mains restoration. |
| Fault or heartbeat loss | Independent Faulted holds; only matching clear/restored events release them. Faults under emergency precedence survive recovery. |
| Occupied between-floor/stuck-door fault; alarm button | Critical entrapment/assistance alerts to security, FM and contractor. Part 5 owns mechanical-fault notifications. |

Timing uses the simulated clock. Older physical assertions/clears are rejected; equal
timestamps follow bus order because contracts have no episode/sequence identifier.

The fire-I/O notification is emitted after fire holds are established. Clear/reset alone
does not reset its deduplication: only successful FR-55 FM recovery ends the episode.
`tests/safety/test_fire_io_alert.py` checks actual inbox delivery, repeated/stale signals,
panel-first loss, recovery and every attempted controller command (NFR-SF3, #81).

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
is retained by integrated #91. This does not approve the separate FR-52 contract in #88.

Part 5 is merged and exercised directly in `tests/integration/test_safety_priority.py`.
Historical regressions associated with [issue #18](https://github.com/nyla111/softcon-elevator-controller/issues/18)
remain inspectable in those tests. Use final CI's actual pass/xfail/xpass results and
the issue's current status; the historical count is not a current blocker inventory.

| Owner / requirement | Current integration / remaining obligation |
|---|---|
| P1/P6 FR-55 | Controller normal/clear reports, explicit authenticated FM confirmation and post-confirm service are integrated (#48/#49/#94/#108). |
| P2 / P5 / P6 | Waiting-call identity and healthy reassignment, overload bilingual/voice/next-car deadlines, camera review and crowd-only distinction are integrated; #99 keeps current main's overload implementation. |
| P3 Access | #96/#100 enforce policy before acceptance, real exemptions, exact replay tokens, owner cancellation and proven door-cycle no-show. Uncertain calls stay. Lockdown retains configured floors through fire/recovery (#92). |
| FR-52 generator operation | BLOCKED: approved commissioning count/orders and Q2-Q7 readiness, revision, permission, acknowledgement, withdrawal and orchestration decisions are absent (#46/#88). The parking primitive is not limited-operation acceptance. |

`call_policy.py` permits five ordinary actions in60s; the sixth starts a fixed60s
block. Denied repeats do not extend it. Access displays the bilingual decision before
publishing a call. The third episode per calendar day writes the credential only in
the security log and sends one anonymous security notice. Midnight resets the daily
count, not an active block. Exit/accessibility/alarm/medical/fire remain exempt.
Exact owner-issued action tokens prove replays; estimates or uncertain occupancy do
not prove duplicates. Safety never filters accepted CallRequested events.

SIM-08/13/14/15/17 are ready with complete checkers. Every mandatory seed1-10 is
enforced in CI, including both full SIM-08 workloads. Passing a selected subcriterion
or an empty shortened run cannot establish acceptance. See [current acceptance
matrix](part4_acceptance.md) for integrated PRs, issue closure gates and remaining
FR-52 and wider scenario limitations. Full final main CI remains the release gate.

## Run

```bash
pytest tests/safety -q
pytest tests/integration/test_safety_priority.py -q
pytest tests/scenarios tests/sim/test_scenarios_registry.py -q
pytest -q
ruff check --select E9,F63,F7,F82 .
```

## SIM-14 complete failed-generator acceptance
The registry now enforces the complete seven-criterion real-service SIM-14 checker.
It selects a genuinely occupied silent-rescue car, delivers current-context entrapment
by60s, records every attempted command and assignment through FM recovery, retains
waiting clocks, exercises early refusal/120s stable mains/clear-normal reports, excludes
a faulted car and proves post-confirm delivery. Seeds1-10 pass individually, with raw
traces and companion active CSV evidence. See tests/safety/test_sim14_journey.py and
docs/passenger_lists/issue33_evidence.md. The implementation is on main through #94; final integrated CI remains the gate;
FR52 successful-generator limited operation is a separate outstanding requirement.
