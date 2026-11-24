# Issue #33 validation evidence

Base: main `bf5c50987397acc41393bcd7262549664c845e04`.
Environment: repository WSL virtualenv, pytest 9.2.0, Ruff 0.17.5.
Only passenger-list documentation, fixtures, and an evidence script are changed.

## Reproduce

Use the single-list command in [README](README.md) for each new CSV. For the
schema, multi-seed results, and supplementary trace assertions, run:

```sh
python docs/passenger_lists/verify_issue33.py
```

This evidence script reports pending expectation failures without changing the
fixtures or injecting alternative faults. Its assertions validate onboard/position
preconditions, observed command/assignment windows, and waiting-call preservation.
It is not a replacement runner or an automatic CI acceptance test.

## Results

| Fixture | Seeds | Current row checks | Interpretation |
|---|---|---|---|
| SIM-14 | 1, 7, 42 | 11/13 each | Blocked: intended silent-rescue rider is evacuated; no entrapment alert. |
| SIM-15 | 1, 7, 42 | 15/15 each | Generic moving-car fault passes; explicit brake injection remains unsupported. |

Preserved SIM-14 failure output (identical across all three seeds):

```text
FAIL row 1: sim14-onboard expected delivered; got evacuated
FAIL row 2: mains_loss expected alert:entrapment; got no_alert:entrapment
```

Seed 1 explains the failure: `GeneratorReady(t=29)` follows mains loss at t=14,
and all nine cars report `rescued_at_floor` (A at 43.09403260091328).
The CSV cannot request failed generator plus silent rescue A. The expected
entrapment alert and intended rider outcome are retained; the list stays pending.

Supplementary trace assertions, seed 1:

- Both lists have one passenger onboard A before the fault, at position about
  4.3327 between floors. SIM-15 starts arrivals at 07:00 (t=25200).
- SIM-14: Safety stays active at t=14, 74, 140, 141, 259, and 260. Recovery becomes
  eligible at t=260 (120 s after restoration at t=140) but Safety remains active
  until fresh FM confirmation at t=261. No EMS commands or ordinary assignments
  occur in [14, 261). These observations use successful generator/rescue behavior,
  so they do not prove recovery with the required failed devices.
- SIM-15: the event at t=25214 is `ControllerFault(car_id='A', code='F-LIVE',
  between_floors=True, doors_stuck=False)`. A critical entrapment alert for A
  occurs at t=25214, within 10 s. Zero EMS command attempts to A occur in
  [25214, 25334), including refused attempts.
- The SIM-15 pre-existing waiting call `sim15-already-waiting` is requested once
  at t=25210, remains waiting immediately before the fault, and boards healthy D
  at t=25232.5. Its wait is 22.5 s from the original request. No replacement
  passenger or reset wait clock is used. This proves retention, not reassignment
  of a call previously assigned to A.
- All 12 CSVs parse. The CI top-level glob selects nine active lists; all three
  pending lists are excluded. Existing SIM-08 and SIM-16 are unchanged.

Focused command from README: **186 passed in 8.50 s** (final run). This includes all nine
active passenger lists, Safety tests, power simulation, FR-55 controller reports,
and Safety/Priority integration. CI Ruff command: **All checks passed**.
The full scenario suite is left to CI; no additional long local suite was run.

## Acceptance gaps

[#55](https://github.com/nyla111/softcon-elevator-controller/issues/55) tracks the
Part 1 CSV event/observation contract. SIM-14 still needs actual failed generator
and one failed/missing rescue report; SIM-15 needs explicit brake code selection.
The paired event checks enforce the alert time bound for these single-incident
lists, but cannot assert the target car. Row expectations alone do not enforce
forbidden command windows, intermediate recovery states, or queue identity.
Supplementary traces cover those observations for the currently expressible
events; they must be repeated with the required events before promotion.

SIM-08 retains its existing pending fixture and historical #26 notes from #44.
FR-55 is now wired by merged #48/#49, but extending or promoting that fixture was
outside the remaining SIM-14/SIM-15 change. No scenario-registry readiness flag was
changed. Keep #33 open until all requested acceptance is demonstrated.

## SIM-14 implementation evidence — 23 November 2026
The previous pending outcome failures above are historical. The active sim14_power_loss.csv
now uses generator_failed=true and failed_rescue_cars=A, with #start=07:00 and bounded
per-car60s checks. Original delivered expectations remain unchanged. Companion tests in
tests/safety/test_sim14_journey.py prove occupancy, no premature alighting, exact74s
notification/context, no attempted EMS command or assignment before261s, original waiting
clocks, refusals at141/259s and valid FM confirmation at261s.

The full registry setup selects an actually occupied silent-rescue car from real telemetry,
keeps B physically faulted, captures trapped occupancy just before restoration, rejects early
FM confirmation, observes the120s boundary, and admits post-confirm traffic. All seven
criteria plus clearance pass on EACH seed1-10;20 fixture/registry tests pass. Redacted raw
traces are retained under .tmp/part4-sim14-evidence (10 JSON files); XML is
.tmp/part4-sim14-complete.xml. Registry readiness now enforces the complete checker.
Review, CI and merge remain required. This failed-generator scenario does not prove FR52.
