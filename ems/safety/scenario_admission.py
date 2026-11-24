"""SIM-17 actual Access admission plus mandatory real-entry-point exemptions."""
from ems.contracts import events as E
from ems.contracts.types import Role
from ems.sim.scripted import ScriptedTraffic, ScriptedTrip


def setup_sim_17(ctx):
    access = ctx.get("access")
    def ordinary(floor):
        return next(person.credential_id for person in ctx.get("population").on_floor(floor)
                    if not person.accessible and not person.vip)
    card, hall_card, uncertain_card, episode_card = (ordinary(floor) for floor in (5, 7, 8, 9))
    proof = ctx.services.setdefault("admission_acceptance", {})
    refs = [access.tap_card(card, "sim17:panel", 1, "LOW") for _ in range(20)]
    proof["twenty_actions"] = len(refs) == 20 and all(refs[:5]) and refs[5:] == [None] * 15
    proof["accepted_ids"] = refs[:5]
    proof["refusal_received"] = len([e for e in ctx.bus.of_type(E.PanelMessage)
        if e.target == "sim17:panel" and e.text_en and e.text_vi and e.t <= 1
        and "Too many" in e.text_en]) == 15

    # Real in-car entry point: three uncertain extra calls are retained, not accused.
    assert access.tap_car_card("A", "staff-fm-1")
    for floor in (5, 6, 7):
        assert access.press_car_floor("A", floor)
    ids = list(access.actions.car_calls)
    proof["uncertain_retained"] = len(ids) == 3 and any(
        a.kind == "uncertain_car_calls" for a in ctx.bus.of_type(E.AlertRaised))
    proof["other_credential_cannot_cancel"] = not access.cancel_action("staff-security-1", ids[0])
    proof["owner_cancel"] = access.cancel_action("staff-fm-1", ids[0])
    token = access.begin_car_action("A")
    assert access.press_car_floor("A", 8, action_id=token)
    count = len(access.actions.car_calls)
    proof["exact_replay"] = access.press_car_floor("A", 8, action_id=token) \
        and len(access.actions.car_calls) == count

    # Exemptions use existing trusted simulator arrival events and real service APIs.
    ScriptedTraffic(ctx, [ScriptedTrip(1, 1, 5, card=card, accessible=True, vip=False,
                                      passenger_id="sim17-accessible"),
                         ScriptedTrip(1, 5, 1, card=card, accessible=False, vip=False,
                                      passenger_id="sim17-exit")]).start()
    ctx.clock.call_at(2, lambda: ctx.get("devices").alarm_button("A"))

    # At60 seconds the fixed block expires; exact replays consume no new call.
    def after_block():
        proof["boundary_allowed"] = access.tap_card(card, "sim17:panel", 1, "LOW") is not None
        hall = access.request_hall_call(hall_card, "sim17:hall", 1, 7, "LOW")
        uncertain = access.request_hall_call(uncertain_card, "sim17:uncertain", 1, 8, "LOW")
        access.actions.mark_uncertain(uncertain)
        proof["hall"], proof["uncertain_hall"] = hall, uncertain
    ctx.clock.call_at(59.999, lambda: proof.update(
        still_blocked=access.tap_card(card, "sim17:panel", 1, "LOW") is None))
    ctx.clock.call_at(60, after_block)

    def medical():
        p = ctx.get("priority")
        # Medical request bypasses the action limiter even with Security blocked.
        for _ in range(6):
            access.actions.limiter.allow("staff-security-1")
        outcome = p.request_medical("staff-security-1", 8, 1)
        proof["medical_exempt"] = outcome.ok
        if outcome.ok:
            p.finish_trip("staff-security-1", outcome.ref)
    ctx.clock.call_at(240, medical)

    def fire():
        # A live limiter block must not obstruct a real fire function.
        for _ in range(6):
            access.actions.limiter.allow("staff-security-1")
        ctx.get("devices").fire.alarm()
        proof["fire_exempt"] = ctx.get("car_states").fire
    ctx.clock.call_at(300, fire)
    ctx.clock.call_at(330, ctx.get("devices").fire.clear)
    ctx.clock.call_at(335, ctx.get("controllers").reset_fire)
    ctx.clock.call_at(337, lambda: proof.update(
        recovered=ctx.get("car_states").confirm_recovery(Role.FM)))
    def episode():
        for _ in range(6):
            access.tap_card(episode_card, "sim17:episodes", 1, "LOW")
    for when in (400, 460, 520):
        ctx.clock.call_at(when, episode)


def check_sim_17(result, ctx):
    proof = ctx.services.get("admission_acceptance", {})
    cancelled = ctx.bus.of_type(E.CallCancelled)
    hall = proof.get("hall")
    uncertain = proof.get("uncertain_hall")
    noshow = [e for e in cancelled if e.call_id == hall and e.reason == "no_show"]
    retained = uncertain and not any(e.call_id == uncertain for e in cancelled)
    arrivals = {e.call.call_id for e in ctx.bus.of_type(E.CallRequested)}
    inbox = ctx.get("notifier").inbox
    alarm = any(2 <= n[0] <= 12 and "passenger_alarm" in n[2] and "security" in n[1] for n in inbox)
    medical = any(240 <= n[0] <= 250 and "medical_request" in n[2] and "security" in n[1]
                  for n in inbox)
    episodes = [n for n in inbox if "action_limit_review" in n[2] and n[0] == 520]
    no_commands = not [c for c in ctx.get("controllers").command_log if 300 <= c[0] < 337]
    return [
        ("twenty real credential actions; sixth refuses before acceptance",
         proof.get("twenty_actions", False) and proof.get("refusal_received", False), str(proof.get("accepted_ids"))),
        ("fixed cooldown exact boundary", proof.get("still_blocked", False)
         and proof.get("boundary_allowed", False), "59.999 denied;60 allowed"),
        ("only owner cancellations or exact proven replays remove calls",
         proof.get("other_credential_cannot_cancel", False) and proof.get("owner_cancel", False)
         and proof.get("exact_replay", False) and proof.get("uncertain_retained", False), "P3-owned actions"),
        ("proven no-show counted; uncertain hall call retained", bool(noshow) and bool(retained),
         f"no-shows={len(noshow)}; uncertain retained={bool(retained)}"),
        ("real accessible and exit admission exempt during active block",
         {"sim17-accessible", "sim17-exit"} <= arrivals, "existing PassengerArrived entry point"),
        ("alarm, medical and fire functions remain exempt", alarm and medical
         and proof.get("medical_exempt", False) and proof.get("fire_exempt", False), "actual APIs/inbox"),
        ("third daily episode reaches security without public credential data",
         len(episodes) == 1 and episodes[0][1] == ["security"]
         and "card-" not in episodes[0][3], f"notices={len(episodes)}"),
        ("fire recovery retains command silence and FM gate",
         no_commands and proof.get("recovered", False), "all attempted commands checked"),
    ]
