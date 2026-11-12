# Decisions

Project decisions that change a default. Each one has an id; code and run manifests refer to it.

## D-01 Peak traffic rate defaults to 35 persons/min (2026-11-12)
**Status:** in force until the stakeholders reply. **Owner:** Part 1.

The SRS asks for 75 persons/min in the up-peak, lunch and evening profiles. The capacity check
(`python -m ems.sim.capacity`, nearest-car baseline, seeds 1-3) showed that this is not carryable
by the 4 + 4 passenger cars in the simulator:

* analytic limit at full cars (21 persons): about 30 persons/min (LOW) and 23 (HIGH) from the lobby;
  75/min is about 34 lobby arrivals/min per group;
* measured: queues stay stable up to about 35/min; at 40-45/min the high-rise lobby queue starts
  to grow, at 60/min and above both groups fail to clear.

A rate revision was already pending with the stakeholders, so the defaults of `up_peak`, `lunch`
and `evening` are 35 persons/min (`ems/sim/traffic.py`). The SRS value stays available as
`SRS_ORIGINAL_PEAK_RATE = 75`, and every run can override the rate (`with_rate(profile, rate)`).
The evidence manifest records `rate_per_min` and `"decision": "D-01"` whenever a peak profile
runs at a rate other than 75. The profiles balanced (20/min) and eco (2/min) are unchanged.
