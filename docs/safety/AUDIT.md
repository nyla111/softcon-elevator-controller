# Stage 1 — Safety correctness and Priority compatibility audit

Historical audit against the older Priority snapshot. For safely synchronized main and the
current production PR #16, use [SYNC_AUDIT.md](SYNC_AUDIT.md), dated 2026-11-15. PR #13 is
archival; these old expected failures are not the current integration target.

Audit date: 2026-11-14. Recommendation: **prepare a Draft PR for the Safety foundation**,
with the external blockers below explicitly listed. Do not represent this as complete Part 4
acceptance or as approval to combine/deploy the Priority branch unchanged.

## Scope and provenance

- Safety working branch: `part4/safety`, base `8b4afdcaf27f3fb242ad3801495ffc5165c7d25a`.
  All pre-existing uncommitted foundation work was preserved.
- Priority branch inspected: `origin/part5/minh-priority-service-car`, pinned at
  `59c5d918887313e8fe182f5afd1c295cddf93bbe`.
- Isolated, detached worktree: `.tmp/safety-audit-priority-59c5d91`. It was created for this
  audit and remains clean. The directory was already excluded by `.git/info/exclude`.
- Cross-branch tests load the **unmodified** Priority package from that worktree under a
  separate module name, with this working tree's real Safety, Simulator and contracts.
  Every loaded source file is compared with its pinned Git blob before testing. No
  Priority implementation is copied over the working branch. Shared contracts/core have
  no differences between the two audited commits.
- Staff authorization is a fixture; Access and Dispatch remain stubs, except for the explicitly
  identified Priority dispatch stand-in in the accessible-call tests. These tests establish
  component compatibility/defects, not full application acceptance.
- Runtime corrections made in this stage are limited to `ems/safety/service.py`. Added
  tests are in `tests/safety/`; documentation changes are under `docs/safety/`. No teammate
  implementation, contract, core file, or existing test assertion was changed by this audit.
- No commit, push, merge, rebase, reset, or PR creation was performed.

## Safety findings

**PASS** means the stated behavior was executed and asserted. **BUG** means an actual Safety
failure was reproduced and fixed here. **BLOCKED** requires another part or a contract.
**UNVERIFIED** identifies a limit of the available evidence. Line numbers below refer to the
final local files; Priority locations refer to the pinned commit above.

| ID / classification | Finding and exact implementation location | Executable evidence |
|---|---|---|
| S-01 — PASS | Effective precedence does not erase independent physical facts. `ems/safety/states.py:28` selects the minimum hold; `:33` keeps each `(state, owner)` separately. `ems/safety/service.py:262` records a mechanical fault even under Fire Recall / Emergency Power. After FM recovery, the car remains FAULTED until ControllerFaultCleared. | `tests/safety/test_correctness_audit.py:21`, `test_physical_fault_recorded_while_masked_and_survives_fm_recovery[fire,power]`: asserts both the masked internal fault hold and the effective post-recovery state, then separately clears it. PASS. |
| S-02 — PASS | One car's fault does not activate a building emergency or exclude healthy cars. `ems/safety/service.py:76`, `:84`, `:253`. | `tests/safety/test_correctness_audit.py:48`, `test_one_physical_fault_does_not_create_a_building_emergency`: B/C/D and E/F/G/H remain dispatchable. PASS. |
| S-03 — PASS | Releasing one state or owner cannot erase another. Public release uses the exact tuple and rejects reserved internal owners (`ems/safety/service.py:70`, `ems/safety/states.py:33`). | `tests/safety/test_correctness_audit.py:57`, `test_same_owner_different_states_and_other_owner_survive_releases`; existing `tests/safety/test_states.py:30`; cross-branch `tests/safety/test_priority_compatibility_audit.py:135`. PASS. |
| S-04 — PASS | A completed FM confirmation is historical evidence, not a reusable unlock. New building emergencies clear attestations (`ems/safety/service.py:114`); early confirmation is not cached (`:241`). | `tests/safety/test_correctness_audit.py:71`, `test_confirmed_previous_episode_does_not_confirm_a_new_emergency`, plus the existing continuous-mains/early-confirmation test. PASS. |
| S-05 — BUG, fixed | Older ControllerFaultCleared/HeartbeatRestored and FireAlarmCleared/MainsRestored events could erase a newer active condition. Added per-signal/per-car timestamp ordering and invalid/future timestamp rejection at `ems/safety/service.py:101`, used by handlers at `:130`, `:137`, `:142`, `:157`, `:173`, `:180`, `:262`, `:270`, `:277`, `:282`, `:287`. Fault/link channels remain independent. | `tests/safety/test_correctness_audit.py:98` and `:115` reproduced four failures before the fix. They now pass, including a subsequent legitimate clear. `:177` verifies stale firefighter-off/generator-ready rejection; `:189` verifies old fault telemetry cannot reassert a newer cleared condition. |
| S-06 — BUG, fixed | A new firefighter-service activation while the old fire recovery hold still existed failed to invalidate all normal/safety attestations. `ems/safety/service.py:142` now starts a fresh emergency gate on re-entry even if effective Fire Recall never changed. Repeated active reports and the normal FireAlarm→FirefighterService fan-out do not repeat handoffs. | `tests/safety/test_correctness_audit.py:127`, `test_new_firefighter_episode_invalidates_all_attestations_under_existing_hold`, failed before and passes after the fix. Existing `test_fault_during_fire_is_retained_and_firefighter_event_does_not_repeat_handoffs` still passes. |
| S-07 — BUG, fixed | Repeated ControllerFault events with weaker flags overwrote earlier between-floor/stuck-door evidence. `ems/safety/service.py:262` now accumulates those facts until the matching clear handler at `:270`. | `tests/safety/test_correctness_audit.py:144`, `test_repeated_fault_does_not_downgrade_persistent_entrapment_evidence`, failed before and passes after the fix. |
| S-08 — BUG, fixed | Safety's generic `kind="fault"` alert duplicated Part 5 Maintenance's FR-30 notification. Removed the mechanical notification from `ems/safety/service.py:253`; retained Safety's separate disconnected alert and urgent entrapment output at `:298`. FR-30 ownership is `scripts/requirements.csv`, Part 5. | `tests/safety/test_correctness_audit.py:171` failed before and passes after the fix. With real Part 5 loaded, `tests/safety/test_priority_compatibility_audit.py:160` verifies exactly one mechanical alert with contractor/FM recipients. |
| S-09 — PASS | No ordinary EMS commands during fire or locked recovery in the actual dispatch-guard path. Safety itself sends no controller commands. Dynamic layouts are read from runtime services (`ems/safety/states.py:10`); gate/filter are `ems/safety/service.py:76`, `:84`. | `tests/safety/test_correctness_audit.py:151` runs the 64-car design-limit layout, attempts ordinary arrivals during fire, after physical reset and after attestations, and asserts **zero attempted commands** until explicit FM confirmation. Exactly one subsequent ordinary stop is accepted. Existing SIM-08 tests also assert zero commands for seeds 1–10. |

The initial new correctness suite produced **7 failed, 6 passed** against the foundation,
before fixes. Four failure instances were stale-clear variants; the other three were
firefighter re-entry, hazard downgrade, and mechanical alert duplication. Additional
follow-up cases were added without relaxing the original 53 Safety tests.

## Priority compatibility findings

The following PASS rows concern the tested paths only. Every BLOCKED row below has a
runtime reproduction, not merely a suspected static mismatch. Strict `xfail` markers preserve
the desired assertions for these external defects; they are not counted as successful
compatibility or scenario acceptance. Running with `--runxfail` yields **9 failures, 7 passes**
at the exact contract assertions described below.

| ID / classification | Finding, pinned Priority location, and required action | Executable evidence |
|---|---|---|
| P5-P1 — PASS | Active VIP trips and active goods periods cancel on FireAlarm/MainsLost; no controller commands are attempted during cancellation. `ems/priority/service.py:709`, `ems/priority/goods.py:244`, `:251`. Their old callbacks do not restart them. | `tests/safety/test_priority_compatibility_audit.py:95`, `test_active_vip_goods_cancel_without_commands_or_automatic_restart[fire,power]`. Uses real trip/goods APIs, actual controller audit, FM recovery and subsequent clock advance beyond the goods end. An explicit existing `retry_reopen()` call is used after FM only for flag cleanup; the missing automatic connection is P5-04. PASS. |
| P5-P2 — PASS | Fresh VIP/medical candidates filter authoritative Faulted/Maintenance states through real Safety, even with healthy physical telemetry. `ems/priority/service.py:240`. | `tests/safety/test_priority_compatibility_audit.py:125`, `test_faulted_maintenance_cars_excluded_from_fresh_vip_and_medical_candidates`. PASS. This does not cover the already-held VIP exception in P5-02. |
| P5-P3 — PASS | Ordinary matching owner tokens work: VIP uses `priority:vip`; goods uses `priority:goods`; releasing a Priority hold does not erase another owner's Maintenance hold. `ems/priority/service.py:199`, `:214`. | `tests/safety/test_priority_compatibility_audit.py:135` and `:95`. PASS. Preemption changes that token and is separately blocked below. |
| P5-P4 — PASS | New medical/VIP requests are blocked by Safety during fire/power. Accessible requests through the actual Priority-aware stand-in go through the real Safety dispatch guard and cause neither assignment nor commands. `ems/priority/service.py:169`, `:526`; `ems/priority/standins.py:96`. | `tests/safety/test_priority_compatibility_audit.py:95` and `:146`, both fire/power variants. PASS at this component boundary; real Access/Dispatch admission remains unavailable. |
| P5-01 — BLOCKED, Part 5 change | VIP→medical (`ems/priority/service.py:549`) and VIP→send-to-lobby (`:619`) change the local Hold.kind without transferring Safety's `(VIP_EXCLUSIVE, "priority:vip")` owner. `give_back` at `:214` later releases `priority:medical` / `priority:send_to_lobby`, which must not clear that different owner. Completed trips leave a stale VIP hold. Part 5 must preserve the acquisition owner or transfer it explicitly; Safety must not weaken owner isolation. | `tests/safety/test_priority_compatibility_audit.py:170`, `test_preempted_vip_releases_its_recorded_owner[medical,send_to_lobby]`. Both reach the same actual VIP car and finish the replacement trip; both fail at `state(car) is GROUP_SERVICE`, with VIP_EXCLUSIVE left over. **2 strict xfails.** |
| P5-02 — BLOCKED, Part 5 change | The held-VIP exception in `candidate_statuses` (`ems/priority/service.py:255`) bypasses effective state/dispatchability checks. `_start_medical` at `:526` can preempt that car directly. A higher-priority Faulted or Maintenance hold in real Safety can therefore be ignored when telemetry is physically healthy. Recheck the authoritative state and owner before express/preemption; cleanup commands also need an appropriate state check. | `tests/safety/test_priority_compatibility_audit.py:186`, `test_vip_preemption_rechecks_authoritative_state[FAULTED,MAINTENANCE]`. The real simulator puts E closer to the pickup than other eligible cars; Priority selects E despite the authoritative isolation. **2 strict xfails.** |
| P5-03 — BLOCKED, Part 5 change | `claim_call` returns its cached car at `ems/priority/service.py:330` before checking emergency/current eligibility. After the trip was cancelled by fire/power, replaying its call returns the unavailable E. A dispatcher using that answer could assign a cancelled exclusive car even though no fresh trip started. Invalidate/revalidate claims and apply safety checks before serving cached answers. | `tests/safety/test_priority_compatibility_audit.py:202`, `test_replayed_vip_claim_is_not_assigned_during_emergency[fire,power]`. Both return E instead of None. **2 strict xfails.** |
| P5-04 — BLOCKED, Part 5/integration change | `retry_reopen` (`ems/priority/service.py:228`) correctly refuses commands while Safety's recovery gate is locked. However its event subscriptions (`:739`) do not retry after FM confirmation, so the flags remain set. Part 5 can consume the existing CarStateChanged recovery transitions and retry only after `safety_active()` is false; any new dedicated recovery event would require tech-lead approval. Do not clear flags during the locked period. | `tests/safety/test_priority_compatibility_audit.py:213`, `test_deferred_priority_flags_clear_after_fm_confirmation[fire,power]`: after real FM confirmation, E remains closed to new calls and `reopen` still contains the cleanup. **2 strict xfails.** |
| P5-05 — BLOCKED, Part 5/integration change | Priority's local fire latch resets only on FirefighterService(active=False), `ems/priority/service.py:696`. The design-limit building has no service group, so that event never arrives. Safety can fully recover while Priority continues reporting an emergency. Use a layout-independent clear/recovery connection that still respects Safety's gate. | `tests/safety/test_priority_compatibility_audit.py:223`, `test_priority_recovers_after_fire_in_layout_without_service_group`: actual design-limit simulator reset + Safety attestations + FM confirmation leave `p.emergency()` true. **1 strict xfail.** |

No Part 5 source was edited to make these tests pass. The new strict expected failures are
optional cross-branch audit tests; without the explicit worktree environment variable they
report skips rather than claiming compatibility with a branch that is not installed.

## Evidence and contract limits

| Classification | Limit and exact boundary |
|---|---|
| BLOCKED | Full SIM-08/13/14/15/17 acceptance still needs real Dispatch, Access, Priority, FM/controller-normal and optional lockdown integration. Their registry readiness/status was not promoted by this audit. Existing scenario checks remain in `ems/sim/scenarios.py`; their full-length Safety subcriteria still run for seeds 1–10. |
| BLOCKED | Same-time/re-stamped stale event replay cannot be distinguished from legitimate same-time transitions using Event.t alone. `ems/contracts/events.py` has no sequence/emergency IDs for these signals. `ems/safety/service.py:101` rejects strictly older timestamps, but intentionally preserves valid bus order for equal timestamps. Approved episode/sequence correlation is needed for stronger guarantees. |
| BLOCKED | Unversioned trusted attestations and `confirm_recovery(Role.FM)` at `ems/safety/service.py:211`, `:215`, `:241` are internal synchronous hooks. Stored attestations are invalidated correctly, but an old external request re-presented as a fresh call cannot be authenticated/correlated without the missing FM/controller API and episode ID. No such integration was invented. |
| BLOCKED | FR-52 generator capacity/allocation, failure withdrawal and sequential parking remain absent from the shared model. Safety continues to fail closed on movement; the existing capacity contract requests remain in INTEGRATION.md. |
| BLOCKED | Main still has no real Part 5 FR-30 publisher. Removing the duplicate generic mechanical alert correctly respects ownership; the full mechanical notification workflow needs Part 5. Safety's urgent entrapment alerts remain implemented independently. |
| UNVERIFIED | Absence of unsafe commands from every future Dispatch/Access plug-in or every untaken Priority callback cannot be proved from these tests. The actual tested ordinary, accessible, new medical/VIP, emergency-cancellation and locked-recovery paths are identified above. Controllers retain physical refusal authority. |
| UNVERIFIED | End-to-end authenticated staff/FM identity, panel duplicate proof, overload reassignment and external I/O replay handling cannot be validated on main's stubs. Successful component tests with staff fixtures are not evidence of those integrations. |

Static publisher review found no Part 5 publication of CarStateChanged, CallAssigned or
CallReassigned in its production service/goods/maintenance modules. Part 4 still publishes
only its own CarStateChanged plus shared AlertRaised/PanelMessage outputs. The executable
single-fault-notification and trip-cancellation assertions prove the tested non-duplication
cases; the static review is not presented as universal runtime proof.

## Verification and reproduction

The pre-audit foundation report was 299 passed, 1 skipped, including 53 Safety tests.
The final audit run has no unexpected failures. The 75 passing Safety-folder tests consist
of 68 local Safety/policy tests and 7 passing cross-branch component tests; the other 9
cross-branch cases remain explicit expected failures, not compatibility passes.

| Check | Result |
|---|---|
| New correctness tests against pre-fix Safety | 7 failed, 6 passed; genuine defects reproduced |
| Post-fix Safety + optional Priority audit | 75 passed, 9 strict xfailed |
| Safety/scenario subset (earlier review pass) | 115 passed, 9 strict xfailed; includes all 40 full-length Safety scenario runs, before two additional stale-event follow-up cases |
| Safety scenario cases in combined and final full runs | 42 passed: 40 runs across seeds 1–10 plus 2 registry/check regressions; full SIM acceptance stays BLOCKED |
| Priority audit with `--runxfail` | 7 passed, 9 failed at desired external contract assertions |
| CI Ruff: `ruff check --select E9,F63,F7,F82 .` | PASS |
| Focused Ruff: `ruff check ems/safety tests/safety` | PASS |
| `git diff --check` | PASS |
| Final full suite, cross-branch audit enabled | **321 passed, 1 skipped, 9 strict xfailed**, 246.21 s; existing Starlette/httpx deprecation warning only |

Run in the existing WSL virtual environment:

```bash
cd /mnt/d/Projects/softcon-elevator-controller
source .venv/bin/activate
export SAFETY_PRIORITY_AUDIT_TREE="$PWD/.tmp/safety-audit-priority-59c5d91"
pytest tests/safety/ -q
pytest tests/scenarios/test_safety_scenarios.py -q
pytest -q
ruff check --select E9,F63,F7,F82 .
ruff check ems/safety tests/safety
git diff --check

# Expose the external failures as normal failures for investigation:
pytest tests/safety/test_priority_compatibility_audit.py --runxfail -q --tb=short
```

The detached worktree is retained clean for reproducibility. If it is recreated elsewhere,
point the environment variable to an unmodified checkout of the exact pinned commit.
The fixture checks source bytes against Git objects, so it also works when a Windows-created
worktree's metadata cannot be read directly by Linux Git.

## Draft PR recommendation

**Yes, for a Draft Safety foundation PR; the final regression run passed.** Its scope
should be the independent state facts, strict recovery gate, conservative supervision, tested
stale-event handling and documented integration boundaries. Include S-05..S-08 as fixed
correctness findings and P5-01..P5-05 as explicit unresolved cross-branch blockers.

Do not describe the current combination as merge-ready operational Safety. In particular,
medical preemption can select an isolated held VIP car on the unmodified Priority branch;
that is a real compatibility defect, not an absence of test data. Neither the PR nor these
test counts should imply full SIM acceptance, a connected FM UI, or completed FR-52 parking.
