# Part 5 hand-off (2026-11-14)

Owners as given by Minh (repo CODEOWNERS still has placeholders; GitHub accounts not verified).
Status words: **ready on Part 5's side** = implemented and tested by Part 5, waiting for the owner;
**proposed** = needs the owner's / tech lead's agreement; **open** = not started.

| Owner | Required integration | Exact API / event | Expected behaviour | Test to run | Status |
|---|---|---|---|---|---|
| Nam (Part 2) | VIP handshake (#9) | `ctx.get("priority").claim_call(call) -> CarId \| None` inside the `CallRequested` handler, before choosing a car | car id: publish `CallAssigned`, no `register_stop`; `None`: dispatch normally with VIP rank; never two assignments | `tests/priority/test_integration_main_services.py` (no duplicate / lost assignment); SIM-12, SIM-18 | ready on Part 5's side; proposed |
| Nam (Part 2) | ceiling check (FR-72, FR-36) | `Dispatcher.would_breach_ceiling_without(car_id) -> bool` | true answer from waiting calls' elapsed + ETA vs the mode ceiling | SIM-12 "ordinary calls within 60 s"; `test_vip::test_ceiling_check...` | open (stub answers False) |
| Nam (Part 2) | FR-04 re-assignment | `Dispatcher.reassign_calls_of(car_id, reason) -> list[CallId]` + `CallReassigned` (created_at kept) | moves every waiting call of a car Part 5 takes (VIP, medical, maintenance, goods) or a faulted car; the returned list is used by Part 5 | SIM-06 "re-assigned" criterion; `test_vip::test_busy_car_is_used_when_part2_moves_its_calls` | open |
| Nam (Part 2) | BR-10 order, FR-08 filter | `priority.order(calls)`, `priority.rank(call)`, `priority.car_ok_for(call, status)` | queued calls served medical > accessible > VIP > ordinary; accessible only to cars with room | SIM-11 "priority order applied by the dispatcher" | ready on Part 5's side |
| Nam (Part 2) | modes | `Dispatcher.current_mode(group)` | Rush during the schedule's Rush blocks / manual Rush | SIM-06 approval gate (Rush) | open (scenarios use `ScheduleModeDispatcher`) |
| Bao Vy (Part 3) | VIP / accessible call priority | `CallRequested.call.priority` from the credential's roles | `ACCESSIBLE` > `VIP` > `NORMAL`; Part 5 trusts `VIP` on `CallRequested` | SIM-11, SIM-12 with the real access service | open |
| Bao Vy (Part 3) | FR-82 service-car restriction | `GoodsMoveScheduled.credential_ids` or `priority.goods.service_car_accepts(cred, car)` | during an active move only the movers' credentials are accepted on that car | `tests/priority/test_goods.py::test_approved_move_runs_on_schedule...` | ready on Part 5's side |
| Bao Vy (Part 3) + Nhi | staff roles (#12) | `AccessService.permission(cred).roles` with SECURITY / FM / TECHNICIAN / TENANT_ADMIN | Part 5 authorises every staff action from these roles | `tests/priority/test_medical.py::test_only_security...` with a real staff card | open |
| Bao Vy (Part 3) | voice | `PanelMessage(voice=True)` from Part 5 for accessible assignments | Part 3 does not voice the same assignment again | `test_medical::test_accessible_voice_feedback...` | ready on Part 5's side |
| Thao Vy (Part 4) | car states | `car_states.request/release(car, state, "priority:<kind>")`, `safety_active()` | grant / refuse per BR-06; release only leaves Part 5's hold; Safety states never overridden | `test_maintenance::test_exit_never_overrides_a_safety_state`; all scenarios with the real service | open |
| Thao Vy (Part 4) | medical state naming (CCR-03) | `VIP_EXCLUSIVE` used for medical / send-to-lobby | decide: document (recommended) or add a state | - | proposed |
| Thao Vy (Part 4) | SIM-08 goods move cancelled | `GoodsMoveEnded` (#11) | Part 5 publishes when a move ends early | - | proposed |
| Nhi (Part 1 / contracts) | door time (#10) | `register_stop(..., min_dwell_s=None)` | EMS requests 10 s for accessible calls | SIM-11 "EMS requests the >= 10 s door time" | proposed |
| Nhi (Part 1 / contracts) | Part 5 Protocol (#9), event (#11) | `contracts/interfaces.py`, `contracts/events.py` | additive, stubbed | - | proposed |
| Nhi (Part 1) | simulator hold-doors | `SimCar._end_dwell` | hold only at the express floor | INTEGRATION.md (worked around in Part 5) | open |
| Nhi (Part 1) | privacy in `/api/state` | `ems/web/server.py::_event_line` | never print `credential_id(s)` | VERIFICATION.md F1 | open |
| Mai (Part 6) | degraded waits | `TripCompleted.degraded` | decide whether calls that waited on a faulted car without re-assignment are degraded | SIM-06 "degraded waits logged" | open |
| Mai (Part 6) | alerts / house rules | Part 5 alert kinds (INTEGRATION.md); `house_rule_limits` | show Part 5 alerts; FM shows limits through `goods.show_house_rules` | demo step 4 | ready on Part 5's side |
| Minh (Part 5) | answer SRS questions | REQUIREMENTS.md section 3 | adjust FR-31 thresholds, FR-70 quota, FR-72 per group?, FR-36, FR-127 yield policy | `tests/priority` | open (needs Minh / Nhi / Team 3) |
| Minh (Part 5) | after each merge of Part 2/3/4 | remove stand-ins from the scenario setups where the real part exists (they already install only when the stub is present), rerun seeds 1-10, remove the strict xfails that pass | `python -m ems.priority.demo scenarios` | open |
