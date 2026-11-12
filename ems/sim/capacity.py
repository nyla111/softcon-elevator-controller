"""How much up-peak traffic can one group carry? (measurement only, no pass/fail)

Run the up_peak profile with the nearest-car baseline dispatcher at several total rates and
report, per group (LOW / HIGH): calls cleared, average and max wait, and whether the lobby
queue grows. Also an analytic round-trip time per group to compare against.

    python -m ems.sim.capacity            # prints the tables (about 1-2 minutes)

"Queue grows" = the number of passengers waiting at the lobby is, on average over the last
10 measured minutes, more than twice and more than 5 persons above the first 10 measured
minutes (queue sampled every 30 s).

Analytic round trip for a car with `load` passengers (all from the lobby, uniform floors):
    RTT = lobby stop + 2 x E[highest floor distance] x s_per_floor + S x tenant stop
    lobby stop  = door open + max(min dwell, load x s_per_passenger) + door close
    tenant stop = door open + max(min dwell, load / S x s_per_passenger) + door close
    S           = expected number of different floors = F x (1 - (1 - 1/F)^load)
    capacity    = cars x load / RTT   (persons per minute, lobby -> tenant floors)
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from ems.contracts import config

from .baseline import install_nearest_car
from .harness import CLEAR_S, MEASURE_S, WARMUP_S, run
from .passengers import Passenger
from .population import PASSENGER_GROUPS, TENANT_FLOORS
from .traffic import PROFILES

RATES = (30, 35, 40, 45, 50, 60, 75)      # total persons per minute
SEEDS = (1, 2, 3)
TENANT_FLOOR_COUNT = sum(len(f) for f in TENANT_FLOORS.values())


# ---------- analytic ----------
def _distance(floor: int) -> int:
    """Floors travelled from the lobby (L1) to this floor."""
    return floor - 1


def analytic_round_trip(group: str, load: float) -> dict:
    p = config.SIM
    dist = sorted(_distance(f) for f in TENANT_FLOORS[group])
    n = len(dist)
    # E[max of `load` uniform picks] = sum d_k x ((k/n)^load - ((k-1)/n)^load)
    top = sum(d * ((k / n) ** load - ((k - 1) / n) ** load) for k, d in enumerate(dist, 1))
    stops = n * (1 - (1 - 1 / n) ** load)
    door = p["door_open_s"] + p["door_close_s"]
    lobby = door + max(p["min_dwell_s"], load * p["s_per_passenger"])
    tenant = door + max(p["min_dwell_s"], load / stops * p["s_per_passenger"])
    rtt = lobby + 2 * top * p["s_per_floor"] + stops * tenant
    cars = len(config.GROUPS[group]["cars"])
    return {"rtt_s": rtt, "stops": stops, "top_floors": top,
            "capacity_per_min": 60.0 * cars * load / rtt}


# ---------- measurement ----------
def queue_length(passengers: list[Passenger], t: float) -> int:
    """Passengers who had arrived by t and had not boarded yet."""
    return sum(1 for p in passengers
               if p.arrived_at <= t and (p.boarded_at is None or p.boarded_at > t))


def queue_grows(passengers: list[Passenger], warmup_s: float, measure_s: float) -> bool:
    window = min(600.0, measure_s / 3)
    def mean(start: float) -> float:
        times = [start + 30.0 * i for i in range(int(window // 30))]
        return sum(queue_length(passengers, t) for t in times) / len(times)
    first, last = mean(warmup_s), mean(warmup_s + measure_s - window)
    return last > 2 * first and last - first > 5


@dataclass
class Row:
    rate: int
    group: str
    arrived: int
    cleared: int
    avg_wait_s: float
    max_wait_s: float
    growing_seeds: int
    seeds: int
    uncleared_seeds: int


def measure(rates=RATES, seeds=SEEDS, warmup_s: float = WARMUP_S,
            measure_s: float = MEASURE_S, clear_s: float = CLEAR_S) -> list[Row]:
    rows = []
    for rate in rates:
        profile = replace(PROFILES["up_peak"], name=f"up_peak_{rate}", rate_per_min=rate)
        per_group = {g: dict(arrived=0, cleared=0, avgs=[], max=0.0, grow=0, bad=0)
                     for g in PASSENGER_GROUPS}
        for seed in seeds:
            r = run(profile, seed, warmup_s=warmup_s, measure_s=measure_s, clear_s=clear_s,
                    setup=install_nearest_car, out_dir=None, keep_ctx=True)
            everyone = list(r.ctx.services["controllers"].passengers.values())
            for g in PASSENGER_GROUPS:
                mine = [p for p in everyone if p.group == g]
                measured = [p for p in mine if warmup_s <= p.arrived_at < warmup_s + measure_s]
                waits = [p.wait_s for p in measured if p.wait_s is not None]
                d = per_group[g]
                d["arrived"] += len(measured)
                d["cleared"] += sum(1 for p in measured if p.alighted_at is not None)
                d["avgs"].append(sum(waits) / len(waits) if waits else 0.0)
                d["max"] = max(d["max"], max(waits, default=0.0))
                d["grow"] += queue_grows(mine, warmup_s, measure_s)
                d["bad"] += any(p.alighted_at is None for p in mine)
        for g, d in per_group.items():
            rows.append(Row(rate, g, d["arrived"], d["cleared"], sum(d["avgs"]) / len(d["avgs"]),
                            d["max"], d["grow"], len(seeds), d["bad"]))
    return rows


def format_tables(rows: list[Row], loads=(10, 21)) -> str:
    out = ["Analytic round trip (up-peak, all passengers from the lobby)",
           "group  load  stops  top_floor  RTT_s  capacity_pax/min"]
    for g in PASSENGER_GROUPS:
        for load in loads:
            a = analytic_round_trip(g, load)
            out.append(f"{g:<6} {load:>4} {a['stops']:>6.1f} {a['top_floors']:>9.1f} "
                       f"{a['rtt_s']:>6.0f} {a['capacity_per_min']:>16.1f}")
    out += ["", "Measured (nearest-car baseline, seeds pooled)",
            "rate  group  cleared/arrived  avg_wait_s  max_wait_s  queue_grows  not_cleared"]
    for r in rows:
        out.append(f"{r.rate:>4}  {r.group:<5}  {r.cleared:>6}/{r.arrived:<7}  "
                   f"{r.avg_wait_s:>10.1f}  {r.max_wait_s:>10.1f}  "
                   f"{r.growing_seeds:>4}/{r.seeds} seeds  {r.uncleared_seeds:>4}/{r.seeds} seeds")
    return "\n".join(out)


if __name__ == "__main__":
    print(format_tables(measure()))
