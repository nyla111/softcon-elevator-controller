# Part 5 requirement traceability

Base: `origin/main` @ `8b4afdc` (re-checked 2026-11-14). Sources in the repo: `scripts/requirements.csv`
(one-line titles), `docs/CONTRACT.md` (+ changelog), `docs/DECISIONS.md`,
`ems/contracts/config.py` (FR-33 600 s, FR-71 30 s, FR-72 6/h), `ems/contracts/interfaces.py`
docstrings, and the Appendix B texts copied into `ems/sim/scenarios.py`. **The SRS v0.13 body and
Appendix B are not in the repo and were not provided**: behaviour beyond those sources is marked
"assumption" and listed as an open question (section 3). No threshold or limit value was invented.

Status words (per requirement, Part 5's share):
**Complete** = built and verified by Part 5 tests (unit with doubles and/or the real simulator);
**Partial** = only the specified part is built (spec gap); **Blocked** = an owner outside Part 5
must act (dependency or contract); **Not verified** = built but no test with the real dependency
is possible yet. Integration with real Part 2/3/4 services is **not verified for any
requirement** (they are stubs on main).

## 1. Matrix

| Req | Source | Behaviour built | Owner | Depends on | Code | Test | Status |
|---|---|---|---|---|---|---|---|
| FR-08 | csv; SIM-11 text; config `accessible_dwell_s` | rank 2; `car_ok_for` = room when the car opens at the floor (riders who stay + assigned calls); door time 10 s (`door_dwell_s`); one voice `PanelMessage` (EN+VI) per assignment / re-assignment; VIP never takes a car an accessible passenger waits for | P5 policy; P2 applies | P2 (#9), door command (#10) | `policy.py`, `service.py` | `test_policy`, `test_vip::test_fr08_capacity...`, `test_medical::test_accessible_voice...`, SIM-11 (capacity, first-opening boarding, voice) | Complete (Part 5 policy, sim-verified 10/10 seeds); **Blocked**: EMS door request (#10), use in real dispatch (#9). The 10 s doors observed are the simulated controller's rule |
| FR-30 | csv | `AlertRaised(high,"fault",["contractor","fm"])` once per fault episode; info on clear | P5 | P1 events | `maintenance.py` | `test_maintenance`, SIM-06 | Complete |
| FR-31 | csv | counters from telemetry (door cycles, floors travelled, starts); one reminder per threshold; reset at maintenance exit | P5 | thresholds: spec gap | `maintenance.py` | `test_maintenance` | Partial (no thresholds in sources, Q5); unit only |
| FR-32 | csv | technician (role from Access) in / out of `MAINTENANCE`; close / re-open; faulted car: state only; exit never overrides a Safety state | P5 | P3 roles (#12), P4 | `maintenance.py` | `test_maintenance`, SIM-06 | Complete on Part 5's side; Not verified with real Access / Safety |
| FR-33 | csv; config 600 s | Rush (P2 mode) + another car of the group out -> FM approval; 600 s timeout; late approval and deny refused; approval revalidated against the car's current state | P5 | P2 `current_mode` | `maintenance.py` | `test_maintenance`, SIM-06 (approve, deny, timeout; 10/10 seeds) | Complete (Rush from the schedule double); Not verified with real Part 2 modes |
| FR-36 | csv; interfaces docstring | independent-service period = goods move on a passenger car: no Rush block, not Rush at start, ceiling check at start | P5 | P2 | `goods.py` | `test_goods` | Partial (assumption Q4); unit only |
| FR-70 | csv | FM per-credential daily quota of exclusive trips, revoke; VIP role from P3's call priority | P5 | P3 | `service.py` | `test_vip::test_fr70...` | Partial (quota meaning, Q1) |
| FR-71 | csv; config 30 s | empty car (no rider, no waiting call) with ETA <= 30 s first, else fastest; skips refused / breaching / not-movable cars | P5 | P2 caller (#9) | `policy.vip_candidates`, `service._start_vip` | `test_policy`, `test_vip`, SIM-12 | Complete on Part 5's side (stand-in caller); **Blocked**: no real caller |
| FR-72 | csv; config 6/h | 1 exclusive car per group (any kind); 6 starts per rolling hour `(t-3600, t]`, counted after a successful start; ceiling check; exclusivity requires waiting calls moved; cleanup on every trip end | P5 | P2 ceiling answer, `reassign_calls_of` | `service.py` | `test_vip`, SIM-12 (caps exercised and justified, no other boarding; 10/10 seeds) | Complete on Part 5's side; ceiling answers **Not verified** (stub), assumption 6/h per building (Q2) |
| FR-76 | csv; BR-10 | `rank` / `order`; Part 5 applies medical > VIP and accessible > VIP in its own car choices | P5 policy; P2 applies | P2 (#9) | `policy.py`, `service.py` | `test_policy`, SIM-11, SIM-18 | Partial: **Blocked** in dispatch ordering (P2) |
| FR-80 | csv | FM shows limits (FM / P6 values) on service-car displays, again at move start | P5 text; P6 values | values: spec gap | `goods.py` | `test_goods`, demo | Partial (values, Q6) |
| FR-81 | csv | tenant admin request, window / overlap / credential checks, FM approve / reject, cancel, start / end on the clock with epoch guards | P5 | P3 roles (#12) | `goods.py` | `test_goods`, demo | Complete on Part 5's side; Not verified with real Access |
| FR-82 | csv; `GoodsMoveScheduled` | `GoodsMoveScheduled.credential_ids`; `service_car_accepts()` for the active move only | P5 data; P3 enforces | P3; #11 | `goods.py` | `test_goods`, demo | Part 5 data complete; **Blocked**: enforcement (P3), early end event (#11) |
| FR-127 | csv; SIM-18 text | security request; fastest healthy car (empty, nobody waiting at that floor, first); VIP trips of the served groups yield; boarded VIP keeps going; unboarded VIP call kept (pick-up re-registered); doors held at origin; depart; finish; re-dispatch if the car is lost before pick-up | P5 | P4 state (CCR-03) | `service.py` | `test_medical`, `test_with_simulator`, SIM-18 (10/10 seeds, event order) | Complete on Part 5's side; Not verified with real Safety |
| FR-128 | csv | security sends a car to the lobby, doors held, riders keep their destinations, finish returns it | P5 | P6 alert reviewed by security | `service.py` | `test_medical`, `test_with_simulator::test_send_to_lobby_on_the_real_simulator...` | Complete on Part 5's side (workflow assumptions Q7) |
| FR-04 | csv (Part 2) | not built by P5; P5 calls `reassign_calls_of` and uses the answer | **P2** | - | - | SIM-06 criterion | Blocked (P2) |
| FR-84 | csv (Part 6) | not built by P5 | **P6** | - | - | - | out of scope |

## 2. Scenarios owned by Part 5 (seeds 1-10, details in VERIFICATION.md)

| Scenario | Part 5 criteria | Failing (owner) | Result |
|---|---|---|---|
| SIM-06 | all pass, 10/10 | FR-04 re-assignment (Nam); degraded marking (Nam, rule Mai); integration | FAIL 10/10 |
| SIM-11 | all pass, 10/10 | EMS door request (#10, Nhi); dispatch order (Nam); Rush wait targets (Nam; fails without Part 5 too); integration | FAIL 10/10 |
| SIM-12 | all pass, 10/10 | ordinary calls <= 60 s (Nam; fails without Part 5 too); integration | FAIL 10/10 |
| SIM-18 | all pass, 10/10 | integration | FAIL 10/10 |

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
    absent): built as "exclusive hold ends; the pick-up stays on the same car as an ordinary
    stop" (FR-122 analogy). Should Part 2 re-assign instead?
11. Medical: is "patient on board" confirmed by security (built) or by another signal?
