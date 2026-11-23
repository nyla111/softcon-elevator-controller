# Part 4 acceptance implementation matrix

Updated 23 November 2026. Source: completed audit at 2885a9e. A proposal, open PR or
selected passing checker is not DONE. Main remains 2885a9e. Preserve 23 Nov 22:00 freeze;
presentation deadline 25 Nov. Shared proposals are in safety_integration_proposal.md.

| Package | Requirements | Owner | Implementation / tracking | Remaining verification / dependencies |
|---|---|---|---|---|
| T1 | FR-110 / VER-10 | P4 | PR #85, 15739ff, CI test+Docker passed; review requested from P1 | Required approval and authorized squash merge; 36 new / 269 focused tests, 22 scenario+Priority tests pass; context in T2 |
| T2 | FR-53/110 / NFR-R4 | P4/P6 | #80; timestamped notification context implemented in isolated branch | Six red regressions; 205 focused tests now pass; full suite running; PR/review/CI/merge pending |
| T3 | NFR-SF3 / FR-50 | P4/P6 | #81; fire-I/O episode alert implemented in isolated branch | Three red regressions and stale guard; 197 focused tests pass; full suite running; PR/review/CI/merge pending |
| T4 | FR-52 / VER-10 | P4/P1/P2/lead | #46; existing primitive only; concrete shared proposal prepared | Approved commissioning/readiness/permission/ack contract, real runtime and complete boundary evidence |
| T5 | SIM-08 / FR-50/55 | P4/P3/P5 | #61/#33; partial registry | Actual goods/VIP/onboard/Rush/fire/FM/recovery journey; P3 lockdown #83 owner review |
| T6 | SIM-14/15 / FR-110/111/112 | P4/P1 | #33/#55; additive runner injection already exists | Convert fixtures, companion full command/occupancy/queue/recovery traces and seeds1-10 |
| T7 | SIM-13/15 / FR-100/84/04 | P4/P2/P6 | #61/#32; partial checks | Nonempty waiting sets; natural overload candidate/ownership investigation; prompt/voice/bulky/crowd proof |
| T8 | FR-121 / NFR-P6 | P4/P3 | #82; correction in progress | Cooldown/calendar episodes/message contract, independent regressions, runtime linked to T9 |
| T9 | FR-125/120/121/122 / BR-07 / SIM-17 | P3/P2/P4/lead | #61; pre-admission proposal prepared | Approved interface/optional scope; real entry-point exemption and duplicate/uncertain/no-show evidence |
| T10 | FR-113 / VER-15 | P4/P5/team | #61; implementation-versus-deferral proposal prepared | Explicit team scope/history/window decision, then corresponding implementation or honest deferral evidence |
| T11 | Traceability, seeds, CI | P4/P1/P6 | #33/#61; this live matrix | Update fixtures/checkers/docs/requirements; collect raw traces; full pytest+Docker and inspect known gaps |

External: #83 effective lockdown (P3); #84 occupied medical ordering (P5/P2/P1).
No external defect is resolved solely by this matrix. No other owner's existing branch is edited.

## Acceptance classifications carried forward pending new evidence

| SIM | Status | Remaining obligation / evidence |
|---|---|---|
| 01 | PARTIALLY VERIFIED | P2 hard wait ceiling gap |
| 02 | PARTIALLY VERIFIED | P2 hard wait ceiling gap |
| 03 | PARTIALLY VERIFIED | P2 hard wait ceiling gap |
| 04 | PARTIALLY VERIFIED | Full seed/energy/wait evidence |
| 05 | PARTIALLY VERIFIED | Full day/seed/target evidence |
| 06 | VERIFIED | Prior full checks across1-10; no new claim |
| 07 | BLOCKED | P3 #60 full outage checker/workload |
| 08 | BLOCKED | Full goods/VIP/FM/lockdown journey |
| 09 | VERIFIED | Prior full checks across1-10; no new claim |
| 10 | PARTIALLY VERIFIED | Design-limit end-to-end/history evidence |
| 11 | PARTIALLY VERIFIED | P2 full Rush wait ceiling |
| 12 | PARTIALLY VERIFIED | P2 ordinary-call wait ceilings; P5 #79 unmerged |
| 13 | PARTIALLY VERIFIED | Non-vacuous reassignment and full overload/camera criteria |
| 14 | BLOCKED | Actual failed-rescue fixture and full restoration path |
| 15 | PARTIALLY VERIFIED | Nonempty waiting set / reassignment / explicit-brake fixture |
| 16 | VERIFIED | Prior full checks across1-10; no new claim |
| 17 | BLOCKED conditional | Optional scope + approved real admission integration; FR125 mandatory |
| 18 | PARTIALLY VERIFIED | #84 delivery-before-reassignment ordering |

FR-52 remains unimplemented at runtime. FR-55 gate/unit/adapter behavior is implemented
and partially integrated, with complete08/14 journey still unverified. VER-10 is partial
until generator limited operation and the full command/notification/recovery matrix pass.
