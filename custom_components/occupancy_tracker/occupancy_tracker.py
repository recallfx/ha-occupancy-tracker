import time
import logging

from typing import Dict, List, Optional, Any

from .components.sensor_state import SensorState
from .components.area_state import AreaState
from .components.sensor_adjacency_tracker import SensorAdjacencyTracker
from .components.warning import Warning
from .components.types import OccupancyTrackerConfig
from .components.anomaly_detector import AnomalyDetector

# Configure logger
logger = logging.getLogger(__name__)


class OccupancyTracker:
    """Main class for tracking occupancy across multiple areas."""

    def __init__(self, config: OccupancyTrackerConfig):
        self.config = config
        self.areas: Dict[str, AreaState] = {}
        self.sensors: Dict[str, SensorState] = {}
        self.last_event_time = time.time()
        self.recent_motion_window = 120  # 2 minutes
        self.adjacency_tracker = SensorAdjacencyTracker()
        self.anomaly_detector = AnomalyDetector(config)

        # Initialize area and sensor objects
        self._initialize_areas()
        self._initialize_sensors()
        self._initialize_adjacency()

    def _initialize_areas(self) -> None:
        """Initialize area tracking objects from configuration."""
        for area_id, area_config in self.config.get("areas", {}).items():
            self.areas[area_id] = AreaState(area_id, area_config)

    def _initialize_sensors(self) -> None:
        """Initialize sensor tracking objects from configuration."""
        for sensor_id, sensor_config in self.config.get("sensors", {}).items():
            self.sensors[sensor_id] = SensorState(sensor_id, sensor_config, time.time())

    def _initialize_adjacency(self) -> None:
        """Initialize sensor adjacency relationships from configuration."""
        adjacency_config = self.config.get("adjacency", {})

        # Build adjacency map for sensors based on their areas
        for sensor_id, sensor in self.sensors.items():
            area_id = sensor.config.get("area")
            if not area_id:
                continue

            # Register sensor-to-area mapping
            self.adjacency_tracker.set_sensor_area(sensor_id, area_id)

            # Find sensors in adjacent areas
            adjacent_areas = adjacency_config.get(area_id, [])
            adjacent_sensors = set()

            for adjacent_area_id in adjacent_areas:
                for other_sensor_id, other_sensor in self.sensors.items():
                    if other_sensor.config.get("area") == adjacent_area_id:
                        adjacent_sensors.add(other_sensor_id)

            # Set adjacency for this sensor
            self.adjacency_tracker.set_adjacency(sensor_id, adjacent_sensors)

    def process_sensor_event(
        self, sensor_id: str, state: bool, timestamp: float
    ) -> None:
        """Process a sensor state change event."""
        if sensor_id not in self.sensors:
            logger.warning(f"Unknown sensor ID: {sensor_id}")
            return

        sensor = self.sensors[sensor_id]
        sensor_type = sensor.config.get("type", "")

        # Add debug logging for motion sensors
        if sensor_type in ["motion", "camera_motion", "camera_person"]:
            logger.debug(
                f"Motion sensor event: {sensor_id}, state={state}, type={sensor_type}"
            )

        state_changed = sensor.update_state(state, timestamp)

        # Add more debug logging about the state change result
        if sensor_type in ["motion", "camera_motion", "camera_person"]:
            logger.debug(f"Motion sensor {sensor_id} state_changed={state_changed}")

        # Skip processing if state didn't actually change
        if not state_changed and state is True:
            # Only motion sensors have meaningful repeated "ON" states
            if sensor.config.get("type", "") in [
                "motion",
                "camera_motion",
                "camera_person",
            ]:
                logger.debug(f"Processing repeated motion for {sensor_id}")
                self._process_repeated_motion(sensor_id, timestamp)
            return

        # Process different sensor types
        sensor_type = sensor.config.get("type", "")

        if sensor_type in ["motion", "camera_motion", "camera_person"] and state:
            logger.debug(f"Processing motion event for {sensor_id}")
            self._process_motion_event(sensor_id, timestamp)

        elif sensor_type == "magnetic":
            self._process_magnetic_event(sensor_id, state, timestamp)

        # Check for stuck sensors after processing the event
        self._check_for_stuck_sensors(sensor_id, timestamp)

        self.last_event_time = timestamp

    def _check_for_stuck_sensors(
        self, triggered_sensor_id: str, timestamp: float
    ) -> None:
        """Check for stuck sensors when a sensor is triggered."""
        triggered_sensor = self.sensors[triggered_sensor_id]
        area_id = triggered_sensor.config.get("area")

        if not area_id or area_id not in self.areas:
            return

        # Record motion in adjacency tracker
        self.adjacency_tracker.record_motion(area_id, timestamp)

        # Delegate to anomaly detector for checking stuck sensors
        self.anomaly_detector.check_for_stuck_sensors(
            self.sensors, self.areas, triggered_sensor_id
        )

    def _process_repeated_motion(self, sensor_id: str, timestamp: float) -> None:
        """Handle repeated motion events from the same sensor."""
        sensor = self.sensors[sensor_id]
        area_id = sensor.config.get("area")

        if not area_id or area_id not in self.areas:
            return

        # Record motion in the area
        self.areas[area_id].record_motion(timestamp)

        # Update motion in adjacency tracker
        self.adjacency_tracker.record_motion(area_id, timestamp)

    def _process_motion_event(self, sensor_id: str, timestamp: float) -> None:
        """Process motion sensor activation."""
        sensor = self.sensors[sensor_id]
        area_id = sensor.config.get("area")

        if not area_id or area_id not in self.areas:
            logger.warning(f"Sensor {sensor_id} is not associated with a valid area")
            return

        area = self.areas[area_id]

        # Record motion in the area
        area.record_motion(timestamp)

        # Record motion in adjacency tracker
        self.adjacency_tracker.record_motion(area_id, timestamp)

        # Check for unexpected motion
        if area.occupancy == 0:
            self._handle_unexpected_motion(area, timestamp)

        # Check for simultaneous motion in adjacent areas
        self._check_simultaneous_motion(area_id, timestamp)

    def _process_magnetic_event(
        self, sensor_id: str, state: bool, timestamp: float
    ) -> None:
        """Process door/window sensor events."""
        sensor = self.sensors[sensor_id]
        between_areas = sensor.config.get("between_areas", [])

        if len(between_areas) != 2:
            logger.warning(
                f"Magnetic sensor {sensor_id} has invalid between_areas configuration"
            )
            return

        # Door/window events are generally just recorded but don't directly change occupancy
        # They help confirm transitions between areas
        # Full transition logic is handled by motion events before/after door events

    def _handle_unexpected_motion(self, area: AreaState, timestamp: float) -> None:
        """Handle unexpected motion in an area that should be unoccupied."""
        # Delegate to anomaly detector to evaluate if this is a valid entry or anomaly
        valid_entry = self.anomaly_detector.handle_unexpected_motion(
            area, self.areas, self.sensors, timestamp, self.adjacency_tracker
        )

        # Always increment occupancy - if valid_entry is True, the person moved from
        # an adjacent area (which was decremented). If False, it's a new entry or anomaly.
        # Either way, someone is now in this area.
        area.record_entry(timestamp)

    def _check_simultaneous_motion(
        self, trigger_area_id: str, timestamp: float
    ) -> None:
        """Check for simultaneous motion in multiple areas."""
        # Delegate to anomaly detector to check for simultaneous motion anomalies
        self.anomaly_detector.check_simultaneous_motion(
            trigger_area_id, self.areas, timestamp
        )

    def _add_warning(
        self,
        warning_type: str,
        message: str,
        area: Optional[str] = None,
        sensor_id: Optional[str] = None,
    ) -> None:
        """Add a warning to the system."""
        # This method is kept for compatibility but delegates to anomaly detector
        self.anomaly_detector._create_warning(warning_type, message, area, sensor_id)

    def get_warnings(self, active_only: bool = True) -> List[Warning]:
        """Get list of warnings, optionally filtered to active ones only."""
        return self.anomaly_detector.get_warnings(active_only)

    def get_occupancy(self, area_id: str) -> int:
        """Get current occupancy count for an area."""
        if area_id not in self.areas:
            return 0
        return self.areas[area_id].occupancy

    def get_occupancy_probability(self, area_id: str) -> float:
        """Get probability score (0-1) that area is occupied.

        This is a simplified score based on occupancy count and recent activity:
        - 0.95 = Definitely occupied (recent motion + occupancy > 0)
        - 0.75 = Likely occupied (occupancy > 0 but no recent motion)
        - 0.05 = Likely unoccupied (no recorded occupancy)
        """
        if area_id not in self.areas:
            return 0.05

        area = self.areas[area_id]
        now = time.time()

        if area.occupancy <= 0:
            return 0.05

        # If there's been motion in the last 5 minutes, high probability
        if now - area.last_motion < 300:
            return 0.95

        # Occupied but no recent motion - slightly lower probability
        return 0.75

    def check_timeouts(self, timestamp: float = None) -> None:
        """Check for timeout conditions like inactivity and extended occupancy."""
        if timestamp is None:
            timestamp = time.time()
        self.anomaly_detector.check_timeouts(self.areas, timestamp)

    def resolve_warning(self, warning_id: str) -> bool:
        """Resolve a specific warning by ID."""
        return self.anomaly_detector.resolve_warning(warning_id)

    def get_area_status(self, area_id: str) -> Dict[str, Any]:
        """Get detailed status information for an area."""
        if area_id not in self.areas:
            return {"error": "Area not found"}

        area = self.areas[area_id]
        now = time.time()
        return {
            "id": area_id,
            "name": area.config.get("name", area_id),
            "occupancy": area.occupancy,
            "last_motion": area.last_motion,
            "time_since_motion": now - area.last_motion
            if area.last_motion > 0
            else None,
            "indoors": area.is_indoors,
            "exit_capable": area.is_exit_capable,
            "adjacent_areas": self.config.get("adjacency", {}).get(area_id, []),
        }

    def get_system_status(self) -> Dict[str, Any]:
        """Get overall system status information."""
        occupied_areas = [
            (area_id, area.occupancy)
            for area_id, area in self.areas.items()
            if area.occupancy > 0
        ]

        return {
            "total_occupancy": sum(occ for _, occ in occupied_areas),
            "occupied_areas": dict(occupied_areas),
            "active_warnings": len(self.get_warnings(active_only=True)),
            "last_event_time": self.last_event_time,
            "uptime": time.time() - self.last_event_time,
        }

    def reset_anomalies(self) -> None:
        """Reset the anomaly detection system without resetting occupancy state."""
        # Create new anomaly detector (resetting warnings)
        self.anomaly_detector = AnomalyDetector(self.config)
        logger.info("Anomaly detection system reset")

    def reset(self) -> None:
        """Reset the entire system state."""
        for area in self.areas.values():
            area.occupancy = 0
            area.last_motion = 0
            area.activity_history = []

        for sensor in self.sensors.values():
            sensor.current_state = False
            sensor.history = []
            sensor.is_reliable = True

        # Reset adjacency tracker
        self.adjacency_tracker = SensorAdjacencyTracker()
        self._initialize_adjacency()

        # Create new anomaly detector (resetting warnings)
        self.anomaly_detector = AnomalyDetector(self.config)

        logger.info("Occupancy tracker system reset")

    def diagnose_motion_issues(self, sensor_id: str = None) -> Dict[str, Any]:
        """Diagnostic method to help identify why motion isn't being detected.

        Args:
            sensor_id: Optional specific sensor to diagnose

        Returns:
            Dict with diagnostic information
        """
        sensors_to_check = [sensor_id] if sensor_id else list(self.sensors.keys())
        results = {}

        for s_id in sensors_to_check:
            if s_id not in self.sensors:
                results[s_id] = {"error": "Sensor not found"}
                continue

            sensor = self.sensors[s_id]
            sensor_type = sensor.config.get("type", "unknown")
            area_id = sensor.config.get("area")

            sensor_info = {
                "sensor_type": sensor_type,
                "is_motion_sensor": sensor_type
                in ["motion", "camera_motion", "camera_person"],
                "current_state": sensor.current_state,
                "area_id": area_id,
                "area_exists": area_id in self.areas if area_id else False,
                "history_length": len(sensor.history)
                if hasattr(sensor, "history")
                else "unknown",
                "is_reliable": sensor.is_reliable
                if hasattr(sensor, "is_reliable")
                else "unknown",
            }

            # Add area information if applicable
            if area_id and area_id in self.areas:
                area = self.areas[area_id]
                sensor_info["area_info"] = {
                    "occupancy": area.occupancy,
                    "last_motion": area.last_motion,
                    "time_since_motion": time.time() - area.last_motion
                    if area.last_motion > 0
                    else None,
                    "activity_history_length": len(area.activity_history),
                }

            results[s_id] = sensor_info

        return results
