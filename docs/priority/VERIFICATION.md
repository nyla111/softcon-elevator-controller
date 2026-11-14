# Part 5 verification record

## 1. Base and environment

| Item | Value |
|---|---|
| Base commit | `origin/main` @ `8b4afdc` ("Implement Part 6 dashboard camera reporting and admin (#8)") |
| Branch | `part5/minh-priority-service-car` |
| Python | 3.11.13 in a local `.venv` (CI uses 3.12; 3.12 was not installed on this machine) |
| Install | `pip install -e ".[dev]"` |
| State of other parts at base | Part 1 simulator and Part 6 monitor are real; **Part 2 dispatch, Part 3 access and Part 4 safety are empty packages**, so `dispatcher`, `access`, `car_states` are the contract stubs (`ems/contracts/stubs.py`) |

## 2. Commands and results

| Command | Before Part 5 (base) | With Part 5 |
|---|---|---|
| `ruff check --select E9,F63,F7,F82 .` (CI) | clean | clean |
| `pytest -q` (whole suite, as CI) | 205 passed, 1 skipped | **305 passed, 1 skipped, 40 xfailed** |
| `pytest tests/priority -q` | 1 passed (smoke) | 61 passed |
| `pytest tests/scenarios -q -k "sim_06 or sim_11 or sim_12 or sim_18"` | - | 40 passed, 40 xfailed |
| `python -m ems.priority.demo scenarios --seeds 1-10 --json ...` (twice) | - | identical criteria and metrics on both runs |

What the counts mean:
* **1 skipped**: `tests/scenarios/test_sim_01_up_peak.py`, "waiting for Part 2 check". It was
  skipped before Part 5 and is not Part 5's.
* **40 xfailed are NOT acceptance passes.** They are `test_sim_XX_full_acceptance[seed]` (4
  scenarios x 10 seeds), marked `xfail(strict=True)` because the scenarios cannot pass while Part
  2/3/4 are stubs. Each one runs the scenario and the scenario really returns FAIL. Being strict,
  they turn red the day a scenario passes, so the marker has to be removed then.
* **40 passed** in the scenario files are `test_sim_XX_part5_criteria[seed]`: every criterion
  passes on its own seed **except** the listed not-Part-5 criteria, which must be reported as
  failed (never PASS), and the run must be FAIL overall.
* Two existing assertions were updated: the "ready scenarios" lists in
  `tests/sim/test_scenarios_registry.py` and `tests/web/test_web.py` now include SIM-06/11/12/18
  (the registry protocol: an owner sets `ready=True` when the check exists). No threshold, physics
  or traffic was changed.

### What kind of evidence each test is
| Level | Where | Meaning |
|---|---|---|
| Unit, protocol doubles | `tests/priority/test_policy, test_vip, test_medical, test_maintenance, test_goods` | Part 5 logic against `FakeControllers / FakeCarStates / FakeDispatcher / FakeAccess` (refusals, faults, timeouts injected) |
| Simulator integration | `tests/priority/test_with_simulator.py`, scenarios | Part 5 with Part 1's real simulator, mock ACS, Part 6 monitor; Part 2/3/4 replaced by `ems/priority/standins.py` |
| Integration with real Part 2/3/4 | none | **not possible yet** (stubs) |
| Scenario acceptance | registry SIM-06/11/12/18 | **FAIL on every seed** (section 3) |

## 3. Scenario acceptance, seeds 1-10 (each seed on its own, no averaging)

`run_scenario(<id>, seed)` through the registry (same path as `/runner/`). P = pass, F = fail.

### SIM-06 Car fault at up-peak, then a second removal - FAIL on 10/10 seeds
```
PPPPPPPPPP  car fault injected during up-peak Rush
PPPPPPPPPP  FR-30 one fault alert to contractor and FM
PPPPPPPPPP  faulted car not moved: no accepted movement command while faulted
PPPPPPPPPP  Part 5 sent no command to the faulted car
PPPPPPPPPP  second removal in Rush waits for FM approval (FM alerted)
PPPPPPPPPP  unapproved request expires after 600 s simulated; late approval refused
PPPPPPPPPP  approved removal enters maintenance only after FM approval, then exits
PPPPPPPPPP  waiting calls of the faulted car retained, wait clock (created_at) kept
FFFFFFFFFF  waiting calls re-assigned away from the faulted car (FR-04, Part 2)      BLOCKED P2
FFFFFFFFFF  degraded waits logged for the affected calls                           P6 marks only re-assigned calls
PPPPPPPPPP  degraded waits reported (affected calls)
PPPPPPPPPP  all calls cleared (none lost)
FFFFFFFFFF  integration with real Part 2/3/4 services (not stand-ins)               BLOCKED
```

### SIM-11 Accessible calls in up-peak - FAIL on 10/10 seeds
```
PPPPPPPPPP  accessible calls mixed into up-peak
PPPPPPPPPP  voice feedback for every accessible assignment (one per call and car)
PPPPPPPPPP  accessible calls assigned to a car with free capacity
PPPPPPPPPP  accessible passengers boarded
PPPPPPPPPP  doors open >= 10 s on every door cycle with an accessible passenger    simulator's own rule
FFFFFFFFFF  EMS requests the >= 10 s door time                                    BLOCKED CCR-02
FFFFFFFFFF  priority order medical > accessible > VIP > ordinary by the dispatcher BLOCKED P2
FFFFFFFFFF  Rush wait targets hold (avg <= 25 s, none over 60 s)                   real failure, see 4
PPPPPPPPPP  all calls cleared (none lost)
FFFFFFFFFF  integration with real Part 2/3/4 services (not stand-ins)              BLOCKED
```

### SIM-12 Ten VIP calls in one group - FAIL on 10/10 seeds
```
PPPPPPPPPP  10 ordinary arrivals of group LOW replaced by VIP arrivals at minutes 20-29 (no traffic added)
PPPPPPPPPP  Part 5 decided every replaced VIP call
PPPPPPPPPP  at most one exclusive car per group at any time
PPPPPPPPPP  at most six exclusive VIP starts per rolling hour
PPPPPPPPPP  FR-72 wait-ceiling check asked before every exclusive start            answers are the P2 stub (False)
PPPPPPPPPP  every VIP call delivered (exclusive or as an ordinary call)
FFFFFFFFFF  ordinary calls of group LOW within the 60 s ceiling                    real failure, see 4
PPPPPPPPPP  all calls cleared (none lost)
FFFFFFFFFF  integration with real Part 2/3/4 services (not stand-ins)             BLOCKED
```

### SIM-18 Medical priority during an occupied VIP trip - FAIL on 10/10 seeds
```
PPPPPPPPPP  medical starts after a real VIP PassengerBoarded, during the VIP trip
PPPPPPPPPP  VIP exclusivity yields to the medical trip
PPPPPPPPPP  never more than one exclusive car per group
PPPPPPPPPP  boarded VIP delivered to their own destination
PPPPPPPPPP  riders already in the medical car delivered to their own destinations
PPPPPPPPPP  a healthy car performs the medical trip to the end
PPPPPPPPPP  medical served above VIP: accepted at once while the VIP trip held the slot
PPPPPPPPPP  all calls cleared (none lost)
FFFFFFFFFF  integration with real Part 2/3/4 services (not stand-ins)             BLOCKED
```

### Per-seed numbers
Waits are passenger waits in the measured window (simulator truth), every measured call counted.

| Seed | SIM-06 faulted car / calls waiting on it / their waits | SIM-11 all calls (Rush: avg <= 25, max <= 60) | SIM-12 ordinary LOW calls (<= 60 s) | SIM-12 replaced VIP calls | SIM-18 medical start / car |
|---|---|---|---|---|---|
| 1 | B / 7 / avg 370.4 max 374.5 | n=2137 avg 68.4 max 475.6, 676 > 60 s | n=954 avg 29.9 max 172.6, 112 > 60 s | 10 x hourly_cap | t=4016.9 s, car G |
| 2 | C / 5 / avg 337.9 max 341.3 | n=2107 avg 58.8 max 323.8, 705 > 60 s | n=982 avg 36.5 max 145.6, 201 > 60 s | 10 x hourly_cap | t=1078.8 s, car S |
| 3 | B / 13 / avg 369.6 max 392.2 | n=2071 avg 54.3 max 307.0, 619 > 60 s | n=959 avg 32.9 max 217.4, 142 > 60 s | 10 x hourly_cap | t=1239.6 s, car S |
| 4 | C / 6 / avg 319.1 max 328.3 | n=2096 avg 59.2 max 336.5, 538 > 60 s | n=870 avg 27.8 max 201.8, 66 > 60 s | 10 x hourly_cap | t=721.9 s, car S |
| 5 | B / 2 / avg 327.1 max 346.8 | n=2093 avg 55.3 max 312.0, 593 > 60 s | n=940 avg 31.4 max 165.2, 128 > 60 s | 10 x hourly_cap | t=853.3 s, car C |
| 6 | C / 2 / avg 374.8 max 439.4 | n=2063 avg 61.9 max 516.4, 609 > 60 s | n=930 avg 29.7 max 157.1, 101 > 60 s | 10 x hourly_cap | t=925.9 s, car S |
| 7 | C / 11 / avg 359.6 max 369.3 | n=2147 avg 52.0 max 451.3, 509 > 60 s | n=953 avg 32.9 max 158.9, 143 > 60 s | 10 x hourly_cap | t=3706.4 s, car F |
| 8 | B / 20 / avg 368.0 max 418.7 | n=2169 avg 70.3 max 723.1, 641 > 60 s | n=981 avg 35.3 max 234.1, 159 > 60 s | 10 x hourly_cap | t=751.4 s, car S |
| 9 | C / 5 / avg 350.2 max 359.5 | n=2117 avg 54.3 max 471.1, 548 > 60 s | n=989 avg 33.9 max 182.9, 141 > 60 s | 10 x hourly_cap | t=948.9 s, car A |
| 10 | D / 10 / avg 324.0 max 375.6 | n=2032 avg 57.5 max 469.8, 567 > 60 s | n=896 avg 28.3 max 159.0, 103 > 60 s | 10 x hourly_cap | t=899.0 s, car S |

SIM-06: the calls waiting on the faulted car wait the whole 5-minute fault (about 320-375 s)
because nobody re-assigns them (FR-04, Part 2). They are kept and delivered with their original
`created_at`, but their trip records are not marked degraded.

SIM-12: in every seed the six exclusive starts of the rolling hour were already used by the
building's own VIP employees (1 % of the population, D-04) before minute 20, so all ten replaced
VIP calls were refused with `hourly_cap` and served as ordinary calls. This is FR-72 doing its job;
it also means FR-71 car choice is exercised by the earlier VIP trips (and by unit tests), not by
the ten replaced calls.

SIM-18: the medical trip starts at the first exclusive VIP boarding of the measured window (the
Appendix B note "after the VIP has boarded" replaces the default minute 20; with minute 20, seeds 5
and 10 had no exclusive VIP trip left because of the hourly cap). In 6 seeds the medical trip used
the service car S (empty and fastest).

## 4. Baseline without VIP exclusivity (why the wait targets fail)

Same up-peak run at 07:00 with Part 1's plain nearest-car stand-in (`install_dispatch_standin`),
which never calls `claim_call`: no exclusive VIP trips (Part 5 registered, but it takes no car).

| Seed | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|
| Baseline avg / max wait (s) | 55.1 / 336.9 | 46.8 / 305.0 | 59.4 / 349.6 | 60.2 / 476.0 | 55.5 / 323.2 | 56.3 / 316.0 | 60.2 / 492.9 | 85.0 / 718.2 | 51.7 / 313.3 | 49.2 / 300.1 |
| With Part 5 (SIM-11 run) avg / max (s) | 68.4 / 475.6 | 58.8 / 323.8 | 54.3 / 307.0 | 59.2 / 336.5 | 55.3 / 312.0 | 61.9 / 516.4 | 52.0 / 451.3 | 70.3 / 723.1 | 54.3 / 471.1 | 57.5 / 469.8 |

The Rush targets (25 s average, 60 s ceiling) fail on every seed **already without Part 5**: the
nearest-car stand-in cannot carry 35 persons/min (D-01). Taking a car out for a VIP trip changes
the average by between -14.7 s and +13.3 s per seed (worse in 5 seeds). Part 5 does not hide this:
the FR-72 ceiling check asks `would_breach_ceiling_without`, which is the Part 2 stub and always
answers False, so exclusive trips are never prevented here. With a real Part 2 dispatcher, both the
waits and the ceiling answers must be measured again.

## 5. Determinism
* `python -m ems.priority.demo scenarios --seeds 1-10` run twice: criteria and metrics identical
  for all 40 runs.
* Event-log digests of SIM-06/11/12/18 seed 3 run twice: equal.
* `tests/priority/test_with_simulator.py::test_up_peak_run_is_deterministic_with_part5...` checks
  it in CI.

## 6. Edge cases covered by unit tests
Rolling-hour boundary (`t0 + 3599` refused, `t0 + 3600` allowed); duplicate `claim_call`,
duplicate `CallAssigned`, duplicate maintenance request; refused `car_states.request`, refused
`close_to_new_calls` / `express` / `set_independent_service` (rollback, quota not used); no
healthy car; ACS timeout and unknown roles; car fault and heartbeat loss during a trip (no command
to the car, re-open after recovery); fire and mains loss (no command, trips and active goods moves
cancelled, retried after the fire reset); EMS server down / up; late callbacks after cancellation
(VIP door cycle, goods start / end); goods overlap, past window, approval after expiry; FR-33
timeout at 599.9 / 600 s and late approval; medical car lost before pick-up (re-dispatch); medical
pre-empting the VIP car itself or another car; riders keep their stops (no `cancel_stop`).

## 7. Blockers (nothing here can be fixed inside Part 5)
| Blocker | Effect | Needed from |
|---|---|---|
| Part 2 dispatcher is a stub | no real VIP assignment, BR-10 order, FR-08 filter, ceiling answers, FR-04 re-assignment, Rush mode | Part 2 + CCR-01 |
| Part 4 car_states is a stub (grants all) | state refusals only tested with doubles | Part 4 |
| Part 3 access is a stub; mock ACS has no staff cards | authorisation in runs uses `StaffDirectory` double | Part 3 / Part 1 (CCR-05) |
| No door-time command | SIM-11 "EMS requests >= 10 s" cannot pass | CCR-02 |
| No goods-move end event | Part 3 cannot lift FR-82 early, SIM-08 evidence | CCR-04 |
| SRS text not in repo | open questions in REQUIREMENTS.md section 3 | Minh / Nhi / Team 3 |
| Simulator: `express(hold_doors_open=True)` during a door cycle at another floor holds the doors there | worked around in Part 5 (hold on arrival) | Part 1 (see INTEGRATION.md) |
