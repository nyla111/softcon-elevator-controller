# Part 4 verification — 2026-11-14

This is the historical foundation verification. The earlier focused correctness audit is
in [AUDIT.md](AUDIT.md). Current synchronized-main/production-PR results and test totals are
in [SYNC_AUDIT.md](SYNC_AUDIT.md), dated 2026-11-15.

## Repository synchronization

Started on `part4/safety` with a clean tree at
`8b4afdcaf27f3fb242ad3801495ffc5165c7d25a`. Ran `git fetch --prune origin`, which discovered
`origin/part5/minh-priority-service-car`. Fetched `origin/main` remained at the same commit;
`git merge --ff-only origin/main` reported already up to date. No divergence or conflicts.
No commit, push, PR, destructive operation, or modification of main was performed.

The source includes Part 1 simulator/scenario setup and Part 6 Monitor/dashboard work.
Dispatch, Access and Priority on main remain stubs. The new Part 5 branch was inspected
read-only for ownership compatibility and was not merged.

## Baseline and focused verification

All tests ran in the existing WSL `.venv` after synchronization and contract inspection.

| Command | Result |
|---|---|
| Baseline `pytest -q` before editing | 204 passed, 1 failed, 1 skipped, 208.74 s |
| Initial Safety/scenario/registry tests | 104 passed, 68.28 s |
| Final `pytest tests/safety/ -q` after review | 53 passed, 1.46 s |
| Final full `pytest -q` | 299 passed, 1 skipped, 244.56 s; no failures |
| `ruff check --select E9,F63,F7,F82 .` (CI command) | PASS |
| Full Ruff check on new Safety modules/tests and changed registry test | PASS |
| `git diff --check` | PASS |

Baseline failure: `tests/web/test_web.py::test_50_concurrent_state_requests_return_within_1_s`.
All 150 requests returned successfully; the maximum response was 1.074868314 s against
the 1 s assertion. No Safety changes existed when this was measured. The assertion and
web implementation were left untouched. The existing Starlette/httpx deprecation warning
also predates these changes. The same dashboard timing test passed in the final full run;
its environment-sensitive baseline failure was not addressed by changing its assertion.
Existing broader Ruff style warnings in the shared scenario
file were not refactored; CI error checks pass.

## Scenario status

The full-length simulator tests execute each Safety subcriterion below for every seed 1–10.
Their success does **not** represent full Appendix B acceptance. Each registered scenario
stays `ready=False`; `passed=None` is preserved by the runner even when partial criteria are
requested. Tests also require the unmet cross-part criteria to remain visible.

| Scenario | Full status | Verified evidence | Remaining acceptance blocker |
|---|---|---|---|
| SIM-08 | BLOCKED | Fire at 17:00 Rush transition, zero attempted EMS commands throughout handling (including after physical reset while recovery is held), isolated B/C excluded, seeds 1–10 | Active goods cancellation and end-to-end FM/controller-normal recovery connection |
| SIM-13 | BLOCKED | At least three overloads exercised and both bulky-item incidents flagged by existing Monitor, seeds 1–10 | Dispatch step-out/next-car message within 1 s and waiting-call reassignment |
| SIM-14 | BLOCKED | Failed generator, exactly one missing rescue, critical POSSIBLE ENTRAPMENT by 60 s, no automatic recovery on mains return, seeds 1–10 | End-to-end FM/controller-normal recovery connection |
| SIM-15 | BLOCKED | Actual occupied brake fault between floors, critical entrapment/contractor alert within 10 s, no attempted command to faulted car, seeds 1–10 | Waiting-call reassignment by Dispatch |
| SIM-17 | BLOCKED | Independent policy tests prove sixth-action rejection, rolling-window boundary, exemptions, and conservative duplicate decisions | Access pre-acceptance action classification and duplicate proof; full panel scenario is not implemented |

Explicit FM recovery through the internal entry points, 120 s stability and restart boundaries,
all 28 precedence pairs, independent owners, fault/disconnection overlap, stuck-door cases,
stale rescue reports, repeat-outage deadline isolation and the 64-car building have unit tests.
The local FM tests are trusted integration fixtures, not evidence of an authenticated UI.

## Review and merge limits

New implementation: `ems/safety/states.py`, `service.py`, `call_policy.py`; registration in
`ems/safety/__init__.py`. Scenario injection/checks and partial-result handling are in the
authorized `ems/sim/scenarios.py`. Tests are added in `tests/safety/` and `tests/scenarios/`.
The existing registry test was updated to require available but blocked Part 4 checks while
retaining the assertion that only SIM-09 and SIM-16 have complete acceptance integrations.
No existing tests were deleted, and no application teammate module was edited.

Safety publishes only its state event and shared human-output events, sends no controller
commands, uses simulated time, adds no random draws, and emits no credentials/passenger IDs.
It relies on Dispatch for waiting-call/overload ownership and Monitor for camera ownership,
as detailed in [INTEGRATION.md](INTEGRATION.md).

FR-52 limited generator operation and sequential parking are **not implemented** because
the shared model has no capacity/allocation interlock or completion contract. GeneratorReady
physically resumes all simulator cars; issuing EMS parking requests cannot establish capacity
compliance. Missing approval proposals, including refusal handling required for a future
parking sequence, are listed precisely in [INTEGRATION.md](INTEGRATION.md).

The service is reviewable as an isolated Safety foundation. It is not a completed Safety
acceptance release: normal traffic works through the existing dispatch guard, but demo fire
or power clear/reset intentionally leaves a recovery hold because no FM/controller safety
adapter is connected. A complete operational merge requires agreement on that behavior and
the missing recovery/dispatch/priority integrations. Contracts and core are unchanged.

Suggested PR title: **BR-06 FR-50 FR-55 FR-110 FR-111 FR-112: Safety states, recovery gates and scenario evidence**.
