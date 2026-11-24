# Part 4 acceptance implementation matrix

Updated 25 November 2026. Integrated runtime baseline: `f4fb40a62aea9103661bdcc37b0e53613faef2dd`.
PRs #108, #96, #100 and #99 are merged, in that order. This document replaces the
23 November branch-only matrix. Final main CI is the acceptance gate; a merged PR,
ready flag, historical JSON or selected passing criterion alone is not verification.
No SRS requirement, safety assertion or GitHub protection was relaxed.

| Package | Requirements | Integrated implementation and verification | Remaining obligation |
|---|---|---|---|
| T1 | FR-110 / VER-10 | #85; missing rescue report alerts at exactly60s despite readiness/restoration; `test_rescue_deadline.py` | FR-52 portion of VER-10 remains blocked in T4 |
| T2 | FR-53/110 / NFR-R4 | #86; scalar notification context; `test_alert_context.py` | Final main CI |
| T3 | NFR-SF3 / FR-50 | #87; fire-I/O episode/deduplication tests; #101 dispatch timing fix already on main | Final main CI; retain real timeout and weekday digest assertions |
| T4 | FR-52 / VER-10 | #46/#88; parking primitive exists, integrated limited service does not | Approved Q2-Q7, actual commissioned count/service and parking orders, readiness/revision/permission/ack contract, P1/P2/P4 runtime and AC1-AC7 evidence |
| T5 | SIM-08 / FR-50/55 | #108 with #92 lockdown; both full runs for every seed1-10 enforced in CI; `test_sim08_journey.py` | Final main CI. Peak lead10s, occupied companion29s; historical22s evidence is labelled historical |
| T6 | SIM-14/15 / FR-110/111/112 | #94/#95; active failed-generator/silent-rescue and explicit occupied brake CSVs; full registry and journey tests | Final main CI; SIM-14 does not prove successful-generator FR-52 |
| T7 | SIM-13/15 / FR-100/84/04 | #99 on current #97 successor and #98; nonempty waiting sets, original clocks, healthy reassignment, camera/voice deadlines | Final main CI; superseded overload mixin was excluded |
| T8 | FR-121 / NFR-P6 | #90 policy plus #96 runtime; fixed cooldown, daily episodes, delivered refusal | Final main CI; 180 focused Access/policy checks passed, one existing skip |
| T9 | FR-120/121/122/125 / BR-07 / SIM-17 | #96/#100; real admission, exact replay proof, owner cancellation, uncertain retention, door-cycle no-show and actual exemptions | Final main CI; all10 full seed tests retained, no N/A/deferral |
| T10 | FR-113 / VER-15 | #91; independent mechanical episodes and inclusive rolling30d threshold; `test_fault_review.py` | Final main CI; per-episode FR-30 remains separate |
| T11 | Traceability / seeds / CI | Current active fixture documentation, complete registry, original assertions and final GitHub CI | Record final run URL/results in issue audit; no broad SRS-completion claim |

External #83 lockdown and #84 occupied-medical changes are already on main through
#92/#93. #93's medical security-notification change is also on main; #100 does not
reintroduce it. Existing overload/camera/cooldown fixes were preserved rather than
duplicated. Existing percentile decisions and wait-target gaps in `DECISIONS.md` and
`dispatch.md` remain visible; this work does not approve further SRS changes.

## Issue audit

| Issue | Closure rule / blocker |
|---|---|
| #33 | Close only after final main CI verifies active SIM-08/14/15/16 lists plus full fire/power/brake companion checks. FR-55 and fault controls are implemented; old pending notes are historical. |
| #61 | All five requested scenarios now execute full checks. Its expanded T1-T11 mapping still includes blocked T4/FR-52; keep the broader checklist open until #46 commissioning, contract and real-service acceptance are satisfied. Passing failed-generator SIM-14 cannot close that obligation. |
| #82 | Implementation selected and integrated. Close only after main CI confirms sixth-at59/until119, denied-repeat stability, calendar-day episode reset, immediate refusal, actual assistance exemptions and uncertain-call retention. |
| #46 | Keep open. No approved commissioning values or Q2-Q7 answers were found in #46/#88 comments or CONTRACT.md; primitive and successful rescue tests are insufficient. |
| #88 | Keep open. Admission/lockdown/medical/FR-113 implementations are integrated, but FR-52 schema/orchestration approval and commissioned count/ordered IDs are absent. No implementation or test fixture may invent them. |

## Acceptance boundaries

SIM-08/13/14/15/17 are ready with complete pass/fail checkers. CI parametrizes every
mandatory seed1-10, including both required SIM-08 workloads. Empty evidence is rejected.
Command attempts, waiting identities and received notifications remain asserted.
Prior diagnostic42/raw-trace references are historical branch evidence unless a current
execution explicitly records otherwise. Final CI establishes only its actual assertions.

FR-52 remains fail-closed and incomplete. GeneratorReady does not authorize ordinary
EMS dispatch; no deployment capacity, permission, acknowledgement or completion is
fabricated. Normal recovery still requires120 continuous mains seconds, clear safety
signals, normal non-isolated controllers and authenticated FM confirmation. Sequential
parking completion OR refusal and controller physical authority remain mandatory.

Other scenario classifications are not upgraded by this integration: SIM-01..05/10/11/12
retain their documented wait/energy/scale gaps; SIM-07 retains its Access outage gap.
SIM-06/09/16 and other previously verified checks retain their existing tests, not a new
blanket acceptance claim. Consult full CI failures/xfails and owner issues for scope.
