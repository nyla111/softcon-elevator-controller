# Part 5 contract change requests

Part 5 did not edit `ems/contracts/` or `ems/core/`. These are the changes Part 5 needs from
the tech lead (contract) or from another part. Each one says what is blocked today and what
Part 5 does meanwhile. Status: proposed (2026-11-14), base `8b4afdc`; no answer on the issues yet. The full
handshake for CCR-01 (semantics, failure handling, example) is in INTEGRATION.md section 4. Issues #9-#12 opened for the four blockers;
CCR-03 is not a blocker and has no issue.

| ID | Blocks | Owner | Kind |
|---|---|---|---|
| CCR-01 ([#9](https://github.com/nyla111/softcon-elevator-controller/issues/9)) | FR-71/72 exclusive VIP trips, FR-08 capacity, FR-76 order in real dispatch | Tech lead (contract) + Part 2 (calls) | document Part 5 API as a Protocol |
| CCR-02 ([#10](https://github.com/nyla111/softcon-elevator-controller/issues/10)) | FR-08 "EMS requests >= 10 s doors" (SIM-11) | Tech lead + Part 1 (adapter) | new optional command argument |
| CCR-03 | Reporting of medical / send-to-lobby car state | Tech lead + Part 4 | state or documentation |
| CCR-04 ([#11](https://github.com/nyla111/softcon-elevator-controller/issues/11)) | FR-82 end of restriction, SIM-08 "goods move cancelled" evidence | Tech lead | new event |
| CCR-05 ([#12](https://github.com/nyla111/softcon-elevator-controller/issues/12)) | Production authorisation of security / FM / technician / tenant admin | Part 1 (mock ACS, population) + Part 3 | data, no contract change |

---

## CCR-01 Priority service Protocol (Part 2 calls Part 5 before assigning)

**Problem.** `Dispatcher` is Part 2's and subscribes to `CallRequested` before Part 5
(`core/app.py` load order: dispatch, then priority). Part 5 cannot change a call after Part 2
has assigned it, and must not publish `CallAssigned`. The contract table says Part 5 offers
"events only + own API", but that API is not written down anywhere, so Part 2 has nothing to
code against.

**Proposal** (`contracts/interfaces.py`, new Protocol, implemented by Part 5 only):

```python
class PriorityService(Protocol):             # Part 5, ctx.services["priority"]
    def claim_call(self, call: Call) -> Optional[CarId]:
        """VIP calls: Part 5 starts an exclusive trip and returns its car; Part 2 then
        publishes CallAssigned(call, car) without register_stop. None = serve normally.
        Idempotent per call_id. During the call Part 5 asks would_breach_ceiling_without(car)
        and, for a car with waiting calls, reassign_calls_of(car) and relies on the RETURNED
        list (calls not moved -> that car is not used). If the trip ends before boarding,
        Part 5 registers the pick-up stop on the same car."""
    def rank(self, call: Call) -> int: ...          # BR-10 / FR-76, lower = first
    def car_ok_for(self, call: Call, status: CarStatus) -> bool: ...   # FR-08 free capacity
    def door_dwell_s(self, call: Call) -> float: ...                   # FR-08 (with CCR-02)
```

**Consumers:** Part 2 (only). **Compatibility:** additive; the stub (`contracts/stubs.py`) would
return `None` / `int(call.priority)` / `True` / 5.0. **Tests:** `tests/priority/test_vip.py`,
`test_policy.py`; reference integration: `ems/priority/standins.py::PriorityAwareNearestCar`.

## CCR-02 Door-time request in ControllerAdapter (FR-08)

**Problem.** FR-08 / SIM-11 asks that the EMS *requests* at least 10 s of open doors for an
accessible passenger. `ControllerAdapter` has no such command. Today the simulated controller
applies 10 s by itself when it sees an accessible passenger (simulator truth), so SIM-11 can
observe the 10 s, but the EMS never asked for it.

**Proposal:** `register_stop(car_id, floor, call_id, min_dwell_s: Optional[float] = None) -> bool`
(the controller may still refuse: False). Part 2 passes `priority.door_dwell_s(call)`.
**Owner:** tech lead (contract), Part 1 (simulator honours it, `command_log` records it).
**Compatibility:** optional argument, default unchanged. **Tests:** simulator dwell test;
SIM-11 criterion "EMS requests the >= 10 s door time" turns into a real check of `command_log`.

## CCR-03 Car state for medical / send-to-lobby trips

**Problem.** `CarState` has no state for a medical or send-to-lobby trip. Part 5 holds
`VIP_EXCLUSIVE` for them (the only exclusive-trip state) and records the real kind; reports
that read `CarStateChanged` alone would show a medical car as "VIP".

**Proposal (choose one, Part 4 decides):** (a) document that `VIP_EXCLUSIVE` means "exclusive
priority trip" and that `PriorityTripStarted.kind` gives the reason (no code change), or
(b) add `PRIORITY_EXPRESS` ranked above `VIP_EXCLUSIVE` (renumbering `CarState`, so every part
must use names, not values). Part 5 recommends (a) for Release 1.

## CCR-04 GoodsMoveEnded event

**Problem.** `GoodsMoveScheduled` announces a move; nothing announces that it ended early
(cancelled by the FM / tenant, refused at start, or cancelled by fire / power loss). Part 3
keeps restricting the car to the movers' credentials until the scheduled end, and SIM-08
(Part 4) needs evidence that the active move was cancelled.

**Proposal:**
```python
@dataclass
class GoodsMoveEnded(Event):          # pub: Part 5
    move_id: str
    car_id: CarId
    cancelled: bool
    reason: str                       # "completed" | "cancelled" | "start_refused" | "emergency:fire" ...
```
**Consumers:** Part 3 (lift FR-82 restriction), Part 4 (SIM-08), Part 6 (reports).
**Compatibility:** additive. Meanwhile `priority.goods.service_car_accepts(credential, car)` gives
the live answer.

## CCR-05 Staff credentials known to the ACS

**Problem.** Part 5 authorises every action by the credential's roles from
`AccessService.permission()` (never a role string chosen by the caller). The mock ACS
(`ems/sim/devices.py`) and population know only employees (roles EMPLOYEE, ACCESSIBLE, VIP) and
visitors, so no security, FM, technician or tenant-admin card exists in a simulated run. Part 5's
scenarios use a labelled double (`standins.StaffDirectory`).

**Proposal:** Part 1 adds a few staff cards to the population / mock ACS (`Role.SECURITY`,
`Role.FM`, `Role.TECHNICIAN`, one `Role.TENANT_ADMIN` per tenant with `tenant_id`), and Part 3's
`permission()` returns them. No contract change (`Role` already has these values).
