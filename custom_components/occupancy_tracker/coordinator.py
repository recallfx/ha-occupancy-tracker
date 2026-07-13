from __future__ import annotations

import logging
import math
import time
from datetime import timedelta
from typing import Any, Dict, List, Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers.debounce import Debouncer
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

# Periodic update interval for consistency checks
PERIODIC_UPDATE_INTERVAL = timedelta(seconds=5)


class OccupancyCoordinator(DataUpdateCoordinator[Dict[str, Any]]):
    """Coordinator for Occupancy Tracker."""

    def __init__(
        self,
        hass: HomeAssistant,
        config: OccupancyTrackerConfig,
        enable_periodic_updates: bool = True,
    ) -> None:
        """Initialize the coordinator."""
        request_refresh_debouncer = Debouncer(
            hass,
            _LOGGER,
            cooldown=0,
            immediate=True,
        )

        # Use periodic updates in production, disable in tests
        update_interval = PERIODIC_UPDATE_INTERVAL if enable_periodic_updates else None

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=update_interval,
            request_refresh_debouncer=request_refresh_debouncer,
        )
        self.config = config
        self.last_event_time = time.time()

        # Initialize state dictionaries directly
        self.areas: Dict[str, AreaState] = {}
        self.sensors: Dict[str, SensorState] = {}
        self._initialize_areas(config)
        self._initialize_sensors(config)

        # Initialize helpers
        self.anomaly_detector = AnomalyDetector(config)
        self.state_recorder = MapStateRecorder()
        self.occupancy_resolver = MapOccupancyResolver(config)
        self.log_formatter = LogFormatter(self.areas, self.sensors)

        # Initialize diagnostics
        self.diagnostics = OccupancyDiagnostics(self)

        # Publish initial state immediately so entities don't show stale restored data
        self.data = self.diagnostics.get_system_status()

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
        # Validate sensor exists
        if sensor_id not in self.sensors:
            _LOGGER.warning(f"Unknown sensor ID: {sensor_id}")
            return

        sensor = self.sensors[sensor_id]
        sensor_type = sensor.config.get("type", "")
        area_ids = sensor.config.get("area", [])
        if isinstance(area_ids, str):
            area_ids = [area_ids]

        # Capture state before processing (all areas, not just linked ones)
        old_occupancy = {aid: area.occupancy for aid, area in self.areas.items()}

        # Update sensor state
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

        # Record snapshot
        snapshot = self._record_snapshot(sensor_id, state, timestamp)

        # Process snapshot through the resolver
        if snapshot:
            # Consistency checks removed in lean architecture
            # Logic is now event-driven only (Motion On/Off)
            self.occupancy_resolver.process_snapshot(
                snapshot, self.areas, self.sensors, self.anomaly_detector
            )

            # Check for stuck sensors after processing
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
        self.async_set_updated_data(self.diagnostics.get_system_status())

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
        # Build new occupancy dict
        new_occupancy = {aid: area.occupancy for aid, area in self.areas.items()}

        # Format the trigger
        trigger = self.log_formatter.format_sensor_trigger(sensor_id, state, area_ids)

        # Format occupancy changes
        changes = self.log_formatter.format_occupancy_changes(
            old_occupancy, new_occupancy
        )

        # Format state view
        state_view = self.log_formatter.format_state_view(self.areas, self.sensors)

        if changes:
            _LOGGER.info(f"📍 {trigger} | {changes} | {state_view}")
        else:
            _LOGGER.info(f"📍 {trigger} | {state_view}")

    def _check_for_stuck_sensors(
        self, triggered_sensor_id: str, timestamp: float
    ) -> None:
        """Check for stuck sensors when a sensor is triggered."""
        self.anomaly_detector.check_for_stuck_sensors(
            self.sensors, self.areas, triggered_sensor_id
        )

    def get_occupancy(self, area_id: str) -> int:
        """Get current occupancy count for an area."""
        area = self.areas.get(area_id)
        return area.occupancy if area else 0

    def get_occupancy_probability(self, area_id: str, timestamp: float = None) -> float:
        """Get probability score (0-1) that area is occupied."""
        area = self.areas.get(area_id)
        if not area:
            return 0.0

        now = timestamp if timestamp is not None else time.time()

        if area.occupancy <= 0:
            return 0.0

        # If area is occupied but no motion ever recorded (e.g. manually set),
        # assume high confidence
        if area.last_motion == 0:
            return 1.0

        time_since_motion = now - area.last_motion

        # High confidence period (0-60s)
        if time_since_motion < 60:
            return 1.0

        # Medium confidence period (1-5 mins)
        if time_since_motion < 300:
            return 0.9

        # Decay period (> 5 mins)
        # Decay from 0.9 down to 0.1 over 1 hour (3600s)
        k = 0.00021
        decay_time = time_since_motion - 300
        probability = 0.1 + 0.8 * math.exp(-k * decay_time)

        return round(probability, 2)

    def get_warnings(self, active_only: bool = True) -> List[Warning]:
        """Get list of warnings."""
        return self.anomaly_detector.get_warnings(active_only)

    def check_timeouts(self, timestamp: float = None) -> None:
        """Check for timeout conditions."""
        if timestamp is None:
            timestamp = time.time()
        cleared_area_ids = self.anomaly_detector.check_timeouts(
            self.areas,
            timestamp,
            sensors=self.sensors,
            probability_fn=self.get_occupancy_probability,
        )
        self._clear_resolver_retention(cleared_area_ids)
        self.state_recorder.maybe_record_tick(
            timestamp,
            self.areas,
            self.sensors,
        )
        # Always update data to reflect probability decay
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

    def reset(self) -> None:
        """Reset the entire system state."""
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
        """
        Verify that recorded history matches replay (determinism check).

        This compares the recorded occupancy state against what the current
        logic produces when replaying the same events. Useful for:
        - Detecting logic drift after code changes
        - Verifying bug fixes
        - Ensuring system determinism

        Returns:
            True if history matches replay, False if differences found
        """
        history = self.state_recorder.get_history()
        if not history:
            _LOGGER.info("No history to verify")
            return True

        # Save current state
        original_areas = {
            aid: (area.occupancy, area.last_motion) for aid, area in self.areas.items()
        }
        original_sensors = {
            sid: (
                sensor.current_state,
                sensor.last_changed,
                sensor.is_available,
                sensor.is_reliable,
                sensor.is_stuck,
            )
            for sid, sensor in self.sensors.items()
        }

        try:
            # Reset and replay
            for area in self.areas.values():
                area.occupancy = 0
                area.last_motion = 0
                area.activity_history = []

            for sensor in self.sensors.values():
                sensor.current_state = False
                sensor.history = []
                sensor.is_available = True
                sensor.is_reliable = True
                sensor.is_stuck = False

            self.occupancy_resolver.reset()

            # Replay history
            self.occupancy_resolver.recalculate_from_history(
                history,
                self.areas,
                self.sensors,
                None,  # Don't generate new warnings during verification
            )

            # Verify
            verifier = HistoryVerifier()
            result = verifier.verify_history(
                history,
                self.areas,
                self.sensors,
            )

            # Log summary
            summary = verifier.get_summary()
            if not result:
                _LOGGER.error(f"History verification failed: {summary}")
            else:
                _LOGGER.info("History verification passed: system is deterministic")

            return result

        finally:
            # Restore original state
            for aid, (occ, motion) in original_areas.items():
                if aid in self.areas:
                    self.areas[aid].occupancy = occ
                    self.areas[aid].last_motion = motion

            for sid, (
                state,
                changed,
                available,
                reliable,
                stuck,
            ) in original_sensors.items():
                if sid in self.sensors:
                    self.sensors[sid].current_state = state
                    self.sensors[sid].last_changed = changed
                    self.sensors[sid].is_available = available
                    self.sensors[sid].is_reliable = reliable
                    self.sensors[sid].is_stuck = stuck

    def diagnose_motion_issues(self, sensor_id: str = None) -> Dict[str, Any]:
        """Diagnostic method to help identify why motion isn't being detected."""
        return self.diagnostics.diagnose_motion_issues(sensor_id)

    async def _async_update_data(self) -> Dict[str, Any]:
        """Periodic update — timeout checks and phantom cleanup."""
        timestamp = time.time()

        old_occupancy = {aid: area.occupancy for aid, area in self.areas.items()}

        cleared_area_ids = self.anomaly_detector.check_timeouts(
            self.areas,
            timestamp,
            sensors=self.sensors,
            probability_fn=self.get_occupancy_probability,
        )
        self._clear_resolver_retention(cleared_area_ids)

        new_occupancy = {aid: area.occupancy for aid, area in self.areas.items()}
        if old_occupancy != new_occupancy:
            state_view = self.log_formatter.format_state_view(self.areas, self.sensors)
            changes = self.log_formatter.format_occupancy_changes(
                old_occupancy, new_occupancy
            )
            if changes:
                _LOGGER.info(f"⏱️ periodic | {changes} | {state_view}")

        return self.diagnostics.get_system_status()

    def _clear_resolver_retention(self, area_ids: List[str]) -> None:
        """Remove areas phantom cleanup already cleared from resolver retention."""
        for area_id in area_ids:
            self.occupancy_resolver.retained.pop(area_id, None)
