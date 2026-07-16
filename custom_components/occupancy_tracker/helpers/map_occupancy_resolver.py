from __future__ import annotations

import logging
from typing import Dict, Iterable, List, Optional, Tuple

from .anomaly_detector import AnomalyDetector
from .area_state import AreaState
from .constants import MAGNETIC_SENSOR_TYPES, MOTION_SENSOR_TYPES
from .map_state_recorder import MapSnapshot
from .sensor_state import SensorState
from .types import OccupancyTrackerConfig


_LOGGER = logging.getLogger("resolver")


class MapOccupancyResolver:
    """Conservative occupancy resolver.

    Trusted positive evidence always wins. Indoor evidence is latched until an
    explicit clear; adjacency is used only to explain suspicious activations.
    """

    def __init__(self, config: OccupancyTrackerConfig) -> None:
        self.adjacency_map = self._build_adjacency(config)
        self.indoor_latched: set[str] = set()
        self._first_activation_time: float = (
            0.0  # Timestamp of the very first sensor activation
        )

    def reset(self) -> None:
        self.indoor_latched.clear()
        self._first_activation_time = 0.0

    def refresh_occupancy(
        self,
        timestamp: float,
        areas: Dict[str, AreaState],
        sensors: Dict[str, SensorState],
    ) -> None:
        """Recompute occupancy after sensor trust or availability changes."""
        self._rebuild_occupancy(timestamp, areas, sensors)

    def clear_stale_indoor_occupancy(
        self,
        timestamp: float,
        areas: Dict[str, AreaState],
        sensors: Dict[str, SensorState],
        area_ids: Iterable[str] | None = None,
    ) -> list[str]:
        """Explicitly clear indoor latches without rejecting active sensors."""
        active_areas = self._compute_sensor_active_areas(sensors)
        targets = (
            set(self.indoor_latched)
            if area_ids is None
            else self.indoor_latched.intersection(area_ids)
        )
        cleared: list[str] = []

        for area_id in sorted(targets):
            if area_id in active_areas:
                continue
            self.indoor_latched.remove(area_id)
            if area_id in areas:
                areas[area_id].clear_occupancy(timestamp, reason="manual_clear")
                cleared.append(area_id)

        return cleared

    # ------------------------------------------------------------------
    # Configuration helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_adjacency(config: OccupancyTrackerConfig) -> Dict[str, List[str]]:
        adjacency = config.get("adjacency", {}) if isinstance(config, dict) else {}
        normalized: Dict[str, List[str]] = {}
        for area_id, neighbors in adjacency.items():
            area_list = normalized.setdefault(area_id, [])
            for neighbor in neighbors:
                if neighbor not in area_list:
                    area_list.append(neighbor)
                reverse_list = normalized.setdefault(neighbor, [])
                if area_id not in reverse_list:
                    reverse_list.append(area_id)
        return normalized

    # ------------------------------------------------------------------
    # Sensor state queries
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_sensor_active_areas(sensors: Dict[str, SensorState]) -> set[str]:
        """Compute the set of area IDs that have at least one active motion/camera sensor."""
        active: set[str] = set()
        for sensor in sensors.values():
            if not sensor.is_trusted_active:
                continue
            if sensor.config.get("type", "") not in MOTION_SENSOR_TYPES:
                continue
            active.update(sensor.area_ids)
        return active

    def _is_area_active(self, area_id: str, sensors: Dict[str, SensorState]) -> bool:
        """Check if any MOTION/CAMERA sensor in the area is currently ON."""
        for sensor in sensors.values():
            if not sensor.is_trusted_active:
                continue
            if sensor.config.get("type", "") not in MOTION_SENSOR_TYPES:
                continue
            if area_id in sensor.area_ids:
                return True
        return False

    def _any_other_motion_sensor_active(
        self, area_id: str, exclude_sensor_id: str, sensors: Dict[str, SensorState]
    ) -> bool:
        for sensor in sensors.values():
            if sensor.id == exclude_sensor_id:
                continue
            if not sensor.is_trusted_active:
                continue
            if sensor.config.get("type", "") not in MOTION_SENSOR_TYPES:
                continue
            if area_id in sensor.area_ids:
                return True
        return False

    # ------------------------------------------------------------------
    # Snapshot processing
    # ------------------------------------------------------------------

    def _parse_sensor_event(self, snapshot: MapSnapshot) -> Optional[Tuple[str, bool]]:
        if snapshot.event_type != "sensor" or not snapshot.description:
            return None
        parts = snapshot.description.split(":")
        if len(parts) != 3 or parts[0] != "sensor":
            return None
        sensor_id = parts[1]
        new_state = parts[2] == "on"
        return sensor_id, new_state

    @staticmethod
    def _parse_availability_event(
        snapshot: MapSnapshot,
    ) -> Optional[Tuple[str, bool]]:
        if snapshot.event_type != "availability" or not snapshot.description:
            return None
        parts = snapshot.description.split(":")
        if len(parts) != 3 or parts[0] != "availability":
            return None
        return parts[1], parts[2] == "available"

    @staticmethod
    def _parse_baseline_event(
        snapshot: MapSnapshot,
    ) -> Optional[Tuple[str, bool]]:
        if snapshot.event_type != "baseline" or not snapshot.description:
            return None
        parts = snapshot.description.split(":")
        if len(parts) != 3 or parts[0] != "baseline":
            return None
        return parts[1], parts[2] == "on"

    @staticmethod
    def _parse_clear_event(snapshot: MapSnapshot) -> list[str]:
        if snapshot.event_type != "clear" or not snapshot.description:
            return []
        prefix, separator, area_list = snapshot.description.partition(":")
        if prefix != "clear" or not separator:
            return []
        return [area_id for area_id in area_list.split(",") if area_id]

    @staticmethod
    def _parse_restore_event(snapshot: MapSnapshot) -> list[str]:
        if snapshot.event_type != "restore" or not snapshot.description:
            return []
        prefix, separator, area_list = snapshot.description.partition(":")
        if prefix != "restore" or not separator:
            return []
        return [area_id for area_id in area_list.split(",") if area_id]

    @staticmethod
    def _restore_snapshot_sensor_metadata(
        snapshot: MapSnapshot,
        sensors: Dict[str, SensorState],
    ) -> None:
        """Restore non-physical trust state for deterministic replay."""
        for sensor_id, data in snapshot.sensors.items():
            sensor = sensors.get(sensor_id)
            if not sensor:
                continue
            sensor.is_available = data.get("available", sensor.is_available)
            sensor.is_reliable = data.get("reliable", sensor.is_reliable)
            sensor.is_stuck = data.get("stuck", sensor.is_stuck)

    def process_snapshot(
        self,
        snapshot: MapSnapshot,
        areas: Dict[str, AreaState],
        sensors: Dict[str, SensorState],
        anomaly_detector: Optional[AnomalyDetector] = None,
    ) -> Optional[str]:
        """Apply a single snapshot event to update occupancy state."""
        restored_area_ids = self._parse_restore_event(snapshot)
        if restored_area_ids:
            for area_id in restored_area_ids:
                area = areas.get(area_id)
                if not area or not area.is_indoors:
                    continue
                stored_area = snapshot.areas.get(area_id, {})
                last_motion = stored_area.get("last_motion")
                if isinstance(last_motion, (int, float)) and last_motion > 0:
                    area.last_motion = float(last_motion)
                stale_since = stored_area.get("stale_since")
                area.stale_since = (
                    float(stale_since)
                    if isinstance(stale_since, (int, float)) and stale_since > 0
                    else snapshot.timestamp
                )
                area.cleared_by = None
                self.indoor_latched.add(area_id)
                area.occupied = True
            return None

        cleared_area_ids = self._parse_clear_event(snapshot)
        if cleared_area_ids:
            for area_id in cleared_area_ids:
                self.indoor_latched.discard(area_id)
                if area_id in areas:
                    areas[area_id].clear_occupancy(
                        snapshot.timestamp, reason="manual_clear"
                    )
            return None

        event = self._parse_sensor_event(snapshot)
        if not event:
            return None

        sensor_id, new_state = event
        sensor = sensors.get(sensor_id)
        if not sensor:
            _LOGGER.debug("Sensor %s not tracked; skipping snapshot", sensor_id)
            return None

        sensor_type = sensor.config.get("type", "")
        timestamp = snapshot.timestamp

        if sensor_type in MOTION_SENSOR_TYPES:
            if new_state:
                return self._handle_motion_on(
                    sensor,
                    timestamp,
                    areas,
                    sensors,
                    anomaly_detector,
                )
            else:
                return self._handle_motion_off(sensor, timestamp, areas, sensors)

        elif sensor_type in MAGNETIC_SENSOR_TYPES:
            return self._handle_magnetic_event(sensor, new_state, timestamp, areas)

        return None

    def recalculate_from_history(
        self,
        snapshots: Iterable[MapSnapshot],
        areas: Dict[str, AreaState],
        sensors: Dict[str, SensorState],
        anomaly_detector: Optional[AnomalyDetector] = None,
    ) -> None:
        """Rebuild occupancy entirely from history."""
        history = sorted(list(snapshots), key=lambda snap: snap.timestamp)
        self.reset()

        for area in areas.values():
            area.reset()
        for sensor in sensors.values():
            sensor.reset()

        for snapshot in history:
            baseline = self._parse_baseline_event(snapshot)
            if baseline:
                sensor_id, state = baseline
                sensor = sensors.get(sensor_id)
                if sensor:
                    sensor.seed_state(state, snapshot.timestamp)
            elif availability := self._parse_availability_event(snapshot):
                sensor_id, is_available = availability
                sensor = sensors.get(sensor_id)
                if sensor:
                    if is_available:
                        sensor.is_available = True
                        sensor.last_update_time = snapshot.timestamp
                    else:
                        sensor.mark_unavailable(snapshot.timestamp)
            else:
                event = self._parse_sensor_event(snapshot)
                if event:
                    sensor_id, new_state = event
                    sensor = sensors.get(sensor_id)
                    if sensor:
                        sensor.update_state(new_state, snapshot.timestamp)
                self.process_snapshot(snapshot, areas, sensors, anomaly_detector)

            self._restore_snapshot_sensor_metadata(snapshot, sensors)
            self.refresh_occupancy(snapshot.timestamp, areas, sensors)

    # ------------------------------------------------------------------
    # Magnetic events
    # ------------------------------------------------------------------

    def _handle_magnetic_event(
        self,
        sensor: SensorState,
        new_state: bool,
        timestamp: float,
        areas: Dict[str, AreaState],
    ) -> Optional[str]:
        """Handle magnetic sensor events (doors/windows)."""
        for area_id in sensor.area_ids:
            area = areas.get(area_id)
            if area:
                area.record_motion(timestamp)
                _LOGGER.debug(f"Magnetic event on {sensor.id} kept {area_id} active")
        return None

    # ------------------------------------------------------------------
    # Motion-ON handler
    # ------------------------------------------------------------------

    def _handle_motion_on(
        self,
        sensor: SensorState,
        timestamp: float,
        areas: Dict[str, AreaState],
        sensors: Dict[str, SensorState],
        anomaly_detector: Optional[AnomalyDetector],
    ) -> Optional[str]:
        """Handle motion sensor turning ON and rebuild occupancy."""
        area_ids = [area_id for area_id in sensor.area_ids if area_id in areas]
        if not area_ids:
            return None

        for area_id in area_ids:
            areas[area_id].record_motion(timestamp)

        # Track the very first activation for bootstrap window calculation
        if self._first_activation_time == 0.0:
            self._first_activation_time = timestamp

        # Rebuild current occupancy.
        self._rebuild_occupancy(timestamp, areas, sensors, anomaly_detector)

        return next((area_id for area_id in area_ids if areas[area_id].occupied), None)

    # ------------------------------------------------------------------
    # Motion-OFF handler
    # ------------------------------------------------------------------

    def _handle_motion_off(
        self,
        sensor: SensorState,
        timestamp: float,
        areas: Dict[str, AreaState],
        sensors: Dict[str, SensorState],
    ) -> Optional[str]:
        """Handle motion sensor turning OFF and rebuild occupancy."""
        area_ids = [area_id for area_id in sensor.area_ids if area_id in areas]
        if not area_ids:
            return None

        for area_id in area_ids:
            areas[area_id].last_off = timestamp
            if (
                area_id in self.indoor_latched
                and not self._is_area_active(area_id, sensors)
                and areas[area_id].stale_since is None
            ):
                areas[area_id].stale_since = timestamp

        # If other motion sensors in this area are still ON, skip rebuild
        if len(area_ids) == 1 and self._any_other_motion_sensor_active(
            area_ids[0], sensor.id, sensors
        ):
            _LOGGER.debug(
                "Motion-OFF in %s: other sensor still active, skipping", area_ids[0]
            )
            return None

        # Rebuild current occupancy.
        self._rebuild_occupancy(timestamp, areas, sensors)

        return None

    # ------------------------------------------------------------------
    # Core occupancy rebuild
    # ------------------------------------------------------------------

    def _rebuild_occupancy(
        self,
        timestamp: float,
        areas: Dict[str, AreaState],
        sensors: Dict[str, SensorState],
        anomaly_detector: Optional[AnomalyDetector] = None,
    ) -> None:
        """Combine live evidence with the conservative indoor latch."""

        # Pre-compute sensor-active areas once for the entire rebuild.
        # This avoids O(sensors) iteration on every _is_area_active call.
        sensor_active_areas = self._compute_sensor_active_areas(sensors)

        # Indoor occupancy is a conservative latch. Once established, silence
        # and movement elsewhere are not proof that the room is empty.
        self.indoor_latched.update(
            area_id
            for area_id in sensor_active_areas
            if area_id in areas and areas[area_id].is_indoors
        )

        if anomaly_detector:
            anomaly_detector.report_unexpected_active_areas(
                timestamp,
                areas,
                sensors,
                self._first_activation_time,
            )

        final_occupied = sensor_active_areas | self.indoor_latched

        for area_id, area in areas.items():
            area.occupied = area_id in final_occupied

        if _LOGGER.isEnabledFor(logging.DEBUG):
            occ = {aid for aid, a in areas.items() if a.occupied}
            _LOGGER.debug(
                "Rebuild @ %.1f: occupied=%s, latched=%s",
                timestamp,
                occ,
                self.indoor_latched,
            )
