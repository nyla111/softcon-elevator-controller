# SIM-08 branch acceptance evidence

23 November 2026; audited main2885a9e, stacked on P3 draft #92.
The task owner approved the pairing in DECISIONS D-09. This is branch evidence,
not acceptance on main and not P3 owner/interface approval.

Both required runs pass every seed1–10: original35/min peak traffic plus a full-service
quiet occupied-VIP companion. Each run independently passes9 criteria,180 checks total.
Paired diagnostic seed42 also passes. The fixed peak VIP lead is22s; companion29s.
An earlier attempt with29s peak timing exposed a legitimate no-show cancellation before
fire in seed8. A later22s quiet attempt left seed8 waiting for pickup. Neither failure
was waived: fixed per-workload setup timing was corrected and both complete sets rerun.

The checker requires real active goods/VIP, actual onboard lobby evacuation, zero EMS
commands/assignments until valid FM recovery, cancelled goods/VIP without automatic
restart, isolated-car exclusion, retained lockdown configuration and genuine service
after confirmation. Early confirmation fails; physical clear/reset alone cannot recover.
Every rider onboard at the alarm needs a real lobby-alighting event. Lobby-bound riders
legitimately have `evacuated=False` because that field means premature destination exit;
event evidence proves their exit during recall. The occupied VIP must additionally be
marked evacuated. No synthetic runtime events or production rule changes are used.

Machine-readable criteria, event digests and raw-trace SHA256 hashes are in
[sim08_acceptance_evidence.json](sim08_acceptance_evidence.json). Large local raw traces:
`.tmp/part4-sim08-peak-v3-evidence` and `.tmp/part4-sim08-occupied-v4-evidence`.
Per-seed pytest XML/logs: `.tmp/part4-sim08-peak-v3-logs`; occupied XML:
`.tmp/part4-sim08-occupied-v4.xml`. Final diagnostic42 traces:
`.tmp/part4-sim08-diagnostic42-v4`.

Additional validation:183 Safety/integration passes;43 registry/CSV checks pass
(2 unrelated cases deselected); two HTTP scenario read endpoints pass with one existing
Starlette deprecation warning; Ruff CI selection and `git diff --check` pass.
The active SIM-08 CSV separately passes actual evacuation/reset/FM/service outcomes.
Full CI and required review remain gates. FR-52 commissioning and runtime contracts
remain unapproved and fail-closed; these fire tests do not establish FR-52 acceptance.
