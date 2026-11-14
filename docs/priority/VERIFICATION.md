# Part 5 verification record

All numbers below were produced on 2026-11-14 from the code at the commit that adds this file
(branch `part5/minh-priority-service-car`), by the commands in section 2. Nothing is averaged
across seeds.

## 1. Base and environment

| Item | Value |
|---|---|
| Base | `origin/main` @ `8b4afdc` (unchanged on 2026-11-14 after `git fetch`) |
| Python | 3.11.13 local `.venv` (`pip install -e ".[dev]"`); CI runs 3.12 on the PR |
| Real services on main | Part 1 simulator, mock ACS, notifier, trip database, dispatch guard, heartbeat, failover; Part 6 monitor |
| Still contract stubs on main | **Part 2 dispatcher, Part 3 access, Part 4 car_states** (each package is the 17-line skeleton) |
| No real caller of `claim_call()` | the only caller is the scenario stand-in `standins.PriorityAwareNearestCar` |

## 2. Commands and results

| Command | Result |
|---|---|
| `ruff check --select E9,F63,F7,F82 .` (CI lint) | clean |
| `pytest -q` (whole suite, as CI) | **336 passed, 1 skipped, 40 xfailed** (base main: 205 passed, 1 skipped) |
| `pytest tests/priority -q` | 92 passed (unit with doubles, simulator, integration with main services, runner API) |
| `pytest tests/scenarios -q -k "sim_06 or sim_11 or sim_12 or sim_18"` | 40 passed, 40 xfailed |
| `python -m ems.priority.demo scenarios --seeds 1-10` twice | identical criteria and metrics for all 40 runs |
| event digests SIM-06/11/12/18 seed 7, two runs each | equal |
| `python -m ems.priority.demo baseline --seeds 1-10` | section 5 |
| `python -m ems.priority.demo` | walkthrough completes all 5 steps |
| Mutation check (each fix disabled by a throw-away pytest plugin) | A1 off: 3 tests fail; A2 off: 1 fails; A3 off: 5 fail |

**Skipped (1):** `tests/scenarios/test_sim_01_up_peak.py` "waiting for Part 2 check" - on main
before Part 5, owner Part 2.

**xfailed (40) are NOT passes.** `test_sim_XX_full_acceptance[seed]`, `xfail(strict=True)`: the
scenario runs and really returns FAIL. Strict, so the test turns red the day a scenario passes and
the marker must be removed. They do not hide Part 5 failures: the companion tests
`test_sim_XX_part5_criteria[seed]` (40 passed) require every criterion to pass except the ones
listed in each file's `NOT_PART5`, and each listed criterion has a trace in section 4. A new or
changed Part 5 failure makes those tests fail.

Test levels: unit tests use protocol doubles (`tests/priority/conftest.py`); simulator tests and
scenarios use Part 1's real simulator with labelled stand-ins for Part 2/3/4
(`ems/priority/standins.py`); `test_integration_main_services.py` adds Part 6 and checks bus
ownership. **No test runs against a real Part 2/3/4** - they do not exist.

## 3. Scenario results, seeds 1-10

| Scenario | PASS | FAIL | NOT RUN | Failing criteria (all 10 seeds) |
|---|---|---|---|---|
| SIM-06 | 0 | 10 | 0 | FR-04 re-assignment; degraded waits logged; integration |
| SIM-11 | 0 | 10 | 0 | EMS door-time request; dispatcher priority order; Rush wait targets; integration |
| SIM-12 | 0 | 10 | 0 | ordinary calls of group LOW <= 60 s; integration |
| SIM-18 | 0 | 10 | 0 | integration |

Every other criterion passes on every seed:

```
SIM-06  PPPPPPPPPP car fault injected during up-peak Rush
        PPPPPPPPPP FR-30 one fault alert to contractor and FM
        PPPPPPPPPP faulted car not moved: no accepted movement command while faulted
        PPPPPPPPPP Part 5 sent no command to the faulted car
        PPPPPPPPPP second removal in Rush waits for FM approval (FM alerted)
        PPPPPPPPPP unapproved request expires after 600 s simulated; late approval refused
        PPPPPPPPPP denied removal never enters maintenance
        PPPPPPPPPP approved removal enters maintenance only after FM approval, then exits
        PPPPPPPPPP waiting calls of the faulted car retained, wait clock (created_at) kept
        FFFFFFFFFF waiting calls re-assigned away from the faulted car (FR-04, Part 2)
        FFFFFFFFFF degraded waits logged for the affected calls
        PPPPPPPPPP affected calls' long waits stay in the run metrics (not dropped)
        PPPPPPPPPP all calls cleared (none lost)
        FFFFFFFFFF integration with real Part 2/3/4 services (not stand-ins)
SIM-11  PPPPPPPPPP accessible calls mixed into up-peak
        PPPPPPPPPP voice feedback for every accessible assignment (one per call and car)
        PPPPPPPPPP accessible calls assigned to a car with room when it opens at their floor
        PPPPPPPPPP every accessible passenger boarded at the first door opening of their car
        PPPPPPPPPP accessible above VIP: no exclusive VIP trip took a car an accessible passenger was waiting for
        PPPPPPPPPP accessible passengers boarded
        PPPPPPPPPP doors open >= 10 s on every door cycle with an accessible passenger (simulator's own rule)
        FFFFFFFFFF EMS requests the >= 10 s door time
        FFFFFFFFFF priority order medical > accessible > VIP > ordinary applied by the dispatcher
        FFFFFFFFFF Rush wait targets hold (average <= 25 s, none over 60 s), every measured call counted
        PPPPPPPPPP all calls cleared (none lost)
        FFFFFFFFFF integration with real Part 2/3/4 services (not stand-ins)
SIM-12  PPPPPPPPPP 10 ordinary arrivals of group LOW replaced by VIP arrivals at minutes 20-29 (no traffic added)
        PPPPPPPPPP Part 5 decided every replaced VIP call
        PPPPPPPPPP at most one exclusive car per group at any time
        PPPPPPPPPP at most six exclusive VIP starts per rolling hour
        PPPPPPPPPP caps exercised and every cap refusal justified at its time
        PPPPPPPPPP exclusive: no other passenger boards a car during its VIP trip
        PPPPPPPPPP FR-72 wait-ceiling check asked before every exclusive start   (answers: Part 2 stub)
        PPPPPPPPPP every VIP call delivered (exclusive or as an ordinary call)
        FFFFFFFFFF ordinary calls of group LOW within the 60 s ceiling
        PPPPPPPPPP all calls cleared (none lost)
        FFFFFFFFFF integration with real Part 2/3/4 services (not stand-ins)
SIM-18  PPPPPPPPPP medical starts after a real VIP PassengerBoarded, during the VIP trip
        PPPPPPPPPP VIP exclusivity yields to the medical trip
        PPPPPPPPPP never more than one exclusive car per group
        PPPPPPPPPP boarded VIP delivered to their own destination
        PPPPPPPPPP riders already in the medical car delivered to their own destinations
        PPPPPPPPPP a healthy car performs the medical trip to the end
        PPPPPPPPPP medical served above VIP: accepted at once while the VIP trip held the group's exclusive slot
        PPPPPPPPPP event order: VIP boarded <= VIP yields = medical start <= origin < destination < medical end
        PPPPPPPPPP medical car held through car_states (requested or converted VIP hold) and released at the end
        PPPPPPPPPP all calls cleared (none lost)
        FFFFFFFFFF integration with real Part 2/3/4 services (not stand-ins)
```

### Per-seed numbers
Waits = boarding - arrival for every measured call; a call never picked up counts with
end-of-run - arrival (there were none: "0 never picked up" on every seed).

| Seed | SIM-06 faulted car / calls waiting on it / their waits (s) | SIM-11 all calls (avg <= 25, max <= 60) | SIM-11 accessible boarded at 1st opening | SIM-12 ordinary LOW calls | SIM-12 VIP starts; hourly_cap refusals | SIM-18 medical start / car |
|---|---|---|---|---|---|---|
| 1 | B / 16 / avg 347.9 max 495.7 | n=2137 avg 79.2 max 736.2, 730 > 60 | 118/118 | n=954 avg 29.8 max 185.8, 106 > 60 | 8; 28 | t=625.1 / S |
| 2 | C / 8 / avg 322.2 max 324.0 | n=2107 avg 58.8 max 480.2, 585 > 60 | 115/115 | n=982 avg 31.4 max 134.4, 127 > 60 | 7; 21 | t=1263.1 / A |
| 3 | C / 3 / avg 416.5 max 420.3 | n=2071 avg 54.4 max 321.3, 571 > 60 | 124/124 | n=959 avg 33.3 max 256.3, 141 > 60 | 6; 22 | t=1250.8 / S |
| 4 | A / 5 / avg 334.0 max 337.6 | n=2096 avg 56.2 max 341.7, 552 > 60 | 117/117 | n=870 avg 29.2 max 201.8, 82 > 60 | 8; 20 | t=728.9 / S |
| 5 | C / 1 / avg 356.4 max 356.4 | n=2093 avg 57.7 max 360.5, 661 > 60 | 98/98 | n=940 avg 35.0 max 209.4, 172 > 60 | 6; 27 | t=844.2 / B |
| 6 | D / 10 / avg 369.4 max 388.9 | n=2063 avg 50.9 max 303.6, 560 > 60 | 134/134 | n=930 avg 32.6 max 147.8, 151 > 60 | 7; 25 | t=929.3 / S |
| 7 | B / 13 / avg 345.9 max 356.8 | n=2147 avg 61.6 max 505.6, 677 > 60 | 122/122 | n=953 avg 35.6 max 213.4, 165 > 60 | 9; 31 | t=3711.8 / S |
| 8 | C / 10 / avg 372.1 max 377.5 | n=2169 avg 87.3 max 741.1, 776 > 60 | 141/141 | n=981 avg 35.4 max 195.6, 149 > 60 | 7; 20 | t=770.3 / S |
| 9 | B / 6 / avg 386.2 max 398.9 | n=2117 avg 59.2 max 314.7, 682 > 60 | 123/123 | n=989 avg 35.4 max 213.3, 155 > 60 | 6; 21 | t=949.2 / A |
| 10 | C / 15 / avg 328.9 max 342.0 | n=2032 avg 50.6 max 307.2, 526 > 60 | 117/117 | n=896 avg 29.5 max 187.9, 91 > 60 | 7; 29 | t=895.3 / C |

SIM-12: all ten replaced VIP calls were refused with `hourly_cap` on every seed (the building's
own VIP employees, 1 % of the population, used the six starts before minute 20). The check
verifies each refusal against the start times (6 starts in `(t-3600, t]` at every refusal), so the
cap criterion does not pass for lack of trips: 6-9 exclusive trips started per seed.

## 4. Audit of every failing, xfailed and fixed item

Categories: **A** Part 5 implementation bug, **B** setup / check / test / metric bug, **C** missing
dependency implementation, **D** missing or contradictory contract, **E** missing specification,
**F** already present on base main, **G** not enough evidence to attribute.

### 4.1 Still failing

| Scenario / req | Seed | Criterion | Expected | Observed | Evidence | Root cause | Owner | Next action |
|---|---|---|---|---|---|---|---|---|
| SIM-06 / FR-04 | 1-10 | waiting calls re-assigned away from the faulted car | every call waiting for the faulted car gets `CallReassigned`, `created_at` kept | 0 of 16/8/3/5/1/10/13/10/6/15 re-assigned; they wait out the 5-min fault (322-417 s avg) | no `CallReassigned` in the bus log; `ScheduleModeDispatcher.reassign_calls_of` delegates to `StubDispatcher` which returns `[]`; no `ControllerFault` subscriber exists in `ems/dispatch` | **C** | Nam (Part 2) | implement FR-04 and `reassign_calls_of`; rerun SIM-06 |
| SIM-06 | 1-10 | degraded waits logged | affected trips `degraded=True` | 0 of n | `MonitorService.on_event` sets `degraded` only on `CallReassigned` (`ems/monitor/service.py`) | **C** (follows from FR-04); **E** whether a call that waited on a faulted car WITHOUT re-assignment is "degraded" | Nam; rule: Mai (Part 6) | rerun after FR-04; if not covered, Mai decides the rule |
| SIM-11 / FR-08 | 1-10 | EMS requests the >= 10 s door time | an EMS command carries the door time | impossible: no such command | `ControllerAdapter` has no door-time parameter; the 10 s (min observed 10.1 s) are the simulated controller's own rule (`cars.py`, `accessible_dwell_s`) | **D** | Nhi (contract, simulator) | issue #10 |
| SIM-11 / BR-10 | 1-10 | priority order applied by the dispatcher | competing waiting calls served medical > accessible > VIP > ordinary | not exercised | the stand-in assigns each call synchronously inside its `CallRequested` handler (no queue), so ordering never happens; Part 5 provides `rank()` / `order()` (unit-tested) | **C** | Nam | Part 2 orders its queue with `priority.order()`; issue #9 |
| SIM-11 | 1-10 | Rush wait targets (avg <= 25 s, max <= 60 s) | every seed | avg 50.6-87.3 s, max 303.6-741.1 s, 526-776 calls > 60 s | the same traffic with Part 1's plain nearest-car and NO Part 5 hook also fails every seed (avg 46.8-85.0 s, max 300.1-718.2 s, section 5) | **C** (no real dispatcher; D-01 notes the building carries about 35/min) + **G** for Part 5's share: the FR-72 ceiling answer is the stub's `False`, so exclusive trips are never prevented | Nam | rerun with the real dispatcher and real `would_breach_ceiling_without` |
| SIM-12 | 1-10 | ordinary calls of group LOW within 60 s | max <= 60 s | max 134.4-256.3 s; 82-172 calls > 60 s | plain nearest-car without Part 5 on the same traffic: max 106.9-221.5 s, 62-205 > 60 s (section 5) | **C** + **G** (same as above) | Nam | same |
| all four | 1-10 | integration with real Part 2/3/4 | real services | stubs + stand-ins | `integration_gaps()`: dispatcher `ScheduleModeDispatcher`, access `StubAccess`, car_states `StubCarStates`, staff cards from `StaffDirectory` | **C** (+ **D** #9, staff cards #12) | Nam, Bao Vy, Thao Vy, Nhi | merge Parts 2/3/4; remove the strict xfails when scenarios pass |

### 4.2 Found and fixed in this round

| ID | Cat. | Scenario / req | Observed before | Evidence / reproduction | Fix | Regression test |
|---|---|---|---|---|---|---|
| A1 | A | FR-71/127 | a VIP whose exclusive trip ended before boarding (car taken for medical, fault, emergency) waited for ever: `WAITING` after 3000 s, car with no stop | scratch reproduction on the real simulator (fault F/G/H/S, VIP at L20, medical at L25) | the pick-up stop is registered on the assigned car once the hold is released (`after_release`) | `test_with_simulator::test_vip_call_is_not_lost...`, `test_vip::test_unboarded_vip_trip_end_re_registers...`, `test_medical::test_medical_over_unboarded_vip...` |
| A2a | A | FR-08 | accessible passengers left behind at the first opening: 16 / 3 / 5 (seeds 1-3) | replay of `DoorsOpened` / `PassengerBoarded` per accessible call | capacity counts calls already assigned | (superseded by A2b) |
| A2b | A | FR-08 | still 1 left behind on seeds 5 and 8 (car 128 % full at L1) | trace seed 5: car picked up an inter-floor rider on its way who stayed past L1 | riders tracked with destinations; committed places = riders who stay + every assigned call not ending at the floor | `test_vip::test_fr08_capacity_counts_riders_who_stay...` |
| A3 | A | FR-72 exclusivity | 21 / 22 / 46 ordinary boardings on "exclusive" VIP cars (seeds 1-3) | replay of `PassengerBoarded` during VIP trips; stub `reassign_calls_of` returned `[]` and Part 5 ignored it | exclusive only if every waiting call is moved (checked against the returned list), else roll back; "empty" = no rider and no waiting call | `test_vip::test_exclusive_vip_needs...`, `...declines_without_using_quota`, `...busy_car_is_used_when_part2_moves...` |
| A4 | A | FR-127 | medical car could be one that ordinary passengers wait for at the medical floor (they would board while doors are held) | code review | rank cars nobody waits for at that floor first | `test_medical::test_medical_prefers_a_car_nobody_waits_for...` |
| B1 | B | SIM-18 seed 10 | medical trip never departed, 10 calls not cleared | the chosen car was already open at the medical floor, no new `DoorsOpened`; the scripted operator waited for one | operator follows Part 5's trip phase; check accepts doors already open (telemetry `_open_at`) | SIM-18 seeds 1-10 |
| B2 | B | SIM-11/12 waits | waits counted delivered calls only | `_waits` dropped calls never picked up | never-picked-up calls count with end - arrival | scenario checks |
| B3 | B | SIM-12 caps | "at most 6/h" and "1 per group" could pass with no trip at all | check accepted 0 starts | require starts, and verify every `hourly_cap` / `group_exclusive_cap` refusal against the event log | scenario checks |
| B4 | B | SIM-06 | no deny path | - | third request denied by the FM; must never enter maintenance | scenario check |
| B5 | B | earlier round | SIM-06 fault car without waiting calls (vacuous pass); command filter counted VIP commands as maintenance | traces in the previous round | dynamic fault car; evidence from `car_states` requests | scenario checks |

### 4.3 Found, not Part 5's, already on main

| ID | Cat. | Item | Evidence | Owner | Next action |
|---|---|---|---|---|---|
| F1 | F | `/api/state` "events" shows every field of every event, so the building page lists `PassengerArrived.credential_id` (on main) and `GoodsMoveScheduled.credential_ids` (contract field Part 5 must fill for FR-82) | `ems.web.server._event_line(GoodsMoveScheduled(...))` prints `credential_ids=['mover-card-1']` | Nhi (server) | redact `credential_id(s)` in `_event_line` (NFR-SE2) |
| F2 | F | simulator: `express(floor, hold_doors_open=True)` sent during a door cycle at another floor keeps the doors open at that floor | previous round, SIM-18 seed 6 trace | Nhi | one-line fix in `SimCar._end_dwell` (INTEGRATION.md); Part 5 works around it |

## 5. Baseline: same traffic with and without VIP exclusivity

`python -m ems.priority.demo baseline --seeds 1-10`. Same seed, same arrivals (including the 10
replaced VIPs; arrival counts identical), same stand-ins. Variants: **with** = SIM-12 as run;
**no claim** = identical except the dispatcher stand-in never calls `claim_call` (only
difference); **plain** = Part 1's nearest-car stand-in, no Part 5 hook at all (also no FR-08
capacity filter).

| Seed | all calls avg / max (s): with | no claim | plain | LOW ordinary max (s), > 60 s: with | no claim | plain |
|---|---|---|---|---|---|---|
| 1 | 79.2 / 736.2 | 58.2 / 576.2 | 55.1 / 336.9 | 185.8, 106 | 106.9, 103 | 106.9, 103 |
| 2 | 58.8 / 480.2 | 53.9 / 333.8 | 46.8 / 305.0 | 134.4, 127 | 221.5, 141 | 221.5, 141 |
| 3 | 54.4 / 321.3 | 63.5 / 454.4 | 59.4 / 349.6 | 256.3, 141 | 176.9, 145 | 176.9, 145 |
| 4 | 56.2 / 341.7 | 58.4 / 508.8 | 60.2 / 476.0 | 201.8, 82 | 201.8, 111 | 201.8, 62 |
| 5 | 57.7 / 360.5 | 60.0 / 537.6 | 55.5 / 323.2 | 209.4, 172 | 137.0, 141 | 137.0, 141 |
| 6 | 50.9 / 303.6 | 46.5 / 303.1 | 56.3 / 316.0 | 147.8, 151 | 161.2, 122 | 161.2, 122 |
| 7 | 61.6 / 505.6 | 56.2 / 324.4 | 60.2 / 492.9 | 213.4, 165 | 184.5, 174 | 199.6, 136 |
| 8 | 87.3 / 741.1 | 61.2 / 327.0 | 85.0 / 718.2 | 195.6, 149 | 189.3, 164 | 189.3, 205 |
| 9 | 59.2 / 314.7 | 51.5 / 337.9 | 51.7 / 313.3 | 213.3, 155 | 208.9, 204 | 208.9, 195 |
| 10 | 50.6 / 307.2 | 55.3 / 330.8 | 49.2 / 300.1 | 187.9, 91 | 127.3, 97 | 127.3, 97 |

All three variants miss the Rush targets on every seed. Compared with "no claim" (the only
difference being VIP exclusivity), taking cars for VIP trips makes the all-calls average worse in
6 of 10 seeds (+4.4 to +26.1 s; seeds 1, 2, 6, 7, 8, 9) and better in 4 (seeds 3, 4, 5, 10): the queueing
is chaotic and the stub never answers "ceiling breached". This is why the FR-72 ceiling check
needs Part 2's real answer before any conclusion about Part 5's effect on waits.

## 6. Edge cases covered by tests
Rolling-hour boundary (`t0 + 3599` refused, `t0 + 3600` allowed); same-timestamp VIP calls;
duplicate `claim_call` / `CallAssigned` / maintenance request; refused `car_states.request` and
refused `close_to_new_calls` / `express` / `set_independent_service` (rollback, quota unused);
cars with waiting calls not moved (declined, quota unused); no healthy car; ACS timeout; fault and
heartbeat loss during a trip (no command to the car, re-open + owed pick-up after recovery); fire
and mains loss (no command; trips and active goods moves cancelled; retried after reset); EMS down
/ up; late callbacks after cancellation (VIP door cycle, goods start / end); goods overlap, past
window, approval after expiry; FR-33 timeout at 599.9 / 600 s, late approval, deny, approval
revalidated against a car taken meanwhile; exit never overrides a Safety state; medical over an
unboarded VIP, medical over the VIP car itself, medical car lost before pick-up; send-to-lobby on
the real simulator (riders kept, delivered after); integration: only Part 5 events from Part 5
modules, one `CallAssigned` per call, no lost call, no `Call` mutated, no credential in trip
records, alerts, audit or decisions, every hold released.
