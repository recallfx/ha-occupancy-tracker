"""
History Verification - Validates system determinism by comparing recorded vs replayed state.

This module provides tools to verify that the occupancy tracking logic is deterministic:
- Recorded History: What actually happened (includes any bugs that existed)
- Replayed History: What the current logic produces from the same events

By comparing these, we can:
1. Detect logic drift (changes in behavior)
2. Verify bug fixes (before/after comparison)
3. Ensure determinism (same inputs = same outputs)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .area_state import AreaState
from .map_state_recorder import MapSnapshot
from .sensor_state import SensorState

_LOGGER = logging.getLogger(__name__)


@dataclass
class StateDifference:
    """Represents a difference between recorded and replayed state."""

    snapshot_index: int
    timestamp: float
    description: str
    area_id: Optional[str] = None
    sensor_id: Optional[str] = None
    recorded_value: Any = None
    replayed_value: Any = None

    def __str__(self) -> str:
        location = self.area_id or self.sensor_id or "system"
        return (
            f"[{self.snapshot_index}] {self.description} in {location}: "
            f"recorded={self.recorded_value}, replayed={self.replayed_value}"
        )


class HistoryVerifier:
    """Verifies system determinism by comparing recorded vs replayed history."""

    def __init__(self, tolerance: float = 0.001):
        """
        Initialize the verifier.

        Args:
            tolerance: Floating point comparison tolerance for timestamps
        """
        self.tolerance = tolerance
        self.differences: List[StateDifference] = []

    def verify_history(
        self,
        recorded_snapshots: List[MapSnapshot],
        replayed_areas: Dict[str, AreaState],
        replayed_sensors: Dict[str, SensorState],
    ) -> bool:
        """
        Compare recorded history against current replayed state.

        Args:
            recorded_snapshots: Original snapshots from MapStateRecorder
            replayed_areas: Area states after replaying history
            replayed_sensors: Sensor states after replaying history

        Returns:
            True if history matches (deterministic), False if differences found
        """
        self.differences.clear()

        if not recorded_snapshots:
            _LOGGER.warning("No history to verify")
            return True

        # Compare the final snapshot (most important)
        final_snapshot = recorded_snapshots[-1]
        self._compare_snapshot(
            len(recorded_snapshots) - 1,
            final_snapshot,
            replayed_areas,
            replayed_sensors,
        )

        has_differences = len(self.differences) > 0

        if has_differences:
            _LOGGER.warning(
                f"History verification failed: {len(self.differences)} difference(s) found"
            )
            for diff in self.differences:
                _LOGGER.warning(f"  {diff}")
        else:
            _LOGGER.info("History verification passed: recorded state matches replay")

        return not has_differences

    def verify_all_snapshots(
        self,
        recorded_snapshots: List[MapSnapshot],
        replay_callback,
    ) -> bool:
        """
        Verify every snapshot by replaying history step-by-step.

        This is more thorough but slower than verify_history().

        Args:
            recorded_snapshots: Original snapshots from MapStateRecorder
            replay_callback: Function that replays history up to index N and returns (areas, sensors)

        Returns:
            True if all snapshots match, False otherwise
        """
        self.differences.clear()

        for i, snapshot in enumerate(recorded_snapshots):
            # Replay history up to this point
            replayed_areas, replayed_sensors = replay_callback(i)

            # Compare
            self._compare_snapshot(i, snapshot, replayed_areas, replayed_sensors)

        has_differences = len(self.differences) > 0

        if has_differences:
            _LOGGER.warning(
                f"Full history verification failed: {len(self.differences)} difference(s) found"
            )
            # Only log first 10 to avoid spam
            for diff in self.differences[:10]:
                _LOGGER.warning(f"  {diff}")
            if len(self.differences) > 10:
                _LOGGER.warning(f"  ... and {len(self.differences) - 10} more")
        else:
            _LOGGER.info(
                f"Full history verification passed: all {len(recorded_snapshots)} snapshots match"
            )

        return not has_differences

    def _compare_snapshot(
        self,
        index: int,
        snapshot: MapSnapshot,
        replayed_areas: Dict[str, AreaState],
        replayed_sensors: Dict[str, SensorState],
    ) -> None:
        """Compare a single snapshot against replayed state."""

        # Compare areas
        for area_id, recorded_data in snapshot.areas.items():
            if area_id not in replayed_areas:
                self.differences.append(
                    StateDifference(
                        snapshot_index=index,
                        timestamp=snapshot.timestamp,
                        description="Area missing in replay",
                        area_id=area_id,
                    )
                )
                continue

            replayed_area = replayed_areas[area_id]

            # Compare occupancy
            recorded_occ = recorded_data.get("occupancy", 0)
            if recorded_occ != replayed_area.occupancy:
                self.differences.append(
                    StateDifference(
                        snapshot_index=index,
                        timestamp=snapshot.timestamp,
                        description="Occupancy mismatch",
                        area_id=area_id,
                        recorded_value=recorded_occ,
                        replayed_value=replayed_area.occupancy,
                    )
                )

            # Compare last_motion (with tolerance for floating point)
            recorded_motion = recorded_data.get("last_motion", 0)
            if abs(recorded_motion - replayed_area.last_motion) > self.tolerance:
                self.differences.append(
                    StateDifference(
                        snapshot_index=index,
                        timestamp=snapshot.timestamp,
                        description="Last motion timestamp mismatch",
                        area_id=area_id,
                        recorded_value=recorded_motion,
                        replayed_value=replayed_area.last_motion,
                    )
                )

        # Compare sensors
        for sensor_id, recorded_data in snapshot.sensors.items():
            if sensor_id not in replayed_sensors:
                self.differences.append(
                    StateDifference(
                        snapshot_index=index,
                        timestamp=snapshot.timestamp,
                        description="Sensor missing in replay",
                        sensor_id=sensor_id,
                    )
                )
                continue

            replayed_sensor = replayed_sensors[sensor_id]

            # Compare state
            recorded_state = recorded_data.get("state", False)
            if recorded_state != replayed_sensor.current_state:
                self.differences.append(
                    StateDifference(
                        snapshot_index=index,
                        timestamp=snapshot.timestamp,
                        description="Sensor state mismatch",
                        sensor_id=sensor_id,
                        recorded_value=recorded_state,
                        replayed_value=replayed_sensor.current_state,
                    )
                )

            # Compare last_changed (with tolerance)
            recorded_changed = recorded_data.get("last_changed", 0)
            if abs(recorded_changed - replayed_sensor.last_changed) > self.tolerance:
                self.differences.append(
                    StateDifference(
                        snapshot_index=index,
                        timestamp=snapshot.timestamp,
                        description="Last changed timestamp mismatch",
                        sensor_id=sensor_id,
                        recorded_value=recorded_changed,
                        replayed_value=replayed_sensor.last_changed,
                    )
                )

            recorded_available = recorded_data.get("available", True)
            if recorded_available != replayed_sensor.is_available:
                self.differences.append(
                    StateDifference(
                        snapshot_index=index,
                        timestamp=snapshot.timestamp,
                        description="Sensor availability mismatch",
                        sensor_id=sensor_id,
                        recorded_value=recorded_available,
                        replayed_value=replayed_sensor.is_available,
                    )
                )

            recorded_reliable = recorded_data.get("reliable", True)
            if recorded_reliable != replayed_sensor.is_reliable:
                self.differences.append(
                    StateDifference(
                        snapshot_index=index,
                        timestamp=snapshot.timestamp,
                        description="Sensor reliability mismatch",
                        sensor_id=sensor_id,
                        recorded_value=recorded_reliable,
                        replayed_value=replayed_sensor.is_reliable,
                    )
                )

            recorded_stuck = recorded_data.get("stuck", False)
            if recorded_stuck != replayed_sensor.is_stuck:
                self.differences.append(
                    StateDifference(
                        snapshot_index=index,
                        timestamp=snapshot.timestamp,
                        description="Sensor stuck-state mismatch",
                        sensor_id=sensor_id,
                        recorded_value=recorded_stuck,
                        replayed_value=replayed_sensor.is_stuck,
                    )
                )

    def get_differences(self) -> List[StateDifference]:
        """Get all detected differences."""
        return self.differences.copy()

    def get_summary(self) -> Dict[str, Any]:
        """Get a summary of verification results."""
        return {
            "total_differences": len(self.differences),
            "passed": len(self.differences) == 0,
            "differences_by_type": self._group_by_type(),
            "affected_areas": list(
                set(d.area_id for d in self.differences if d.area_id)
            ),
            "affected_sensors": list(
                set(d.sensor_id for d in self.differences if d.sensor_id)
            ),
        }

    def _group_by_type(self) -> Dict[str, int]:
        """Group differences by description type."""
        counts: Dict[str, int] = {}
        for diff in self.differences:
            counts[diff.description] = counts.get(diff.description, 0) + 1
        return counts
