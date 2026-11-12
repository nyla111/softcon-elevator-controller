from ems.core.app import build
from ems.sim.population import ALL_TENANT_FLOORS, PUBLIC_FLOORS, Population


def pop(seed=1, **kw):
    return Population.build(build(seed=seed, parts=[]).rng, **kw)


def test_about_2500_employees_in_25_tenants():
    p = pop()
    assert len(p.employees) == 2500 and len(p.tenants) == 25
    assert sorted(len(f) for f in p.tenants.values()).count(2) == 4
    per_floor = [len(p.on_floor(f)) for f in ALL_TENANT_FLOORS]
    assert set(per_floor) <= {86, 87} and sum(per_floor) == 2500


def test_tenants_are_contiguous_in_floor_order_and_never_cross_l15_l16():
    for seed in range(1, 11):
        p = pop(seed)
        floors = [f for t in p.tenants.values() for f in t]
        assert floors == ALL_TENANT_FLOORS                       # in order, every floor once
        for t in p.tenants.values():
            assert t == list(range(t[0], t[0] + len(t)))        # contiguous
            assert not (15 in t and 16 in t)


def test_ids_home_floor_and_allowed_floors():
    p = pop()
    e = p.lookup("card-L18-007")
    assert e.home_floor == e.default_floor == 18
    assert e.allowed_floors == frozenset(p.tenants[e.tenant_id]) | PUBLIC_FLOORS
    two = next(t for t, fl in p.tenants.items() if len(fl) == 2)
    for e in p.employees:
        if e.tenant_id == two:
            assert e.allowed_floors == frozenset(p.tenants[two]) | PUBLIC_FLOORS
    assert p.lookup("card-L1-001") is None


def test_exact_accessible_and_vip_counts():
    p = pop()
    assert sum(e.accessible for e in p.employees) == round(0.05 * 2500)
    assert sum(e.vip for e in p.employees) == round(0.01 * 2500)


def test_same_seed_same_population_and_registered_by_sim():
    assert pop(3).employees == pop(3).employees
    assert pop(3).tenants != pop(4).tenants or pop(3).employees != pop(4).employees
    ctx = build(seed=3, parts=["sim"])
    assert ctx.services["population"].employees == pop(3).employees
