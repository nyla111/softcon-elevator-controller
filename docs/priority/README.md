# Part 5 Priority & service car

Owner: Part 5. Package `ems/priority`, service key `ctx.services["priority"]`.

## Scope
VIP exclusive trips (FR-70/71/72), priority order (FR-76 / BR-10), accessible calls (FR-08),
medical priority and send-to-lobby (FR-127/128), maintenance (FR-30/31/32/33), goods moves, the
service car and house rules (FR-36/80/81/82). Scenarios SIM-06, SIM-11, SIM-12, SIM-18.
Not Part 5: FR-04 re-assignment (Part 2), FR-84 bulky items (Part 6), car-state transitions
(Part 4). Traceability and open questions: [REQUIREMENTS.md](REQUIREMENTS.md).

## Architecture (short)
```
ems/priority/
  __init__.py     register(ctx): PriorityService + .maintenance + .goods, bus subscriptions
  service.py      car "holds" (the only place that asks car_states), VIP claim_call, medical,
                  send-to-lobby, accessible voice feedback, faults / emergencies / EMS down
  maintenance.py  FR-30 fault alerts, FR-31 counters + reminders, FR-32 in/out, FR-33 approvals
  goods.py        FR-81 requests + approval, FR-82 credentials, FR-36 periods, FR-80 house rules
  policy.py       pure functions Part 2 can call: rank/order, free capacity, door time, FR-71
  auth.py         credential -> roles through the Access service (no caller-chosen roles)
  standins.py     SCENARIO / TEST DOUBLES ONLY for the missing Part 2/3/4 services
  acceptance.py   SIM-06/11/12/18 setups and checks (wired into ems/sim/scenarios.py)
  demo.py         CLI walkthrough and per-seed scenario table
```
Part 2 asks Part 5 before assigning a VIP call (`claim_call`); Part 5 never publishes
Part 2/3/4 events. API, events and per-part integration: [INTEGRATION.md](INTEGRATION.md).
Contract changes Part 5 needs: [CONTRACT_CHANGE_REQUESTS.md](CONTRACT_CHANGE_REQUESTS.md).

## Setup, run, test
```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/priority -q                                  # unit tests (doubles + simulator)
pytest tests/scenarios -q -k "sim_06 or sim_11 or sim_12 or sim_18"   # seeds 1-10
ruff check --select E9,F63,F7,F82 .                       # what CI runs
pytest -q                                                 # whole suite
```

## Demo (3-5 minutes)
```bash
python -m ems.priority.demo                     # walkthrough: VIP + accessible, medical over VIP,
                                                # fault + second removal approval, goods move
python -m ems.priority.demo scenarios           # SIM-06/11/12/18 x seeds 1-10, per criterion
uvicorn ems.web.server:app                      # then http://localhost:8000/runner/
```
Walkthrough script (what to say):
1. **Start** - `python -m ems.priority.demo`. The first lines list the stand-ins in use: Part 2,
   3 and 4 are still contract stubs, so this is the simulator + Part 5, not the full system.
2. **VIP / accessible** - 6 exclusive VIP starts then `hourly_cap` (FR-72); accessible voice
   message in English and Vietnamese (FR-08). Say: the 10 s doors are the simulated controller's
   rule; the EMS cannot request them yet (CCR-02).
3. **Medical over VIP** - a VIP is on board, security requests medical; the VIP trip ends with
   `cancelled=True` (yields), the medical car holds its doors, departs, finishes; the VIP still
   reaches their floor (FR-127).
4. **Second removal** - a real car fault, FR-30 alert to contractor + FM; a technician asks for a
   second car in Rush -> `approval_required`; FM approves; another request expires after 10 min
   and the late approval is refused (FR-33).
5. **Goods move** - house rules on the service car, tenant admin request, FM approval,
   `GoodsMoveScheduled`, only the movers' cards accepted during the move (FR-80/81/82). Say: the
   panels themselves are Part 3's; early-end event is CCR-04.
6. **Scenario results** - `/runner/` (SIM-06, 11, 12, 18 now runnable) or the `scenarios` table.
   Every run FAILS on "integration with real Part 2/3/4 services" (BLOCKED), SIM-11 and SIM-12
   also fail their wait targets with the nearest-car stand-in (numbers in VERIFICATION.md).

The `/building/` live page uses Part 1's plain nearest-car stand-in, which does not call
`claim_call`, and the stub `car_states` shows every car as GROUP_SERVICE, so VIP / medical trips
do not appear there yet; accessible voice messages do appear in its event list.
