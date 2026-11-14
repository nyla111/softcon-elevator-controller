"""Part 5 with every REAL service that exists on main: Part 1 simulator / mock ACS / notifier /
database / dispatch guard, Part 6 monitor. Part 2/3/4 are still contract stubs, replaced by the
labelled stand-ins of ems/priority/standins.py (the dispatcher stand-in is the only caller of
claim_call; no real Part 2 caller exists yet).

The bus is OBSERVED (publish is wrapped to record which module published each event); nothing of
Dispatch / Access / Safety is patched.
"""
from __future__ import annotations

import sys
from collections import Counter
from dataclasses import asdict

import pytest
from fastapi.testclient import TestClient

from ems.contracts.events import (AlertRaised, CallAssigned, CallRequested, GoodsMoveScheduled,
                                  PanelMessage, PriorityTripEnded, PriorityTripStarted)
from ems.priority import acceptance as p5
from ems.sim.harness import run

PART5_PRODUCTION = ("ems.priority.service", "ems.priority.maintenance", "ems.priority.goods")
PART5_EVENTS = {PriorityTripStarted, PriorityTripEnded, GoodsMoveScheduled, AlertRaised,
                PanelMessage}


def _observed(setup):
    def wrapped(ctx):
        published: list[tuple] = []
        calls: dict[str, dict] = {}
        original = ctx.bus.publish

        def publish(ev):
            published.append((type(ev), sys._getframe(1).f_globals.get("__name__", "?")))
            if isinstance(ev, CallRequested):
                calls[ev.call.call_id] = (ev.call, asdict(ev.call))
            original(ev)

        ctx.bus.publish = publish
        ctx.services["observed"] = (published, calls)
        setup(ctx)
    return wrapped


@pytest.fixture(scope="module", params=["SIM-06", "SIM-12", "SIM-18"])
def observed_run(request):
    setup = {"SIM-06": p5.setup_sim_06, "SIM-12": p5.setup_sim_12,
             "SIM-18": p5.setup_sim_18}[request.param]
    r = run("up_peak", 1, setup=_observed(setup), keep_ctx=True, out_dir=None,
            start_hhmm="07:00")
    return request.param, r


def test_part5_publishes_only_its_own_events(observed_run):
    _, r = observed_run
    published, _ = r.ctx.services["observed"]
    mine = Counter(t.__name__ for t, mod in published if mod in PART5_PRODUCTION)
    assert mine, "Part 5 published nothing"
    assert {t for t, mod in published if mod in PART5_PRODUCTION} <= PART5_EVENTS, mine


def test_no_duplicate_and_no_lost_assignment(observed_run):
    _, r = observed_run
    bus = r.ctx.bus
    assigned = Counter(e.call_id for e in bus.of_type(CallAssigned))
    requested = [e.call.call_id for e in bus.of_type(CallRequested)]
    assert len(requested) == len(set(requested))
    assert all(assigned[c] == 1 for c in requested), [c for c in requested if assigned[c] != 1]
    assert r.metrics["calls_not_cleared"] == 0


def test_calls_are_never_mutated_after_they_were_requested(observed_run):
    _, r = observed_run
    _, calls = r.ctx.services["observed"]
    changed = [cid for cid, (obj, snap) in calls.items() if asdict(obj) != snap]
    assert not changed


def test_every_claim_decided_once_and_every_hold_released(observed_run):
    _, r = observed_run
    p = r.ctx.services["priority"]
    ids = [d["call_id"] for d in p.decisions]
    assert len(ids) == len(set(ids))
    bus = r.ctx.bus
    assert len(bus.of_type(PriorityTripStarted)) == len(bus.of_type(PriorityTripEnded))
    assert p.holds == {} and p.trips == {} and p.reopen == {} and p.after_release == {}
    sim = r.ctx.services["controllers"]
    assert not any(c.closed_to_new_calls or c.independent or c.hold_doors
                   for c in sim.cars.values())


def test_no_credential_in_trip_records_or_part5_alerts(observed_run):
    _, r = observed_run
    creds = {e.call.credential_id for e in r.ctx.bus.of_type(CallRequested)} - {None}
    rows = r.ctx.services["reports"].trips + r.ctx.services["db"].records()
    assert rows
    values = {v for row in rows for v in row.values() if isinstance(v, str)}
    assert not values & creds
    p = r.ctx.services["priority"]
    texts = [str(asdict(e)) for e in r.ctx.bus.of_type(AlertRaised)] + [str(p.audit),
                                                                       str(p.decisions)]
    assert not [c for c in creds if any(c in t for t in texts)]


def test_runner_api_runs_a_part5_scenario_and_reports_blocked():
    from ems.web.server import app
    client = TestClient(app)
    listing = {s["id"]: s for s in client.get("/api/scenarios").json()}
    assert all(listing[s]["ready"] for s in ("SIM-06", "SIM-11", "SIM-12", "SIM-18"))
    r = client.post("/api/scenarios/SIM-18/run?seed=1").json()
    assert r["ready"] is True and r["passed"] is False and r["message"] == "FAIL"
    blocked = [c for c in r["criteria"] if not c["passed"]]
    assert [c["criterion"] for c in blocked] == [
        "integration with real Part 2/3/4 services (not stand-ins)"]
    assert blocked[0]["detail"].startswith("BLOCKED")
    assert client.get("/api/state").status_code == 200
