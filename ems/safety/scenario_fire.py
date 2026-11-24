"""SIM-08 real goods, VIP, onboard fire and explicit FM recovery journey."""
from ems.contracts import events as E
from ems.contracts.types import CarState, Mode, Role
from ems.sim.faults import measured
from ems.sim.scripted import ScriptedTraffic, ScriptedTrip

ALARM = measured(20)
CLEAR = ALARM + 300
CONFIRM = CLEAR + 7


def setup_sim_08(ctx, *, vip_lead_s=10):
    p, states, sim = ctx.get("priority"), ctx.get("car_states"), ctx.get("controllers")
    log = ctx.services.setdefault("fire_acceptance", {"attempts": []})
    sim.inject_fault("B", "scenario:isolation")
    states.request("C", CarState.MAINTENANCE, "scenario:maintenance")
    access = ctx.get("access")
    access.lock_floors("staff-security-1", [5])
    permission = access.permission("card-L5-001")
    log["locked_before"] = not access.floor_access.lockdown.can_visit_floor(5, permission)
    move = p.goods.request("staff-admin-T01", 0, ALARM + 900, ["staff-admin-T01"], "S")
    assert move.ok and p.goods.approve("staff-fm-1", move.ref).ok
    log["move"] = move.ref
    # Reserve the rolling-hour budget for one explicit, genuinely authorized VIP.
    # Background VIP calls remain admitted and served normally after FM revocation.
    candidates = [person for person in ctx.get("population").employees if person.vip
                  and not person.accessible and person.home_floor >= 20]
    target = max(candidates, key=lambda person: person.home_floor)
    log["vip_floor"] = target.home_floor
    for person in ctx.get("population").employees:
        if person.vip:
            assert p.revoke_vip("staff-fm-1", person.credential_id).ok

    def prepare_vip():
        # Explicit FM manual Rush before the scheduled17:00 transition; expires there.
        ctx.get("dispatcher").set_manual_mode(Mode.RUSH, "HIGH")
        assert p.set_vip_quota("staff-fm-1", target.credential_id, None).ok

    def attempt_vip():
        # Submit the real lobby request even when every car is occupied. P5 owns
        # candidate selection; the default peak run may legitimately await pickup.
        # The separate quiet companion must additionally prove actual VIP boarding.
        index = log.get("vip_attempts", 0) + 1
        log["vip_attempts"] = index
        ref = f"sim08-explicit-vip-{index}"
        ScriptedTraffic(ctx, [ScriptedTrip(ctx.clock.now(), 1, target.home_floor,
            card=target.credential_id, accessible=False, vip=True,
            passenger_id=ref)]).start()
        def inspect():
            if p.trip_of_call(ref) is not None:
                log["vip_ref"] = ref
            elif ctx.clock.now() < ALARM - 4:
                ctx.clock.call_later(2, attempt_vip)
        ctx.clock.call_later(0.001, inspect)

    def before_alarm():
        rider = sim.passengers.get(log.get("vip_ref"))
        trip = p.trip_of_call(log.get("vip_ref"))
        log["onboard_before"] = bool(rider and rider.boarded_at is not None
                                      and rider.alighted_at is None)
        log["active_vip"] = bool(trip and trip.kind == "vip")
        log["vip_car"] = trip.car_id if trip else None
        log["onboard_ids"] = [person.passenger_id for person in sim.passengers.values()
                              if person.boarded_at is not None and person.alighted_at is None]
        log["goods_active"] = p.goods.moves[move.ref].status == "active"

    def inspect_fire():
        log["lock_suspended"] = access.floor_access.lockdown.can_visit_floor(5, permission)

    def confirm():
        log["attempts"].append((ctx.clock.now(), states.confirm_recovery(Role.FM)))

    ctx.clock.call_at(ALARM - 30, prepare_vip)
    # Keep peak requests inside a complete pickup/no-show door cycle, even
    # when the current dispatcher immediately chooses a car at the lobby.
    # The occupied companion separately allows time for actual boarding.
    ctx.clock.call_at(ALARM - vip_lead_s, attempt_vip)
    ctx.clock.call_at(ALARM - 0.001, before_alarm)
    ctx.clock.call_at(ALARM + 1, inspect_fire)
    ctx.clock.call_at(ALARM + 0.5,
                      lambda: p.revoke_vip("staff-fm-1", target.credential_id))
    ctx.clock.call_at(CLEAR + 1, confirm)  # Before physical reset: must fail.
    ctx.clock.call_at(CONFIRM, confirm)
    ctx.clock.call_at(CONFIRM + 0.001, lambda: log.update(
        locks_restored=not access.floor_access.lockdown.can_visit_floor(5, permission)))
    ScriptedTraffic(ctx, [ScriptedTrip(CONFIRM + 1, 1, 6, card="card-L6-001",
                                      accessible=False, vip=False,
                                      passenger_id="sim08-after-confirm")]).start()


def setup_occupied_sim_08(ctx):
    # Quiet companion gives the genuine VIP time to board; peak requests stay
    # inside their pickup/no-show window. Both use the same production services.
    setup_sim_08(ctx, vip_lead_s=29)


def check_sim_08(result, ctx, *, require_occupied_vip=False):
    from .acceptance import _fire_goods_check
    log = ctx.services.get("fire_acceptance", {})
    sim, states = ctx.get("controllers"), ctx.get("car_states")
    alarms, clears = ctx.bus.of_type(E.FireAlarm), ctx.bus.of_type(E.FireAlarmCleared)
    commands = [c for c in sim.command_log if ALARM <= c[0] < CONFIRM]
    assignments = [e for e in ctx.bus.of_type(E.CallAssigned) if ALARM <= e.t < CONFIRM]
    vip = sim.passengers.get(log.get("vip_ref"))
    ended = [e for e in ctx.bus.of_type(E.PriorityTripEnded)
             if e.kind == "vip" and e.car_id == log.get("vip_car")
             and e.t == ALARM and e.cancelled]
    # New passengers after recovery are legitimate; only the interrupted trip
    # identity is forbidden to restart automatically.
    restarted = [trip for trip in list(ctx.get("priority").trips.values())
                 + ctx.get("priority").finished if trip.kind == "vip"
                 and trip.call_id == log.get("vip_ref") and trip.started_at > ALARM]
    post = sim.passengers.get("sim08-after-confirm")
    status = ctx.get("car_states").recovery_status()
    onboard = log.get("onboard_ids", [])
    # `Passenger.evacuated` means "left before the intended destination". A
    # lobby-bound rider correctly has that flag false even when leaving in recall.
    # Require an actual lobby alighting event for every onboard identity instead.
    lobby_alighted = {e.call_id for e in ctx.bus.of_type(E.PassengerAlighted)
                      if e.floor == 1 and ALARM <= e.t <= CLEAR}
    evacuated = bool(onboard) and set(onboard) <= lobby_alighted
    return [
        ("fire at 17:00 Rush transition", bool(alarms) and alarms[0].t == ALARM
         and result.start.endswith("16:30:00"), f"alarm={alarms[0].t if alarms else None}"),
        ("real active VIP, onboard passengers and FM-approved goods before alarm",
         bool(onboard) and (not require_occupied_vip or log.get("onboard_before", False))
         and log.get("active_vip", False)
         and log.get("goods_active", False), str(log)),
        ("zero EMS controller commands throughout fire handling",
         bool(alarms) and not commands and not assignments,
         f"attempted={len(commands)}; assignments={len(assignments)}"),
        ("healthy recovery requires explicit FM confirmation",
         bool(clears) and log.get("attempts") == [(CLEAR + 1, False), (CONFIRM, True)]
         and states.recovery_log == [(CONFIRM, "fm_confirmed")]
         and status["safety_inputs_clear"] and not status["missing_controller_reports"],
         str(log.get("attempts"))),
        ("isolated cars remain excluded", states.state("B") is CarState.FAULTED
         and states.state("C") is CarState.MAINTENANCE
         and not ({"B", "C"} & set(states.dispatchable("LOW"))), "FaultB; maintenanceC"),
        (_fire_goods_check(ctx, alarms[0]) if alarms else ("goods cancellation", False, "no fire")),
        ("onboard evacuation and VIP cancellation without automatic restart",
         evacuated and (not log.get("onboard_before") or bool(vip and vip.evacuated))
         and bool(ended) and not restarted, f"riders={len(onboard)}; ends={len(ended)}; restarts={len(restarted)}"),
        ("configured lockdown suspended and restored without erasure",
         log.get("locked_before", False) and log.get("lock_suspended", False)
         and log.get("locks_restored", False)
         and ctx.get("access").floor_access.lockdown.locked_floors == {5}, "P3 draft interface"),
        ("post-confirm healthy service", bool(post and post.alighted_at is not None
         and not post.evacuated and post.boarded_at >= CONFIRM), f"post={post is not None}"),
    ]


def check_paired_sim_08(results, contexts):
    """User-approved pairing: full peak run AND a controlled occupied-VIP fire run."""
    if len(results) != 2 or len(contexts) != 2:
        return [("both required SIM-08 runs present", False, "default plus occupied-VIP required")]
    return [(f"{label}: {name}", passed, detail)
            for label, result, ctx, occupied in zip(
                ("default Rush transition", "occupied-VIP companion"), results, contexts, (False, True))
            for name, passed, detail in check_sim_08(result, ctx, require_occupied_vip=occupied)]
