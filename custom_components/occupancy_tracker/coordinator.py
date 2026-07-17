from __future__ import annotations

import logging
import math
import time
from typing import Any, Dict, Iterable, List, Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN
from .helpers.constants import MOTION_SENSOR_TYPES
from .helpers.types import OccupancyTrackerConfig
from .helpers.anomaly_detector import AnomalyDetector
from .helpers.warning import Warning
from .helpers.map_state_recorder import MapStateRecorder, MapSnapshot
from .helpers.map_occupancy_resolver import MapOccupancyResolver
from .helpers.area_state import AreaState
from .helpers.sensor_state import SensorState
from .helpers.history_verifier import HistoryVerifier
from .helpers.log_formatter import LogFormatter
from .diagnostics import OccupancyDiagnostics

_LOGGER = logging.getLogger("coordinator")

STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.occupancy_state"


class OccupancyCoordinator(DataUpdateCoordinator[Dict[str, Any]]):
    """Coordinator for Occupancy Tracker."""

    def __init__(
        self,
        hass: HomeAssistant,
        config: OccupancyTrackerConfig,
        store: Store[dict[str, Any]] | None = None,
    ) -> None:
        """Initialize the coordinator."""
        request_refresh_debouncer = Debouncer(
            hass,
            _LOGGER,
            cooldown=0,
            immediate=True,
        )

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=None,
            request_refresh_debouncer=request_refresh_debouncer,
        )
        self.config = config
        self.last_event_time = time.time()

        self.areas: Dict[str, AreaState] = {}
        self.sensors: Dict[str, SensorState] = {}
        self._initialize_areas(config)
        self._initialize_sensors(config)

        self.anomaly_detector = AnomalyDetector(config)
        self.state_recorder = MapStateRecorder()
        self.occupancy_resolver = MapOccupancyResolver(config)
        self.log_formatter = LogFormatter(self.areas, self.sensors)
        self._store = store or Store(
            hass, STORAGE_VERSION, STORAGE_KEY, atomic_writes=True
        )

        self.diagnostics = OccupancyDiagnostics(self)

        self.data = self.diagnostics.get_system_status()

    async def _async_update_data(self) -> Dict[str, Any]:
        """Return current state when a coordinator entity requests a refresh."""
        return self.diagnostics.get_system_status()

    async def async_restore_occupancy(self) -> None:
        """Restore conservative indoor occupancy before sensor baselines."""
        stored = await self._store.async_load()
        if not stored:
            return
        if not isinstance(stored, dict):
            _LOGGER.warning("Ignoring invalid stored occupancy state")
            return

        stored_areas = stored.get("areas")
        if not isinstance(stored_areas, dict):
            _LOGGER.warning("Ignoring invalid stored occupancy state")
            return

        restored: list[str] = []
        restore_time = time.time()
        for area_id, area_data in stored_areas.items():
            area = self.areas.get(area_id)
            if not area or not area.is_indoors or not isinstance(area_data, dict):
                continue

            last_motion = area_data.get("last_motion")
            if isinstance(last_motion, (int, float)) and last_motion > 0:
                area.last_motion = float(last_motion)
            area.stale_since = restore_time
            area.cleared_by = None
            area.occupied = True
            self.occupancy_resolver.indoor_latched.add(area_id)
            restored.append(area_id)

        if restored:
            self.state_recorder.record_restore_event(
                time.time(), restored, self.areas, self.sensors
            )
            _LOGGER.info("Restored occupied areas: %s", ", ".join(sorted(restored)))
            self.data = self.diagnostics.get_system_status()

    def _stored_occupancy(self) -> dict[str, Any]:
        """Build the minimal durable conservative-occupancy state."""
        return {
            "areas": {
                area_id: {
                    "last_motion": self.areas[area_id].last_motion,
                }
                for area_id in sorted(self.occupancy_resolver.indoor_latched)
                if area_id in self.areas and self.areas[area_id].is_indoors
            }
        }

    def _schedule_occupancy_save(self) -> None:
        """Persist a latch change without blocking sensor processing."""
        self._store.async_delay_save(self._stored_occupancy)

    def _initialize_areas(self, config: OccupancyTrackerConfig) -> None:
        """Initialize area tracking objects from configuration."""
        for area_id, area_config in config.get("areas", {}).items():
            self.areas[area_id] = AreaState(area_id, area_config)

    def _initialize_sensors(self, config: OccupancyTrackerConfig) -> None:
        """Initialize sensor tracking objects from configuration."""
        for sensor_id, sensor_config in config.get("sensors", {}).items():
            self.sensors[sensor_id] = SensorState(sensor_id, sensor_config, time.time())

    def process_sensor_event(
        self, sensor_id: str, state: bool, timestamp: float
    ) -> None:
        """Process a sensor state change event."""
        if sensor_id not in self.sensors:
            _LOGGER.warning(f"Unknown sensor ID: {sensor_id}")
            return

        sensor = self.sensors[sensor_id]
        sensor_type = sensor.config.get("type", "")
        area_ids = sensor.config.get("area", [])
        if isinstance(area_ids, str):
            area_ids = [area_ids]

        old_occupancy = {aid: area.occupancy for aid, area in self.areas.items()}
        old_latched = set(self.occupancy_resolver.indoor_latched)

        was_available = sensor.is_available
        state_changed = sensor.update_state(state, timestamp)

        # Repeated ON is useful as presence keep-alive only for motion sensors.
        if not state_changed and (
            not state
            or sensor_type not in MOTION_SENSOR_TYPES
            or not sensor.is_trusted_active
        ):
            if not was_available:
                self._record_sensor_availability(sensor_id, True, timestamp)
                self.async_set_updated_data(self.diagnostics.get_system_status())
            return

        snapshot = self._record_snapshot(sensor_id, state, timestamp)

        if snapshot:
            self.occupancy_resolver.process_snapshot(
                snapshot, self.areas, self.sensors, self.anomaly_detector
            )

            if old_latched != self.occupancy_resolver.indoor_latched:
                self._schedule_occupancy_save()

            self._check_for_stuck_sensors(sensor_id, timestamp)

            # Persist trust changes made by anomaly detection in this event's
            # replayable snapshot.
            self._refresh_latest_snapshot_state()
            self._log_state_change(
                sensor_id, sensor_type, state, area_ids, old_occupancy, timestamp
            )

            self.last_event_time = timestamp
            self.async_set_updated_data(self.diagnostics.get_system_status())

    def invalidate_sensor_state(self, sensor_id: str, timestamp: float) -> None:
        """Stop trusting cached state without inventing a physical OFF edge."""
        sensor = self.sensors.get(sensor_id)
        if sensor is None:
            _LOGGER.warning("Unknown sensor ID: %s", sensor_id)
            return

        was_available = sensor.is_available
        sensor.mark_unavailable(timestamp)
        if was_available:
            self._record_sensor_availability(sensor_id, False, timestamp)
            self._refresh_after_trust_change(timestamp)
            self._refresh_latest_snapshot_state()
        self.async_set_updated_data(self.diagnostics.get_system_status())

    def _refresh_after_trust_change(self, timestamp: float) -> None:
        """Apply current trusted sensor state and mark quiet latches stale."""
        self.occupancy_resolver.refresh_occupancy(timestamp, self.areas, self.sensors)
        for area_id in self.occupancy_resolver.indoor_latched:
            area = self.areas.get(area_id)
            if (
                area
                and not self.get_active_sensor_ids(area_id)
                and area.stale_since is None
            ):
                area.stale_since = timestamp

    def seed_sensor_state(self, sensor_id: str, state: bool, timestamp: float) -> None:
        """Set an initial HA state without replaying it as a fresh edge."""
        sensor = self.sensors.get(sensor_id)
        if sensor is None:
            _LOGGER.warning("Unknown sensor ID: %s", sensor_id)
            return
        sensor.seed_state(state, timestamp)
        if state:
            self.state_recorder.record_sensor_baseline(
                timestamp=timestamp,
                sensor_id=sensor_id,
                state=state,
                areas=self.areas,
                sensors=self.sensors,
            )

    def _record_sensor_availability(
        self, sensor_id: str, available: bool, timestamp: float
    ) -> None:
        """Persist availability for deterministic state replay."""
        self.state_recorder.record_sensor_availability(
            timestamp=timestamp,
            sensor_id=sensor_id,
            available=available,
            areas=self.areas,
            sensors=self.sensors,
        )

    def _record_snapshot(
        self, sensor_id: str, state: bool, timestamp: float
    ) -> Optional[MapSnapshot]:
        """Capture a map snapshot that will be used to derive occupancy."""
        return self.state_recorder.record_sensor_event(
            timestamp=timestamp,
            sensor_id=sensor_id,
            new_state=state,
            areas=self.areas,
            sensors=self.sensors,
        )

    def _refresh_latest_snapshot_state(self) -> None:
        """Update the latest snapshot with current state."""
        self.state_recorder.update_latest_state(
            self.areas,
            self.sensors,
        )

    def _log_state_change(
        self,
        sensor_id: str,
        sensor_type: str,
        state: bool,
        area_ids: List[str],
        old_occupancy: Dict[str, int],
        timestamp: float,
    ) -> None:
        """Log detailed state change information using compact notation."""
        new_occupancy = {aid: area.occupancy for aid, area in self.areas.items()}

        trigger = self.log_formatter.format_sensor_trigger(sensor_id, state, area_ids)

        changes = self.log_formatter.format_occupancy_changes(
            old_occupancy, new_occupancy
        )

        state_view = self.log_formatter.format_state_view(self.areas, self.sensors)

        if changes:
            _LOGGER.info(f"📍 {trigger} | {changes} | {state_view}")
        else:
            _LOGGER.info(f"📍 {trigger} | {state_view}")

    def _check_for_stuck_sensors(
        self, triggered_sensor_id: str, timestamp: float
    ) -> None:
        """Check for stuck sensors when a sensor is triggered."""
        trusted_before = {
            sensor_id: sensor.is_trusted_active
            for sensor_id, sensor in self.sensors.items()
        }
        self.anomaly_detector.check_for_stuck_sensors(
            self.sensors, self.areas, triggered_sensor_id
        )
        if any(
            trusted_before[sensor_id] != sensor.is_trusted_active
            for sensor_id, sensor in self.sensors.items()
        ):
            self._refresh_after_trust_change(timestamp)

    def get_occupancy(self, area_id: str) -> int:
        """Get current occupancy count for an area."""
        area = self.areas.get(area_id)
        return area.occupancy if area else 0

    def get_active_sensor_ids(self, area_id: str) -> list[str]:
        """Return trusted active motion sensors providing live evidence."""
        return sorted(
            sensor.id
            for sensor in self.sensors.values()
            if sensor.is_trusted_active
            and sensor.config.get("type", "") in MOTION_SENSOR_TYPES
            and area_id in sensor.area_ids
        )

    def get_occupancy_evidence(self, area_id: str) -> str:
        """Explain why an area currently reports occupied or vacant."""
        area = self.areas.get(area_id)
        if area is None:
            return "vacant"
        if self.get_active_sensor_ids(area_id):
            return "active"
        if area_id in self.occupancy_resolver.indoor_latched:
            return "stale"
        if area.occupied:
            return "inferred"
        return "vacant"

    def get_occupancy_freshness(self, area_id: str, timestamp: float = None) -> float:
        """Get a time-since-motion freshness score from 0 to 1."""
        area = self.areas.get(area_id)
        if not area:
            return 0.0

        now = timestamp if timestamp is not None else time.time()

        if area.occupancy <= 0:
            return 0.0

        # Manually set occupancy has no motion timestamp.
        if area.last_motion == 0:
            return 1.0

        time_since_motion = now - area.last_motion

        if time_since_motion < 60:
            return 1.0

        if time_since_motion < 300:
            return 0.9

        k = 0.00021
        decay_time = time_since_motion - 300
        freshness = 0.1 + 0.8 * math.exp(-k * decay_time)

        return round(freshness, 2)

    def get_occupancy_probability(self, area_id: str, timestamp: float = None) -> float:
        """Compatibility alias for the historical probability API."""
        return self.get_occupancy_freshness(area_id, timestamp)

    def get_warnings(self, active_only: bool = True) -> List[Warning]:
        """Get list of warnings."""
        return self.anomaly_detector.get_warnings(active_only)

    def check_timeouts(self, timestamp: float = None) -> None:
        """Check for timeout conditions."""
        if timestamp is None:
            timestamp = time.time()
        self.anomaly_detector.check_timeouts(
            self.areas,
            timestamp,
            sensors=self.sensors,
            freshness_fn=self.get_occupancy_freshness,
        )
        self.state_recorder.maybe_record_tick(
            timestamp,
            self.areas,
            self.sensors,
        )
        # Always update data to reflect freshness decay.
        self.async_set_updated_data(self.diagnostics.get_system_status())

    def resolve_warning(self, warning_id: str) -> bool:
        """Resolve a specific warning by ID."""
        result = self.anomaly_detector.resolve_warning(warning_id)
        if result:
            self.async_set_updated_data(self.diagnostics.get_system_status())
        return result

    def get_area_status(self, area_id: str) -> Dict[str, Any]:
        """Get detailed status information for an area."""
        return self.diagnostics.get_area_status(area_id)

    def get_system_status(self) -> Dict[str, Any]:
        """Get overall system status information."""
        return self.diagnostics.get_system_status()

    def reset_anomalies(self) -> None:
        """Reset the anomaly detection system without resetting occupancy state."""
        self.anomaly_detector = AnomalyDetector(self.config)
        _LOGGER.info("Anomaly detection system reset")
        self.async_set_updated_data(self.diagnostics.get_system_status())

    def reset_warnings(self) -> None:
        """Clear all active warnings without altering other state."""
        if self.anomaly_detector.clear_warnings():
            _LOGGER.info("Active warnings cleared")
        self.async_set_updated_data(self.diagnostics.get_system_status())

    def clear_stale_occupancy(self, area_ids: Iterable[str] | None = None) -> list[str]:
        """Clear conservatively latched rooms whose sensors are currently off."""
        timestamp = time.time()
        cleared = self.occupancy_resolver.clear_stale_indoor_occupancy(
            timestamp, self.areas, self.sensors, area_ids
        )
        if cleared:
            self.state_recorder.record_clear_event(
                timestamp, cleared, self.areas, self.sensors
            )
            self._schedule_occupancy_save()
            _LOGGER.info("Cleared stale occupancy: %s", ", ".join(sorted(cleared)))
        self.async_set_updated_data(self.diagnostics.get_system_status())
        return cleared

    def reset(self) -> None:
        """Reset the entire system state."""
        had_latched_occupancy = bool(self.occupancy_resolver.indoor_latched)
        # Reset all areas using proper reset method
        for area in self.areas.values():
            area.reset()

        # Reset all sensors
        for sensor in self.sensors.values():
            sensor.reset()

        self.occupancy_resolver.reset()

        # Create new anomaly detector and state recorder
        self.anomaly_detector = AnomalyDetector(self.config)
        self.state_recorder.reset()

        if had_latched_occupancy:
            self._schedule_occupancy_save()

        _LOGGER.info("Occupancy tracker system reset")
        self.async_set_updated_data(self.diagnostics.get_system_status())

    def rebuild_from_history(self) -> None:
        """Rebuild occupancy state from recorded history."""
        history = self.state_recorder.get_history()
        if not history:
            return

        self.occupancy_resolver.recalculate_from_history(
            history,
            self.areas,
            self.sensors,
            self.anomaly_detector,
        )
        self._refresh_latest_snapshot_state()

    def verify_history(self) -> bool:
        """Verify that replayed history matches without mutating live state."""
        history = self.state_recorder.get_history()
        if not history:
            _LOGGER.info("No history to verify")
            return True

        verifier = HistoryVerifier()
        result = verifier.replay_and_verify(
            history,
            self.occupancy_resolver,
            self.areas,
            self.sensors,
        )
        if result:
            _LOGGER.info("History verification passed: system is deterministic")
        else:
            _LOGGER.error("History verification failed: %s", verifier.get_summary())
        return result

    def diagnose_motion_issues(self, sensor_id: str = None) -> Dict[str, Any]:
        """Diagnostic method to help identify why motion isn't being detected."""
        return self.diagnostics.diagnose_motion_issues(sensor_id)
