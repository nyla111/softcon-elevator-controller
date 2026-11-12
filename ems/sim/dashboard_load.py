"""Dashboard load for SIM-10: N users fetch GET /api/state at the same time.

Fires `users` threads at once (a barrier lines them up), each doing `requests_each` requests
against the FastAPI app of ems/web/server.py through a TestClient of its own, and records every
response time. The app serves the given context while this runs. Response time is real
(wall-clock) time by nature, so perf_counter is used here; the simulation clock is not touched
(the run is paused while the burst runs).
"""
from __future__ import annotations

import threading
import time

from .workload import summarize


def run(ctx, users: int = 50, requests_each: int = 5) -> dict:
    from fastapi.testclient import TestClient

    from ems.web import server

    times: list[float] = []
    errors: list[str] = []
    guard = threading.Lock()
    barrier = threading.Barrier(users)

    def user() -> None:
        client = TestClient(server.app)
        barrier.wait()
        for _ in range(requests_each):
            started = time.perf_counter()
            try:
                ok = client.get("/api/state").status_code == 200
            except Exception as exc:                     # a failed request is a finding, not a crash
                ok, _ = False, errors.append(repr(exc))
            elapsed = time.perf_counter() - started
            with guard:
                times.append(elapsed)
                if not ok:
                    errors.append("bad status")

    with server.app.state.lock:
        previous = server.app.state.ctx
        server.app.state.ctx = ctx
    try:
        threads = [threading.Thread(target=user) for _ in range(users)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        with server.app.state.lock:
            server.app.state.ctx = previous
    return {"users": users, "requests": len(times), "errors": len(errors),
            "response_s": summarize(times)}
