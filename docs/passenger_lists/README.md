# Passenger lists

CSV columns and supported events are defined in
[`ems/sim/scripted.py`](../../ems/sim/scripted.py) and
[`ems/sim/live.py`](../../ems/sim/live.py). Use the existing header:

```csv
t,origin,destination,card,accessible,vip,passenger_id,event,car,duration,expect,max_wait_s,note
```

Each row is a passenger or an event. Passenger outcomes are `delivered`, `refused`,
`cancelled`, `evacuated`, or `waiting`; these are evaluated at run end.
`max_wait_s` bounds time to boarding, including for an eventually evacuated rider.
Event expectations are `alert:<kind>` or `no_alert:<kind>`; `alert:entrapment` is
supported. Event `max_wait_s` is ignored, so it must not be used as an alert deadline.
Notes are descriptive and do not execute assertions.

An event expectation searches from its row's time through run end, inclusive.
For these single-incident safety lists, `alert:entrapment` at injection paired with
`no_alert:entrapment` at the next representable float after the deadline requires
an alert by the deadline and rejects later entrapment alerts. The cutoffs are
`math.nextafter(74.0, math.inf)` and `math.nextafter(25224.0, math.inf)`.
This combination does not identify the alerted car or inspect command windows.

Run a single list from the repository root (installed development environment):

```sh
python - docs/passenger_lists/pending/sim14_power_loss.csv <<'PY'
import sys
from ems.sim.scripted import load, run_list
ctx, checks = run_list(load(sys.argv[1]), seed=1)
for check in checks:
    print(check)
raise SystemExit(0 if all(check.ok for check in checks) else 1)
PY
```

Repeat with `pending/sim15_brake_fault.csv`. Pending failures are expected evidence,
not successful acceptance. `run_list` uses the registered real dispatcher when
available; the nearest-car stand-in is installed only for `StubDispatcher`.
It starts at midnight, runs through the last row, then allows up to 1800 additional
seconds for outstanding passengers. SIM-15 therefore uses t=25200 for 07:00 up-peak.

Validate all active lists and relevant safety behavior:

```sh
python -m pytest -q tests/sim/test_scripted.py tests/safety tests/sim/test_power.py tests/sim/test_recovery_reports.py tests/integration/test_safety_priority.py
python -m ruff check --select E9,F63,F7,F82 .
```

CI runs `pytest -q` and the Ruff command above. The passenger-list test parametrizes
`LISTS.glob("*.csv")`, **not** `rglob`: only top-level CSVs run automatically.
`pending/` is excluded. All pending lists still need to parse successfully.
Do not move a list into the active directory merely because its current row
expectations pass; verify the intended fault and all acceptance conditions first.

## Issue #33 coverage audit

Audit base: `bf5c50987397acc41393bcd7262549664c845e04` (refreshed main).
No SIM-14/SIM-15 CSVs existed. Existing scenario-registry setups and Safety unit
tests are separate from passenger-list coverage; registry readiness was not changed.

| Scenario | File | Status | Remaining acceptance |
|---|---|---|---|
| SIM-08 | `pending/sim08_fire_recall.csv` | Preserved from merged [#44](https://github.com/nyla111/softcon-elevator-controller/pull/44) | Historical notes refer to #26; post-FM recovery is absent from this fixture. No duplicate or promotion in this change. |
| SIM-14 | `sim14_power_loss.csv` | Full fixture + registry checks pass seeds1-10 on the acceptance branch | Actual failed generator/silent occupied car, exact60s context delivery, no attempted commands/assignments before FM,120s stability, early-refusal and isolated-car exclusion; review/CI/merge pending. |
| SIM-15 | `sim15_brake_fault.csv` | Full fixture/registry checks pass seeds1-10 on acceptance branch | Explicit occupied moving brake, nonempty original waiting set, healthy reassignment and exact command/alert windows; owner review and CI pending. |
| SIM-16 | `sim16_link_loss.csv` | Active, preserved | Existing FR-104 coverage; no duplicate. |

[#26](https://github.com/nyla111/softcon-elevator-controller/issues/26) is closed.
Merged [#48](https://github.com/nyla111/softcon-elevator-controller/pull/48) wires
controller-normal/safety-clear reports and `fm_confirm`; merged #49 adds dashboard
actions. FR-55 support is no longer the reason for blocking the new lists.
[#46](https://github.com/nyla111/softcon-elevator-controller/issues/46) concerns
generator integration and does not supply the missing CSV event controls.
No Part 1, Part 2, Part 6, or shared-contract implementation belongs in this change.

See [issue33 evidence](issue33_evidence.md) for actual results and acceptance gaps.
Issue #33 must remain open while those gaps remain.

## Part 2 lists: dispatch and modes (#32)

Each list starts at the time of day of its mode (`# start=HH:MM`), so the schedule (FR-11)
puts the dispatcher in that mode. `max_wait_s` is the mode's wait ceiling (Rush 60 s,
Balanced 90 s, Eco 120 s). Results below are seed 1 on main.

| File | Shows | Notes |
|---|---|---|
| `sim01_up_peak_sample.csv` | SIM-01, FR-01/02/10: Rush morning peak, lobby up calls and one down call | waits 3-14 s |
| `sim02_lunch.csv`, `sim02_evening.csv` | SIM-02, FR-10: Rush two-way lunch peak and evening down-peak | waits 4-40 s |
| `sim03_balanced_sample.csv` | SIM-03, FR-10: Balanced office hours | waits 3-15 s |
| `sim04_eco_sample.csv` | SIM-04, FR-10: Eco at night, two cars per group in service | waits 3-36 s |
| `fr04_car_fault_reassign.csv` | FR-04: car A faults while two calls wait for it; both move to C with their wait clock | not vacuous: A was assigned before the fault |
| `fr100_overload.csv` | FR-100: phantom overload on A at the lobby; ov-1 is re-assigned A -> B | the step-out message is checked in `tests/dispatch` |
| `fr15_mode_change.csv` | FR-15, BR-02, FR-11: calls made in Eco just before 07:00 finish after the change to Rush; new calls get the Rush ceiling | |

FR-05 (no new call for a car at 80% load) has no list: with idle cars around, the engine spreads
a lobby group over them (17 people end up 4-5 per car), so no car reaches 80%. It is covered by
`tests/dispatch/test_dispatcher.py::test_no_new_hall_call_for_a_car_loaded_80_percent_fr05`.
Trips in lists start or end at the lobby (inter-floor taps are refused by Access), so the samples
use lobby trips. The wear-balancing NFR (CR-01) is validated by long runs in #104, not by a list.
