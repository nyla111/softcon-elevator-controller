# Elevator Management System (EMS), Release 1

Team 1's implementation of Team 3's SRS v0.13: supervisory software for a
30-floor office tower (8 passenger cars + 1 service car), running on simulated
controllers, access control, cameras and building signals.

**Read first:** [docs/CONTRACT.md](docs/CONTRACT.md) (rules every part follows).

## Setup
```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest                                            # must pass before every PR
uvicorn ems.web.server:app --reload               # http://localhost:8000
```

## Layout
```
ems/
  contracts/   shared types, events, interfaces, config, stubs   (tech lead only)
  core/        clock, event bus, app wiring                       (tech lead only)
  sim/         Part 1 Simulator & integration
  dispatch/    Part 2 Dispatch & modes
  access/      Part 3 Access & panels
  safety/      Part 4 Safety & edge cases
  priority/    Part 5 Priority & service car
  monitor/     Part 6 Dashboard, camera & admin
  web/         FastAPI server + static pages (dashboard, panel, building, runner)
tests/
  <part>/      your unit tests
  scenarios/   SIM-xx / VER-xx acceptance tests (Appendix B)
docs/          contract, scope note for Team 3
scripts/       create_issues.sh + requirements.csv
```

## Workflow
1. Pick an issue assigned to you (one issue = one requirement ID).
2. Branch from `main`: `part<N>/<short-name>`, e.g. `part5/vip-caps`.
3. Commit small and often; push your branch daily.
4. Open a PR titled with the requirement ID (`FR-72 VIP caps`), link the issue.
5. CI must be green and 1 reviewer must approve; then squash-merge.
6. Need a contract change? Open a "Contract change request" issue; don't edit `contracts/`.
