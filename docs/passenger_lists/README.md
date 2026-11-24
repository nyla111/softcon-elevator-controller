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
python - docs/passenger_lists/sim14_power_loss.csv <<'PY'
import sys
from ems.sim.scripted import load, run_list
ctx, checks = run_list(load(sys.argv[1]), seed=1)
for check in checks:
    print(check)
raise SystemExit(0 if all(check.ok for check in checks) else 1)
PY
```

Repeat with `sim15_brake_fault.csv`. Both lists are active; pending files, if any,
are historical/blocked evidence rather than acceptance. `run_list` uses the registered real dispatcher when
available; the nearest-car stand-in is installed only for `StubDispatcher`.
It starts at midnight, runs through the last row, then allows up to 1800 additional
seconds for outstanding passengers. SIM-15 therefore uses t=25200 for 07:00 up-peak.
A list may set its start time with a first line `# start=HH:MM`. The VIP lists
`sim12_vip_caps.csv` and `sim18_medical_over_vip.csv` use `# start=07:00`, because VIP
exclusive trips start only in Rush (FR-71, #65).

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

Integrated runtime audit: `f4fb40a` (25 November 2026). All four requested safety
lists are active at the top level and selected by CI. Historical pending-fixture
limitations in issue33_evidence.md are superseded by its integration update.

| Scenario | Active file | Required companion evidence |
|---|---|---|
| SIM-08 | `sim08_fire_recall.csv` | #108: both full workloads/seeds1-10; actual VIP/goods cancellation, all onboard lobby alighting, zero attempted EMS commands, lockdown retention and explicit FM recovery |
| SIM-14 | `sim14_power_loss.csv` | #94: failed generator, silent occupied A rescue, exact60s received alert/context, zero attempts before FM,120s stable mains, original waiting clocks and early-confirmation rejection |
| SIM-15 | `sim15_brake_fault.csv` | #95: explicit occupied between-floor brake, nonempty waiting set, healthy reassignment, original clocks, no boarded reassignment, exact command and alert windows |
| SIM-16 | `sim16_link_loss.csv` | Existing FR-104 link-loss coverage preserved |

FR-55 controller reports and authenticated FM actions are implemented (#48/#49).
Issue #55 supplied the necessary event controls; do not recreate them. Failed-generator
SIM-14 does not establish successful limited-generator FR-52 (#46).
See [issue33 evidence](issue33_evidence.md) and [acceptance matrix](../part4_acceptance.md).
Close #33 only after final main CI verifies the lists and complete companion assertions.
