from .constants import MAX_HISTORY_LENGTH
from .room_profiles import resolve_room_profile
from .types import AreaConfig


class AreaState:
    """Tracks occupancy and activity in a single area."""

    def __init__(self, area_id: str, area_config: AreaConfig):
        self.id = area_id
        self.config = area_config
        self.last_motion: float = 0
        self.last_contact: float = 0
        self.last_contact_open: bool | None = None
        self.last_off: float = 0  # Timestamp of last motion-OFF event
        self.stale_since: float | None = None
        self.cleared_by: str | None = None
        self.activity_history = []  # List of (timestamp, activity_type) tuples
        self.is_indoors = area_config.get("indoors", True)
        self.is_exit_capable = area_config.get("exit_capable", False)
        self.is_transition = area_config.get("transition", False)
        self.profile = resolve_room_profile(area_config)
        self.profile_name = self.profile.name
        self._occupied: bool = False
        # Missing durable indoor state is uncertainty, not confident vacancy.
        self.state_known: bool = not self.is_indoors
        self.last_occupied_at: float = 0  # Timestamp when area was last occupied

    @property
    def occupancy(self) -> int:
        """Occupancy: 1 if occupied, 0 if not."""
        return 1 if self._occupied else 0

    @occupancy.setter
    def occupancy(self, value: int) -> None:
        """Backward-compatible setter."""
        self._occupied = value > 0
        self.state_known = True

    @property
    def occupied(self) -> bool:
        return self._occupied

    @occupied.setter
    def occupied(self, value: bool) -> None:
        self._occupied = value
        self.state_known = True
        if value:
            self.last_occupied_at = self.last_motion or 0

    @property
    def claims(self) -> set[str]:
        """Backward-compatible claims property.

        Returns a live proxy set: mutations (add/clear/discard) are
        reflected onto the underlying ``_occupied`` bool.
        """
        return _ClaimsProxy(self)

    def record_entry(self, timestamp: float, claim_id: str | None = None) -> None:
        """Backward-compatible: mark area as occupied."""
        self._occupied = True
        self.state_known = True
        self.activity_history.append((timestamp, "entry"))
        if len(self.activity_history) > MAX_HISTORY_LENGTH:
            self.activity_history.pop(0)

    def apply_engine_state(self, occupied: bool, known: bool) -> None:
        """Apply an occupancy engine decision, including unknown evidence."""
        self._occupied = occupied
        self.state_known = known
        if occupied:
            self.last_occupied_at = self.last_motion or 0

    def apply_resolved_occupancy(self, occupied: bool) -> None:
        """Apply resolver output without turning uncertainty into vacancy."""
        self._occupied = occupied
        if occupied:
            self.state_known = True
            self.last_occupied_at = self.last_motion or 0
        elif not self.is_indoors:
            self.state_known = True

    def record_exit(self, timestamp: float) -> bool:
        """Backward-compatible: clear occupancy. Returns True if was occupied."""
        if not self._occupied:
            return False
        self._occupied = False
        self.state_known = True
        self.activity_history.append((timestamp, "exit"))
        if len(self.activity_history) > MAX_HISTORY_LENGTH:
            self.activity_history.pop(0)
        return True

    def clear_occupancy(
        self,
        timestamp: float,
        target_id: str | list[str] | None = None,
        reason: str | None = None,
    ) -> None:
        """Clear all occupancy from this area."""
        was_occupied = self._occupied
        was_known = self.state_known
        self._occupied = False
        self.state_known = True
        self.cleared_by = reason or (
            target_id if isinstance(target_id, str) else "unspecified"
        )
        self.stale_since = None
        if was_occupied or not was_known:
            self.activity_history.append((timestamp, "clear"))
            if len(self.activity_history) > MAX_HISTORY_LENGTH:
                self.activity_history.pop(0)

    def record_motion(self, timestamp: float) -> None:
        """Record motion activity in this area."""
        self.last_motion = timestamp
        self.stale_since = None
        self.cleared_by = None
        self.activity_history.append((timestamp, "motion"))
        if len(self.activity_history) > MAX_HISTORY_LENGTH:
            self.activity_history.pop(0)

    def record_contact(self, timestamp: float, is_open: bool) -> None:
        """Record a door/window edge without fabricating motion evidence."""
        self.last_contact = timestamp
        self.last_contact_open = is_open
        activity = "contact_open" if is_open else "contact_closed"
        self.activity_history.append((timestamp, activity))
        if len(self.activity_history) > MAX_HISTORY_LENGTH:
            self.activity_history.pop(0)

    @property
    def last_activity(self) -> float:
        """Latest motion or boundary activity timestamp."""
        return max(self.last_motion, self.last_contact)

    def get_inactivity_duration(self, timestamp: float) -> float:
        """Returns time in seconds since last motion."""
        if self.last_motion == 0:
            return float("inf")
        return timestamp - self.last_motion

    def has_recent_motion(self, timestamp: float, within_seconds: float = 120) -> bool:
        """Check if there has been motion in this area within the specified time."""
        if self.last_motion == 0:
            return False
        return (timestamp - self.last_motion) <= within_seconds

    def reset(self) -> None:
        """Reset area state to initial values."""
        self._occupied = False
        self.state_known = not self.is_indoors
        self.last_motion = 0
        self.last_contact = 0
        self.last_contact_open = None
        self.last_off = 0
        self.stale_since = None
        self.cleared_by = None
        self.last_occupied_at = 0
        self.activity_history = []

    @property
    def last_positive_evidence(self) -> float:
        """Timestamp of the latest positive occupancy evidence."""
        return self.last_motion

    @property
    def is_occupied(self) -> bool:
        """Whether this area currently has one or more occupants."""
        return self._occupied


class _ClaimsProxy(set):
    """A set-like proxy that maps mutations back to AreaState._occupied.

    This allows legacy code like ``area.claims.add("c0")`` or
    ``area.claims.clear()`` to work, while the underlying representation
    is a simple bool.
    """

    def __init__(self, area: AreaState):
        super().__init__()
        self._area = area
        # Populate the real set contents from the bool
        if area._occupied:
            super().add("_occupied")

    # --- mutators ---------------------------------------------------

    def add(self, item):
        super().add(item)
        self._area._occupied = True
        self._area.state_known = True

    def discard(self, item):
        super().discard(item)
        if not self:
            self._area._occupied = False
            self._area.state_known = True

    def remove(self, item):
        super().remove(item)
        if not self:
            self._area._occupied = False
            self._area.state_known = True

    def clear(self):
        super().clear()
        self._area._occupied = False
        self._area.state_known = True

    def pop(self):
        result = super().pop()
        if not self:
            self._area._occupied = False
            self._area.state_known = True
        return result
