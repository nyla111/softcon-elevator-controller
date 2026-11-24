# Saved run history (Part 6, issue #62)

Start a live run in `/building/`, advance it, then use **Save run**. Part 1 writes a JSON
snapshot in `runs/live/` and exposes it through `/api/runs` and `/api/runs/{run_id}`.
Open `/history/` to select two or three saved runs. The page compares run metadata, overall
and per-mode waits, per-car travel within each group, run time, mode changes, Eco step-ups,
alerts, faults, and the recorded rolling wait series. This supports Balanced versus Eco,
wear-policy, and before/after-fault comparisons when the corresponding runs are saved.

Compare runs with the same seed, rate, duration, and building layout when attributing a
difference to a policy. A saved run is a snapshot at the time **Save run** was clicked; it
does not continue updating. The per-run wear table shows floors traveled during that run,
while `/dashboard/` shows Part 5's calendar month and six-month counters.
