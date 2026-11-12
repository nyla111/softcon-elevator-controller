# Release 1 decision log

Decisions Team 1 made while implementing Team 3's SRS v0.13. Team 3 allowed us to revise
the SRS and notify them. A note on the traffic rate (D-01) was sent on 2026-11-12 with
no reply, so Team 1 decided. Every entry below will be sent to Team 3 and presented to the
course staff.

| ID | Decision | Status |
|---|---|---|
| D-01 | Peak traffic rate 75 to 35 persons/min | Decided 2026-11-12 |
| D-02 | Redundant requirements not built in Release 1 | Decided 2026-11-10 |
| D-03 | Infrastructure simplified for a simulator release | Decided 2026-11-10 |
| D-04 | Synthetic population and inter-floor trips | Decided 2026-11-12 |
| D-05 | Simulator modelling assumptions | Decided 2026-11-12 |
| D-06 | Architecture: one Python app, event bus, simulated clock | Decided 2026-11-10 |

---

## D-01 Peak traffic rate revised from 75 to 35 persons/min

**Applies to:** Appendix B up-peak, lunch and evening profiles. Balanced (20/min) and Eco
quiet (2/min) are unchanged. All wait targets and pass criteria are unchanged. The SIM-04
Eco surge phase keeps 75/min on purpose: it tests the FR-14 step-up beyond the normal
traffic envelope, and Appendix B already logs it as degraded service.

**Why:**
1. The profile contradicts the reference building (SRS 2.4: about 2,500 workers).
   75/min for the 60-minute window is 4,500 arrivals, 1.8 times the workforce.
2. The building cannot carry it. Using the Appendix B physics (1.5 s/floor, 2 s + 2 s
   doors, 5 s dwell, 1 s/passenger, 21 persons), the best-case capacity with full cars
   every trip is:

   | Group | Round trip | Max capacity | Lobby demand at 75/min |
   |---|---|---|---|
   | Low-rise L2-L15 | ~166 s | ~30 persons/min | ~33/min |
   | High-rise L16-L30 | ~214 s | ~23 persons/min | ~35/min |

3. Measured with the simulator (nearest-car baseline dispatcher, seeds 1-3): the high-rise
   queue starts growing between 35 and 40/min; at 75/min, across the 3 seeds, 1,770
   high-rise and 178 low-rise arrivals were never delivered and average waits exceeded
   900 s in both groups.

Appendix B states that if a profile proves physically infeasible, the team must revise it
and record the change before acceptance, and must not silently relax a failed test. This
entry is that record.

**New value:** 35 persons/min (about 2,100 arrivals per hour, about 84% of the workforce).
The final value is confirmed with the Part 2 dispatcher and written in each run's
evidence manifest. The original 75/min stays available as a named setting for comparison.

**Evidence:** `python -m ems.sim.capacity` (sweep 30-75/min); `ems/sim/capacity.py`.

## D-02 Redundant requirements not built in Release 1

Each item is already covered by another requirement that is built, so the stakeholder
outcome is kept.

| Not built | Already covered by |
|---|---|
| FR-07 estimated wait on panel | Panel shows the assigned car; waits are on the dashboard |
| FR-16 FM edits mode settings | Defaults fixed in configuration (FR-62) |
| FR-17 one-off event schedule | FM manual Rush override (FR-12) |
| FR-24 tenant report, FR-26 PDF/CSV export | Owner report (FR-23) on the dashboard |
| FR-25 what-if estimate | Monthly mode comparison in reports (FR-23) |
| FR-35 contractor remote diagnostics | Fault alerts (FR-30) and the technician role |
| FR-83 extend goods move by 15 min | FM reschedules the move (FR-36) |
| FR-102 load vs. camera mismatch | Overload handling (FR-100, FR-84) |
| FR-106 panel offline, FR-108 camera offline | Generic device-offline alert, same mechanism as FR-104 |
| FR-114 degraded-car preference | Fault handling (FR-04, FR-112, FR-113) |
| FR-115 "clear the doorway" | Camera door-held event and reminder (FR-90, FR-95) |
| FR-116 mark fire as drill | Fire already reported as a degraded period (4.3) |
| FR-123 repeated landing presses, FR-124 stuck button | No-show cancel (FR-122) and rate limits (FR-121) |
| BMS interface | Holiday calendar entered by the FM (FR-11) |

## D-03 Infrastructure simplified for a simulator release

Release 1 runs on simulated devices (SRS 1.2, C-05); these items belong to deployment.

| SRS item | Release 1 | Reason |
|---|---|---|
| Redundant server pair, 10 s failover (NFR-R2, SIM-09) | Primary/standby failover modelled in the simulator | Proves the failover logic; dual hardware is deployment |
| MFA, TLS, VPN, network isolation (NFR-SE1, VER-13) | Role-based login | Belongs to the deployment network |
| Design-limit scale (SIM-10) | Design-limit layout (64 cars, 8 groups, 128 stops, 200 tenants) and the 400 calls + 400 credential lookups/min workload, but no 3 years of preloaded history; measured window shortened only if the run is too slow, recorded in the manifest | SIM-10 checks software latency only; synthetic multi-year history adds no extra verification of latency in a simulator |
| Usability studies (VER-12) | Scripted task walkthrough | Needs recruited users; formal study before go-live |
| Camera video clips | Events carry a clip reference only | Cameras send events (A-11); no video in a simulator |

## D-04 Synthetic population and inter-floor trips

- 25 tenants on L2-L30, 4 of them on 2 floors (never crossing L15/L16); about 2,500
  employees, about 86 per floor (SRS 2.4). 5% accessible, 1% VIP.
- Employee access: their tenant's floors plus public floors (lobby).
- Inter-floor trips go to "eligible" floors (Appendix B): within the employee's own tenant
  using their card, otherwise a visit to another tenant using a visitor QR pass issued by
  the host tenant (FR-41). Giving everyone access to the whole group would defeat floor
  access control (4.2).

## D-05 Simulator modelling assumptions

- The wait clock ends when the assigned car's doors are fully open.
- A full car leaves waiting passengers at the floor and reports overload; they are
  re-assigned.
- Calls not delivered within 30 minutes after the arrival window fail the run (warm-up
  arrivals included).
- Riders who leave a car during fire recall or power rescue are counted as evacuated,
  reported separately from normal deliveries.
- Rescue devices bring a car to the nearest floor 20-40 s after mains loss; a car with a
  failed rescue device stays stopped until power returns.
- Heartbeat loss is measured from the last status message (status every 1 s, timeout 3 s).
- Trip records are flushed to storage every 30 s, so a server crash loses at most 30 s of
  records (NFR-R2 allows 1 minute).
- Server failover runs in one process: during the gap, controllers refuse EMS commands and
  serve passengers with their built-in nearest-car logic (FR-54).

## D-06 Architecture

One Python application with an in-process event bus and a simulated clock. Six parts talk
only through shared events and interfaces (`ems/contracts/`). The simulated clock makes
every run reproducible per seed (Appendix B: seeds 1-10) and lets 24-hour and retention
scenarios run in seconds.
