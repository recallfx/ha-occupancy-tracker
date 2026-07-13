from .types import SensorConfig
from .sensor_history_item import SensorHistoryItem
from .constants import MAX_HISTORY_LENGTH


class SensorState:
    """Tracks state and history of a single sensor."""

    def __init__(self, sensor_id: str, sensor_config: SensorConfig, timestamp: float):
        self.id = sensor_id
        self.config = sensor_config
        # Pre-compute normalized area IDs (config never changes after init)
        from .constants import normalize_area_ids

        self.area_ids: list[str] = normalize_area_ids(sensor_config.get("area"))
        self.current_state = False
        self.last_changed = 0  # Only set by real state changes, not init
        self.activated_at = None  # Timestamp when sensor last transitioned OFF→ON (None if never activated)
        self.last_update_time = timestamp
        self.history = []  # List of (timestamp, state) tuples
        self.is_available = True
        self.is_reliable = True
        self.is_stuck = False

    def update_state(self, new_state: bool, timestamp: float) -> bool:
        """Update sensor state and return whether state changed."""

        # Update last update time
        self.last_update_time = timestamp

        # A valid HA state restores transport availability. Reliability is a
        # separate signal used for genuinely stuck sensors.
        self.is_available = True

        # Add to history
        self.history.append(SensorHistoryItem(new_state, timestamp))
        if len(self.history) > MAX_HISTORY_LENGTH:
            self.history.pop(0)

        # Check if state actually changed
        if new_state != self.current_state:
            # If sensor was stuck but is now transitioning, restore reliability
            if self.is_stuck:
                self.is_stuck = False
                self.is_reliable = True
            self.current_state = new_state
            self.last_changed = timestamp
            # Track activation time when transitioning OFF→ON
            if new_state:
                self.activated_at = timestamp
            return True
        return False

    def mark_unavailable(self, timestamp: float) -> None:
        """Stop trusting the last physical state without inventing an edge."""
        self.last_update_time = timestamp
        self.is_available = False

    def seed_state(self, state: bool, timestamp: float) -> None:
        """Set a startup baseline without recording a new sensor event."""
        self.current_state = state
        self.last_update_time = timestamp
        self.is_available = True

    @property
    def is_trusted_active(self) -> bool:
        """Return whether the current ON reading is usable as live evidence."""
        return self.current_state and self.is_available and self.is_reliable

    def calculate_is_stuck(self, timestamp: float) -> bool:
        """Detect if sensor appears stuck in one state."""

        if not self.is_available:
            return False

        # For ON state, check if it's been stuck for 24 hours (86400 seconds)
        if self.current_state and (timestamp - self.last_changed) > 86400:
            self.is_stuck = True
            return True

        # Removed "adjacent motion implies stuck" logic as it produces false positives.
        # A sensor being OFF while adjacent areas are active is a normal condition
        # (e.g. person is in the adjacent room but not entering this one).

        self.is_stuck = False
        return False

    def reset(self) -> None:
        """Reset sensor state to initial values."""
        self.current_state = False
        self.activated_at = None
        self.history = []
        self.is_available = True
        self.is_reliable = True
        self.is_stuck = False
