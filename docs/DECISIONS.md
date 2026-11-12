TASK: "Part 1, Session D": make every Appendix B scenario setup executable
(time of day, 24 h weekday runs, Eco surge, design-limit SIM-10) in the EMS repo.
Sessions A-C are merged into main.

SCOPE RULE: you implement ONLY Part 1 (simulator, scenario setup, integration). Do not
write dispatch, access, safety, priority or reporting logic, and no pass checks except
SIM-09/SIM-16 (already done). Other parts' checks stay empty slots.

SETUP: git checkout main && git pull && pip install -e ".[dev]" && pytest -q (green).
Read docs/CONTRACT.md, docs/DECISIONS.md (D-01, D-03), ems/core/, ems/sim/ first.
If this brief disagrees with the repo, the repo wins; tell me.

=== REPO RULES (mandatory) ===
- Branch part1/scenario-setup from main; never commit to main; push when done (I open
  the PR in the web UI; title "SIM-SETUP time of day, weekday runs, SIM-10 setup").
- NO AI attribution anywhere (no Co-Authored-By, no "Generated with/by" in commits,
  comments, docs). Commit as the configured git user only.
- No data dumps, large files, caches, venvs, tokens, keys, passwords. runs/ gitignored.
- Allowed paths: ems/sim/, ems/core/app.py (build() signature only), ems/web/server.py,
  ems/web/static/runner/, ems/web/static/building/, tests/sim/, tests/scenarios/,
  tests/web/, docs/CONTRACT.md (services table + changelog), README.md (milestones).
  Not: ems/contracts/, other parts' folders, dashboard/ or panel/ pages.
- ctx.clock / ctx.rng only (the existing perf_counter in dispatch_guard is the only
  exception). Deterministic per seed. pytest -q green; mark slow tests; keep the default
  suite under ~3 minutes.

=== 1. TIME OF DAY ===
- build(seed=1, parts=None, start=None): pass `start` (datetime) to SimClock; default
  stays Monday 2026-11-16 00:00. Keep backward compatible.
- Each scenario run's start time of day is applied through this (SIM-08 starts 16:30
  so its alarm lands on the 17:00 Rush transition).
- Traffic and anything time-based use ctx.clock.now_dt() for time of day.
- docs/CONTRACT.md changelog: "Time of day = ctx.clock.now_dt(); runs may start at any
  time. Parts reading schedules must use it, never assume t=0 is midnight."

=== 2. 24 h WEEKDAY RUN (SIM-05) ===
- Harness supports a run whose traffic follows the weekday schedule mode already in
  traffic.py (profile per block: Rush 07:00-09:30 up_peak, 11:30-13:30 lunch,
  17:00-18:30 evening; Balanced blocks balanced; Eco blocks eco_quiet), using the
  D-01 peak rate (35/min).
- RunResult gains per-block metrics (block label, start, end, profile, avg/max wait,
  calls) so the Part 2 owner can check each mode's targets, plus "stranded" = calls
  created before a block change and still not delivered 30 min after it.
- Add "eco_quiet" as a real profile name in traffic.py (alias of the 2/min balanced-mix
  profile) and use it in scenarios.py instead of the in-file rename.
- Test (slow): seeds 1-2 complete, every block present, deterministic.

=== 3. SIM-04 ECO SURGE ===
Appendix B: "a separate Eco surge phase at 75 arrivals/minute for 15 minutes: check
FR-14 triggers". The building must already be in an Eco period with quiet traffic:
start the run at 21:00 (Eco block), 10 min eco_quiet warm-up, then 75/min up_peak-mix
for 15 min, then up to 30 min to clear. 75/min is intentional here (DECISIONS D-01:
surge keeps 75/min). Record in the manifest that this run is outside the normal
envelope. The Eco/Balanced pair runs also start at 21:00 with the same seed.

=== 4. SIM-10 DESIGN-LIMIT SETUP (DECISIONS D-03) ===
Appendix B: 64 cars, 8 groups, 128 stops, 200 tenants, 50 concurrent dashboard users,
400 calls and 400 credential lookups per minute for 60 measured minutes, 2,000 camera
events/day equivalent; it checks software latency, not passenger capacity.
- Building layout must be configurable per run: add a BuildingSpec (groups -> cars,
  floors served) in ems/sim/, default = the reference building from config.GROUPS.
  Store the active one at ctx.services["building"]; the simulator, population, traffic
  and nearest-car fallback read it from there instead of config.GROUPS.
  docs/CONTRACT.md changelog: "Read the building layout from ctx.services['building'];
  do not import config.GROUPS directly (SIM-10 uses a different layout)."
- Design-limit spec: 8 groups x 8 cars = 64 cars, 128 stops (e.g. B2-B1, L1-L126 split
  into 8 zones of 16 floors, every group also serving L1), 200 tenants, population
  scaled to ~20,000 people (state your choice in the docstring).
- Workload: 400 calls/min and 400 credential lookups/min (each call's credential lookup
  counts; top up with extra lookups if needed), camera events at 2,000/day equivalent.
- Measurement hooks only (no pass check, that is Part 2's): record per-call assignment
  latency (dispatch_guard compute time per call), per-lookup ACS response time, camera
  event count; expose them in RunResult. Dashboard load: add
  ems/sim/dashboard_load.py that fires 50 concurrent GET /api/state requests in a loop
  against the FastAPI app (TestClient + threads) and records response times.
- 3-year history is NOT preloaded (D-03). If 60 measured minutes take longer than
  ~5 min wall time, use 15 measured minutes and record "measured_minutes" and the
  reason in the manifest; tell me the runtime either way.
- scenarios.py: SIM-10 gets its run setup; waiting_for becomes "Part 2 check".

=== 5. RUNNER + TESTS + README ===
- run_scenario executes every run type above; the runner page shows per-block results
  for SIM-05 and the latency summaries for SIM-10 (metrics only, no pass/fail for
  checks that are still empty).
- Tests: time-of-day start, weekday blocks, surge starts in an Eco block, BuildingSpec
  default equals the reference building (all existing tests still pass unchanged),
  design-limit layout counts (64/8/128/200), latency hooks populated (short run).
- README milestones: v0.4-scenario-setup.

=== DONE WHEN ===
pytest -q green; branch pushed. Reply with: files changed, SIM-05 per-block metrics for
seed 1 (baseline), SIM-04 surge summary, SIM-10 runtime and latency summary (p50/p99
assignment, p95 lookup, dashboard max), and every change other parts must know.
