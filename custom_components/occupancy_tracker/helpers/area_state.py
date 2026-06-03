from .constants import MAX_HISTORY_LENGTH
from .types import AreaConfig


class AreaState:
    """Tracks occupancy and activity in a single area."""

    def __init__(self, area_id: str, area_config: AreaConfig):
        self.id = area_id
        self.config = area_config
        self.last_motion: float = 0
        self.last_off: float = 0  # Timestamp of last motion-OFF event
        self.activity_history = []  # List of (timestamp, activity_type) tuples
        self.is_indoors = area_config.get("indoors", True)
        self.is_exit_capable = area_config.get("exit_capable", False)
        self.is_transition = area_config.get("transition", False)
        self.open_plan_group: str | None = None

        # Set by cluster rebuild, not by individual events
        self._occupied: bool = False
        self.last_occupied_at: float = 0  # Timestamp when area was last occupied
        self.cluster_id: int | None = None

    @property
    def occupancy(self) -> int:
        """Occupancy: 1 if occupied, 0 if not."""
        return 1 if self._occupied else 0

    @occupancy.setter
    def occupancy(self, value: int) -> None:
        """Backward-compatible setter."""
        self._occupied = value > 0

    @property
    def occupied(self) -> bool:
        return self._occupied

    @occupied.setter
    def occupied(self, value: bool) -> None:
        self._occupied = value
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
        self.activity_history.append((timestamp, "entry"))
        if len(self.activity_history) > MAX_HISTORY_LENGTH:
            self.activity_history.pop(0)

    def record_exit(self, timestamp: float) -> bool:
        """Backward-compatible: clear occupancy. Returns True if was occupied."""
        if not self._occupied:
            return False
        self._occupied = False
        self.cluster_id = None
        self.activity_history.append((timestamp, "exit"))
        if len(self.activity_history) > MAX_HISTORY_LENGTH:
            self.activity_history.pop(0)
        return True

    def clear_occupancy(
        self, timestamp: float, target_id: str | list[str] | None = None
    ) -> None:
        """Clear all occupancy from this area."""
        if self._occupied:
            self._occupied = False
            self.cluster_id = None
            self.activity_history.append((timestamp, "clear"))
            if len(self.activity_history) > MAX_HISTORY_LENGTH:
                self.activity_history.pop(0)

    def record_motion(self, timestamp: float) -> None:
        """Record motion activity in this area."""
        self.last_motion = timestamp
        self.activity_history.append((timestamp, "motion"))
        if len(self.activity_history) > MAX_HISTORY_LENGTH:
            self.activity_history.pop(0)

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
        self.cluster_id = None
        self.last_motion = 0
        self.last_off = 0
        self.last_occupied_at = 0
        self.activity_history = []

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
            super().add("_cluster")

    # --- mutators ---------------------------------------------------

    def add(self, item):
        super().add(item)
        self._area._occupied = True

    def discard(self, item):
        super().discard(item)
        if not self:
            self._area._occupied = False

    def remove(self, item):
        super().remove(item)
        if not self:
            self._area._occupied = False

    def clear(self):
        super().clear()
        self._area._occupied = False

    def pop(self):
        result = super().pop()
        if not self:
            self._area._occupied = False
        return result
