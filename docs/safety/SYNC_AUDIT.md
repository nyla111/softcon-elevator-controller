# Safely synchronized main and current Priority audit — 2026-11-15

Current integration target: **PR #16**, not archival PR #13. Recommendation: **Draft Safety
foundation PR**, with the current failures and missing integrations disclosed. Full scenario
acceptance and operational readiness are not established by this audit.

## 1. Backup, synchronization, and exact local state

Started on `part4/safety` at `8b4afdcaf27f3fb242ad3801495ffc5165c7d25a` with 3 tracked
modifications and 12 untracked files. Nothing was staged. Before fetching, created:

`D:\Projects\softcon-elevator-controller\.tmp\safety-backup-20261115-stage1`

The backup contains all 15 changed files with their relative paths under `files/`, a
SHA-256/length manifest, binary combined/staged/unstaged patches, original status/branch/HEAD
metadata, and a `HEAD.tar` archive of every tracked file at the old commit. File bytes were
verified against the manifest. The combined patch also parses successfully with
`git apply --stat`. The staged patch is empty because there were no staged changes.

`git fetch --prune origin` advanced origin/main to
`6aab02e8cd5f83c7c7a0eed3d4924946cb9d3dc9` (6 commits, 12 changed paths). Ahead/behind was
`0 / 6`. None of those incoming paths overlapped a local changed path. The authorized
`git merge --ff-only origin/main` therefore succeeded without creating a merge commit.
Every one of the 15 local file hashes was checked again **after** the fast-forward and was
unchanged. The working branch stayed `part4/safety`; the local `main` reference was not moved.

No commit, stash, push, PR creation/merge, reset, rebase, clean or force checkout was used.
The fetch reported an upstream force-update of the **archival remote-tracking** branch;
no local branch was force-updated by us.

During the subsequent authorized audit, only these pre-existing local files changed relative
to that backup: `ems/sim/scenarios.py` (the Part 4-owned SIM-08 criterion) and the three
`docs/safety/` reports. All other original local files, including Safety runtime source and
existing tests, retain their backed-up bytes. New files added by this audit are:

- `tests/safety/test_priority_pr16_audit.py`
- `tests/safety/test_updated_contract_evidence.py`
- `docs/safety/SYNC_AUDIT.md`

Thus the current tree has 3 tracked modifications and 15 untracked files, all unstaged.
The pre-existing tracked modifications remain `ems/safety/__init__.py`,
`ems/sim/scenarios.py`, and `tests/sim/test_scenarios_registry.py`. The backup is retained;
no local work was dropped. The old backup is a pre-sync snapshot, not a claim to contain
the new audit additions.

Exact changed-file inventory relative to synchronized HEAD:

```text
 M ems/safety/__init__.py
 M ems/sim/scenarios.py
 M tests/sim/test_scenarios_registry.py
?? docs/safety/AUDIT.md
?? docs/safety/INTEGRATION.md
?? docs/safety/SYNC_AUDIT.md
?? docs/safety/VERIFICATION.md
?? ems/safety/call_policy.py
?? ems/safety/service.py
?? ems/safety/states.py
?? tests/safety/test_call_policy.py
?? tests/safety/test_correctness_audit.py
?? tests/safety/test_emergencies.py
?? tests/safety/test_priority_compatibility_audit.py
?? tests/safety/test_priority_pr16_audit.py
?? tests/safety/test_states.py
?? tests/safety/test_updated_contract_evidence.py
?? tests/scenarios/test_safety_scenarios.py
```

## 2. Actual team code versus PR-only code

GitHub metadata and Actions were inspected through the connected GitHub API, then compared
with freshly fetched Git objects. Descriptions alone were not accepted as implementation
evidence.

| Item | Pinned state | Verified conclusion |
|---|---|---|
| [PR #15](https://github.com/nyla111/softcon-elevator-controller/pull/15) | MERGED; origin/main and Safety HEAD `6aab02e8cd5f83c7c7a0eed3d4924946cb9d3dc9` | Approved contract/Simulator/privacy changes are actually in this working environment. |
| [PR #16](https://github.com/nyla111/softcon-elevator-controller/pull/16) | OPEN, not merged; `origin/part5/minh-priority` at `4a7b11348056083ada4f30187af708afa812a417` | Current production Priority target; [CI run 43691582418](https://github.com/nyla111/softcon-elevator-controller/actions/runs/43691582418) succeeds. Tested unmodified in `.tmp/safety-audit-pr16-4a7b113`; not merged or copied into the application package. |
| [PR #13](https://github.com/nyla111/softcon-elevator-controller/pull/13) | OPEN DRAFT, explicitly archival; remote head `c7df693dd253dd342656a08e8789b56238f1272b` | Historical only. Earlier `59c5d91` audit is retained as history; its optional tests are disabled in the current run. |
| [PR #14](https://github.com/nyla111/softcon-elevator-controller/pull/14) | OPEN, not merged; `origin/part3/floor-access` at `ccaa6ffb3325d8036c3e9c4f7d109ed4ddbef910` | **Not fixed.** All twelve added Access implementation modules, including `service.py`, are zero-byte blobs (`e69de29…`). `ems/access/__init__.py` still registers nothing. [CI run 43679024362](https://github.com/nyla111/softcon-elevator-controller/actions/runs/43679024362) fails at its Tests step (job 132705393265). No Access integration was attempted. |
| Dispatch / Part 2 | `ems/dispatch/__init__.py` remains a registration stub on current main | No newer Dispatch implementation in fetched refs; its history still ends at the initial skeleton. The repository's open PR search returned only #13/#14/#16, with no Dispatch PR. |
| Simulator / Part 1 and Monitor / Part 6 | Already merged before this sync; Simulator additionally updated by #15 | Real implementations are available. Safety remains local and uncommitted. Priority/Access implementations are PR-only, not merged production services on main. |

CI success on Priority's own branch does not imply compatibility with the real local Safety
service. Its ordinary main dependencies still include the stub CarStates implementation.

## 3. Updated contracts and their Safety consequences

Read the current `docs/CONTRACT.md` including its changelog, `docs/DECISIONS.md`,
`ems/contracts/events.py`, `interfaces.py`, `types.py`, and `stubs.py`. Decisions and
CarStates signatures/BR-06 enum order are unchanged. No contracts or core files were
manually edited; those updates came from the main fast-forward.

| Approved change from #15 | Consequence / observed implementation |
|---|---|
| `PriorityService` protocol: `claim_call`, `rank`, `order`, `car_ok_for`, `door_dwell_s`; `StubPriority` | Dispatch has an approved interface now, rather than an informal handshake. Main still has no real Dispatcher caller. PR #16 exposes those methods and passes the focused protocol/policy test. |
| `register_stop(..., min_dwell_s=None)` | Simulator applies and audits requested minimum dwell. It is additive and does not give Priority or Safety permission to bypass controller refusals or safety states. |
| `GoodsMoveEnded(move_id, car_id, cancelled, reason)`, Part 5 publisher | Goods-cancellation observability is **resolved**, not a missing-event blocker anymore. Part 4's SIM-08 criterion now validates the real terminal event's correlation, cancellation reason/time, uniqueness and absence of restart evidence. It never releases a state hold based on that event. |
| Staff cards in the mock ACS | Security/FM/technician/admin role data is available. Current integration tests authorize actual merged staff cards through an explicitly labelled Access-to-ACS fixture. This is not a working Part 3 API or an FM recovery endpoint. |
| VIP_EXCLUSIVE means every exclusive priority trip | Safety's existing emergency invalidation applies to VIP, medical and send-to-lobby exclusive holds. The priority kind remains in the Part 5 trip event; the enum precedence is unchanged. |
| Credential redaction from `/api/state` event lines | Approved main privacy fix is present; Safety adds no credential logging. |

Still absent from approved contracts: authenticated episode-correlated FM recovery
confirmation, controller-normal/reset and safety-clear telemetry, generator capacity and
allocation/failure withdrawal, stronger signal/rescue sequence correlation, and optional
lockdown-suspension integration. Staff cards and GoodsMoveEnded do not resolve these gaps.

## 4. Safety correctness on synchronized main

**No new Safety runtime bug was found** from these additive main changes. The unchanged
local Safety baseline passes 68 tests on `6aab02e` before any new audit edit. Prior correctness
fixes remain in place; their pre-sync source bytes were preserved.

| Status | Requirement / exact code and executable evidence |
|---|---|
| PASS | Fire→mechanical fault→clear/reset/FM and Power→fault→stable restoration/FM both retain `(FAULTED, safety:fault)` while masked and expose FAULTED afterward. `ems/safety/service.py:262`, `ems/safety/states.py:33`; `tests/safety/test_correctness_audit.py:21`, both variants. |
| PASS | Isolated A does not stop healthy B/C/D or E/F/G/H: `ems/safety/service.py:76`, `:84`; `tests/safety/test_correctness_audit.py:48`. |
| PASS | Precedence and exact owner/state release remain independent: `ems/safety/states.py:28`, `:33`, `ems/safety/service.py:70`; all 28 precedence-pair tests and `tests/safety/test_correctness_audit.py:57`. |
| PASS | New emergencies invalidate stored attestations; prior/early FM confirmation is not a saved unlock. `ems/safety/service.py:114`, `:142`, `:241`; `tests/safety/test_correctness_audit.py:71`, `:127` and existing mains stability boundary tests. |
| PASS | Strictly older clear/status/readiness events cannot erase newer conditions; repeating a fault cannot discard stronger entrapment facts. `ems/safety/service.py:101`, `:262`; audit tests at `:98`, `:115`, `:144`, `:177`, `:189`. Equal-time/re-stamped replay still needs contract correlation. |
| PASS | Real ordinary dispatch guard issues zero commands during fire, after physical reset, and after attestations until explicit FM confirmation, on the 64-car design-limit layout. `tests/safety/test_correctness_audit.py:151`. PR #16 medical/new-VIP commands also remain blocked through locked recovery in `tests/safety/test_priority_pr16_audit.py:121`, both variants. |
| PASS | FM recovery clears only Safety's internal emergency holds, preserving Priority Maintenance and another operator's Independent Service, while separately faulted A stays excluded. `tests/safety/test_priority_pr16_audit.py:165`, both variants. |
| PASS | A goods terminal event cannot clear Safety's independent controller fault. `tests/safety/test_priority_pr16_audit.py:215`. |

The only behavior/check update in this stage is Part 4's now-obsolete SIM-08 goods blocker.
`ems/sim/scenarios.py:279` consumes the approved event. Negative tests at
`tests/safety/test_updated_contract_evidence.py:15` and `:23` reject a wrong car, wrong
reason, non-cancellation, wrong time, missing active move and restart command. The actual
PR #16 fixture at `tests/safety/test_priority_pr16_audit.py:199` proves valid cancellation
evidence passes and duplicate terminal publication fails. This does not promote full SIM-08
readiness.

## 5. Current PR #16 compatibility: executable results

The clean detached worktree is source-byte-verified against Git commit `4a7b113` with a
read-only Git archive before importing its package under an isolated name. Every Priority
`.py` file is verified. This uses current main's real contracts, Simulator and local Safety;
it does not alter or merge Priority. The Access fixture delegates `permission` to the real
mock ACS, using actual approved staff IDs. No real Access/Dispatch integration is claimed.

| Status | Finding and current production location | Test evidence |
|---|---|---|
| PASS | Approved protocol methods, BR-10 ordering, accessible dwell and merged staff roles. `ems/priority/service.py`, `policy.py`, `auth.py`. | `tests/safety/test_priority_pr16_audit.py:105`. |
| PASS | Active VIP and goods operations cancel on fire/power with no attempted controller command. Exactly one GoodsMoveEnded has the matching move/car, `cancelled=True`, `reason=emergency:fire/power`, at the alarm; old callbacks do not restart it. `ems/priority/service.py` emergency handlers; `goods.py:205`, `:262`. | `tests/safety/test_priority_pr16_audit.py:121`, both variants, advancing through t=650 after FM. |
| PASS — earlier blocker resolved | PR #16 consumes CarStateChanged (`ems/priority/service.py:414`) and retries deferred flag cleanup (`:177`) after FM recovery. No manual retry is needed; no cleanup is sent before confirmation. | `tests/safety/test_priority_pr16_audit.py:149`, both variants. This supersedes historical P5-04 in AUDIT.md. |
| PASS | Normal release tokens and higher-priority state protection; fresh VIP/medical candidates exclude Faulted/Maintenance cars; terminal goods events do not erase physical faults. `ems/priority/service.py:137`, `:152`, `:194`. | Tests at `:165`, `:180`, `:215`; positive normal completion at `:190` also emits one terminal event. |
| PASS, static publisher check plus runtime event checks | Production Priority publishes only approved PriorityTripStarted/Ended, GoodsMoveScheduled/Ended, or shared PanelMessage/AlertRaised. Safety retains CarStateChanged ownership. | AST check `tests/safety/test_priority_pr16_audit.py:224` and real terminal-event/cancellation assertions above. |
| **FAIL — PR16-01** | VIP→medical and VIP→send-to-lobby replace Hold.kind without transferring Safety's acquisition owner. `ems/priority/medical.py:56`, `:125`; release uses the changed kind at `service.py:152`. The original `priority:vip` hold remains after the replacement finishes. Safety must not weaken owner isolation to mask this. | `tests/safety/test_priority_pr16_audit.py:242`, two strict xfails. Unmasked failures reach `state(car) is GROUP_SERVICE` with actual state VIP_EXCLUSIVE. |
| **FAIL — PR16-02** | The held-VIP exception in `ems/priority/service.py:194` bypasses authoritative state checks; medical preemption can choose a car with a higher Faulted/Maintenance hold while telemetry is physically healthy. | `tests/safety/test_priority_pr16_audit.py:258`, two strict xfails. Unmasked result chooses E despite its authoritative isolation. |
| **FAIL / contract-admission gap — PR16-03** | `ems/priority/vip.py:51` returns cached E for an interrupted VIP claim while Safety is active. The approved protocol says idempotent per call ID but does not define cache invalidation; a cached ID must not be treated as fresh assignment authorization. Priority/Dispatch must revalidate or the tech lead must clarify that boundary. This finding does not assert that every cached answer sends a controller command. | `tests/safety/test_priority_pr16_audit.py:270`, fire/power strict xfails. Both unmasked assertions observe E instead of the desired safe no-claim result. |
| **FAIL — PR16-04** | Priority's local fire latch clears only on FirefighterService off, `ems/priority/service.py:373`. The design-limit building has no service group, so after physical reset and valid Safety FM recovery, Priority stays in emergency mode. | `tests/safety/test_priority_pr16_audit.py:279`, one strict xfail. |

There are **seven** strict expected-failure instances across the four current failure groups.
They were also run with `--runxfail`: **12 passed, 7 failed** at the stated assertions, not
at setup assumptions. They are **not compatibility passes**. No teammate code was edited
and no existing test assertion was relaxed.

## 6. Scenario status and remaining blockers

| Scenario | Full acceptance | Verified available evidence / remaining blocker |
|---|---|---|
| SIM-08 | **BLOCKED** | Safety's zero-command/isolated-car subcriteria pass seeds 1–10. Actual PR #16 goods cancellation now has executable component evidence via GoodsMoveEnded. Main still has StubPriority; its standard scenario does not exercise a real active move. FM/controller recovery and optional lockdown integration remain absent. |
| SIM-13 | **BLOCKED** | Overload bursts and existing Monitor bulky-item flags pass seeds 1–10. Dispatch's step-out/next-car message and waiting-call reassignment remain missing. |
| SIM-14 | **BLOCKED** | Failed generator, exactly one missing rescue, urgent alert by 60 s, and no automatic recovery pass seeds 1–10. Live authenticated FM/controller-normal connection is absent. |
| SIM-15 | **BLOCKED** | Actual occupied brake fault, urgent entrapment/contractor output within 10 s, and zero commands to faulted car pass seeds 1–10. Real Dispatch waiting-call reassignment remains missing. |
| SIM-17 | **BLOCKED** | Local duplicate/rate-limit policies are tested; real pre-admission Access classification, duplicate proof, exits/assistance enforcement are not connected. PR #14 is still unimplemented in its committed source. |

Full acceptance readiness flags were left false. No full scenario was marked PASS.
FR-52 capacity allocation and sequential parking remain blocked by the same physical-model
and contract gaps; no movement sequence was invented. Timestamp checks cannot authenticate
same-time/re-stamped replays or an unversioned external FM request. The new staff cards solve
role data, not that missing confirmation workflow.

## 7. Verification scope and results

All local tests below run on **synchronized main `6aab02e` plus preserved local Safety**.
The optional PR #16 package is loaded only in its audit fixtures. This is not the full
standalone PR #16 test collection and does not imply that PR was merged.

| Check | Result |
|---|---|
| Post-sync, unchanged local Safety baseline excluding optional archival audit | 68 passed, 2.65 s |
| Current PR #16 audit, before goods-evidence follow-up | 11 passed, 7 strict xfailed |
| Current PR #16 final unmasked investigation | 12 passed, 7 failed (intentional exposure of external defects) |
| Final targeted Safety + Safety scenario suite | **127 passed, 16 skipped, 7 strict xfailed**, 70.92 s |
| Final Safety-folder suite | **85 passed, 16 skipped, 7 strict xfailed**, 12.39 s |
| CI Ruff selection and focused Safety Ruff | PASS |
| `git diff --check` | PASS |
| Final full pytest suite | **334 passed, 17 skipped, 7 strict xfailed**, 200.10 s; no unexpected failures, existing Starlette/httpx warning only |

The 16 targeted skips are **only the explicitly disabled historical Priority snapshot tests**.
The current production target has no skipped cases in the enabled fixture. The full suite
also contains the existing SIM-01 placeholder skip. The seven expected failures are current
PR #16 integration defects. These categories must not be added together as acceptance passes.

```bash
cd /mnt/d/Projects/softcon-elevator-controller
source .venv/bin/activate
# Keep SAFETY_PRIORITY_AUDIT_TREE unset: that variable targets the archival snapshot.
export SAFETY_PR16_TREE="$PWD/.tmp/safety-audit-pr16-4a7b113"
pytest tests/safety/ -q
pytest tests/safety/ tests/scenarios/test_safety_scenarios.py -q
pytest -q
ruff check --select E9,F63,F7,F82 .
ruff check ems/safety tests/safety
git diff --check

# Show the actual external failures for investigation:
pytest tests/safety/test_priority_pr16_audit.py --runxfail -q --tb=short
```

## 8. Foundation PR recommendation

**Ready for a Draft Part 4 foundation PR**, with the final regression run passing and
this report and the existing requirement limits attached. The base is now current main,
local work is recoverable, existing Safety invariants hold, and approved interfaces were
tested against the correct production Priority target.

This is **not approval to merge the combined application as complete Safety**: PR #16 has
the four reproduced failure groups, Access is still broken in PR #14, Dispatch remains a
stub, and recovery/power contracts remain incomplete. PR #13 should remain historical
context, not the integration target or a reason to preserve a now-resolved cleanup blocker.
