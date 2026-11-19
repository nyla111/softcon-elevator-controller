"""Reproduce pending #33 evidence without changing simulator behavior.

Run from the repository root. Pending row failures are printed, while trace and
schema regressions raise AssertionError. This script is deliberately outside CI.
"""

import csv
import math
from pathlib import Path

from ems.contracts.events import AlertRaised, CallAssigned, CallRequested, ControllerFault
from ems.core.app import build
from ems.sim.scripted import ScriptedTraffic, load, run_list

root = Path('docs/passenger_lists')
print('Schema and CI selection')
active = sorted(root.glob('*.csv'))
pending = sorted((root / 'pending').glob('*.csv'))
print('active:', ', '.join(p.name for p in active))
print('pending excluded from active glob:', ', '.join(p.name for p in pending))
for path in active + pending:
    load(path)
print(f'All {len(active) + len(pending)} CSVs parse')
for name in ('sim14_power_loss.csv', 'sim15_brake_fault.csv'):
    path = root / 'pending' / name
    rows = list(csv.DictReader(path.open()))
    assert 5 <= len(rows) <= 20
    assert all(r['expect'] and '#33' in r['note'] and '#55' in r['note'] for r in rows)
    for seed in (1, 7, 42):
        ctx, checks = run_list(load(path), seed=seed)
        print(f'{name} seed={seed}: {sum(c.ok for c in checks)}/{len(checks)} checks')
        for c in checks:
            if not c.ok:
                print(f'  FAIL row {c.row}: {c.subject} expected {c.expected}; got {c.actual}')

print('Supplementary trace, seed=1; supported CSV behavior only')
for name, before, fault, end in (
    ('sim14_power_loss.csv', 13.999, 14, 261),
    ('sim15_brake_fault.csv', 25213.999, 25214, 25334),
):
    ctx = build(seed=1)
    traffic = ScriptedTraffic(ctx, load(root / 'pending' / name))
    traffic.start()
    sim = ctx.services['controllers']
    ctx.clock.run_until(before)
    status = sim.status('A')
    print(name, 'before fault:', f'passengers={status.passengers}, position={status.position}')
    print('waiting call IDs:', [p.passenger_id for p in sim.passengers.values()
                                if p.boarded_at is None])
    assert status.passengers == 1 and status.position % 1 != 0
    if name.startswith('sim14'):
        for t in (14, 74, 140, 141, 259, 260):
            ctx.clock.run_until(t)
            safety = ctx.services['car_states']
            print(f't={t}: safety_active={safety.safety_active()}, '
                  f'recovery_eligible={safety.recovery_eligible()}')
            assert safety.safety_active()
        ctx.clock.run_until(261)
        print('t=261: safety_active=', ctx.services['car_states'].safety_active())
        assert not ctx.services['car_states'].safety_active()
    ctx.clock.run_until(end)
    cmds = [c for c in sim.command_log if fault <= c[0] < end
            and (name.startswith('sim14') or c[1] == 'A')]
    print('EMS commands in forbidden window:', cmds)
    assert not cmds
    if name.startswith('sim14'):
        assigned = [e for e in ctx.bus.of_type(CallAssigned) if fault <= e.t < end]
        print('ordinary assignments in forbidden window:', assigned)
        assert not assigned
    else:
        fault_event = ctx.bus.of_type(ControllerFault)[0]
        print('fault:', fault_event)
        alerts = [a for a in ctx.bus.of_type(AlertRaised) if a.kind == 'entrapment']
        print('entrapment:', alerts)
        assert fault_event.between_floors and alerts[0].t - fault <= 10
        requested = [e for e in ctx.bus.of_type(CallRequested)
                     if e.call.call_id == 'sim15-already-waiting']
        p = sim.passengers['sim15-already-waiting']
        print('retained request:', requested, 'boarded_at=', p.boarded_at,
              'wait_s=', p.wait_s, 'car=', p.car_id)
        assert len(requested) == 1 and p.boarded_at > fault and p.car_id != 'A'
        assert p.wait_s == p.boarded_at - 25210
print('Deadline cutoffs:', math.nextafter(74.0, math.inf),
      math.nextafter(25224.0, math.inf))
