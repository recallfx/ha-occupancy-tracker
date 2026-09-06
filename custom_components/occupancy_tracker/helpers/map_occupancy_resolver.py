from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, Iterable, Optional, Tuple

from .anomaly_detector import AnomalyDetector
from .area_state import AreaState
from .constants import MAGNETIC_SENSOR_TYPES, MOTION_SENSOR_TYPES
from .map_state_recorder import MapSnapshot
from .occupancy_engine import (
    OCCUPIED_STATES,
    STATE_PENDING,
    STATE_RETAINED,
    OccupancyEngine,
)
from .sensor_state import SensorState
from .types import OccupancyTrackerConfig


_LOGGER = logging.getLogger(__name__)

#: States in which a room is held on past evidence rather than live motion.
HELD_STATES = frozenset({STATE_PENDING, STATE_RETAINED})


class MapOccupancyResolver:
    """Translate sensor snapshots into occupancy engine decisions.

    The resolver is deliberately thin: it turns Home Assistant sensor state
    into the two sets the engine needs -- areas with trusted live motion, and
    areas whose motion inputs have all gone unavailable -- and writes the
    engine's answer back onto the area objects.
    """

    def __init__(
        self,
        config: OccupancyTrackerConfig,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.adjacency_map = self._build_adjacency(config)
        self.engine = OccupancyEngine(config, clock=clock)
        self._first_activation_time: float = (
            0.0  # Timestamp of the very first sensor activation
        )

    def reset(self) -> None:
        self.engine.reset()
        self._first_activation_time = 0.0

    @property
    def retained_areas(self) -> set[str]:
        """Rooms held without a departure trail, for diagnostics."""
        return {
            area_id
            for area_id, room in self.engine.rooms.items()
            if room.state == STATE_RETAINED
        }

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
        reason: str = "manual_clear",
    ) -> list[str]:
        """Explicitly clear indoor rooms without rejecting active sensors."""
        requested = set(areas) if area_ids is None else set(area_ids)
        targets = [
            area_id
            for area_id in sorted(requested)
            if area_id in areas and areas[area_id].is_indoors
        ]
        cleared = self.engine.force_vacant(targets, timestamp, reason)
        for area_id in cleared:
            areas[area_id].clear_occupancy(timestamp, reason=reason)
        self._publish(timestamp, areas)
        return cleared

    # ------------------------------------------------------------------
    # Verification support
    # ------------------------------------------------------------------

    def capture_state(self) -> dict[str, Any]:
        """Snapshot resolver internals so a replay can be rolled back."""
        return {
            "rooms": {
                area_id: dict(vars(room)) for area_id, room in self.engine.rooms.items()
            },
            "active": set(self.engine._active),
            "unavailable": set(self.engine._unavailable),
            "on_edges": dict(self.engine._last_on_edge),
            "last_timestamp": self.engine._last_timestamp,
            "first_activation": self._first_activation_time,
        }

    def restore_state(self, state: dict[str, Any]) -> None:
        """Restore internals captured by :meth:`capture_state`."""
        for area_id, values in state["rooms"].items():
            room = self.engine.rooms.get(area_id)
            if room is None:
                continue
            for key, value in values.items():
                setattr(room, key, value)
        self.engine._active = set(state["active"])
        self.engine._unavailable = set(state["unavailable"])
        self.engine._last_on_edge = dict(state["on_edges"])
        self.engine._last_timestamp = state["last_timestamp"]
        self._first_activation_time = state["first_activation"]

    # ------------------------------------------------------------------
    # Configuration helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_adjacency(config: OccupancyTrackerConfig) -> Dict[str, list[str]]:
        adjacency = config.get("adjacency", {}) if isinstance(config, dict) else {}
        normalized: Dict[str, list[str]] = {}
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
        """Return areas with at least one trusted, active motion sensor."""
        active: set[str] = set()
        for sensor in sensors.values():
            if not sensor.is_trusted_active:
                continue
            if sensor.config.get("type", "") not in MOTION_SENSOR_TYPES:
                continue
            active.update(sensor.area_ids)
        return active

    @staticmethod
    def _compute_unavailable_areas(sensors: Dict[str, SensorState]) -> set[str]:
        """Return areas whose motion inputs have all gone unavailable.

        Unavailable is not OFF: an area with no usable transport must publish
        unknown rather than a confident answer.
        """
        seen: set[str] = set()
        usable: set[str] = set()
        for sensor in sensors.values():
            if sensor.config.get("type", "") not in MOTION_SENSOR_TYPES:
                continue
            seen.update(sensor.area_ids)
            if sensor.is_available:
                usable.update(sensor.area_ids)
        return seen - usable

    def _is_area_active(self, area_id: str, sensors: Dict[str, SensorState]) -> bool:
        """Check if any motion sensor in the area is currently trusted ON."""
        return area_id in self._compute_sensor_active_areas(sensors)

    # ------------------------------------------------------------------
    # Snapshot parsing
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
        cleared_area_ids = self._parse_clear_event(snapshot)
        if cleared_area_ids:
            self.clear_stale_indoor_occupancy(
                snapshot.timestamp,
                areas,
                sensors,
                cleared_area_ids,
                reason="manual_clear",
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
        area_ids = [area_id for area_id in sensor.area_ids if area_id in areas]

        if sensor_type in MAGNETIC_SENSOR_TYPES:
            for area_id in area_ids:
                areas[area_id].record_contact(timestamp, is_open=new_state)
            if new_state:
                # A verified envelope contact pulse is a boundary event on a
                # way out of the room; the engine decides whether it lands in
                # the trail window.
                self.engine.record_contact(area_ids, timestamp)
            self._rebuild_occupancy(timestamp, areas, sensors, anomaly_detector)
            return None

        if sensor_type not in MOTION_SENSOR_TYPES:
            return None

        if new_state:
            for area_id in area_ids:
                areas[area_id].record_motion(timestamp)
            if self._first_activation_time == 0.0:
                self._first_activation_time = timestamp
        else:
            for area_id in area_ids:
                areas[area_id].last_off = timestamp

        self._rebuild_occupancy(timestamp, areas, sensors, anomaly_detector)

        if new_state:
            return next(
                (area_id for area_id in area_ids if areas[area_id].occupied), None
            )
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
                        sensor.last_source_timestamp = snapshot.timestamp
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
    # Core occupancy rebuild
    # ------------------------------------------------------------------

    def _rebuild_occupancy(
        self,
        timestamp: float,
        areas: Dict[str, AreaState],
        sensors: Dict[str, SensorState],
        anomaly_detector: Optional[AnomalyDetector] = None,
    ) -> None:
        """Advance the engine with current sensor evidence and publish it."""
        active_areas = self._compute_sensor_active_areas(sensors)
        unavailable_areas = self._compute_unavailable_areas(sensors)

        changes = self.engine.apply(timestamp, active_areas, unavailable_areas)

        if anomaly_detector:
            anomaly_detector.report_unexpected_active_areas(
                timestamp,
                areas,
                sensors,
                self._first_activation_time,
            )

        self._publish(timestamp, areas)

        if changes and _LOGGER.isEnabledFor(logging.DEBUG):
            _LOGGER.debug("Occupancy transitions @ %.1f: %s", timestamp, changes)

    def _publish(self, timestamp: float, areas: Dict[str, AreaState]) -> None:
        """Copy engine decisions onto the area objects."""
        for area_id, area in areas.items():
            room = self.engine.rooms.get(area_id)
            if room is None:
                continue
            area.apply_engine_state(
                occupied=room.state in OCCUPIED_STATES,
                known=self.engine.is_known(area_id, timestamp),
            )
            area.stale_since = room.state_since if room.state in HELD_STATES else None
