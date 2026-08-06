import logging
from typing import Callable, Dict, List, Optional, Set

from .area_state import AreaState
from .audit import audit_event
from .constants import MOTION_SENSOR_TYPES, normalize_area_ids
from .sensor_state import SensorState
from .warning import Warning
from .types import OccupancyTrackerConfig

# Configure logger
logger = logging.getLogger(__name__)


class AnomalyDetector:
    """Detects anomalies in sensor readings and occupancy patterns."""

    MAX_WARNING_HISTORY = 500
    PLAUSIBLE_SOURCE_WINDOW = 10.0
    OUTDOOR_INTRUSION_WINDOW = 300.0
    BOOTSTRAP_WINDOW = 120.0
    RECENTLY_OCCUPIED_WINDOW = 300.0

    def __init__(self, config: OccupancyTrackerConfig):
        self.config = config
        self.warnings: List[Warning] = []
        self.recent_motion_window = 120  # 2 minutes
        self.extended_occupancy_threshold = 12 * 3600  # 12 hours
        self.adjacency_map = self._build_adjacency(config)

        # Stale-looking occupancy diagnostic thresholds
        self.phantom_inactivity_threshold = 1800  # 30 minutes
        self.phantom_freshness_threshold = 0.20
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
        timestamp_or_sensor_id: float | str,
    ) -> None:
        """Evaluate stuck predicates from an event or periodic timestamp."""
        if isinstance(timestamp_or_sensor_id, str):
            triggered_sensor = sensors[timestamp_or_sensor_id]
            timestamp = triggered_sensor.last_update_time
        else:
            timestamp = timestamp_or_sensor_id

        for sensor_id, sensor in sensors.items():
            is_stuck = sensor.calculate_is_stuck(timestamp)
            sensor_area = sensor.config.get("area", "unknown")
            area_str = str(sensor_area)

            if is_stuck and sensor.is_reliable:
                self._sync_warning(
                    True,
                    "stuck_sensor",
                    f"Sensor {sensor_id} in area {area_str} may be stuck",
                    area=area_str,
                    sensor_id=sensor_id,
                    timestamp=timestamp,
                )
                sensor.is_reliable = False
            elif not is_stuck:
                self._sync_warning(
                    False,
                    "stuck_sensor",
                    "",
                    area=area_str,
                    sensor_id=sensor_id,
                    timestamp=timestamp,
                )

    def check_timeouts(
        self,
        areas: Dict[str, AreaState],
        timestamp: float,
        sensors: Optional[Dict[str, SensorState]] = None,
        freshness_fn: Optional[Callable[[str, float], float]] = None,
    ) -> None:
        """Report timeout conditions without changing occupancy."""

        for area_id, area in areas.items():
            has_timed_occupancy = area.occupancy > 0 and area.last_motion > 0
            inactivity_duration = (
                area.get_inactivity_duration(timestamp) if has_timed_occupancy else 0
            )

            exit_stale = (
                has_timed_occupancy
                and area.is_exit_capable
                and not area.is_indoors
                and inactivity_duration > 300
            )
            self._sync_warning(
                exit_stale,
                "exit_area_stale",
                f"Exit-capable area {area_id} may be stale after "
                f"{inactivity_duration / 60:.1f} minutes of inactivity",
                area=area_id,
                timestamp=timestamp,
            )

            extended = (
                has_timed_occupancy
                and inactivity_duration > area.profile.extended_occupancy_seconds
            )
            self._sync_warning(
                extended,
                "extended_occupancy",
                f"Area {area_id} has been occupied for "
                f"{inactivity_duration / 3600:.1f} hours with limited activity",
                area=area_id,
                timestamp=timestamp,
            )

        if sensors is not None and freshness_fn is not None:
            self._check_phantom_occupancy(areas, timestamp, sensors, freshness_fn)

    def _check_phantom_occupancy(
        self,
        areas: Dict[str, AreaState],
        timestamp: float,
        sensors: Dict[str, SensorState],
        freshness_fn: Callable[[str, float], float],
    ) -> None:
        """Report occupancy when all evidence suggests a phantom occupant.

        Warns only when ALL conditions are true:
        1. Inactivity exceeds the room profile threshold
        2. Freshness has decayed below the diagnostic threshold
        3. The area's own motion sensors are inactive
        4. All neighboring areas are quiet (protects sleeping people)
        5. No recent contact events (door/window)
        6. Area is not exit-capable
        """
        for area_id, area in areas.items():
            inactivity = area.get_inactivity_duration(timestamp)
            freshness = freshness_fn(area_id, timestamp)
            suspicious = (
                area.occupancy > 0
                and not area.is_exit_capable
                and area.last_motion > 0
                and inactivity >= area.profile.phantom_inactivity_seconds
                and freshness < self.phantom_freshness_threshold
            )

            # Current sensor state is stronger evidence than freshness decay.
            own_sensor_active = any(
                sensor.is_trusted_active
                and sensor.config.get("type", "") in MOTION_SENSOR_TYPES
                and area_id in sensor.area_ids
                for sensor in sensors.values()
            )
            if own_sensor_active:
                suspicious = False

            # Check ALL neighbors for recent activity
            any_neighbor_active = False
            for neighbor_id in self.adjacency_map.get(area_id, []):
                neighbor = areas.get(neighbor_id)
                if neighbor and neighbor.has_recent_motion(
                    timestamp, area.profile.neighbor_activity_seconds
                ):
                    any_neighbor_active = True
                    break

                # Also check if any motion sensor in the neighbor is currently ON
                for sensor in sensors.values():
                    if not sensor.is_trusted_active:
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
                suspicious = False

            # Area contact history survives sensor baselines and restarts.
            recent_contact = bool(
                area.last_contact
                and (timestamp - area.last_contact)
                <= area.profile.contact_evidence_seconds
            )
            if recent_contact:
                suspicious = False

            self._sync_warning(
                suspicious,
                "phantom_occupancy_suspected",
                f"Occupancy in {area_id} may be stale after "
                f"{inactivity / 60:.0f} minutes of inactivity "
                f"(freshness: {freshness:.0%})",
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
        logger.warning(f"⚠️ {message}")
        self.warnings.append(warning)
        while len(self.warnings) > self.MAX_WARNING_HISTORY:
            resolved_index = next(
                (
                    index
                    for index, existing in enumerate(self.warnings)
                    if not existing.is_active
                ),
                None,
            )
            if resolved_index is None:
                break
            self.warnings.pop(resolved_index)
        audit_event(
            "warning_opened",
            timestamp=timestamp,
            warning_type=warning_type,
            warning_id=warning.id,
            area=area,
            sensor_id=sensor_id,
            message=message,
        )
        return warning

    def _sync_warning(
        self,
        condition: bool,
        warning_type: str,
        message: str,
        area: Optional[str] = None,
        sensor_id: Optional[str] = None,
        timestamp: Optional[float] = None,
    ) -> Optional[Warning]:
        """Open or resolve a warning so active warnings match current state."""
        active = next(
            (
                warning
                for warning in self.warnings
                if warning.is_active
                and warning.type == warning_type
                and warning.area == area
                and warning.sensor_id == sensor_id
            ),
            None,
        )
        if condition:
            return active or self._create_warning(
                warning_type,
                message,
                area=area,
                sensor_id=sensor_id,
                timestamp=timestamp,
            )
        if active:
            active.resolve()
            logger.info("Resolved %s warning for %s", warning_type, area or sensor_id)
            audit_event(
                "warning_resolved",
                timestamp=timestamp,
                warning_type=warning_type,
                warning_id=active.id,
                area=area,
                sensor_id=sensor_id,
                reason="predicate_cleared",
            )
        return None

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

    def report_unexpected_active_areas(
        self,
        timestamp: float,
        areas: Dict[str, AreaState],
        sensors: Dict[str, SensorState],
        first_activation_time: float,
    ) -> None:
        """Report active rooms that lack a plausible adjacent source."""
        for area_id in areas:
            active = self._is_area_active(area_id, sensors)
            unexpected = active and not self._has_plausible_source(
                area_id, timestamp, areas, sensors, first_activation_time
            )
            self._sync_warning(
                unexpected,
                "unexpected_motion",
                f"Unexpected motion in {area_id} (no_plausible_source)",
                area=area_id,
                timestamp=timestamp,
            )

    def _has_plausible_source(
        self,
        area_id: str,
        timestamp: float,
        areas: Dict[str, AreaState],
        sensors: Dict[str, SensorState],
        first_activation_time: float,
    ) -> bool:
        area = areas[area_id]

        if area.is_exit_capable or not self.adjacency_map.get(area_id):
            return True

        for neighbor_id in self.adjacency_map.get(area_id, []):
            neighbor = areas.get(neighbor_id)
            if not neighbor:
                continue
            if self._is_area_active(neighbor_id, sensors):
                return True
            if (
                neighbor.last_motion > 0
                and (timestamp - neighbor.last_motion) <= self.RECENTLY_OCCUPIED_WINDOW
            ):
                return True
        if area.last_contact and area.last_contact >= (
            timestamp - self.OUTDOOR_INTRUSION_WINDOW
        ):
            return True

        if first_activation_time == 0.0 and self.adjacency_map.get(area_id):
            return True
        if (
            first_activation_time > 0
            and (timestamp - first_activation_time) <= self.BOOTSTRAP_WINDOW
            and area.is_indoors
            and self.adjacency_map.get(area_id)
        ):
            return True

        if area.is_indoors and self.adjacency_map.get(area_id):
            recent_activations = sum(
                1
                for sensor in sensors.values()
                if sensor.config.get("type", "") in MOTION_SENSOR_TYPES
                and area_id in sensor.area_ids
                for item in sensor.history
                if item.state and (timestamp - item.timestamp) <= 300
            )
            if recent_activations >= 2:
                logger.info(
                    "Persistent activation in %s: %d activations in 5min, accepting",
                    area_id,
                    recent_activations,
                )
                return True

        return False

    @staticmethod
    def _is_area_active(area_id: str, sensors: Dict[str, SensorState]) -> bool:
        return any(
            sensor.is_trusted_active
            and sensor.config.get("type", "") in MOTION_SENSOR_TYPES
            and area_id in sensor.area_ids
            for sensor in sensors.values()
        )

    def resolve_warning(self, warning_id: str) -> bool:
        """Resolve a specific warning by ID."""
        for warning in self.warnings:
            if warning.id == warning_id and warning.is_active:
                warning.resolve()
                audit_event(
                    "warning_resolved",
                    warning_type=warning.type,
                    warning_id=warning.id,
                    area=warning.area,
                    sensor_id=warning.sensor_id,
                    reason="manual",
                )
                return True
        return False

    def clear_warnings(self) -> bool:
        """Resolve all active warnings."""
        cleared = False
        for warning in self.warnings:
            if warning.is_active:
                warning.resolve()
                audit_event(
                    "warning_resolved",
                    warning_type=warning.type,
                    warning_id=warning.id,
                    area=warning.area,
                    sensor_id=warning.sensor_id,
                    reason="reset_all",
                )
                cleared = True
        return cleared
