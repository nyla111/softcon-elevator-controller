# Part 5 requirement traceability

Base: `origin/main` @ `8b4afdc` (2026-11-14). Sources in the repo: `scripts/requirements.csv`
(one-line titles), `docs/CONTRACT.md` (+ changelog), `docs/DECISIONS.md`,
`ems/contracts/config.py` (FR-33 600 s, FR-71 30 s, FR-72 6/h), `ems/contracts/interfaces.py`
docstrings, and the Appendix B texts copied into `ems/sim/scenarios.py`. **The SRS v0.13 body and
Appendix B are not in the repo and were not provided**: behaviour beyond those sources is marked
"assumption" and listed as an open question (section 3). No threshold or limit value was invented.

Status words: **Unit** = verified with protocol doubles (`tests/priority/`); **Sim** = verified
against Part 1's real simulator with Part 5 stand-ins for Part 2/3/4; **Blocked** = needs a part or
contract change that does not exist yet; **Partial** = only the specified part is built.
No requirement is "integration verified with real Part 2/3/4 services": those parts are stubs.

## 1. Matrix

| Req | Source | Behaviour built | Owner | Depends on | Code | Test | Status / blocker |
|---|---|---|---|---|---|---|---|
| FR-08 | csv; SIM-11 text; config `accessible_dwell_s` | rank 2; `car_ok_for` free places >= party size; `door_dwell_s` = 10 s; one voice `PanelMessage` (EN+VI) per accessible assignment / re-assignment | P5 policy; P2 applies | P2 (CCR-01), door command (CCR-02) | `policy.py`, `service.py` (`_announce`, `car_ok_for`) | `test_policy`, `test_medical::test_accessible_voice...`, SIM-11 | Unit + Sim. **Blocked**: EMS door-time request (CCR-02); dispatch ordering/filter in real P2 (CCR-01). 10 s doors observed are the simulated controller's own rule |
| FR-30 | csv | `AlertRaised(high,"fault",["contractor","fm"])` once per fault episode; info on clear | P5 | P1 `ControllerFault` | `maintenance.py` | `test_maintenance`, SIM-06 | Unit + Sim |
| FR-31 | csv | counters from telemetry (door cycles, floors travelled, starts); reminder once per FM/technician threshold; reset at maintenance exit | P5 | thresholds: **spec gap** | `maintenance.py` | `test_maintenance` | Unit. **Partial**: no default thresholds (none in sources, Q5) |
| FR-32 | csv | technician (role from Access) enters/exits `MAINTENANCE`; close/re-open to new calls; reassign request; faulted car: state only | P5 | P3 roles (CCR-05), P4 states, P2 reassign | `maintenance.py` | `test_maintenance`, SIM-06 | Unit + Sim (staff card double) |
| FR-33 | csv; config 600 s | Rush (P2 mode) + another car of the group out -> FM approval, 600 s simulated timeout, late approval refused, deny | P5 | P2 `current_mode` | `maintenance.py` | `test_maintenance`, SIM-06 | Unit + Sim (mode from schedule double) |
| FR-36 | csv; `would_breach_ceiling_without` docstring | independent-service period = goods move on a passenger car: not in a Rush block (schedule) nor Rush at start (P2 mode), ceiling check at start | P5 | P2 | `goods.py` | `test_goods` | Unit. Assumption: Rush rule only for passenger cars (Q4) |
| FR-70 | csv | FM per-credential daily quota of exclusive trips, revoke; VIP role itself from ACS / P3 call priority | P5 | P3 | `service.py` | `test_vip::test_fr70...` | Unit. **Partial**: quota meaning not specified (Q1) |
| FR-71 | csv; config 30 s | empty car with ETA <= 30 s first (fastest), else fastest; refused / breaching cars skipped | P5 | P2 must assign the call to that car (CCR-01) | `policy.vip_candidates`, `service._start_vip` | `test_policy`, `test_vip`, SIM-12 | Unit + Sim |
| FR-72 | csv; config 6/h | 1 exclusive car per group (any kind); 6 starts per rolling hour `(t-3600, t]`, counted only after a successful start; ceiling check before each start; trip ends on delivery / cancel / re-assign / not boarded in one door cycle / fault / emergency / medical | P5 | P2 `would_breach_ceiling_without` (stub answers False) | `service.py` | `test_vip`, SIM-12 | Unit + Sim. Ceiling answers are stub (**Blocked** on P2). Assumption: 6/h building-wide (Q2) |
| FR-76 | csv; BR-10 | `rank`, `order` (priority, then created_at) | P5 policy; P2 applies | P2 (CCR-01) | `policy.py` | `test_policy`, SIM-11 (blocked criterion) | Unit. **Blocked** in dispatch |
| FR-80 | csv | FM shows limits (values given by FM / P6 config) on service-car displays, again at move start | P5 text; P6 values | values: **spec gap** | `goods.py` | `test_goods` | Unit. Values not defined by Part 5 (Q6) |
| FR-81 | csv | tenant admin request (tenant from Access), window/overlap/credential checks, FM approve/reject, cancel, start/end on the clock | P5 | P3 roles (CCR-05) | `goods.py` | `test_goods`, demo | Unit + demo |
| FR-82 | csv; `GoodsMoveScheduled` | `GoodsMoveScheduled.credential_ids`; `service_car_accepts()` during the active move | P5 data; **P3 enforces** | P3; CCR-04 for early end | `goods.py` | `test_goods`, demo | Unit. Enforcement at panels is P3's |
| FR-127 | csv; SIM-18 text | security request; fastest healthy car (empty first); VIP trips of the served groups yield (VIP rider keeps going to their floor); doors held at origin; depart; finish; re-dispatch if the car is lost before pick-up | P5 | P4 state (CCR-03) | `service.py` | `test_medical`, SIM-18 | Unit + Sim |
| FR-128 | csv | security sends a car to the lobby, doors held, riders keep their destinations, finish returns it | P5 | P6 camera alert reviewed by security | `service.py` | `test_medical` | Unit. Not auto-started by camera events (Q7) |
| FR-04 | csv (Part 2) | not built by P5; P5 calls `reassign_calls_of` | **P2** | - | - | SIM-06 criterion | **Blocked** (P2 stub) |
| FR-84 | csv (Part 6) | not built by P5 (P6 already voices the redirect) | **P6** | - | - | - | out of scope |

## 2. Scenarios owned by Part 5

| Scenario | Part 5 criteria (seeds 1-10) | Not Part 5 / blocked | Result |
|---|---|---|---|
| SIM-06 | all pass | FR-04 re-assignment (P2), degraded marking (P6 on P2's event), integration | FAIL (blocked) |
| SIM-11 | all pass | EMS door request (CCR-02), dispatch order (P2), Rush wait targets (stand-in dispatcher misses them), integration | FAIL |
| SIM-12 | all pass | ordinary calls <= 60 s (stand-in dispatcher), integration | FAIL |
| SIM-18 | all pass | integration | FAIL (blocked) |

Per-seed numbers: `VERIFICATION.md`.

## 3. Open questions (need Minh / Nhi / Team 3)

1. FR-70: what is a VIP "quota" (per credential per day? per tenant?) and who grants VIP roles?
2. FR-72: is "6 exclusive starts per rolling hour" per building (built) or per group?
3. FR-127: should a VIP trip yield when the medical trip uses another car (built: yes, for every
   group serving the medical floors), and should an empty car be preferred over a faster busy one
   (built: yes)?
4. FR-36: does "not in Rush" apply to the service car? (built: passenger cars only; SIM-08 has a
   goods move active at the 17:00 Rush transition). What does "free" mean (no charge / free slots)?
5. FR-31: counter names and reminder thresholds.
6. FR-80: the limit values. Part 6 configures 25 kg / 1.5 m / 0.6 m; is that from the SRS?
7. FR-128: who may send a car to the lobby (built: security), and when does it end (built:
   security finishes it)?
8. FR-08: does "free capacity" mean extra space for a wheelchair (built: room for the party)?
9. FR-33: which cars count as "out" (built: faulted, disconnected, MAINTENANCE,
   INDEPENDENT_SERVICE, FIRE_RECALL, EMERGENCY_POWER, and Part 5 maintenance / goods holds)?
10. VIP not on board one full door cycle after the car opened at their floor (car full or VIP
    absent): built as "exclusive hold ends, the call stays with Part 2" (FR-122 analogy).
11. Medical: is "patient on board" confirmed by security (built) or by another signal?
