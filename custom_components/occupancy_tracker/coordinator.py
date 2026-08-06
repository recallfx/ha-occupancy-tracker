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
from .helpers.audit import audit_event
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

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.occupancy_state"
MAX_SOURCE_CLOCK_SKEW = 60.0


def _valid_source_timestamp(source: float, received: float) -> bool:
    """Reject corrupt or implausibly future source times."""
    return (
        math.isfinite(source)
        and math.isfinite(received)
        and source >= 0
        and source <= received + MAX_SOURCE_CLOCK_SKEW
    )


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
        try:
            stored = await self._store.async_load()
        except Exception as err:  # Store errors must fail safe.
            _LOGGER.error(
                "Could not restore occupancy state; leaving rooms unknown: %s", err
            )
            audit_event("occupancy_restore_failed", reason="store_error")
            return
        if not stored:
            _LOGGER.warning("No stored occupancy state; indoor rooms remain unknown")
            audit_event(
                "occupancy_restored",
                status="missing",
                possible=[],
                cleared=[],
                unknown=sorted(
                    area_id for area_id, area in self.areas.items() if area.is_indoors
                ),
            )
            return
        if not isinstance(stored, dict):
            _LOGGER.warning("Ignoring invalid stored occupancy state")
            audit_event("occupancy_restore_failed", reason="invalid_root")
            return

        stored_areas = stored.get("areas")
        if not isinstance(stored_areas, dict):
            _LOGGER.warning("Ignoring invalid stored occupancy state")
            audit_event("occupancy_restore_failed", reason="invalid_areas")
            return

        restored: list[str] = []
        cleared: list[str] = []
        invalid_evidence: list[str] = []
        restore_time = time.time()
        legacy_store = stored.get("initialized") is not True
        for area_id, area in self.areas.items():
            if not area.is_indoors:
                continue
            area_data = stored_areas.get(area_id)
            if not isinstance(area_data, dict):
                continue

            last_motion = area_data.get("last_motion")
            if (
                isinstance(last_motion, (int, float))
                and not isinstance(last_motion, bool)
                and math.isfinite(last_motion)
                and 0 < last_motion <= restore_time + MAX_SOURCE_CLOCK_SKEW
            ):
                area.last_motion = float(last_motion)
            elif last_motion not in (None, 0):
                invalid_evidence.append(f"{area_id}.last_motion")
            last_contact = area_data.get("last_contact")
            if (
                isinstance(last_contact, (int, float))
                and not isinstance(last_contact, bool)
                and math.isfinite(last_contact)
                and 0 < last_contact <= restore_time + MAX_SOURCE_CLOCK_SKEW
            ):
                area.last_contact = float(last_contact)
            elif last_contact not in (None, 0):
                invalid_evidence.append(f"{area_id}.last_contact")

            state = area_data.get("state")
            if state == "unknown":
                continue
            if state == "cleared":
                area.clear_occupancy(
                    restore_time,
                    reason=area_data.get("cleared_by") or "persisted_clear",
                )
                cleared.append(area_id)
                continue
            if state not in (None, "possible"):
                _LOGGER.warning(
                    "Ignoring invalid stored state for %s: %s", area_id, state
                )
                continue
            area.stale_since = restore_time
            area.cleared_by = None
            area.apply_resolved_occupancy(True)
            self.occupancy_resolver.indoor_latched.add(area_id)
            restored.append(area_id)

        if restored:
            self.state_recorder.record_restore_event(
                time.time(), restored, self.areas, self.sensors
            )
            _LOGGER.info("Restored occupied areas: %s", ", ".join(sorted(restored)))
        if cleared:
            self.state_recorder.record_clear_event(
                restore_time, cleared, self.areas, self.sensors
            )
            _LOGGER.info(
                "Restored explicitly cleared areas: %s", ", ".join(sorted(cleared))
            )
        if legacy_store:
            self._schedule_occupancy_save()
        audit_event(
            "occupancy_restored",
            timestamp=restore_time,
            status="legacy_migrated" if legacy_store else "current",
            possible=sorted(restored),
            cleared=sorted(cleared),
            unknown=sorted(
                area_id
                for area_id, area in self.areas.items()
                if area.is_indoors and not area.state_known
            ),
            invalid_evidence=sorted(invalid_evidence),
        )
        self.data = self.diagnostics.get_system_status()

    def _stored_occupancy(self) -> dict[str, Any]:
        """Build the minimal durable conservative-occupancy state."""
        areas: dict[str, dict[str, Any]] = {}
        for area_id, area in sorted(self.areas.items()):
            if not area.is_indoors:
                continue
            if area_id in self.occupancy_resolver.indoor_latched:
                areas[area_id] = {
                    "state": "possible",
                    "last_motion": area.last_motion,
                    "last_contact": area.last_contact,
                }
            elif area.state_known:
                areas[area_id] = {
                    "state": "cleared",
                    "last_motion": area.last_motion,
                    "last_contact": area.last_contact,
                    "cleared_by": area.cleared_by or "explicit_clear",
                }
            else:
                areas[area_id] = {"state": "unknown"}
        return {
            "initialized": True,
            "areas": areas,
        }

    def _schedule_occupancy_save(self, delay: float = 0) -> None:
        """Persist a latch change without blocking sensor processing."""
        self._store.async_delay_save(self._stored_occupancy, delay=delay)

    def _initialize_areas(self, config: OccupancyTrackerConfig) -> None:
        """Initialize area tracking objects from configuration."""
        for area_id, area_config in config.get("areas", {}).items():
            self.areas[area_id] = AreaState(area_id, area_config)

    def _initialize_sensors(self, config: OccupancyTrackerConfig) -> None:
        """Initialize sensor tracking objects from configuration."""
        for sensor_id, sensor_config in config.get("sensors", {}).items():
            self.sensors[sensor_id] = SensorState(sensor_id, sensor_config, time.time())

    def process_sensor_event(
        self,
        sensor_id: str,
        state: bool,
        timestamp: float,
        received_timestamp: float | None = None,
    ) -> None:
        """Process a sensor state change event."""
        received_at = (
            received_timestamp if received_timestamp is not None else timestamp
        )
        if not _valid_source_timestamp(timestamp, received_at):
            audit_event(
                "sensor_event",
                timestamp=received_at if math.isfinite(received_at) else None,
                source_timestamp=(
                    timestamp if math.isfinite(timestamp) else str(timestamp)
                ),
                sensor_id=sensor_id,
                state=state,
                decision="ignored_invalid_timestamp",
            )
            return
        if sensor_id not in self.sensors:
            _LOGGER.warning("Unknown sensor ID: %s", sensor_id)
            audit_event(
                "sensor_event",
                timestamp=received_at,
                source_timestamp=timestamp,
                sensor_id=sensor_id,
                state=state,
                decision="ignored_unknown_sensor",
            )
            return

        sensor = self.sensors[sensor_id]
        sensor_type = sensor.config.get("type", "")
        area_ids = sensor.config.get("area", [])
        if isinstance(area_ids, str):
            area_ids = [area_ids]

        if timestamp < sensor.last_source_timestamp:
            audit_event(
                "sensor_event",
                timestamp=received_at,
                source_timestamp=timestamp,
                sensor_id=sensor_id,
                sensor_type=sensor_type,
                state=state,
                areas=sorted(area_ids),
                decision="ignored_out_of_order",
                latest_source_timestamp=sensor.last_source_timestamp,
            )
            return

        old_occupancy = {aid: area.occupancy for aid, area in self.areas.items()}
        old_latched = set(self.occupancy_resolver.indoor_latched)
        old_evidence = {
            area_id: (area.last_motion, area.last_contact)
            for area_id, area in self.areas.items()
            if area.is_indoors
        }

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
            audit_event(
                "sensor_event",
                timestamp=received_at,
                source_timestamp=timestamp,
                sensor_id=sensor_id,
                sensor_type=sensor_type,
                state=state,
                areas=sorted(area_ids),
                decision="ignored_duplicate",
                availability_recovered=not was_available,
            )
            return

        snapshot = self._record_snapshot(sensor_id, state, timestamp)

        if snapshot:
            self.occupancy_resolver.process_snapshot(
                snapshot, self.areas, self.sensors, self.anomaly_detector
            )

            if old_latched != self.occupancy_resolver.indoor_latched:
                self._schedule_occupancy_save()
            elif any(
                old_evidence[area_id]
                != (self.areas[area_id].last_motion, self.areas[area_id].last_contact)
                for area_id in old_evidence
            ):
                self._schedule_occupancy_save(delay=30)

            # Stuck duration is evaluated at receipt time. The source time may
            # be an old HA startup state and is retained for evidence aging.
            self._check_for_stuck_sensors(received_at)

            # Persist trust changes made by anomaly detection in this event's
            # replayable snapshot.
            self._refresh_latest_snapshot_state()
            self._log_state_change(
                sensor_id, sensor_type, state, area_ids, old_occupancy, timestamp
            )

            new_occupancy = {
                area_id: area.occupancy for area_id, area in self.areas.items()
            }
            occupancy_changes = {
                area_id: {"from": old_occupancy[area_id], "to": occupancy}
                for area_id, occupancy in new_occupancy.items()
                if old_occupancy[area_id] != occupancy
            }
            new_latched = self.occupancy_resolver.indoor_latched
            audit_event(
                "sensor_event",
                timestamp=received_at,
                source_timestamp=timestamp,
                sensor_id=sensor_id,
                sensor_type=sensor_type,
                state=state,
                areas=sorted(area_ids),
                decision=(
                    "accepted_transition" if state_changed else "accepted_keepalive"
                ),
                availability_recovered=not was_available,
                trusted=sensor.is_trusted_active,
                available=sensor.is_available,
                reliable=sensor.is_reliable,
                stuck=sensor.is_stuck,
                occupancy_changes=occupancy_changes,
                latches_added=sorted(new_latched - old_latched),
                latches_removed=sorted(old_latched - new_latched),
            )

            self.last_event_time = received_at
            self.async_set_updated_data(self.diagnostics.get_system_status())

    def invalidate_sensor_state(
        self,
        sensor_id: str,
        timestamp: float,
        received_timestamp: float | None = None,
    ) -> None:
        """Stop trusting cached state without inventing a physical OFF edge."""
        received_at = (
            received_timestamp if received_timestamp is not None else timestamp
        )
        if not _valid_source_timestamp(timestamp, received_at):
            audit_event(
                "sensor_availability",
                timestamp=received_at if math.isfinite(received_at) else None,
                source_timestamp=(
                    timestamp if math.isfinite(timestamp) else str(timestamp)
                ),
                sensor_id=sensor_id,
                available=False,
                decision="ignored_invalid_timestamp",
            )
            return
        sensor = self.sensors.get(sensor_id)
        if sensor is None:
            _LOGGER.warning("Unknown sensor ID: %s", sensor_id)
            audit_event(
                "sensor_availability",
                timestamp=received_at,
                source_timestamp=timestamp,
                sensor_id=sensor_id,
                available=False,
                decision="ignored_unknown_sensor",
            )
            return

        was_available = sensor.is_available
        if timestamp < sensor.last_source_timestamp:
            audit_event(
                "sensor_availability",
                timestamp=received_at,
                source_timestamp=timestamp,
                sensor_id=sensor_id,
                available=False,
                decision="ignored_out_of_order",
                latest_source_timestamp=sensor.last_source_timestamp,
            )
            return
        sensor.mark_unavailable(timestamp)
        if was_available:
            self._record_sensor_availability(sensor_id, False, timestamp)
            self._refresh_after_trust_change(timestamp)
            self._refresh_latest_snapshot_state()
        audit_event(
            "sensor_availability",
            timestamp=received_at,
            source_timestamp=timestamp,
            sensor_id=sensor_id,
            available=False,
            decision="invalidated" if was_available else "already_unavailable",
        )
        self.last_event_time = received_at
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

    def seed_sensor_state(
        self,
        sensor_id: str,
        state: bool,
        timestamp: float,
        received_timestamp: float | None = None,
    ) -> None:
        """Set an initial HA state without replaying it as a fresh edge."""
        received_at = (
            received_timestamp if received_timestamp is not None else timestamp
        )
        if not _valid_source_timestamp(timestamp, received_at):
            audit_event(
                "sensor_baseline",
                timestamp=received_at if math.isfinite(received_at) else None,
                source_timestamp=(
                    timestamp if math.isfinite(timestamp) else str(timestamp)
                ),
                sensor_id=sensor_id,
                state=state,
                decision="ignored_invalid_timestamp",
            )
            return
        sensor = self.sensors.get(sensor_id)
        if sensor is None:
            _LOGGER.warning("Unknown sensor ID: %s", sensor_id)
            audit_event(
                "sensor_baseline",
                timestamp=received_at,
                source_timestamp=timestamp,
                sensor_id=sensor_id,
                state=state,
                decision="ignored_unknown_sensor",
            )
            return
        sensor.seed_state(state, timestamp)
        self.state_recorder.record_sensor_baseline(
            timestamp=timestamp,
            sensor_id=sensor_id,
            state=state,
            areas=self.areas,
            sensors=self.sensors,
        )
        audit_event(
            "sensor_baseline",
            timestamp=received_at,
            source_timestamp=timestamp,
            sensor_id=sensor_id,
            sensor_type=sensor.config.get("type", ""),
            state=state,
            areas=sorted(sensor.area_ids),
            decision="seeded",
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

        if changes:
            _LOGGER.info("%s | %s", trigger, changes)
        else:
            _LOGGER.debug(
                "%s | no occupancy change | type=%s source_timestamp=%.3f",
                trigger,
                sensor_type,
                timestamp,
            )

    def _check_for_stuck_sensors(self, timestamp: float) -> None:
        """Check for stuck sensors and audit trust transitions."""
        trusted_before = {
            sensor_id: sensor.is_trusted_active
            for sensor_id, sensor in self.sensors.items()
        }
        self.anomaly_detector.check_for_stuck_sensors(
            self.sensors, self.areas, timestamp
        )
        trust_changes = {
            sensor_id: {
                "from": trusted_before[sensor_id],
                "to": sensor.is_trusted_active,
                "stuck": sensor.is_stuck,
                "available": sensor.is_available,
                "reliable": sensor.is_reliable,
            }
            for sensor_id, sensor in self.sensors.items()
            if trusted_before[sensor_id] != sensor.is_trusted_active
        }
        if trust_changes:
            self._refresh_after_trust_change(timestamp)
            self._refresh_latest_snapshot_state()
            audit_event(
                "sensor_trust_changed",
                timestamp=timestamp,
                sensors=trust_changes,
            )

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
        if not area.state_known:
            return "unknown"
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

        # Missing evidence history is not full freshness.
        if area.last_motion == 0:
            return 0.0

        time_since_motion = max(0.0, now - area.last_motion)
        scaled_time = time_since_motion / area.profile.freshness_scale

        if scaled_time < 60:
            return 1.0

        if scaled_time < 300:
            return 0.9

        k = 0.00021
        decay_time = scaled_time - 300
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
        self._check_for_stuck_sensors(timestamp)
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
        self.anomaly_detector.clear_warnings()
        self.anomaly_detector = AnomalyDetector(self.config)
        _LOGGER.info("Anomaly detection system reset")
        audit_event("anomaly_detector_reset")
        self.async_set_updated_data(self.diagnostics.get_system_status())

    def reset_warnings(self) -> None:
        """Clear all active warnings without altering other state."""
        if self.anomaly_detector.clear_warnings():
            _LOGGER.info("Active warnings cleared")
        self.async_set_updated_data(self.diagnostics.get_system_status())

    def clear_stale_occupancy(
        self,
        area_ids: Iterable[str] | None = None,
        *,
        actor: str = "direct",
        reason: str = "manual_clear",
    ) -> list[str]:
        """Clear conservatively latched rooms whose sensors are currently off."""
        timestamp = time.time()
        if area_ids is None:
            requested = None
        elif isinstance(area_ids, str):
            requested = [area_ids]
        else:
            requested = sorted(set(area_ids))
        cleared = self.occupancy_resolver.clear_stale_indoor_occupancy(
            timestamp, self.areas, self.sensors, requested, reason=reason
        )
        if cleared:
            self.state_recorder.record_clear_event(
                timestamp, cleared, self.areas, self.sensors
            )
            self._schedule_occupancy_save()
            _LOGGER.info("Cleared stale occupancy: %s", ", ".join(sorted(cleared)))
        audit_event(
            "occupancy_clear",
            timestamp=timestamp,
            actor=actor,
            reason=reason,
            requested="all_indoor" if requested is None else requested,
            cleared=sorted(cleared),
            refused_active=sorted(
                area_id
                for area_id in (self.areas if requested is None else requested)
                if self.get_active_sensor_ids(area_id)
            ),
            ignored_invalid=sorted(
                area_id
                for area_id in (requested or [])
                if area_id not in self.areas or not self.areas[area_id].is_indoors
            ),
        )
        self.async_set_updated_data(self.diagnostics.get_system_status())
        return cleared

    def reset(self) -> None:
        """Reset the entire system state."""
        had_durable_state = bool(self.occupancy_resolver.indoor_latched) or any(
            area.is_indoors and area.state_known for area in self.areas.values()
        )
        # Reset all areas using proper reset method
        for area in self.areas.values():
            area.reset()

        # Reset all sensors
        for sensor in self.sensors.values():
            sensor.reset()

        self.occupancy_resolver.reset()

        # Create new anomaly detector and state recorder
        self.anomaly_detector.clear_warnings()
        self.anomaly_detector = AnomalyDetector(self.config)
        self.state_recorder.reset()

        if had_durable_state:
            self._schedule_occupancy_save()

        _LOGGER.info("Occupancy tracker system reset")
        audit_event(
            "system_reset",
            durable_state_reset=had_durable_state,
        )
        self.async_set_updated_data(self.diagnostics.get_system_status())

    def rebuild_from_history(self) -> bool:
        """Compatibility alias for safe, non-mutating history verification.

        The recorder is intentionally bounded and therefore cannot be an
        authoritative source for clearing durable occupancy.
        """
        return self.verify_history()

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
