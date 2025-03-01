from .constants import MAX_HISTORY_LENGTH
from .types import AreaConfig


class AreaState:
    """Tracks occupancy and activity in a single area."""

    def __init__(self, area_id: str, area_config: AreaConfig):
        self.id = area_id
        self.config = area_config
        self.occupancy = 0
        self.last_motion: float = 0
        self.activity_history = []  # List of (timestamp, activity_type) tuples
        self.is_indoors = area_config.get("indoors", True)
        self.is_exit_capable = area_config.get("exit_capable", False)

    def record_motion(self, timestamp: float) -> None:
        """Record motion activity in this area."""
        self.last_motion = timestamp
        self.activity_history.append((timestamp, "motion"))
        if len(self.activity_history) > MAX_HISTORY_LENGTH:
            self.activity_history.pop(0)

    def record_entry(self, timestamp: float) -> None:
        """Record entry into this area."""
        self.occupancy += 1
        self.activity_history.append((timestamp, "entry"))
        if len(self.activity_history) > MAX_HISTORY_LENGTH:
            self.activity_history.pop(0)

    def record_exit(self, timestamp: float) -> bool:
        """Record exit from this area. Returns False if no occupancy to decrement."""
        if self.occupancy <= 0:
            return False
        self.occupancy -= 1
        self.activity_history.append((timestamp, "exit"))
        if len(self.activity_history) > MAX_HISTORY_LENGTH:
            self.activity_history.pop(0)
        return True

    def get_inactivity_duration(self, timestamp: float) -> float:
        """Returns time in seconds since last motion."""
        return timestamp - self.last_motion

    def has_recent_motion(self, timestamp: float, within_seconds: float = 120) -> bool:
        """Check if there has been motion in this area within the specified time."""
        if self.last_motion == 0:  # No motion ever recorded
            return False
        return (timestamp - self.last_motion) <= within_seconds
