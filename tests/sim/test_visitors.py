"""Inter-floor trips: own-tenant trips use the card, visits use a valid QR pass."""
from ems.contracts.events import PassengerArrived
from ems.core.app import build
from ems.sim.traffic import PROFILES, TrafficGenerator


def lunch_day(seed=1, hours=3):
    ctx = build(seed=seed, parts=["sim"])
    TrafficGenerator.fixed(ctx, PROFILES["lunch"], 0.0, hours * 3600.0).start()
    ctx.clock.run_until(hours * 3600.0 - 1)
    return ctx, ctx.bus.of_type(PassengerArrived)


def test_visitor_qr_passes_are_valid_for_the_visited_floor_only():
    ctx, arrivals = lunch_day()
    acs, pop = ctx.services["acs"], ctx.services["population"]
    visits = [a for a in arrivals if a.credential_id.startswith("QR-")]
    assert len(visits) > 100
    for a in visits[:200]:
        perm = acs.lookup(a.credential_id)
        assert perm.valid and a.destination in perm.allowed_floors
        assert perm.tenant_id == pop.tenant_of_floor[a.destination]       # issued by the host
        assert pop.tenant_of_floor[a.origin] != perm.tenant_id            # visiting someone else
        assert a.origin != 1 and a.origin in range(*{"LOW": (2, 16), "HIGH": (16, 31)}[a.group])


def test_card_trips_are_within_the_cards_allowed_floors():
    ctx, arrivals = lunch_day()
    acs = ctx.services["acs"]
    for a in arrivals:
        if a.credential_id.startswith("card-"):
            perm = acs.lookup(a.credential_id)
            assert perm.valid and {a.origin, a.destination} <= perm.allowed_floors


def test_half_of_multi_floor_tenant_inter_floor_trips_stay_in_the_tenant():
    ctx, arrivals = lunch_day(hours=12)
    pop = ctx.services["population"]
    multi = {t for t, fl in pop.tenants.items() if len(fl) > 1}
    inter = [a for a in arrivals if a.origin != 1 and a.destination != 1
             and pop.tenant_of_floor[a.origin] in multi]
    own = [a for a in inter if pop.tenant_of_floor[a.destination]
           == pop.tenant_of_floor[a.origin]]
    assert len(inter) > 300 and abs(len(own) / len(inter) - 0.5) < 0.08
    assert all(a.credential_id.startswith("card-") for a in own)
