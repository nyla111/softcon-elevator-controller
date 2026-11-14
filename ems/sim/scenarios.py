"""Scenario registry: SIM-01 .. SIM-18 (Appendix B) in one place, shared by tests and the runner.

Each Scenario says what to run (profile, rate, faults, run lengths) and, once an owner has
written it, how to judge the run (`check`). Ownership:
    Part 1: SIM-09, SIM-16 (checks below)
    Part 2: SIM-01 02 03 04 05 10     Part 3: SIM-07
    Part 4: SIM-08 13 14 15 17        Part 5: SIM-06 11 12 18
HOW A PART ADDS ITS SCENARIO: edit the entry in SCENARIOS (below), set `faults`, write a function
  check(result, ctx) -> list[(criterion, passed, detail)]
and set check=..., ready=True, waiting_for=None. Nothing else changes: tests/scenarios and the
runner page pick it up. Part 1 only runs the scenarios; it does not write the other criteria.

Titles, `srs_criteria` (the SRS pass criteria, shown in the runner table) and `verifies` (the
requirement ids) are the Appendix B texts. `notes` carries the Appendix B fault-injection notes.
Default: faults start 20 min into the measured window and are held 5 min, unless a note says so.
Traffic setup (Part 1's job) is `runs`: a list of RunSetup, one per simulator run; every run type
is executable. Each run starts at `start_hhmm` (time of day, via build(start=...); Part 2 reads it
with ctx.clock.now_dt()). Most scenarios have one run (up-peak ones start at 07:00, balanced ones at
13:30). SIM-02 has two (lunch from 11:30, evening from 17:00). SIM-04 has three, all from 21:00
(Eco block, same seed): the Eco/Balanced pair ("same initial state, seed, quiet arrivals and
configuration": both runs use eco_quiet traffic at 2/min, only `requested_mode` differs, "eco" vs
"balanced", for Part 2 to apply), and a separate surge (10 min of
eco_quiet, then 75/min up_peak mix for 15 min, up to 30 min to clear; outside the normal
envelope, DECISIONS D-01). SIM-05 is mode "weekday": 24 h from 00:00 following the weekday
schedule, with per-block metrics in RunResult.blocks. SIM-08 starts at 16:30 so the fire alarm
(measured minute 20) falls on the 17:00 Rush transition. SIM-10 is the design-limit run: building
"design_limit" (64 cars, 8 groups, 128 stops, 200 tenants, 20,000 people), 400 calls/min,
400 lookups/min, 2,000 camera events/day, 50 dashboard users; latency hooks only. Its checks
are Part 2's ("Part 2 check").
rate=None means "the profile's default rate" (D-01). A check gets the RunResult and the finished
Context (bus log, services).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from ems.contracts.events import (AlertRaised, CallAssigned, CallRequested, CarStatusUpdated,
                                  EmsServerDown, EmsServerUp, HeartbeatLost, HeartbeatRestored,
                                  PassengerAlighted)

from .baseline import install_dispatch_standin
from .building import BUILDINGS
from .faults import Fault, ems_server_crash, fire_alarm, link_loss, measured, plugin_crash
from .harness import CLEAR_S, MEASURE_S, WARMUP_S, RunResult, run
from .traffic import PROFILES, SRS_ORIGINAL_PEAK_RATE, with_rate

from ems.priority import acceptance as p5     # Part 5 setups and checks (SIM-06 11 12 18)

Criteria = list  # of (criterion, passed, detail)


START_BY_PROFILE = {"up_peak": "07:00", "lunch": "11:30", "evening": "17:00",
                    "balanced": "13:30", "weekday": "00:00"}


@dataclass
class RunSetup:
    """One simulator run: traffic and timing (plain data, goes into the runner table)."""
    label: str
    profile: object                             # profile name, a Profile, or "weekday"
    rate: Optional[float] = None                # None = the profile's default rate (D-01)
    mode: str = "fixed"                         # "fixed" profile, or "weekday" (24 h schedule)
    faults: list[Fault] = field(default_factory=list)
    warmup_s: float = WARMUP_S
    measure_s: float = MEASURE_S
    clear_s: float = CLEAR_S
    setup: Optional[Callable] = None            # runs after build(), before traffic starts
    pair: Optional[str] = None                  # runs with the same pair id share seed and start state
    start_hhmm: Optional[str] = None            # time of day at run time 0 (default 00:00)
    warmup_profile: object = None               # traffic during the warm-up only (SIM-04 surge)
    building: Optional[str] = None              # "design_limit" (default: reference building)
    workload: Optional[dict] = None             # SIM-10 load hooks (workload.py)
    requested_mode: Optional[str] = None        # mode Part 2 must apply for this run ("eco", "balanced")
    envelope_note: Optional[str] = None         # set when the run is outside the normal envelope

    def __post_init__(self) -> None:
        if self.start_hhmm is None:                 # a start that puts the run in its own period
            self.start_hhmm = START_BY_PROFILE.get(self.profile_name, "00:00")

    @property
    def profile_name(self) -> str:
        return getattr(self.profile, "name", self.profile)




@dataclass
class Scenario:
    id: str
    title: str
    owner_part: int
    profile: Optional[str]                      # first run's profile name; None = not supported yet
    rate: Optional[float] = None
    faults: list[Fault] = field(default_factory=list)
    warmup_s: float = WARMUP_S
    measure_s: float = MEASURE_S
    clear_s: float = CLEAR_S
    setup: Optional[Callable] = None            # runs after build(), before traffic starts
    runs: list[RunSetup] = field(default_factory=list)   # several runs: SIM-02, SIM-04
    check: Optional[Callable] = None            # check(result, ctx) -> criteria or None
    ready: bool = False
    waiting_for: Optional[str] = None
    srs_criteria: str = ""                      # SRS pass criteria (text only, not checked here)
    verifies: str = ""                          # requirement ids
    notes: str = ""                             # Appendix B fault-injection notes

    def __post_init__(self) -> None:
        if not self.runs and self.profile is not None:
            self.runs = [RunSetup("run", self.profile, self.rate, faults=self.faults,
                                  warmup_s=self.warmup_s, measure_s=self.measure_s,
                                  clear_s=self.clear_s, setup=self.setup)]


def _setup_with_recorder(ctx) -> None:
    """Dispatch stand-in while Part 2 is a stub + a stand-in for Part 6: one DB record per
    delivered passenger (SIM-09 checks how much of that is lost in the crash)."""
    install_dispatch_standin(ctx)
    db = ctx.services["db"]
    ctx.bus.subscribe(PassengerAlighted, lambda e: db.write({"t": e.t, "call_id": e.call_id}))


# ---------------------------------------------------------------- SIM-09 (Part 1)
CRASH_AT = measured(20)
TAKEOVER_S = 8.0


def check_sim_09(result: RunResult, ctx) -> Criteria:
    bus, sim = ctx.bus, ctx.services["controllers"]
    downs, ups = bus.of_type(EmsServerDown), bus.of_type(EmsServerUp)
    if len(downs) != 1 or len(ups) != 1:
        return [("one EmsServerDown and one EmsServerUp", False,
                 f"{len(downs)} down, {len(ups)} up")]
    down, up = downs[0], ups[0]
    in_gap = [e for e in bus.of_type(PassengerAlighted) if down.t <= e.t <= up.t]
    arrived = [p for p in sim.passengers.values() if down.t <= p.arrived_at <= up.t]
    refused = [c for c in sim.command_log if down.t <= c[0] < up.t and c[4]]
    lost = ctx.services["failover"].lost_records
    oldest = max((down.t - r["t"] for r in lost), default=0.0)
    return [
        ("failover gap <= 10 s", up.gap_s <= 10, f"{up.gap_s:.1f} s"),
        ("crash happens at the injected time", down.t == CRASH_AT, f"t={down.t:.1f}"),
        ("calls still delivered during the gap", len(in_gap) > 0, f"{len(in_gap)} delivered"),
        ("every passenger arriving in the gap got a car",
         bool(arrived) and all(p.car_id for p in arrived), f"{len(arrived)} arrived"),
        ("no EMS command accepted while the server is down", not refused,
         f"{len(refused)} accepted"),
        ("at most 30 s of records lost", oldest <= 30.0,
         f"{len(lost)} records, oldest {oldest:.1f} s"),
        ("all calls cleared", result.metrics["calls_not_cleared"] == 0,
         f"{result.metrics['calls_not_cleared']} not cleared"),
    ]


# ---------------------------------------------------------------- SIM-16 (Part 1)
LINK_CAR, LINK_AT, LINK_S = "B", measured(20), 300.0          # half 1: link loss
CRASH_PLUGIN_AT, CRASH_PLUGIN_S = measured(40), 300.0         # half 2: dispatch plug-in crash


def check_sim_16(result: RunResult, ctx) -> Criteria:
    bus = ctx.bus
    out: Criteria = []
    # --- half 1: link loss
    lost = [e for e in bus.of_type(HeartbeatLost) if e.car_id == LINK_CAR]
    if lost:
        silent_since = max((e.t for e in bus.of_type(CarStatusUpdated)
                            if e.status.car_id == LINK_CAR and e.t < lost[0].t), default=0.0)
        out.append(("HeartbeatLost within 3 s of the last status",
                    lost[0].t - silent_since <= 3.0 + 1e-9,
                    f"{lost[0].t - silent_since:.1f} s after last status, "
                    f"{lost[0].t - LINK_AT:.1f} s after the link dropped"))
    else:
        out.append(("HeartbeatLost within 3 s of the last status", False, "never published"))
    back = [e for e in bus.of_type(HeartbeatRestored) if e.car_id == LINK_CAR]
    out.append(("HeartbeatRestored when the link returns",
                bool(back) and back[0].t >= LINK_AT + LINK_S,
                f"t={back[0].t:.0f}" if back else "never"))
    out.append(("all calls delivered", result.metrics["calls_not_cleared"] == 0,
                f"{result.metrics['calls_not_cleared']} not cleared"))
    # --- half 2: plug-in crash
    guard = ctx.services["dispatch_guard"]
    start, end = CRASH_PLUGIN_AT, CRASH_PLUGIN_AT + CRASH_PLUGIN_S
    calls = {e.call.call_id for e in bus.of_type(CallRequested) if start <= e.t < end}
    fallback_ids = {entry[1] for entry in guard.log if entry[3] and start <= entry[0] < end}
    assigned = {e.call_id for e in bus.of_type(CallAssigned) if start <= e.t < end}
    out.append(("every call during the plug-in crash assigned via fallback",
                bool(calls) and calls <= fallback_ids and calls <= assigned,
                f"{len(calls & fallback_ids & assigned)} of {len(calls)} calls"))
    alerts = [a for a in bus.of_type(AlertRaised) if a.kind == "dispatch_fallback"]
    out.append(("one dispatch_fallback alert (high, to fm) for the outage",
                len(alerts) == 1 and alerts[0].severity == "high" and alerts[0].recipients == ["fm"]
                and start <= alerts[0].t < end, f"{len(alerts)} alert(s)"))
    recovered = [a for a in bus.of_type(AlertRaised) if a.kind == "dispatch_recovered"]
    out.append(("info alert when the plug-in answers again",
                len(recovered) == 1 and recovered[0].severity == "info" and recovered[0].t >= end,
                f"{len(recovered)} alert(s)"))
    return out


# ---------------------------------------------------------------- the registry
DESIGN_CALLS_PER_MIN = 400.0
DESIGN_MEASURE_S = 3600.0
DESIGN_SHORT_REASON = "60 measured minutes took longer than about 5 min of wall time"
DESIGN_WORKLOAD = {"lookups_per_min": 400.0, "camera_per_day": 2000.0,
                   "dashboard_users": 50, "dashboard_requests": 5}

def _todo(sid: str, title: str, part: int, profile: Optional[str], srs: str, verifies: str,
          notes: str = "", waiting_for: Optional[str] = None, **kw) -> Scenario:
    return Scenario(sid, title, part, profile, waiting_for=waiting_for or f"Part {part} check",
                    srs_criteria=srs, verifies=verifies, notes=notes, **kw)


SCENARIOS: dict[str, Scenario] = {s.id: s for s in [
    _todo("SIM-01", 'Morning up-peak, all passengers with cards', 2, "up_peak", 'Average wait 25 s or less, none over 60 s; car shown within 1.5 s', 'NFR-P3, NFR-P5'),
    _todo("SIM-02", 'Lunch and evening peaks', 2, "lunch", 'As SIM-01', 'NFR-P3', runs=[RunSetup("lunch", "lunch"), RunSetup("evening", "evening")]),
    _todo("SIM-03", 'Office hours, inter-floor traffic (Balanced)', 2, "balanced", 'Average 30 s or less, none over 90 s', 'NFR-P3'),
    _todo("SIM-04", 'Eco quiet paired with Balanced; separate surge phase', 2, "eco_quiet", 'At least 15% run-time saving in every quiet pair; Eco mean <=45 s and ceiling <=120 s; surge switches under FR-14', 'FR-14, NFR-E1, NFR-P3', runs=[
        RunSetup("quiet eco", "eco_quiet", pair="quiet", start_hhmm="21:00", requested_mode="eco"),
        RunSetup("quiet balanced", "eco_quiet", pair="quiet", start_hhmm="21:00",
                 requested_mode="balanced"),
        RunSetup("surge", "up_peak", SRS_ORIGINAL_PEAK_RATE, start_hhmm="21:00",
                 warmup_s=600.0, measure_s=900.0, clear_s=1800.0, warmup_profile="eco_quiet",
                 envelope_note="Eco surge at 75/min for 15 min is outside the normal envelope "
                               "(D-01: the carryable peak rate is about 35/min)")]),
    _todo("SIM-05", 'Full weekday on the default schedule', 2, "weekday", 'Each mode meets targets; no one stranded at mode changes', 'FR-11, FR-15', runs=[RunSetup("weekday 24 h", "weekday", mode="weekday", warmup_s=0.0,
                                  measure_s=86400.0)]),
    Scenario("SIM-06", 'Car fault at up-peak, then a second removal requested', 5, "up_peak", setup=p5.setup_sim_06, check=p5.check_sim_06, ready=True, srs_criteria='Waiting calls retained/reassigned; faulted car not moved; second removal needs FM approval; degraded waits logged', verifies='FR-04, FR-33'),
    _todo("SIM-07", 'ACS outage for 15 minutes at up-peak', 3, "up_peak", 'Fresh cached permissions used, stale/missing cache restricted to public floors; security alerted; outage waits reported separately', 'FR-49, NFR-SE5, NFR-SE6', notes='ACS outage lasts 15 min (not 5), starting 20 min into the measured window.'),
    _todo("SIM-08", 'Fire alarm at Rush transition with active goods move; optional lockdown variant', 4, "up_peak", 'No controller command after alarm; goods move cancelled; healthy-car recovery requires FM confirmation; isolated cars remain excluded', 'FR-50, FR-55, NFR-SF1', notes='Goods move is enabled before the alarm; the fire alarm is raised at a Rush transition.', runs=[RunSetup("alarm at 17:00 Rush transition", "up_peak", start_hhmm="16:30",
                                   faults=[fire_alarm(measured(20))])]),
    Scenario("SIM-09", 'EMS server failure at up-peak', 1, "up_peak", faults=[
        ems_server_crash(CRASH_AT, TAKEOVER_S)], setup=_setup_with_recorder,
        check=check_sim_09, ready=True,
        srs_criteria='Failover within 10 s; service continues on controller logic', verifies='FR-54, NFR-R2'),
    _todo("SIM-10", 'Design-limit configuration and concurrent workload', 2, "balanced", 'At least 99% of calls assigned <=1 s; all dashboard changes <=1 s; at least 95% of credential responses <=1.5 s', 'NFR-P1, NFR-P2, NFR-P5, NFR-S2', runs=[RunSetup(
        "design limit", "balanced", DESIGN_CALLS_PER_MIN, start_hhmm="10:00", building="design_limit",
        measure_s=DESIGN_MEASURE_S, workload=DESIGN_WORKLOAD,
        envelope_note="SIM-10 checks software latency at the SRS design limits, not passenger "
                      "capacity; 3-year history is not preloaded (D-03)")]),
    Scenario("SIM-11", 'Accessible calls mixed into up-peak', 5, "up_peak", setup=p5.setup_sim_11, check=p5.check_sim_11, ready=True, srs_criteria='Priority below medical and above VIP; free assigned capacity; >=10 s door request; voice feedback; mode wait targets hold', verifies='FR-08, BR-10'),
    Scenario("SIM-12", 'Ten VIP calls during up-peak in one group', 5, "up_peak", setup=p5.setup_sim_12, check=p5.check_sim_12, ready=True, srs_criteria='At most one exclusive car; at most six exclusive starts per rolling hour; other calls remain within 60 s normal-service ceiling', verifies='FR-71, FR-72, FR-76', notes='Ten ordinary arrivals in one group are replaced by VIP arrivals at minutes 20-29.'),
    _todo("SIM-13", 'Overload bursts at the lobby, and a bulky item boarded twice', 4, "up_peak", 'Step-out message within 1 s; calls re-assigned; bulky item flagged', 'FR-100, FR-84', notes='Three overloads within 5 min, plus bulky-item and crowd-only camera events.'),
    _todo("SIM-14", 'Total power loss with a failed generator; one rescue device fails', 4, "up_peak", 'Entrapment alert for that car within 60 s; resume only after 2 min stable mains and FM confirmation', 'FR-110, FR-111', notes='Mains lost at minute 20, with one rescue-device report missing.'),
    _todo("SIM-15", 'Brake fault with passengers between floors at up-peak', 4, "up_peak", 'Entrapment alert within 10 s; calls re-assigned; no EMS command to the car', 'FR-112, NFR-R4'),
    Scenario("SIM-16", 'Controller link lost; dispatch plug-in crash', 1, "up_peak", faults=[
        link_loss(LINK_CAR, LINK_AT, LINK_S), plugin_crash(CRASH_PLUGIN_AT, CRASH_PLUGIN_S)],
        setup=install_dispatch_standin, check=check_sim_16, ready=True,
        srs_criteria='Car marked Disconnected in 3 s; fallback rule assigns all calls', verifies='FR-104, FR-105'),
    _todo("SIM-17", 'Conditional: duplicate/uncertain calls and 20 card actions', 4, "balanced", 'Only proven duplicates/cancelled calls removed; uncertain calls retained; sixth ordinary action starts limit; exits and assistance exempt', 'FR-120, FR-121, FR-125, BR-07'),
    Scenario("SIM-18", 'Medical priority starts during an occupied VIP trip', 5, "up_peak", setup=p5.setup_sim_18, check=p5.check_sim_18, ready=True, srs_criteria='VIP exclusivity yields; already-boarded riders are safely delivered; a healthy available car performs medical trip above VIP/accessible', verifies='FR-127, BR-10', notes='Medical priority starts after the VIP has boarded.'),
]}


def get(scenario_id: str) -> Scenario:
    return SCENARIOS[scenario_id]


def _run_rate(r: RunSetup) -> Optional[float]:
    if r.mode != "fixed":
        return None
    if r.rate is not None:
        return r.rate
    base = PROFILES[r.profile] if isinstance(r.profile, str) else r.profile
    return base.rate_per_min


def describe(s: Scenario) -> dict:
    """Plain data for the web API."""
    runs = [{"label": r.label, "profile": r.profile_name, "rate": _run_rate(r), "mode": r.mode,
             "warmup_s": r.warmup_s, "measure_s": r.measure_s, "pair": r.pair,
             "start_hhmm": r.start_hhmm,
             "faults": [f"{f.kind} at {f.at_s:.0f} s" for f in r.faults]} for r in s.runs]
    first = runs[0] if runs else None
    return {"id": s.id, "title": s.title, "owner_part": s.owner_part, "profile": s.profile,
            "rate": first["rate"] if first else None, "ready": s.ready,
            "waiting_for": s.waiting_for, "srs_criteria": s.srs_criteria,
            "verifies": s.verifies, "notes": s.notes, "runs": runs,
            "faults": first["faults"] if first else []}


def run_setup(r: RunSetup, seed: int, out_dir=None) -> RunResult:
    """Execute one run of a scenario (every run type: fixed, weekday, surge, design limit)."""
    profile = with_rate(r.profile, r.rate) if r.rate is not None else r.profile
    building = BUILDINGS[r.building]() if r.building else None
    extra = {"outside_normal_envelope": r.envelope_note} if r.envelope_note else {}
    if r.building:
        extra["measured_minutes"] = r.measure_s / 60.0
        if r.measure_s < 3600.0:
            extra["measured_minutes_reason"] = DESIGN_SHORT_REASON
    return run(profile, seed, r.faults, warmup_s=r.warmup_s, measure_s=r.measure_s,
               clear_s=r.clear_s, setup=r.setup or install_dispatch_standin, out_dir=out_dir,
               keep_ctx=True, start_hhmm=r.start_hhmm, traffic=r.mode,
               warmup_profile=r.warmup_profile, building=building, workload=r.workload,
               manifest_extra=extra)


def _run_summary(r: RunSetup, result: RunResult) -> dict:
    return {"label": r.label, "profile": result.profile, "start": result.start,
            "metrics": result.metrics, "blocks": result.blocks, "latency": result.latency,
            "event_digest": result.event_digest}


def run_scenario(scenario_id: str, seed: int, out_dir=None, metrics_only: bool = False) -> dict:
    """Run ONE seed headless (every run of the scenario). Returns plain data: metrics + criteria,
    or 'not ready'. One run: check(result, ctx). Several runs: check(results, ctxs), in run order.
    metrics_only=True runs the setup of a scenario whose check is still an empty slot and returns
    the metrics (per-block, latency, ...) with no pass/fail."""
    s = get(scenario_id)
    base = {"id": s.id, "title": s.title, "seed": seed, "owner_part": s.owner_part,
            "profile": s.profile}
    not_ready = s.check is None or not s.ready
    if not_ready and not (metrics_only and s.runs):
        return base | {"ready": False, "passed": None, "metrics": None, "criteria": [],
                       "runs": [], "message": f"not ready: waiting for {s.waiting_for}"}
    results = [run_setup(r, seed, out_dir) for r in s.runs]
    runs = [_run_summary(r, x) for r, x in zip(s.runs, results)]
    if not_ready:
        return base | {"ready": False, "passed": None, "metrics": results[0].metrics,
                       "criteria": [], "runs": runs, "metrics_only": True,
                       "message": f"metrics only: waiting for {s.waiting_for}"}
    if len(results) == 1:
        raw = s.check(results[0], results[0].ctx)
    else:
        raw = s.check(results, [x.ctx for x in results])
    criteria = [{"criterion": c, "passed": bool(ok), "detail": str(d)} for c, ok, d in (raw or [])]
    passed = bool(criteria) and all(c["passed"] for c in criteria)
    return base | {"ready": True, "passed": passed, "metrics": results[0].metrics,
                   "criteria": criteria, "runs": runs, "message": "PASS" if passed else "FAIL"}


def run_all_seeds(scenario_id: str, seeds=range(1, 11), out_dir=None) -> list[dict]:
    """Every seed on its own. The scenario passes only if every seed passes (no averaging)."""
    return [run_scenario(scenario_id, seed, out_dir) for seed in seeds]
