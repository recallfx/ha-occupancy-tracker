import logging
from typing import Callable, Dict, List, Optional, Set

from .area_state import AreaState
from .constants import MAGNETIC_SENSOR_TYPES, MOTION_SENSOR_TYPES, normalize_area_ids
from .sensor_state import SensorState
from .warning import Warning
from .types import OccupancyTrackerConfig

# Configure logger
logger = logging.getLogger("anomaly_detector")


class AnomalyDetector:
    """Detects anomalies in sensor readings and occupancy patterns."""

    def __init__(self, config: OccupancyTrackerConfig):
        self.config = config
        self.warnings: List[Warning] = []
        self.recent_motion_window = 120  # 2 minutes
        self.motion_timeout = 24 * 3600  # 24 hours
        self.extended_occupancy_threshold = 12 * 3600  # 12 hours
        self.adjacency_map = self._build_adjacency(config)

        # Phantom occupancy cleanup thresholds
        self.phantom_inactivity_threshold = 1800  # 30 minutes
        self.phantom_probability_threshold = 0.20
        self.phantom_neighbor_activity_window = 1800  # 30 minutes
        self.phantom_magnetic_window = 1800  # 30 minutes

    def _build_adjacency(self, config: OccupancyTrackerConfig) -> Dict[str, List[str]]:
        adjacency_config = (
            config.get("adjacency", {}) if isinstance(config, dict) else {}
        )
        adjacency_map: Dict[str, Set[str]] = {}
        for area_id, neighbors in adjacency_config.items():
            area_set = adjacency_map.setdefault(area_id, set())
            for neighbor_id in neighbors:
                area_set.add(neighbor_id)
                adjacency_map.setdefault(neighbor_id, set()).add(area_id)
        return {
            area_id: sorted(list(neighbors))
            for area_id, neighbors in adjacency_map.items()
        }

    def check_for_stuck_sensors(
        self,
        sensors: Dict[str, SensorState],
        areas: Dict[str, AreaState],
        triggered_sensor_id: str,
    ) -> None:
        """Check for stuck sensors when a sensor is triggered."""
        triggered_sensor = sensors[triggered_sensor_id]
        area_config = triggered_sensor.config.get("area")

        if not area_config:
            return

        # We don't need to check if area exists here, as we just want to trigger the check
        # The actual check iterates over all sensors

        # Update adjacent area motion records for all sensors in adjacent areas
        # This logic is complex with bridging sensors, and the "adjacent motion implies stuck"
        # logic is being removed, so we can simplify this.
        # We only need to check for "Stuck ON" (timeout) which doesn't require adjacency info.

        # Check if sensors are stuck
        for sensor_id, sensor in sensors.items():
            # Calculate if stuck (only checks for long active duration now)
            is_stuck = sensor.calculate_is_stuck(triggered_sensor.last_update_time)

            if is_stuck and sensor.is_reliable:
                sensor_area = sensor.config.get("area", "unknown")
                # Handle list of areas for display
                area_str = (
                    str(sensor_area) if isinstance(sensor_area, list) else sensor_area
                )

                self._create_warning(
                    "stuck_sensor",
                    f"Sensor {sensor_id} in area {area_str} may be stuck",
                    area=area_str,
                    sensor_id=sensor_id,
                    timestamp=triggered_sensor.last_update_time,
                )
                sensor.is_reliable = False

    def check_timeouts(
        self,
        areas: Dict[str, AreaState],
        timestamp: float,
        sensors: Optional[Dict[str, SensorState]] = None,
        probability_fn: Optional[Callable[[str, float], float]] = None,
    ) -> List[str]:
        """Check for timeout conditions like inactivity and extended occupancy."""
        cleared_area_ids: List[str] = []

        for area_id, area in areas.items():
            # Check exit-capable areas for auto-clear (shorter timeout)
            if area.is_exit_capable and area.occupancy > 0:
                # Exit-capable areas clear after 5 minutes of inactivity
                # (people can leave the system from these areas)
                exit_timeout = 300  # 5 minutes
                inactivity_duration = area.get_inactivity_duration(timestamp)

                if inactivity_duration > exit_timeout:
                    logger.info(
                        f"Auto-clearing exit-capable area {area_id} after {inactivity_duration:.0f}s of inactivity"
                    )
                    area.clear_occupancy(timestamp, target_id="timeout")
                    self._create_warning(
                        "exit_area_auto_clear",
                        f"Exit-capable area {area_id} was auto-cleared after {inactivity_duration / 60:.1f} minutes of inactivity",
                        area=area_id,
                        timestamp=timestamp,
                    )
                    continue  # Skip regular timeout checks for this area

            # Check for inactivity timeout if area is occupied
            if area.occupancy > 0:
                inactivity_duration = area.get_inactivity_duration(timestamp)

                # Reset room after 24 hours of inactivity
                if inactivity_duration > self.motion_timeout:
                    logger.warning(
                        f"Resetting area {area_id} due to {inactivity_duration / 3600:.1f} hours of inactivity"
                    )
                    area.clear_occupancy(timestamp, target_id="timeout")
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

        if sensors is not None and probability_fn is not None:
            cleared_area_ids.extend(
                self._check_phantom_occupancy(areas, timestamp, sensors, probability_fn)
            )

        return cleared_area_ids

    def _check_phantom_occupancy(
        self,
        areas: Dict[str, AreaState],
        timestamp: float,
        sensors: Dict[str, SensorState],
        probability_fn: Callable[[str, float], float],
    ) -> List[str]:
        """Clear occupancy when all evidence suggests a phantom occupant.

        Only clears when ALL conditions are true:
        1. Inactivity exceeds threshold (30 min)
        2. Probability has decayed below threshold (~170 min)
        3. All neighboring areas are quiet (protects sleeping people)
        4. No recent magnetic events (door/window)
        5. Area is not exit-capable
        """
        cleared_area_ids: List[str] = []

        for area_id, area in areas.items():
            if area.occupancy <= 0:
                continue

            if area.is_exit_capable:
                continue

            inactivity = area.get_inactivity_duration(timestamp)
            if inactivity < self.phantom_inactivity_threshold:
                continue

            probability = probability_fn(area_id, timestamp)
            if probability >= self.phantom_probability_threshold:
                continue

            # Check ALL neighbors for recent activity
            any_neighbor_active = False
            for neighbor_id in self.adjacency_map.get(area_id, []):
                neighbor = areas.get(neighbor_id)
                if neighbor and neighbor.has_recent_motion(
                    timestamp, self.phantom_neighbor_activity_window
                ):
                    any_neighbor_active = True
                    break

                # Also check if any motion sensor in the neighbor is currently ON
                for sensor in sensors.values():
                    if not sensor.current_state:
                        continue
                    sensor_type = sensor.config.get("type", "")
                    if sensor_type not in MOTION_SENSOR_TYPES:
                        continue
                    sensor_areas = normalize_area_ids(sensor.config.get("area"))
                    if neighbor_id in sensor_areas:
                        any_neighbor_active = True
                        break
                if any_neighbor_active:
                    break

            if any_neighbor_active:
                continue

            # Check for recent magnetic events on this area
            recent_magnetic = False
            for sensor in sensors.values():
                sensor_type = sensor.config.get("type", "")
                if sensor_type not in MAGNETIC_SENSOR_TYPES:
                    continue
                sensor_areas = normalize_area_ids(sensor.config.get("area"))
                if area_id not in sensor_areas:
                    continue
                if (
                    sensor.last_changed
                    and (timestamp - sensor.last_changed)
                    <= self.phantom_magnetic_window
                ):
                    recent_magnetic = True
                    break

            if recent_magnetic:
                continue

            # All conditions met — clear phantom occupancy
            logger.info(
                f"Clearing phantom occupancy in {area_id}: "
                f"inactive {inactivity / 60:.0f}min, probability {probability:.2f}, "
                f"no neighbor activity, no recent magnetic events"
            )
            area.clear_occupancy(timestamp)
            cleared_area_ids.append(area_id)
            self._create_warning(
                "phantom_occupancy_cleared",
                f"Phantom occupancy cleared in {area_id} after "
                f"{inactivity / 60:.0f} minutes of inactivity "
                f"(probability: {probability:.0%})",
                area=area_id,
                timestamp=timestamp,
            )

        return cleared_area_ids

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
        logger.warning(f"⚠️ {message}")
        self.warnings.append(warning)
        return warning

    def get_warnings(self, active_only: bool = True) -> List[Warning]:
        """Get list of warnings, optionally filtered to active ones only."""
        if active_only:
            return [w for w in self.warnings if w.is_active]
        return self.warnings

    def record_unexpected_activation(
        self,
        area_id: str,
        sensor_id: Optional[str],
        timestamp: float,
        context: str = None,
    ) -> None:
        """Create a warning when motion cannot be explained by adjacency."""
        message = f"Unexpected motion in {area_id}"
        if sensor_id:
            message += f" via {sensor_id}"
        if context:
            message += f" ({context})"

        self._create_warning(
            "unexpected_motion",
            message,
            area=area_id,
            sensor_id=sensor_id,
            timestamp=timestamp,
        )

    def resolve_warning(self, warning_id: str) -> bool:
        """Resolve a specific warning by ID."""
        for warning in self.warnings:
            if warning.id == warning_id and warning.is_active:
                warning.resolve()
                return True
        return False

    def clear_warnings(self) -> bool:
        """Resolve all active warnings."""
        cleared = False
        for warning in self.warnings:
            if warning.is_active:
                warning.resolve()
                cleared = True
        return cleared
