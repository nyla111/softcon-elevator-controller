"""End an unboarded VIP pickup only after the overloaded door cycle closes."""
from ems.contracts.types import DoorState


class OverloadMixin:
    def on_overload(self, event):
        trip = self.trip_of_car(event.car_id)
        if trip is not None and trip.kind == "vip" and not trip.boarded:
            self.overloaded_pickups[event.car_id] = (trip.trip_id, event.floor, event.t)

    def on_overload_status(self, event):
        status = event.status
        pending = self.overloaded_pickups.get(status.car_id)
        if pending is None or event.t < pending[2]:
            return
        trip = self.trips.get(pending[0])
        if trip is None or trip.boarded:
            self.overloaded_pickups.pop(status.car_id, None)
            return
        if status.fault_code or status.disconnected:
            return  # Existing fault/emergency owners end the trip without unsafe commands.
        if status.floor == pending[1] and status.doors in (DoorState.CLOSING, DoorState.CLOSED):
            self.overloaded_pickups.pop(status.car_id, None)
            self.end_trip(trip, True, "overload_not_boarded")
