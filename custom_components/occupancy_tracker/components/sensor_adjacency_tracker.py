from typing import Dict, Set


class SensorAdjacencyTracker:
    """Tracks motion relationships between adjacent sensors/areas."""

    def __init__(self):
        self.adjacency_map: Dict[
            str, Set[str]
        ] = {}  # sensor_id -> set of adjacent sensor_ids
        self.motion_times: Dict[str, float] = {}  # area_id -> last motion time

    def set_adjacency(self, sensor_id: str, adjacent_sensor_ids: Set[str]) -> None:
        """Define which sensors are adjacent to a given sensor."""
        self.adjacency_map[sensor_id] = adjacent_sensor_ids

    def get_adjacency(self, sensor_id: str) -> Set[str]:
        """Get the set of sensors adjacent to a given sensor."""
        return self.adjacency_map.get(sensor_id, set())

    def record_motion(self, area_id: str, timestamp: float) -> None:
        """Record motion in an area."""
        self.motion_times[area_id] = timestamp

    def check_adjacent_motion(
        self, sensor_id: str, timestamp: float, timeframe: float = 30
    ) -> bool:
        """Check if there was recent motion in any adjacent area.

        Args:
            sensor_id: The sensor to check against
            timeframe: Time window in seconds to consider motion as "recent"

        Returns:
            True if any adjacent area had motion within the timeframe
        """
        adjacent_areas = self.adjacency_map.get(sensor_id, set())

        for area_id in adjacent_areas:
            motion_time = self.motion_times.get(area_id)
            if motion_time and (timestamp - motion_time) < timeframe:
                return True

        return False
