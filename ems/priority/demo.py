"""Part 5 walkthrough and scenario table (headless, deterministic, simulated time only).

    python -m ems.priority.demo                      # 3-5 minute walkthrough of every workflow
    python -m ems.priority.demo scenarios            # SIM-06/11/12/18, seeds 1-10, per criterion
    python -m ems.priority.demo scenarios --seeds 1-3 --json runs/part5.json

The walkthrough runs Part 1's simulator at 07:00 (up-peak, Rush) with Part 5 registered and the
STAND-INS of ems/priority/standins.py for Part 2 / Part 3 / Part 4 (still contract stubs). It
says so at the start. Every action goes through the public Part 5 API with staff cards of the
StaffDirectory double; nothing touches private simulator state except the fault injection
helpers Part 1 provides for tests and demos.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta

from ems.contracts.events import (AlertRaised, GoodsMoveScheduled, PanelMessage,
                                  PassengerBoarded, PriorityTripEnded, PriorityTripStarted)
from ems.core.app import build
from ems.sim.faults import car_fault, schedule_fault
from ems.sim.traffic import PROFILES, TrafficGenerator

from .standins import install_scenario_standins, integration_gaps

START = datetime(2026, 11, 16, 7, 0)
SECURITY, FM, TECH, ADMIN = ("staff-security-1", "staff-fm-1", "staff-tech-1",
                             "staff-tenant-admin-T01")
SHOWN = (PriorityTripStarted, PriorityTripEnded, GoodsMoveScheduled, AlertRaised)


class Walkthrough:
    def __init__(self, seed: int = 1) -> None:
        self.ctx = build(seed=seed, start=START)
        self.installed = install_scenario_standins(self.ctx)
        self.p = self.ctx.services["priority"]
        self.sim = self.ctx.services["controllers"]
        self.seen = 0
        TrafficGenerator.fixed(self.ctx, PROFILES["up_peak"], 0.0, 4 * 3600.0).start()

    # ---------- output ----------
    def clock(self) -> str:
        return (START + timedelta(seconds=self.ctx.clock.now())).strftime("%H:%M:%S")

    def say(self, text: str) -> None:
        print(f"[{self.clock()}] {text}")

    def title(self, text: str) -> None:
        print(f"\n=== {text}")

    def events(self) -> None:
        """Print the Part 5 relevant events published since the last call."""
        log = self.ctx.bus.log
        for ev in log[self.seen:]:
            if isinstance(ev, SHOWN):
                t = (START + timedelta(seconds=ev.t)).strftime("%H:%M:%S")
                fields = {k: v for k, v in vars(ev).items() if k not in ("t", "credential_ids")}
                if isinstance(ev, GoodsMoveScheduled):
                    fields["credentials"] = len(ev.credential_ids)
                print(f"    {t} {type(ev).__name__} {fields}")
        self.seen = len(log)

    def run(self, seconds: float) -> None:
        self.ctx.clock.run_until(self.ctx.clock.now() + seconds)

    def run_until(self, cond, limit: float, step: float = 1.0) -> bool:
        end = self.ctx.clock.now() + limit
        while self.ctx.clock.now() < end:
            if cond():
                return True
            self.run(step)
        return cond()

    # ---------- the steps ----------
    def intro(self) -> None:
        self.title("0. Setup")
        self.say("Up-peak traffic (35/min, D-01) from 07:00, Part 1 simulator, Part 5 registered")
        print("    STAND-INS in use (not production integration):")
        for gap in integration_gaps(self.ctx):
            print(f"      - {gap}")
        self.run(600)
        self.seen = len(self.ctx.bus.log)

    def vip(self) -> None:
        self.title("1. VIP (FR-70/71/72) and accessible calls (FR-08)")
        self.run(300)
        ok = [d for d in self.p.decisions if d["ok"]]
        no = [d for d in self.p.decisions if not d["ok"]]
        self.say(f"VIP decisions so far: {len(ok)} exclusive, {len(no)} served as ordinary "
                 f"({sorted({d['reason'] for d in no})}); starts in the last hour: "
                 f"{self.p.vip_starts_in_last_hour()} / 6")
        voice = [m for m in self.ctx.bus.of_type(PanelMessage) if m.voice]
        if voice:
            self.say(f"{len(voice)} accessible voice messages, e.g. to {voice[-1].target}:")
            print(f"    EN: {voice[-1].text_en}\n    VI: {voice[-1].text_vi}")
        self.seen = len(self.ctx.bus.log)

    def medical(self) -> None:
        self.title("2. Medical priority during an occupied VIP trip (FR-127)")
        boarded = {}

        def vip_on_board() -> bool:
            for e in self.ctx.bus.of_type(PassengerBoarded):
                trip = self.p.trip_of_call(e.call_id)
                if trip is not None and trip.kind == "vip" and trip.car_id == e.car_id:
                    boarded["trip"] = trip
                    return True
            return False

        if not self.run_until(vip_on_board, 3600):
            self.say("no exclusive VIP trip in the next hour (caps): step skipped")
            return
        vip = boarded["trip"]
        self.say(f"VIP on board car {vip.car_id} ({vip.origin} -> {vip.destination})")
        floors = self.ctx.services["building"].tenant_floors[vip.group]
        out = self.p.request_medical(SECURITY, floors[len(floors) // 2], 1)
        self.say(f"security requests medical L{floors[len(floors) // 2]} -> L1: {out}")
        self.events()
        trip = self.p.trips.get(out.ref)
        if trip is None:
            return
        self.run_until(lambda: trip.phase == "at_origin", 300)
        self.say(f"car {trip.car_id} at L{trip.origin}, doors held open; patient loaded (30 s)")
        self.run(30)
        self.say(f"depart: {self.p.medical_depart(SECURITY, trip.trip_id)}")
        self.run_until(lambda: trip.phase == "at_destination", 300)
        self.run(30)
        self.say(f"finish: {self.p.finish_trip(SECURITY, trip.trip_id)}")
        self.events()
        vip_rider = self.sim.passengers[vip.call_id]
        self.run_until(lambda: vip_rider.alighted_at is not None, 600)
        self.say(f"VIP rider delivered to L{vip_rider.destination}: "
                 f"{vip_rider.alighted_at is not None and not vip_rider.evacuated}")

    def maintenance(self) -> None:
        self.title("3. Fault, then a second removal in Rush (FR-30/32/33)")
        m = self.p.maintenance
        group = self.ctx.services["building"].group("LOW")
        faulty, second, third = [c for c in group.cars if c not in self.p.holds][:3]
        schedule_fault(self.ctx, car_fault(faulty, "F-DEMO", self.ctx.clock.now(), 300.0))
        self.run(0)
        self.events()
        ask = m.request_maintenance(TECH, second)
        self.say(f"technician asks for car {second}: {ask}")
        self.events()
        self.run(60)
        self.say(f"FM approves: {m.approve_removal(FM, ask.ref)}")
        ask2 = m.request_maintenance(TECH, third)
        self.say(f"technician asks for car {third}: {ask2.reason}; the FM does not answer")
        self.run(600)
        self.say(f"after 10 min: request {ask2.ref} is {m.requests[ask2.ref].status}; late "
                 f"approval -> {m.approve_removal(FM, ask2.ref).reason}")
        self.say(f"technician exits car {second}: {m.exit_maintenance(TECH, second).reason}")
        self.events()

    def goods(self) -> None:
        self.title("4. Goods move on the service car (FR-80/81/82)")
        g = self.p.goods
        now = self.ctx.clock.now()
        limits = self.ctx.services["reports"].configuration["house_rule_limits"] \
            if "reports" in self.ctx.services else {}
        self.say(f"FM shows house rules (values from Part 6 configuration): "
                 f"{g.show_house_rules(FM, limits)}")
        out = g.request(ADMIN, now + 60, now + 960, ["mover-card-1", "mover-card-2"])
        self.say(f"tenant admin requests a 15 min move: {out}")
        self.say(f"FM approves: {g.approve(FM, out.ref)}")
        self.events()
        self.run(61)
        car = out.car_id
        self.say(f"move {g.moves[out.ref].status}; car {car} accepts mover card: "
                 f"{g.service_car_accepts('mover-card-1', car)}, other card: "
                 f"{g.service_car_accepts('card-L2-001', car)}")
        self.run(900)
        self.say(f"move {g.moves[out.ref].status}; car {car} accepts any card again: "
                 f"{g.service_car_accepts('card-L2-001', car)}")

    def summary(self) -> None:
        self.title("5. Where to see the scenario results")
        print("    python -m ems.priority.demo scenarios     (or /runner/: SIM-06, 11, 12, 18)")
        print("    Every run reports 'integration with real Part 2/3/4 services' as BLOCKED")
        print("    until those parts are merged; see docs/priority/VERIFICATION.md.")

    def all(self) -> None:
        self.intro()
        self.vip()
        self.medical()
        self.maintenance()
        self.goods()
        self.summary()


def _seeds(text: str) -> list[int]:
    if "-" in text:
        a, b = text.split("-")
        return list(range(int(a), int(b) + 1))
    return [int(x) for x in text.split(",")]


def scenario_table(seeds: list[int], out_json: str | None = None) -> dict:
    from ems.sim.scenarios import run_scenario
    data = {}
    for sid in ("SIM-06", "SIM-11", "SIM-12", "SIM-18"):
        rows = [run_scenario(sid, s) for s in seeds]
        data[sid] = [{k: v for k, v in r.items() if k != "runs"} for r in rows]
        print(f"\n{sid}  overall: " + " ".join(f"{r['seed']}:{r['message']}" for r in rows))
        names = list(dict.fromkeys(c["criterion"] for r in rows for c in r["criteria"]))
        for name in names:
            marks = "".join(
                next(("P" if c["passed"] else "F" for c in r["criteria"]
                      if c["criterion"] == name), "-") for r in rows)
            print(f"  {marks}  {name}")
    if out_json:
        with open(out_json, "w") as fh:
            json.dump(data, fh, indent=1, default=str)
    return data


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("what", nargs="?", default="walkthrough", choices=["walkthrough", "scenarios"])
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--seeds", default="1-10")
    ap.add_argument("--json")
    args = ap.parse_args(argv)
    if args.what == "scenarios":
        scenario_table(_seeds(args.seeds), args.json)
    else:
        Walkthrough(args.seed).all()


if __name__ == "__main__":
    main()
