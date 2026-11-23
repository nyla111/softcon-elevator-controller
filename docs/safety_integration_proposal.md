# Safety integration decisions for owner review

Status: PROPOSED, not an approved contract or changed SRS. Main baseline 2885a9e.
Owners: P1/tech lead nyla111, P2 NguyenCongNam1405, P3 baovy511,
P4 baobinh-asatsugi, P5 Mancupfire. Related #46, #61, #82, #83, #84.
No default commissioned capacity, acknowledgement, or permission is inferred.

## FR-52 power contract (P1/P2/P4 and tech lead)

Proposed additive event, published only by P1:

```python
EmergencyPowerStatus(t, episode_id, revision, generator_running,
                     ready_cars, commissioned_count, service_order, parking_order)
```

- `episode_id` uniquely identifies the physical mains-loss episode; `revision`
  increases on every readiness/configuration change. P1 supplies both, not a caller.
- `ready_cars` is the set whose controllers attest emergency-power readiness.
- Count and ordered car IDs come from explicit commissioning configuration. Missing,
  invalid or inconsistent configuration grants nothing and raises a configuration
  alert. A test's explicit capacity is not a commissioned deployment value.
- P4 selects at most the configured count from the supplied service order, intersected
  with ready, healthy, non-isolated cars. Fire/firefighter service overrides every grant.
- P4 requests per-car enablement. P1 publishes a matching enable/refusal acknowledgement
  carrying episode, revision, car and request ID. A request alone is not permission.
- Existing global `GeneratorReady` does not grant scoped EMS permission. Normal
  recovery remains held throughout limited service.

Proposed controller methods: `ep_enable(car, episode, revision, request_id)`,
`ep_park(car, lobby, permit)`, `ep_register_stop(car, floor, call_id, dwell, permit)`.
Every method returns acceptance/refusal; accepted movement also produces a terminal
`EmergencyPowerCommandResult(t, episode_id, revision, request_id, car_id, outcome)`
from P1, where outcome is `completed` or `refused`. Completion must be based on the
physical command's recorded destination/doors, never inferred by P4 from elapsed time.

P4 owns an immutable, current, single-use command permit and one outstanding movement
reservation across parking/service (conservative VER-10 serialization). P1 validates
it and its own physical interlocks immediately before each request. P2 requests a
service permit before calling the adapter; P4 alone requests lobby parking. No global
`safety_active()` bypass is permitted. Adapter refusal releases the exact reservation;
accepted requests wait for matching completion/refusal. Missing acknowledgements hold
the sequence indefinitely; no unapproved numerical timeout or synthetic completion.

Proposed startup order: park eligible non-service cars serially at the lobby before
enabling limited passenger service. An unready car receives no request. A refusal
advances the sequence, with the refused car excluded, never labelled successfully parked.
Waiting calls retain identity/time; boarded riders retain ownership. Controller rescue
state must be compatible with enabling; readiness never substitutes for rescue evidence.

Withdrawal, reduced configuration, isolation, EMS failure, mains restoration and new
fire invalidate permits immediately. P1 enforces withdrawal independently of bus
subscriber order. Old/duplicate/future revisions, acknowledgements and tokens cannot
enable movement. Mains restoration retires EP permissions; ordinary dispatch waits for
120 continuous seconds, clear signals, normal non-isolated controllers and valid FM.

Decisions needed: approve these fields/methods and startup order; supply actual
commissioned count/orders (or explicitly leave runtime fail-closed); decide whether
another serial service order is required. Alternatives: a P1-orchestrated parking
adapter under P4 permits, or the proposed P4 orchestrator. Neither may fake completion.

Acceptance: readiness truth table, useful real-dispatch passenger service, capacity
limit, serial lobby completion OR refusal, no pre-ready/fire commands including refused
attempts, withdrawal/stale/duplicate/new-episode tests, isolated cars and FR-55 exit.

## Pre-admission policy (P3/P2/P4, #61/#82)

Proposed P4 helper `ActionLimiter.evaluate(credential_id, exit=False,
assistance=False, fire=False) -> ActionDecision(allowed, message_en, message_vi,
retry_at)`. Existing `allow` remains a compatibility wrapper. The sixth ordinary action
starts exactly 60 seconds of refusal; denied repeats do not extend it. A callback on
the third episode per simulated calendar day lets P3 write the credential only to its
security log and send an anonymous security notice. P3 displays the returned refusal
message within one simulated second.

P3 invokes policy after authentication/authorization and before publishing an accepted
call or cancellation. Exit/accessible/alarm/medical/fire classification comes from
trusted entry-point semantics and credential roles, not client-supplied privilege flags.
P2 supplies waiting/boarded/cancelled lifecycle facts keyed by call/action ID. An action
ID proves a replay only when issued/correlated by its owner; matching card/floor/load/
camera alone never proves a duplicate. No Safety subscription drops accepted calls.

For FR-120, exceeding estimated riders plus two only triggers review; remove only a
proven duplicate or requesting-credential cancellation. Uncertain calls remain and are
flagged. FR-122 cancellation requires an observed full door cycle with reliable proof
that nobody boarded that hall call; uncertainty is retained. Record cancellation once
as an action under FR-121, exempting exit and assistance.

Scope decision recorded from the task owner: implement FR-120/122, with actual runtime
integration and meaningful acceptance tests. Draft #96 implements this proposal; #100
provides complete SIM-17 seed evidence. P3 owner/interface approval remains a merge gate.
FR-121 helper correction is requested independently. Mandatory
FR-125/BR-07 real-entry-point evidence cannot be deferred. SIM-17 is not N/A by default.
Acceptance: 20 ordinary actions, sixth-action and midnight boundaries, proven and
uncertain cases, delivered refusal and third-episode notice, real exemption entry points.

## Effective fire lockdown (P3, #83)

Proposal: retain configured locked floors; suspend effective lockdown while the existing
Safety fire/firefighter or unrecovered fire hold is active. Reapply configured locks
after successful FR-55 FM recovery, before new ordinary admission. Other permission
checks still apply; suspension does not grant arbitrary tenant access. Existing
CarStates state/safety queries suffice; no erasure of another owner's configuration.
Alternative for team decision: restore enforcement on physical fire reset while normal
dispatch remains held. Test both edges against the approved choice before sign-off.

## Occupied-car medical ordering (P5/P2, #84)

Proposal: empty healthy candidate first. If only an occupied healthy candidate exists,
record medical as pending without express/reassignment of that car. Retain existing
boarded destinations and waiting-call ownership; after actual destination alighting,
acquire the medical hold and start express. No synthetic alighting or fixed delay.
Fire/power/fault invalidates a pending start. Review medical urgency/no-available-car
notice while pending; a controller-required diversion must be explicit trace evidence.

## FR-113 scope (P4/P5/team)

Propose implementation: observe independent mechanical fault episodes, count a new
episode only after a matching clear ends the previous one, and send FM a review notice
when the rolling 30-day count crosses from fewer than three to at least three. Duplicate
fault/telemetry messages do not add episodes. Use the simulated clock and per-car data.
P5 retains per-episode FR-30 notification ownership. The task owner selected implementation,
not deferral: PR #91 implements independent fault episodes and an inclusive rolling30d
threshold with runtime tests. Review the window/interface in that PR before merge.

## SIM-08 evidence pairing

The task owner explicitly approved two mandatory full real-service runs per seed1–10:
the default35/min up-peak Rush-transition/fire scenario, and a controlled quiet companion
requiring an actual occupied VIP at the alarm. The default may have an active VIP waiting
for pickup while ordinary riders are onboard. Both runs independently require goods
cancellation, zero EMS commands during fire handling, actual onboard evacuation, no
restart of the interrupted VIP, isolated-car exclusion, retained lockdown configuration
and explicit FM recovery followed by real service. Neither run substitutes for the other.
No production rule or safety assertion is relaxed. Paired results remain pending.
