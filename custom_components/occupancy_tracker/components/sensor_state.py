from .types import SensorConfig
from .sensor_history_item import SensorHistoryItem
from .constants import MAX_HISTORY_LENGTH


class SensorState:
    """Tracks state and history of a single sensor."""

    def __init__(self, sensor_id: str, sensor_config: SensorConfig, timestamp: float):
        self.id = sensor_id
        self.config = sensor_config
        self.current_state = False
        self.last_changed = timestamp
        self.last_update_time = timestamp
        self.history = []  # List of (timestamp, state) tuples
        self.is_reliable = True
        self.is_stuck = False
        self.adjacent_motion_times = []  # List of (area_id, timestamp) tuples

    def update_state(self, new_state: bool, timestamp: float) -> bool:
        """Update sensor state and return whether state changed."""

        # Update last update time
        self.last_update_time = timestamp

        # Add to history
        self.history.append(SensorHistoryItem(new_state, timestamp))
        if len(self.history) > MAX_HISTORY_LENGTH:
            self.history.pop(0)

        # Check if state actually changed
        if new_state != self.current_state:
            self.current_state = new_state
            self.last_changed = timestamp
            return True
        return False

    def calculate_is_stuck(
        self, has_recent_adjacent_motion: bool, timestamp: float
    ) -> bool:
        """Detect if sensor appears stuck in one state."""

        # For ON state, check if it's been stuck for 24 hours (86400 seconds)
        if self.current_state and (timestamp - self.last_changed) > 86400:
            self.is_stuck = True
            return True

        # For any state, check if there's been adjacent area activity that should have triggered this sensor
        sensor_type = self.config.get("type", "")
        if sensor_type in ["motion", "camera_motion", "camera_person"]:
            # If recent motion in adjacent area but no state change here for a while
            if has_recent_adjacent_motion and (timestamp - self.last_changed) > 30:
                self.is_stuck = True
                return True

        self.is_stuck = False
        return False

    def record_adjacent_motion(self, area_id: str, timestamp: float) -> None:
        """Record motion in an adjacent area.

        Args:
            area_id: The ID of the adjacent area with motion
            timestamp: The timestamp of the motion event
        """
        self.adjacent_motion_times.append((area_id, timestamp))
        # Keep only recent history (last 100 events)
        if len(self.adjacent_motion_times) > 100:
            self.adjacent_motion_times.pop(0)
