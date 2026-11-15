"""FR-43: Record access attempts and let Security view the log."""

from ..contracts.events import (
    AccessRefused,
    CallReplaced,
    CallRequested,
)
from ..contracts.types import Role


class AccessLogging:
    def __init__(self, ctx, retention_days=90):
        self.ctx = ctx
        self.retention_days = retention_days

        # Store access history
        self.logs = []

        # Listen to access events
        ctx.bus.subscribe(
            CallRequested, self.on_call_requested
        )

        ctx.bus.subscribe(
            CallReplaced, self.on_call_replaced
        )

        ctx.bus.subscribe(
            AccessRefused, self.on_access_refused
        )

    def delete_old_logs(self):
        """Delete logs older than the retention period."""

        # STEP 1: Get current simulation time
        now = self.ctx.clock.now()

        # STEP 2: Convert days into seconds
        max_age = self.retention_days * 24 * 60 * 60

        # STEP 3: Keep only recent logs
        self.logs = [
            log for log in self.logs
            if now - log["time"] < max_age
        ]

    def add_log(
        self, time, credential_id, panel_id,
        action, destination=None, reason=None
    ):
        """Save a single access record."""

        # STEP 1: Delete old records
        self.delete_old_logs()

        # STEP 2: Create a log entry
        log = {
            "time": time,
            "credential_id": credential_id,
            "panel_id": panel_id,
            "action": action,
            "destination": destination,
            "reason": reason,
        }

        # STEP 3: Save the log
        self.logs.append(log)

    def on_call_requested(self, event):
        """Record an accepted elevator request."""

        call = event.call

        # Public floor requests without a card
        # are not credential uses.
        if call.credential_id is None:
            return

        self.add_log(
            time=event.t,
            credential_id=call.credential_id,
            panel_id=call.panel_id,
            action="ALLOWED",
            destination=call.destination,
        )

    def on_call_replaced(self, event):
        """Record a successful Other Floor choice."""

        call = event.new_call

        if call.credential_id is None:
            return

        self.add_log(
            time=event.t,
            credential_id=call.credential_id,
            panel_id=call.panel_id,
            action="REPLACED",
            destination=call.destination,
        )

    def on_access_refused(self, event):
        """Record an access denial."""

        self.add_log(
            time=event.t,
            credential_id=event.credential_id,
            panel_id=event.panel_id,
            action="REFUSED",
            reason=event.reason,
        )

    def get_logs(self, security_card):
        """Only Security can read access logs."""

        # STEP 1: Verify Security directly with ACS
        try:
            permission = self.ctx.services["acs"].lookup(
                security_card
            )
        except TimeoutError:
            raise PermissionError(
                "Cannot verify Security while ACS is offline"
            )

        # STEP 2: Check permission
        if (
            not permission.valid
            or permission.from_cache
            or Role.SECURITY not in permission.roles
        ):
            raise PermissionError(
                "Only Security may view access logs"
            )

        # STEP 3: Delete expired logs
        self.delete_old_logs()

        # STEP 4: Return a copy of the records
        return [log.copy() for log in self.logs]
