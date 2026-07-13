from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, Optional

from .area_state import AreaState
from .sensor_state import SensorState


@dataclass
class MapSnapshot:
    """Immutable snapshot of the system map at a point in time."""

    timestamp: float
    event_type: str
    description: Optional[str]
    areas: Dict[str, Dict[str, Any]]
    sensors: Dict[str, Dict[str, Any]]


class MapStateRecorder:
    """Keeps a rolling log of full-map snapshots for later playback."""

    def __init__(
        self,
        max_snapshots: int = 500,
        tick_interval: float = 300.0,
    ) -> None:
        self.max_snapshots = max_snapshots
        self.tick_interval = tick_interval
        self.snapshots: Deque[MapSnapshot] = deque(maxlen=max_snapshots)
        self.last_snapshot_time: float = 0.0
        self.last_event_snapshot_time: float = 0.0

    def record_sensor_event(
        self,
        timestamp: float,
        sensor_id: str,
        new_state: bool,
        areas: Dict[str, AreaState],
        sensors: Dict[str, SensorState],
    ) -> MapSnapshot:
        """Record a snapshot triggered by a concrete sensor transition."""
        description = f"sensor:{sensor_id}:{'on' if new_state else 'off'}"
        snapshot = self._build_snapshot(
            timestamp=timestamp,
            event_type="sensor",
            description=description,
            areas=areas,
            sensors=sensors,
        )
        self.last_snapshot_time = timestamp
        self.last_event_snapshot_time = timestamp
        return snapshot

    def record_sensor_availability(
        self,
        timestamp: float,
        sensor_id: str,
        available: bool,
        areas: Dict[str, AreaState],
        sensors: Dict[str, SensorState],
    ) -> MapSnapshot:
        """Record availability without inventing a physical ON/OFF edge."""
        description = (
            f"availability:{sensor_id}:{'available' if available else 'unavailable'}"
        )
        snapshot = self._build_snapshot(
            timestamp=timestamp,
            event_type="availability",
            description=description,
            areas=areas,
            sensors=sensors,
        )
        self.last_snapshot_time = timestamp
        self.last_event_snapshot_time = timestamp
        return snapshot

    def record_sensor_baseline(
        self,
        timestamp: float,
        sensor_id: str,
        state: bool,
        areas: Dict[str, AreaState],
        sensors: Dict[str, SensorState],
    ) -> MapSnapshot:
        """Record a startup state without treating it as a fresh edge."""
        description = f"baseline:{sensor_id}:{'on' if state else 'off'}"
        snapshot = self._build_snapshot(
            timestamp=timestamp,
            event_type="baseline",
            description=description,
            areas=areas,
            sensors=sensors,
        )
        self.last_snapshot_time = timestamp
        self.last_event_snapshot_time = timestamp
        return snapshot

    def maybe_record_tick(
        self,
        timestamp: float,
        areas: Dict[str, AreaState],
        sensors: Dict[str, SensorState],
    ) -> Optional[MapSnapshot]:
        """Capture a periodic snapshot if no recent events were recorded."""
        if not self.snapshots:
            # Always capture the very first snapshot to seed history.
            snapshot = self._build_snapshot(
                timestamp=timestamp,
                event_type="tick",
                description="initial",
                areas=areas,
                sensors=sensors,
            )
            self.last_snapshot_time = timestamp
            return snapshot

        # Skip tick snapshots if we recently captured a sensor-triggered snapshot.
        if self.last_event_snapshot_time and (
            timestamp - self.last_event_snapshot_time < self.tick_interval
        ):
            return None

        if self.last_snapshot_time and (
            timestamp - self.last_snapshot_time < self.tick_interval
        ):
            return None

        snapshot = self._build_snapshot(
            timestamp=timestamp,
            event_type="tick",
            description="periodic",
            areas=areas,
            sensors=sensors,
        )
        self.last_snapshot_time = timestamp
        return snapshot

    def reset(self) -> None:
        """Clear recorded history."""
        self.snapshots.clear()
        self.last_snapshot_time = 0.0
        self.last_event_snapshot_time = 0.0

    def _build_snapshot(
        self,
        timestamp: float,
        event_type: str,
        description: Optional[str],
        areas: Dict[str, AreaState],
        sensors: Dict[str, SensorState],
    ) -> MapSnapshot:
        snapshot = MapSnapshot(
            timestamp=timestamp,
            event_type=event_type,
            description=description,
            areas=self._serialize_areas(areas),
            sensors=self._serialize_sensors(sensors),
        )
        self.snapshots.append(snapshot)
        return snapshot

    def update_latest_state(
        self,
        areas: Dict[str, AreaState],
        sensors: Dict[str, SensorState],
    ) -> None:
        """Refresh the most recent snapshot with the latest state."""
        if not self.snapshots:
            return
        latest = self.snapshots[-1]
        latest.areas = self._serialize_areas(areas)
        latest.sensors = self._serialize_sensors(sensors)

    def _serialize_areas(
        self, areas: Dict[str, AreaState]
    ) -> Dict[str, Dict[str, Any]]:
        payload: Dict[str, Dict[str, Any]] = {}
        for area_id, area in areas.items():
            payload[area_id] = {
                "occupancy": area.occupancy,
                "is_occupied": area.is_occupied,
                "last_motion": area.last_motion,
            }
        return payload

    def _serialize_sensors(
        self, sensors: Dict[str, SensorState]
    ) -> Dict[str, Dict[str, Any]]:
        payload: Dict[str, Dict[str, Any]] = {}
        for sensor_id, sensor in sensors.items():
            payload[sensor_id] = {
                "state": sensor.current_state,
                "last_changed": sensor.last_changed,
                "available": sensor.is_available,
                "reliable": sensor.is_reliable,
                "stuck": sensor.is_stuck,
            }
        return payload

    def get_history(self) -> list[MapSnapshot]:
        """Return a list copy of all recorded snapshots."""
        return list(self.snapshots)

    @staticmethod
    def merge_with_config(
        snapshot: MapSnapshot,
        config: Dict[str, Any],
    ) -> MapSnapshot:
        """Combine dynamic snapshot data with static configuration metadata."""
        area_configs = config.get("areas", {}) if isinstance(config, dict) else {}
        sensor_configs = config.get("sensors", {}) if isinstance(config, dict) else {}

        merged_areas: Dict[str, Dict[str, Any]] = {}
        for area_id, dynamic_state in snapshot.areas.items():
            base = area_configs.get(area_id, {})
            merged = dict(base)
            merged.update(dynamic_state)
            merged_areas[area_id] = merged

        merged_sensors: Dict[str, Dict[str, Any]] = {}
        for sensor_id, dynamic_state in snapshot.sensors.items():
            base = sensor_configs.get(sensor_id, {})
            merged = dict(base)
            merged.update(dynamic_state)
            merged_sensors[sensor_id] = merged

        return MapSnapshot(
            timestamp=snapshot.timestamp,
            event_type=snapshot.event_type,
            description=snapshot.description,
            areas=merged_areas,
            sensors=merged_sensors,
        )
