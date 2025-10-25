import logging
from typing import Dict, List, Optional

from .area_state import AreaState
from .sensor_state import SensorState
from .warning import Warning
from .types import OccupancyTrackerConfig

# Configure logger
logger = logging.getLogger(__name__)


class AnomalyDetector:
    """Detects anomalies in sensor readings and occupancy patterns."""

    def __init__(self, config: OccupancyTrackerConfig):
        self.config = config
        self.warnings: List[Warning] = []
        self.recent_motion_window = 120  # 2 minutes
        self.motion_timeout = 24 * 3600  # 24 hours
        self.extended_occupancy_threshold = 12 * 3600  # 12 hours

    def check_for_stuck_sensors(
        self,
        sensors: Dict[str, SensorState],
        areas: Dict[str, AreaState],
        triggered_sensor_id: str,
    ) -> None:
        """Check for stuck sensors when a sensor is triggered."""
        triggered_sensor = sensors[triggered_sensor_id]
        area_id = triggered_sensor.config.get("area")

        if not area_id or area_id not in areas:
            return

        # Get adjacent areas from configuration
        adjacency = self.config.get("adjacency", {}).get(area_id, [])

        # Update adjacent area motion records for all sensors in those areas
        for adjacent_area_id in adjacency:
            for sensor_id, sensor in sensors.items():
                if sensor.config.get("area") == adjacent_area_id:
                    sensor.record_adjacent_motion(
                        area_id, triggered_sensor.last_update_time
                    )

        # Check if sensors are stuck - need to calculate first!
        for sensor_id, sensor in sensors.items():
            # Get adjacent areas for this sensor's area
            sensor_area = sensor.config.get("area")
            if not sensor_area:
                continue

            # Check if there was recent motion in adjacent areas
            sensor_adjacency = self.config.get("adjacency", {}).get(sensor_area, [])
            has_recent_adjacent_motion = any(
                areas.get(adj_area_id)
                and areas[adj_area_id].has_recent_motion(
                    triggered_sensor.last_update_time, 60
                )
                for adj_area_id in sensor_adjacency
            )

            # Now calculate if stuck
            is_stuck = sensor.calculate_is_stuck(
                has_recent_adjacent_motion, triggered_sensor.last_update_time
            )

            if is_stuck and sensor.is_reliable:
                sensor_area = sensor.config.get("area", "unknown")
                self._create_warning(
                    "stuck_sensor",
                    f"Sensor {sensor_id} in area {sensor_area} may be stuck",
                    area=sensor_area,
                    sensor_id=sensor_id,
                    timestamp=triggered_sensor.last_update_time,
                )
                sensor.is_reliable = False

    def handle_unexpected_motion(
        self,
        area: AreaState,
        areas: Dict[str, AreaState],
        sensors: Dict[str, SensorState],
        timestamp: float,
        adjacency_tracker,
    ) -> bool:
        """Handle unexpected motion in an area that should be unoccupied.

        Returns:
            bool: True if this is valid entry (not an anomaly), False if anomaly detected
        """
        # Check for valid paths from occupied areas
        valid_entry = False

        # Check adjacent areas for recent activity
        adjacency = self.config.get("adjacency", {}).get(area.id, [])
        for adjacent_area_id in adjacency:
            if adjacent_area_id in areas:
                adjacent_area = areas[adjacent_area_id]
                if adjacent_area.occupancy > 0 and adjacent_area.has_recent_motion(
                    timestamp, self.recent_motion_window
                ):
                    # Likely moved from adjacent occupied room
                    valid_entry = True
                    logger.info(f"Person moved from {adjacent_area_id} to {area.id}")
                    adjacent_area.record_exit(timestamp)
                    break

        # Check if there was recent motion in adjacent areas using adjacency tracker
        if not valid_entry:
            for sensor_id, sensor in sensors.items():
                if sensor.config.get("area") == area.id:
                    # Check if this sensor had adjacent motion recently
                    if adjacency_tracker.check_adjacent_motion(
                        sensor_id, timestamp, timeframe=60
                    ):
                        valid_entry = True
                        logger.info(
                            f"Motion in {area.id} linked to recent adjacent motion"
                        )
                        break

        if not valid_entry:
            # Could be entry from outside if this is an entry point
            if area.is_exit_capable:
                logger.info(f"Person entered {area.id} from outside")
            else:
                logger.warning(
                    f"Unexpected motion in {area.id} without clear entry path"
                )
                self._create_warning(
                    "unexpected_motion",
                    f"Unexpected motion in {area.id}",
                    area=area.id,
                    timestamp=timestamp,
                )

        return valid_entry

    def check_simultaneous_motion(
        self, trigger_area_id: str, areas: Dict[str, AreaState], timestamp: float
    ) -> None:
        """Check for simultaneous motion in multiple areas."""
        # Get list of areas with recent motion
        areas_with_recent_motion = []
        for area_id, area in areas.items():
            if area_id != trigger_area_id and area.has_recent_motion(
                timestamp, 10
            ):  # Motion within 10 seconds
                areas_with_recent_motion.append(area_id)

        if areas_with_recent_motion:
            # Check if any are not adjacent to the trigger area
            adjacency = self.config.get("adjacency", {}).get(trigger_area_id, [])
            non_adjacent = [a for a in areas_with_recent_motion if a not in adjacency]

            if non_adjacent:
                self._create_warning(
                    "simultaneous_motion",
                    f"Motion detected simultaneously in non-adjacent areas: {trigger_area_id} and {', '.join(non_adjacent)}",
                    timestamp=timestamp,
                )

    def check_timeouts(self, areas: Dict[str, AreaState], timestamp: float) -> None:
        """Check for timeout conditions like inactivity and extended occupancy."""

        for area_id, area in areas.items():
            # Check for inactivity timeout if area is occupied
            if area.occupancy > 0:
                inactivity_duration = area.get_inactivity_duration(timestamp)

                # Reset room after 24 hours of inactivity
                if inactivity_duration > self.motion_timeout:
                    logger.warning(
                        f"Resetting area {area_id} due to {inactivity_duration / 3600:.1f} hours of inactivity"
                    )
                    area.occupancy = 0
                    self._create_warning(
                        "inactivity_timeout",
                        f"Area {area_id} was reset after {inactivity_duration / 3600:.1f} hours of inactivity",
                        area=area_id,
                        timestamp=timestamp,
                    )

                # Warning for extended occupancy (12+ hours)
                elif inactivity_duration > self.extended_occupancy_threshold:
                    # Check if we already have an active warning for this
                    has_warning = any(
                        w.is_active
                        and w.type == "extended_occupancy"
                        and w.area == area_id
                        for w in self.warnings
                    )
                    if not has_warning:
                        self._create_warning(
                            "extended_occupancy",
                            f"Area {area_id} has been occupied for {inactivity_duration / 3600:.1f} hours with limited activity",
                            area=area_id,
                            timestamp=timestamp,
                        )

    def _create_warning(
        self,
        warning_type: str,
        message: str,
        area: Optional[str] = None,
        sensor_id: Optional[str] = None,
        timestamp: Optional[float] = None,
    ) -> Warning:
        """Add a warning to the system and return it."""
        if timestamp is None:
            import time

            timestamp = time.time()
        warning = Warning(warning_type, message, area, sensor_id, timestamp)
        logger.warning(f"New warning: {warning}")
        self.warnings.append(warning)
        return warning

    def get_warnings(self, active_only: bool = True) -> List[Warning]:
        """Get list of warnings, optionally filtered to active ones only."""
        if active_only:
            return [w for w in self.warnings if w.is_active]
        return self.warnings

    def resolve_warning(self, warning_id: str) -> bool:
        """Resolve a specific warning by ID."""
        for warning in self.warnings:
            if warning.id == warning_id and warning.is_active:
                warning.resolve()
                return True
        return False
