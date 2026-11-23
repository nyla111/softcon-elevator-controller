# Part 4 acceptance implementation matrix

Updated 23 November 2026. Source: completed audit at 2885a9e. A proposal, open PR or
selected passing checker is not DONE. Main remains 2885a9e. Preserve 23 Nov 22:00 freeze;
presentation deadline 25 Nov. Shared proposals are in safety_integration_proposal.md.

| Package | Requirements | Owner | Implementation / tracking | Remaining verification / dependencies |
|---|---|---|---|---|
| T1 | FR-110 / VER-10 | P4 | PR #85, 15739ff, CI test+Docker passed; review requested from P1 | Required approval and authorized squash merge; 36 new / 269 focused tests, 22 scenario+Priority tests pass; context in T2 |
| T2 | FR-53/110 / NFR-R4 | P4/P6 | #80; PR #86 timestamped scalar notification snapshots; CI passed | Six red regressions; 205 focused passes; review/merge pending |
| T3 | NFR-SF3 / FR-50 | P4/P6 | #81; PR #87 episode alert and deduplication | Three red regressions and stale guard; 197 focused passes; CI blocked by confirmed wall-clock fallback divergence #101; review pending |
| T4 | FR-52 / VER-10 | P4/P1/P2/lead | #46; existing primitive only; concrete shared proposal prepared | Approved commissioning/readiness/permission/ack contract, real runtime and complete boundary evidence |
| T5 | SIM-08 / FR-50/55 | P4/P3/P5 | #61/#33; draft #102 dc70751; all20 paired real-service runs pass,180 criteria plus paired diagnostic42 | Unchanged35/min peak plus occupied-VIP companion; #92 P3 owner agreement, CI/review/integration pending |
| T6 | SIM-14/15 / FR-110/111/112 | P4/P1 | #33/#55; PR #94/#95 active fixtures and complete registry each pass seeds1-10; CI passed | Required review/merge pending |
| T7 | SIM-13/15 / FR-100/84/04 | P4/P2/P5/P6 | #61/#32; PR #97/#98/#99 and #95; full all10-seed traces and CI pass | P2/P5 overload and P6 camera-review owner agreement; review/merge |
| T8 | FR-121 / NFR-P6 | P4/P3 | #82; PR #90 corrected fixed cooldown/calendar episodes; CI passed | Four red regressions; 127 Safety passes; runtime #96; review/merge |
| T9 | FR-125/120/121/122 / BR-07 / SIM-17 | P3/P2/P4/lead | #61; user selected implementation; runtime draft #96, full acceptance draft #100; CI passed | 177 Access/policy tests and all10 SIM-17 seeds pass; P3 owner/interface agreement, review/merge |
| T10 | FR-113 / VER-15 | P4/P5/team | #61; user selected implementation; PR #91 runtime fault-episode threshold notice; CI passed | Four red regressions; 165 focused passes; review/merge |
| T11 | Traceability, seeds, CI | P4/P1/P6 | #33/#61; this live matrix | Update fixtures/checkers/docs/requirements; collect raw traces; full pytest+Docker and inspect known gaps |

External: #83 effective lockdown (P3); #84 occupied medical ordering (P5/P2/P1).
No external defect is resolved solely by this matrix. No other owner's existing branch is edited.

Owner-review drafts: #92 lockdown (162 Access passes), #93 occupied medical ordering
(155 focused passes, then56 joined notification passes), #96 admission (177 passes),
#97 overloaded VIP pickup (55 joined passes), #98 camera-review distinction (24 Monitor
passes). User explicitly approved draft publication; owner implementation/interface
agreement remains required before merge.

New branch evidence: full SIM-13 (#99), SIM-14 (#94), SIM-15 (#95) and SIM-17 (#100)
each pass all10 seeds locally. This updates branch evidence only; the audited main
classifications below remain unchanged until integrated acceptance. SIM-08 draft #102
now passes both full real-service runs for every seed1–10 (20runs,180criteria), and paired
diagnostic42. It remains pending P3 agreement/CI/review/integration, with no main promotion.
Full checker diagnostic seed42 also passes for SIM-13, SIM-14, SIM-15 and SIM-17.

PR #87 preserves the weekday digest assertion and real dispatch timeout. New CI evidence
confirms the repeated seed1 run triggered FR-105 fallback at1.579471202s, whereas the
first run did not; event digests differ. #101 owns P1/P2 investigation of the transient
slowness and repeatability contract. Two local full-day runs have identical digests
without fallback; this does not negate CI evidence. One local
full suite had846 passes,2 skips,21 xfails,3 xpasses and37 temporary-folder permission
errors: this is not a clean pass. Subsequent local full suites need workspace basetemp.

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

FR-52 remains unimplemented at runtime and fail-closed pending tech lead/P1/P2/P4
commissioning and interface approval requested in #46. FR-55 now has full SIM-08/14
branch journey evidence; required review/integration remains pending. VER-10 is partial
until generator limited operation and the full command/notification/recovery matrix pass.
