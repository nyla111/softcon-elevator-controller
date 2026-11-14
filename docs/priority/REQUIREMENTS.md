# Part 5 requirement traceability (PLANNING VERSION)

Base: `origin/main` @ `8b4afdc`. This planning matrix is written before any code and is
replaced by the verified matrix once the implementation and tests exist.

Sources available in the repo: `scripts/requirements.csv` (titles only), `docs/CONTRACT.md`,
`docs/DECISIONS.md`, `ems/contracts/config.py` (FR-33, FR-71, FR-72 numbers), scenario
registry texts in `ems/sim/scenarios.py` (Appendix B criteria). **The SRS v0.13 text and
Appendix B are not in the repo**; anything not in those files is marked "spec gap".

State of the other parts at the base commit: Part 2 (dispatch), Part 3 (access) and Part 4
(safety) are empty packages, so `dispatcher`, `access` and `car_states` are the contract stubs.

| Req | Source | Planned behaviour | Owner | Depends on | Status (plan) |
|---|---|---|---|---|---|
| FR-08 | csv, SIM-11 text | priority rank, free-capacity check, 10 s dwell, voice | P5 policy; P2 applies | P2 hook; no dwell command in ControllerAdapter | to do |
| FR-30 | csv | AlertRaised to contractor + FM once per fault episode | P5 | P1 ControllerFault | to do |
| FR-31 | csv | counters from DoorsOpened / status deltas; reminders at FM-set thresholds | P5 | thresholds: spec gap | to do |
| FR-32 | csv | technician in/out via Access roles; MAINTENANCE via car_states | P5 | P3 roles, P4 states | to do |
| FR-33 | csv, config | 2nd car out in Rush -> FM approval, 600 s timeout | P5 | P2 current_mode | to do |
| FR-36 | csv, interfaces docstring | independent-service periods, not in Rush, ceiling check | P5 | P2 would_breach..., P4 | to do |
| FR-70 | csv | VIP grants/quotas | P5 | spec gap | to do (partial) |
| FR-71 | csv, config | empty car within 30 s else fastest | P5 | P2 must assign VIP call to the car | to do |
| FR-72 | csv, config | 1 exclusive car/group, 6 starts/rolling hour, ceiling check | P5 | P2 would_breach... | to do |
| FR-76 | csv, BR-10 | medical > accessible > VIP > others | P5 policy; P2 applies | P2 | to do |
| FR-80 | csv | house-rule limits on panels | P5 text; values from P6 config | values: spec gap | to do |
| FR-81 | csv | tenant request + FM approval | P5 | P3 roles | to do |
| FR-82 | csv, GoodsMoveScheduled | only the move's credentials on the car | P5 data; P3 enforces | P3 | to do |
| FR-127 | csv, SIM-18 text | medical express above VIP | P5 | P4 states | to do |
| FR-128 | csv | send car to lobby, doors open (security action) | P5 | P6 camera alert -> security | to do |
| FR-04 | csv | reassign waiting calls | **P2** | - | not Part 5 (SIM-06 checks it) |
| FR-84 | csv | bulky-item redirect | **P6** (already in monitor) | - | not Part 5 |
