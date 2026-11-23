# Part 2 Dispatch & modes

Package `ems/dispatch`, service `ctx.services["dispatcher"]` (the `Dispatcher` protocol in
`contracts/interfaces.py`). The dispatch plug-in (FR-06) is `EtdEngine`, always called through
Part 1's FR-105 guard (`ctx.services["dispatch_guard"].choose_car`).

## What it does
| Requirement | Behaviour | Module |
|---|---|---|
| FR-01/02, UC-01 | `CallRequested` -> candidate cars -> engine (via guard) -> `register_stop` -> `CallAssigned`; one car per hall call | `service.py`, `assign.py` |
| FR-04 | fault, lost link, non-dispatchable car state, Part 5 removal: waiting calls move with `CallReassigned`, original `created_at` kept; calls that cannot move yet are retried every second | `assign.py` |
| FR-05 | no new hall call for a car at or above 80% load | `assign.py` |
| FR-06 | `EtdEngine` behind `DispatchEngine` (replaceable) | `engine.py` |
| FR-08, BR-10 | `claim_call` for VIP; `car_ok_for` for accessible calls; `door_dwell_s` as `min_dwell_s`; pending queue ordered by Part 5 `order()` | `assign.py` |
| FR-10/11/12 | Rush / Balanced / Eco; weekday, weekend and holiday schedule; FM manual switch until a time or the next scheduled change (`set_manual_mode`) | `modes.py` |
| FR-13, BR-01 | ceiling penalties in the cost; `WaitCeilingRisk` at 90% of the ceiling; Eco wakes a Standby car; breaches logged with cause, FM alerted (at most one alert per group per 5 min) | `engine.py`, `ceiling.py` |
| FR-14, BR-05 | Eco -> Balanced when > 10 calls wait for 30 s or the 5-min average wait > 45 s; FM alert; stays until the next scheduled change | `ceiling.py`, `modes.py` |
| FR-15, BR-02 | a mode change never cancels a call or a trip | `modes.py` |
| FR-100 | overload: step-out message naming the next car (car display + panels) at once, then the calls left at that floor are re-assigned | `assign.py` |
| FR-101 | the trip model never puts more persons on one trip than rated (or the FM limit, `set_persons_limit`) | `model.py`, `engine.py` |
| 4.3 table | parking: Rush -> lobby (up-peak) / spread over the zone (down-peak) / mixed; Balanced -> lobby + busiest recent floors; Eco -> stay where the last trip ended, 2 cars per group in service, the rest in Standby | `policy.py` |
| Pauses | EMS server down (controllers serve calls), fire (until cleared and `safety_active()` is False, FR-55), mains lost (FR-110): no command sent, calls queue; after fire every waiting call is re-assigned | `pauses.py` |

## The algorithm (ETD, estimated time to destination)
The dispatcher knows every passenger's destination (destination panels); the controller only learns
it when the passenger boards. So the dispatcher keeps a book of who rides and who waits for each car,
and `model.replay()` replays the controller's stop logic (simple collective, Appendix B timings) to
predict every pick-up and arrival time. Prediction error measured at up-peak: about 1.5 s on average.

## NFR-W1 wear policy and real-traffic validation

The engine computes the normal mode-dependent ETD cost for every eligible car first. It then
considers cars whose ETD cost is within **15** of the best car (**5** in Eco) **and** whose predicted pickup is
at most **5 seconds** later. Among these candidates it assigns the least-used car by the Part 5
six-month distance counter. Both bounds preserve wait-time priority; wear cannot select a car
with an arbitrarily long pickup. In Eco, an idle active car is exchanged with a less-used
standby car when their distance gap reaches **20 floors**, no more often than every **900 seconds**,
only when all active cars are idle, no calls are open, and the two cars are within **10 floors**.
Busy cars are never put into standby by this exchange.

Measured with `python -m ems.sim.long_run --days 1 --seed 1` on the same simulated weekday
(2026-12-01, 25,220 passengers) before and after this change:

| Policy | LOW imbalance | HIGH imbalance | Average wait |
|---|---:|---:|---:|
| Wear preference disabled | 30.55% | 39.65% | 22.95 s |
| Previous 5-cost shortlist; no Eco rotation | 30.81% | 37.65% | 22.8 s |
| Final 15-cost (5 in Eco), 5-second shortlist; nearby Eco rotation | 0.50% | 0.89% | 22.4 s |

An initial 15-cost Eco allowance triggered an unwanted step-up in SIM-04. Eco's cost allowance
was restored to 5 and rotation restricted to quiet, nearby exchanges. This is a **one-day
measurement**, not proof of the
monthly <2% or six-month <=5% targets.
The fast SIM-19 scenario seeds equal historical distances and checks counter behavior; it is
not real-traffic acceptance. Run `EMS_LONG=1 pytest tests/scenarios/test_sim_19_wear.py -q`
for the real 24-hour check. Use `python -m ems.sim.long_run --months 1 --seed 1` and then
`--months 6` for the calendar-window evidence. The 24-hour run still has a maximum wait of
196 s, so the existing maximum-wait gap remains open.

For a new call each candidate car is replayed with and without it. Cost:

```
w_wait * f(new wait) + w_ride * new ride
+ sum over the car's other passengers of extra wait (f) and extra arrival delay (w_ride)
+ w_run  * extra moving seconds + door cycles      (energy)
+ w_busy * extra seconds the car stays busy        (later passengers wait for it)
+ penalty for every passenger pushed past 75% of the mode ceiling
+ hard penalty (Balanced: 100 per s) for every passenger pushed past the ceiling itself
f(w) = w + w^2 / ceiling                            (long waits weigh more)
```

| Mode | w_wait | w_ride | w_run | w_busy |
|---|---|---|---|---|
| Rush | 1.0 | 0.25 | 0 | 1.0 |
| Balanced | 1.0 | 0.5 | 0.2 | 0.5 |
| Eco | 1.0 | 0.3 | 3.0 | 0 |

Rush up-peak also asks for a door hold of up to 10 s at the lobby when the next car is far away,
so a car is there to board (the wait clock stops at boarding).

## Results (simulator, real Parts 2-5 on main, 35 persons/min peaks, seeds 1-10)
| Scenario | Nearest-car stand-in | Part 2 | Target |
|---|---|---|---|
| SIM-01 up-peak | avg 47-55 s, max 305-337 s | avg 18-24 s, p95 48-64 s, max 93-249 s | 25 / 60 |
| SIM-02 lunch | avg 55-60 s | avg 23-25 s, p95 53-62 s, max 113-207 s | 25 / 60 |
| SIM-02 evening | avg 55-60 s | avg 24-26.5 s, p95 54-60 s, max 103-143 s | 25 / 60 |
| SIM-03 balanced | avg 37-38 s, max 180-213 s | avg 17-20 s, p95 45-57 s, max 76-105 s (7/10 seeds <= 90 s) | 30 / 90 |
| SIM-04 Eco pair + surge | n/a | PASS: saving 20-27%, Eco avg 14 s; step-up after about 1 min, FM alerted | 15%, 45 / 120 |
| SIM-05 weekday | n/a | schedule, no stranding, all cleared; Rush and Balanced max over the ceiling | per block |
| SIM-06, SIM-18 (Part 5) | n/a | PASS on all 10 seeds with real Parts 2/3/4 | |
| SIM-10 engine time per call | n/a | p99 about 5 ms | 99% <= 1 s |

**Known gap: the "none over" ceilings.** Averages pass. In Rush 2-7% of passengers wait more than
60 s, almost all in the high-rise group (L16-L30) at the lobby; in Balanced 0-0.33% wait more than
90 s (seeds 3, 7, 10: 102-104 s, a lobby burst while a VIP trip holds a car or all four cars are
far away). At 35/min the high-rise group runs at about 80% of its best-case capacity (D-01 table:
about 23 persons/min with full cars), so cars leave the lobby with about 12 stops per trip and the
next car is often 60-90 s away; even at 20/min a few waits pass 60 s. Tried and measured, without
fixing the maximum: other cost weights, quadratic and hard ceiling penalties, lobby door holds,
two lobby cars in Balanced, moving at-risk calls to a sooner car. The maximum depends on a few
unlucky passengers (93-249 s across seeds) while p95 is stable, so a percentile criterion is
proposed below. The failing criteria stay visible in the tests as xfails.

**Proposed D-07 (for the team to decide):**
| Mode | SRS now | Proposed |
|---|---|---|
| Rush | avg <= 25 s, none over 60 s | avg <= 25 s, >= 95% <= 65 s, >= 99% <= 95 s |
| Balanced | avg <= 30 s, none over 90 s | avg <= 30 s, >= 95% <= 60 s, >= 99% <= 90 s |
| Eco | avg <= 45 s, none over 120 s | unchanged (passes) |

**Part 5 integration notes:** a VIP `claim_call` can pick a car that already has 21 riders, so the
VIP is left behind (one 220 s wait in SIM-01 seed 3); the claimed call is Part 5's (no
`register_stop` by Part 2). The VIP-after-medical case is issue #18 (PR16-01).

## Run
```bash
pytest tests/dispatch -q                                   # unit tests
pytest tests/scenarios -q -k "sim_01 or sim_02 or sim_03 or sim_04 or sim_05"
EMS_SLOW=1 pytest tests/scenarios -q -k "sim_04 or sim_05 or sim_10"   # every seed, SIM-10
python -c 'from ems.sim.scenarios import run_scenario as r; print(r("SIM-03", 1)["criteria"])'
```
