# Part 5 Priority & service car

Package `ems/priority`, service `ctx.services["priority"]` (the `PriorityService` protocol in
`contracts/interfaces.py`). Full working notes, the requirement matrix and the earlier evidence
are kept on the archive branch `part5/minh-priority-service-car` (PR #13).

## What it does
| Requirement | Behaviour | Module |
|---|---|---|
| FR-70/71/72/73 VIP | `claim_call(call)`: exclusive trips only while the call's group is in Rush (outside Rush: refused as `not_rush`, normal dispatch with VIP queue rank); empty car within 30 s else fastest; 1 exclusive car per group; 6 starts per rolling hour, counted after a successful start; ceiling check; a car is exclusive only if every call waiting for it was moved by Part 2; FM quota / revoke | `vip.py` |
| FR-76, FR-08 | `rank`, `order`, `car_ok_for` (room after riders who stay and calls already assigned), `door_dwell_s` (10 s accessible); one voice message per accessible assignment | `policy.py`, `service.py` |
| FR-127/128 | security: medical (VIP trips of the served groups yield, riders keep their destinations), send-to-lobby; doors held on arrival; `finish_trip` | `medical.py` |
| FR-30/31/32/33 | fault alerts once per episode; telemetry counters and FM-set reminders; technician in / out; 2nd car out in Rush waits for the FM (600 s, late approval refused) | `maintenance.py` |
| FR-36/80/81/82 | goods move request / approval / start / end with `GoodsMoveScheduled` and `GoodsMoveEnded`; only the movers' cards during the move; house rules on the service car | `goods.py` |

Rules kept: car state only through `car_states` (Part 5 releases only states it holds);
every refused command is rolled back and costs nothing; no command to a faulted car or during
fire, power loss or EMS downtime; a VIP pick-up owed after an interrupted trip is registered only
while that call is still assigned to that car and the car is in group service.

With Part 4's Safety (issue #18): `claim_call` gives a car only while that VIP trip is still
running and no emergency is active, and never starts a cancelled trip again for the same call; a
medical / send-to-lobby trip taking a VIP car asks `car_states` for the state under its own owner
before the VIP owner releases it, and is refused if Part 4 has isolated the car since; Part 5's
fire latch ends on `FireAlarmCleared` or the firefighter reset, while recovery stays gated by
`car_states.safety_active()` until the FM confirms. Events published:
`PriorityTripStarted/Ended`, `GoodsMoveScheduled/Ended`, `AlertRaised`, `PanelMessage`. Staff
actions are authorised from `access.permission(card).roles`; no credential id in records or alerts.

## VIP exclusivity only in Rush (FR-71, FR-73, issue #65)
* **Eligibility.** `claim_call` decides once per call, in the mode Part 2 reports for the call's
  group at that moment (`dispatcher.current_mode(group)`). Only `Mode.RUSH` (scheduled FR-11
  or an FM switch FR-12) may start an exclusive trip. Order of the refusal reasons:
  `emergency` / `ems_down` (safety keeps its own restriction in every mode), then `not_rush`,
  then `no_destination`, `vip_revoked` / `vip_quota`, `group_exclusive_cap`, `hourly_cap`,
  `no_car`, `ceiling` / `calls_not_moved` / `refused`.
* **Outside Rush (Balanced, Eco).** `claim_call` returns `None` with reason `not_rush`: no car
  state is asked, no controller command is sent, and nothing is counted. The call keeps
  `CallPriority.VIP`, so Part 2 dispatches it as a normal call (`register_stop`, cars shared
  with other passengers) and orders its queue with `order()`: VIP before ordinary calls,
  behind medical and accessible calls (FR-73, BR-10). The passenger is never dropped.
* **Entering Rush.** A call already decided outside Rush is not promoted: Part 2 asks again
  when it re-queues the call, and the cached decision (`None`) is returned. A call first
  decided in Rush goes through every admission check (group cap, hourly cap, ceiling check).
* **Leaving Rush.** FR-15 / BR-02 (a mode change never cancels a call or a trip; new rules
  apply to new decisions): an exclusive trip already started keeps its car until its normal
  end (delivered, not boarded in one door cycle, medical / send-to-lobby preemption, fault,
  emergency). No cabin is stopped, no rider or stop is removed, and the start is neither
  counted again nor refunded. VIP calls decided after the change are served as normal calls.
* **Quota.** The 6 starts per rolling hour stay building-wide and are counted only after a
  successful start, so `not_rush` refusals (like every other refusal) use no quota.
* **Medical and safety.** Medical (FR-127) and send-to-lobby (FR-128) are not mode-gated and
  still take precedence over a running VIP trip, keeping the riders' destination stops. During
  fire recall, power loss or EMS downtime the reason is `emergency` / `ems_down` in every mode.
* **Passenger lists.** `sim12_vip_caps.csv` and `sim18_medical_over_vip.csv` start at 07:00
  (`# start=07:00`, Rush) so they keep showing exclusive trips; `br10_priority_order.csv` runs
  at 00:00 (Eco), where its VIP is served as a normal call ahead of ordinary calls.

## For the other parts
* **Part 2:** for a VIP call call `claim_call(call)` before choosing a car; a car id means publish
  `CallAssigned` without `register_stop`, `None` means dispatch normally. Order queues with
  `order()`, filter cars with `car_ok_for()`, pass `door_dwell_s(call)` as `min_dwell_s`. Part 5
  relies on true answers from `would_breach_ceiling_without()` and on the list returned by
  `reassign_calls_of()`.
* **Part 3:** set `Call.priority` from the card's roles; lift the FR-82 restriction on
  `GoodsMoveEnded`; do not voice accessible assignments again (Part 5 does).
* **Part 4:** requests arrive as `by="priority:<kind>"` (vip, medical, send_to_lobby,
  maintenance, goods); `VIP_EXCLUSIVE` covers every exclusive priority trip.

## Scenarios (seeds 1-10, each seed on its own)
Part 2, 3 and 4 are still contract stubs on `main`, so the setups install labelled doubles
(`standins.py`, only where the real part is missing) and every run reports
"integration with real Part 2/3/4 services" as BLOCKED. Result: FAIL on 10/10 seeds for each.

| Scenario | Part 5 criteria | Still failing (all seeds) |
|---|---|---|
| SIM-06 | all pass (fault alert, approval / deny / 600 s timeout, waiting calls kept with their wait clock) | FR-04 re-assignment and degraded marking (Part 2); integration |
| SIM-11 | all pass (capacity, first-opening boarding, voice, 10 s door request in the command log) | dispatcher priority order (Part 2); Rush targets: avg 50.6-87.3 s, max 303.6-741.1 s; integration |
| SIM-12 | all pass (10 replaced VIPs, caps exercised and justified, no other boarding during VIP trips) | ordinary LOW calls <= 60 s: max 134.4-256.3 s; integration |
| SIM-18 | all pass (medical after a real VIP boarding, VIP yields, riders delivered, event order) | integration |

The wait targets also fail on the same traffic with Part 1's nearest-car stand-in and no Part 5
hook, so they need the real dispatcher (and its ceiling answers) before any conclusion.
`tests/scenarios/test_sim_*` check the Part 5 criteria per seed; the full-acceptance tests are
strict xfails (the scenarios really fail) and turn red the day a scenario passes.

## Run
```bash
pip install -e ".[dev]"
pytest tests/priority -q                                              # unit + simulator + integration
pytest tests/scenarios -q -k "sim_06 or sim_11 or sim_12 or sim_18"   # seeds 1-10
python -c 'from ems.sim.scenarios import run_scenario as r; print(r("SIM-18", 1)["criteria"])'
uvicorn ems.web.server:app                                            # /runner/: SIM-06, 11, 12, 18
```

## Open questions (SRS text not in the repo)
VIP quota meaning (FR-70); 6 starts per hour per building or per group (FR-72, built: building);
VIP yield when medical uses another car (FR-127, built: yes); VIP trip at the end of Rush
(built: runs to its end, FR-15 / BR-02); Rush rule for the service car
(FR-36, built: passenger cars only); FR-31 thresholds and FR-80 limit values (not invented: set by
the FM); FR-128 workflow (built: security starts and finishes it).
