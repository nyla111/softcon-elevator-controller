# Part 2 Dispatch & modes

Package `ems/dispatch`, service `ctx.services["dispatcher"]` (the `Dispatcher` protocol in
`contracts/interfaces.py`). The dispatch plug-in (FR-06) is `EtdEngine`, always called through
Part 1's FR-105 guard (`ctx.services["dispatch_guard"].choose_car`).

## What it does
| Requirement | Behaviour | Module |
|---|---|---|
| FR-01/02, UC-01 | `CallRequested` -> candidate cars -> engine (via guard) -> `register_stop` -> `CallAssigned`; one car per hall call | `service.py` |
| FR-04 | fault, lost link, non-dispatchable car state, Part 5 removal: waiting calls move with `CallReassigned`, original `created_at` kept; calls that cannot move yet are retried every second | `service.py` |
| FR-05 | no new hall call for a car at or above 80% load | `service.py` |
| FR-06 | `EtdEngine` behind `DispatchEngine` (replaceable) | `engine.py` |
| FR-08, BR-10 | `claim_call` for VIP; `car_ok_for` for accessible calls; `door_dwell_s` as `min_dwell_s`; pending queue ordered by Part 5 `order()` | `service.py` |
| FR-10/11/12 | Rush / Balanced / Eco; weekday, weekend and holiday schedule; FM manual switch until a time or the next scheduled change (`set_manual_mode`) | `modes.py` |
| FR-13, BR-01 | ceiling penalty in the cost; `WaitCeilingRisk` at 90% of the ceiling; Eco wakes a Standby car; breaches logged with cause, FM alerted (at most one alert per group per 5 min) | `engine.py`, `service.py` |
| FR-14, BR-05 | Eco -> Balanced when > 10 calls wait for 30 s or the 5-min average wait > 45 s; FM alert; stays until the next scheduled change | `service.py`, `modes.py` |
| FR-15, BR-02 | a mode change never cancels a call or a trip | `modes.py` |
| FR-100 | overload: step-out message naming the next car (car display + panels) at once, then the calls left at that floor are re-assigned | `service.py` |
| FR-101 | the trip model never puts more persons on one trip than rated (or the FM limit, `set_persons_limit`) | `model.py`, `engine.py` |
| 4.3 table | parking: Rush -> lobby (up-peak) / spread over the zone (down-peak) / mixed; Balanced -> lobby + busiest recent floors; Eco -> stay where the last trip ended, 2 cars per group in service, the rest in Standby | `service.py` |
| Pauses | EMS server down (controllers serve calls), fire (until cleared and `safety_active()` is False, FR-55), mains lost (FR-110): no command sent, calls queue; after fire every waiting call is re-assigned | `service.py` |

## The algorithm (ETD, estimated time to destination)
The dispatcher knows every passenger's destination (destination panels); the controller only learns
it when the passenger boards. So the dispatcher keeps a book of who rides and who waits for each car,
and `model.replay()` replays the controller's stop logic (simple collective, Appendix B timings) to
predict every pick-up and arrival time. Prediction error measured at up-peak: about 1.5 s on average.

For a new call each candidate car is replayed with and without it. Cost:

```
w_wait * f(new wait) + w_ride * new ride
+ sum over the car's other passengers of extra wait (f) and extra arrival delay (w_ride)
+ w_run  * extra moving seconds + door cycles      (energy)
+ w_busy * extra seconds the car stays busy        (later passengers wait for it)
+ penalty for every passenger pushed past 75% of the mode ceiling
f(w) = w + w^2 / ceiling                            (long waits weigh more)
```

| Mode | w_wait | w_ride | w_run | w_busy |
|---|---|---|---|---|
| Rush | 1.0 | 0.25 | 0 | 1.5 |
| Balanced | 1.0 | 0.5 | 0.2 | 0.5 |
| Eco | 1.0 | 0.3 | 3.0 | 0 |

Rush up-peak also asks for a door hold of up to 10 s at the lobby when the next car is far away,
so a car is there to board (the wait clock stops at boarding).

## Results (simulator, 35 persons/min peaks, D-01)
| Scenario | Nearest-car stand-in | Part 2 | Target |
|---|---|---|---|
| SIM-01 up-peak, avg / max | 47-55 s / 305-337 s | about 20 s / 95-240 s | 25 / 60 |
| SIM-02 lunch, evening avg | 55-60 s | 23-25 s | 25 / 60 |
| SIM-03 balanced, avg / max | 37-38 s / 180-213 s | 18-19 s / 80-89 s | 30 / 90 |
| SIM-04 Eco saving / Eco waits | n/a | 20-27% / avg 14 s, max <= 67 s | 15%, 45 / 120 |
| SIM-04 surge | n/a | steps up after about 1 min, FM alerted | FR-14 |
| SIM-05 | n/a | Eco and evening Balanced pass; Rush max fails; Balanced max 90-91 s | per block |
| SIM-10 engine time per call | n/a | p99 about 5 ms | 99% <= 1 s |

**Known gap: no wait over 60 s in Rush.** Averages pass; about 2-4% of passengers wait more than
60 s, almost all in the high-rise group (L16-L30) at the lobby. At 35/min the high-rise group runs
at about 80% of its best-case capacity (D-01 table: about 23 persons/min with full cars), so cars
leave the lobby with 12 stops per trip and the next car is often 60-90 s away. Even at 20/min a few
waits pass 60 s. Options for the team: revise the high-rise rate or the Rush ceiling in D-01 (the
SRS allows a recorded revision), or accept longer rides for shorter waits (longer lobby holds).

**Part 5 integration note:** a VIP `claim_call` can pick a car that already has 21 riders; the VIP
is left behind (one case of 220 s in SIM-01 seed 3). The claimed call is Part 5's (no `register_stop`
by Part 2), so Part 2 does not re-assign it.

## Run
```bash
pytest tests/dispatch -q                                   # unit tests
pytest tests/scenarios -q -k "sim_01 or sim_02 or sim_03 or sim_04 or sim_05"
EMS_SLOW=1 pytest tests/scenarios -q -k "sim_04 or sim_05 or sim_10"   # every seed, SIM-10
python -c 'from ems.sim.scenarios import run_scenario as r; print(r("SIM-03", 1)["criteria"])'
```
