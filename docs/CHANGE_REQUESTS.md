# Customer change requests

Requirements added by the customer (Prof. Cha) after Team 3's SRS v0.13. Each entry states
the requirement as given, Team 1's working interpretation, and who builds what. Every entry
will be sent to Team 3 together with `docs/DECISIONS.md`.

| ID | Requirement | Received | Demo |
|---|---|---|---|
| CR-01 | NFR-W1 Balanced wear and tear across cars | 2026-11-17 | Friday 2026-11-20 |

---

## CR-01 NFR-W1 Balanced wear and tear across cars

**As given:** The EMS shall balance wear and tear across the elevator cars. The difference
between the minimum and maximum accumulated travel distance of the cars shall be under 2%
per month and at most 5% over 6 months. The trip counter resets every 6 months.

**Working interpretation (Team 1):**

1. **Distance** is counted in floors travelled, from car telemetry (the FR-31
   `floors_travelled` counter, `ems/priority/maintenance.py`).
2. **Balance is measured per group.** LOW (A-D) and HIGH (E-H) serve different floor ranges,
   so their distances cannot be equal. The service car S is alone in its group and is
   balanced by definition.
3. **Imbalance** of a group over a period = (max - min) / max of the cars' distances in that
   period. Pass: monthly imbalance < 2% for every month, and 6-month imbalance <= 5%.
4. **Period:** a counter period starts at the reset and lasts 6 months; monthly figures are
   the distance accumulated in each calendar month inside it. At the 6-month reset the
   period counters go to 0; FR-31 totals and since-service counters are not reset.
5. **Cars out of service** (fault, maintenance, fire recall, emergency power) still count
   their distance. After a car returns, balancing lets it catch up gradually; wait targets
   (SRS 4.3, NFR-P3) always take priority over wear balance.
6. **Exclusive trips** (VIP, medical, goods move, independent service) count towards the
   car's distance like any other trip.

**How it is met:** the dispatcher uses accumulated distance as a cost term (prefer the car
with less distance when the wait cost is close), and Eco rotates which cars park instead of
always parking the same ones.

**Verification:** new scenario SIM-19 runs one month and six months of compressed weekday
traffic and checks the per-group imbalance against both limits. Evidence is written in the
run manifest like every other SIM run.

### Work split

| Part | Owner | Task |
|---|---|---|
| Part 5 | Minh | Period counters on top of FR-31: distance per car per month and per 6-month period, reset at 6 months, `wear_report()` returning per-group imbalance. Register as `ctx.services["usage"]`. |
| Part 2 | Nam | Wear-aware choice: distance as a tie-break / small cost term in the ETD engine; Eco parking rotation. SIM-01..05 wait targets must still pass. |
| Part 1 | Nhi | Contract entry for `usage`; fast-forward runs of 1 and 6 simulated months; SIM-19 setup and check; demo script for Friday. |
| Part 6 | Mai | Dashboard: per-car distance this month and this period, per-group imbalance against 2% / 5%; included in the monthly report. |
| Part 4 | Thao Vy | Out-of-service rule (point 5): passenger list where a car is faulted for several days, then returns; check catch-up does not break wait ceilings. |
| Part 3 | Bao Vy | Write CR-01 into the SRS revision and the Team 3 notification; one slide for Friday. |
