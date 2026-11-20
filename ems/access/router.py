"""Demo-only destination panel API for Access & Panels (Part 3).

The route sends a real PassengerArrived event through the event bus.
AccessManager decides whether the request is accepted or refused.
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ems.contracts.events import (
    AccessRefused,
    CallRequested,
    PanelMessage,
    PassengerArrived,
)

router = APIRouter(prefix="/api/access", tags=["Access demo"])


class PanelInput(BaseModel):
    credential_id: str = Field(min_length=1, max_length=128)
    destination: int


@router.post("/panel/request")
def submit_panel_request(body: PanelInput, request: Request) -> dict:
    """Submit one lobby panel request using real ACS and AccessManager."""
    with request.app.state.lock:
        ctx = request.app.state.ctx
        if type(ctx.services["access"]).__name__ == "StubAccess":
            raise HTTPException(503, "The real Access service is not registered")

        building = ctx.services["building"]
        group = building.group_of_floor.get(body.destination)
        if group is None and body.destination in building.parking_floors:
            group = building.parking_floors[body.destination][0]
        if group is None:
            raise HTTPException(400, "Choose an existing tenant or parking floor")

        panel_id = "demo-lobby-panel"
        call_id = f"panel-demo-{len(ctx.bus.log)}"
        original_log_length = len(ctx.bus.log)

        ctx.bus.publish(
            PassengerArrived(
                t=ctx.clock.now(),
                passenger_id=call_id,
                panel_id=panel_id,
                origin=building.lobby,
                destination=body.destination,
                group=group,
                credential_id=body.credential_id,
                accessible=False,
                vip=False,
            )
        )

        new_events = ctx.bus.log[original_log_length:]
        accepted = any(
            isinstance(event, CallRequested) and event.call.call_id == call_id
            for event in new_events
        )
        refused = any(
            isinstance(event, AccessRefused) and event.panel_id == panel_id
            for event in new_events
        )
        message = next(
            (
                event.text_en
                for event in new_events
                if isinstance(event, PanelMessage) and event.target == panel_id
            ),
            None,
        )

        if not accepted and not refused:
            raise HTTPException(500, "Access produced neither a call nor a refusal")
        if refused and not message:
            raise HTTPException(500, "Access refused without a panel message")

        return {
            "accepted": accepted,
            "destination": body.destination,
            "message": message or "Destination accepted. Elevator requested.",
        }
