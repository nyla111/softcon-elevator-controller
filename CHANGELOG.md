# Changelog

Release 1 of the EMS (Team 3 SRS v0.13). Each tag marks a version shown to the course staff
or a major milestone. Decisions referenced here (D-xx, CR-xx) are in `docs/DECISIONS.md` and
`docs/CHANGE_REQUESTS.md`.

## Unreleased (target v1.0, presentation 2026-11-25)

Planned work is tracked in the GitHub issues: wear balance in the high-rise group (CR-01),
real multi-week wear runs, basement floors B1/B2 (#25), remaining safety scenarios
(SIM-07, 08, 13, 14, 15, 17), saved run history, building page layout and controls.

- Passenger list for BR-07 (the way down to the lobby is always open).

## v0.5-phase2-demo (2026-11-20)

Version presented to Prof. Cha and the TAs on 2026-11-20. All six parts integrated on main.

**Parts**
- Part 2 dispatcher: ETD cost engine with per-mode weights, Rush / Balanced / Eco modes,
  weekday schedule, Eco step-up (FR-14), ceiling watch (FR-13), plug-in guard with
  nearest-car fallback (FR-105).
- Part 3 access: floor access by card, visitor QR passes, refusal message without reasons
  (FR-46), ACS cache fallback (FR-49), lobby panel demo page.
- Part 4 safety: car states and precedence (BR-06), fire recall, emergency power, recovery
  only after healthy controllers and FM confirmation (FR-55), sequential parking (FR-52).
- Part 5 priority: VIP exclusive trips with caps (FR-71, FR-72), medical priority (FR-127),
  accessible calls (FR-08), goods moves and maintenance (FR-30..36), travel counters.
- Part 6 dashboard: live cars, alerts, wait and run time reports, role-based views, FM
  actions, camera incident review, audit and configuration.
- NFR-W1 (CR-01): wear-aware car choice and Eco parking rotation; wear balance panel on
  the dashboard; SIM-19 on a compressed month and six-month period.

**Simulator and tooling (Part 1)**
- Docker image and docker-compose for a one-command start; CI builds the image.
- Scripted passenger lists (CSV/JSON) with expected outcomes, run in CI and from the
  building page.
- Live fault buttons with restore (link loss, ACS outage, plug-in crash, fire, power).

**Decisions:** D-01 peak rate 35/min, D-02 redundant requirements, D-03 simulated
infrastructure, D-04 synthetic population, D-05 modelling assumptions, D-06 architecture,
D-07 wait ceilings as percentiles. **Change request:** CR-01 wear balance.

**Known gaps:** Rush p95 wait 48-64 s (some seeds over 60 s); SIM-07, 08, 13, 14, 15, 17
not ready in the runner; wear balance in the high-rise group not met on real (uncompressed)
runs.

## v0.4-scenario-setup (2026-11-12)

Every Appendix B scenario has an executable setup: time of day, 24 h weekday run, Eco
surge, design-limit building and SIM-10 workload hooks. Scenario registry and runner page.

## v0.3-demo (2026-11-12)

First demo pages: building animation, dashboard, lobby panel. Simulator core, event bus,
simulated clock and the shared contract.
