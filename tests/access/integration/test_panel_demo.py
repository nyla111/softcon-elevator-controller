"""Check the real panel endpoint, ACS permission, and Access event flow."""

from fastapi.testclient import TestClient

from ems.contracts.events import AccessRefused, CallRequested, PanelMessage
from ems.core.app import build
from ems.web.server import app


def test_panel_refuses_employee_card_for_another_group_floor():
    # Isolate the web app from other tests and use a real simulator + Access.
    previous_ctx = app.state.ctx
    ctx = build(seed=1, parts=["sim", "access"])
    app.state.ctx = ctx

    try:
        client = TestClient(app)
        response = client.post(
            "/api/access/panel/request",
            json={"credential_id": "card-L5-001", "destination": 18},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["accepted"] is False
        assert "Not recognised" in data["message"]
        assert "card-L5-001" not in response.text
        assert len(ctx.bus.of_type(AccessRefused)) == 1
        assert len(ctx.bus.of_type(PanelMessage)) == 1
        assert ctx.bus.of_type(CallRequested) == []
    finally:
        app.state.ctx = previous_ctx


def test_panel_accepts_employee_card_for_own_floor():
    previous_ctx = app.state.ctx
    ctx = build(seed=1, parts=["sim", "access"])
    app.state.ctx = ctx

    try:
        response = TestClient(app).post(
            "/api/access/panel/request",
            json={"credential_id": "card-L5-001", "destination": 5},
        )
        assert response.status_code == 200
        assert response.json()["accepted"] is True
        assert len(ctx.bus.of_type(CallRequested)) == 1
        assert ctx.bus.of_type(AccessRefused) == []
    finally:
        app.state.ctx = previous_ctx
