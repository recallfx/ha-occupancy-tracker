"""
Tests for history verification functionality.

Verifies that the system is deterministic by comparing recorded vs replayed state.
"""

import pytest
import time
from unittest.mock import Mock

from custom_components.occupancy_tracker.coordinator import OccupancyCoordinator
from custom_components.occupancy_tracker.helpers.map_state_recorder import (
    MapStateRecorder,
)
from custom_components.occupancy_tracker.helpers.history_verifier import (
    HistoryVerifier,
    StateDifference,
)


@pytest.fixture
def config():
    """Basic configuration for testing."""
    return {
        "areas": {
            "room_a": {"name": "Room A"},
            "room_b": {"name": "Room B", "exit_capable": True},
        },
        "sensors": {
            "motion_a": {"area": "room_a", "type": "motion"},
            "motion_b": {"area": "room_b", "type": "motion"},
        },
        "adjacency": {
            "room_a": ["room_b"],
        },
    }


def test_history_verifier_identical_states(config):
    """Test that identical states produce no differences."""
    from custom_components.occupancy_tracker.helpers.area_state import AreaState
    from custom_components.occupancy_tracker.helpers.sensor_state import SensorState
    from custom_components.occupancy_tracker.helpers.map_state_recorder import (
        MapSnapshot,
    )

    # Create identical recorded and replayed states
    recorded_snapshot = MapSnapshot(
        timestamp=100.0,
        event_type="sensor",
        description="test",
        areas={
            "room_a": {"occupancy": 1, "last_motion": 100.0, "is_occupied": True},
            "room_b": {"occupancy": 0, "last_motion": 0.0, "is_occupied": False},
        },
        sensors={
            "motion_a": {"state": True, "last_changed": 100.0},
            "motion_b": {"state": False, "last_changed": 0.0},
        },
    )

    replayed_areas = {
        "room_a": AreaState("room_a", {"name": "Room A"}),
        "room_b": AreaState("room_b", {"name": "Room B"}),
    }
    replayed_areas["room_a"].claims.add("_test_0")
    replayed_areas["room_a"].last_motion = 100.0

    replayed_sensors = {
        "motion_a": SensorState("motion_a", {"type": "motion"}, 0.0),
        "motion_b": SensorState("motion_b", {"type": "motion"}, 0.0),
    }
    replayed_sensors["motion_a"].current_state = True
    replayed_sensors["motion_a"].last_changed = 100.0

    # Verify
    verifier = HistoryVerifier()
    result = verifier.verify_history(
        [recorded_snapshot], replayed_areas, replayed_sensors
    )

    assert result is True
    assert len(verifier.get_differences()) == 0


def test_history_verifier_detects_occupancy_mismatch(config):
    """Test that occupancy differences are detected."""
    from custom_components.occupancy_tracker.helpers.area_state import AreaState
    from custom_components.occupancy_tracker.helpers.sensor_state import SensorState
    from custom_components.occupancy_tracker.helpers.map_state_recorder import (
        MapSnapshot,
    )

    recorded_snapshot = MapSnapshot(
        timestamp=100.0,
        event_type="sensor",
        description="test",
        areas={
            "room_a": {"occupancy": 1, "last_motion": 100.0, "is_occupied": True},
        },
        sensors={
            "motion_a": {"state": True, "last_changed": 100.0},
        },
    )

    replayed_areas = {
        "room_a": AreaState("room_a", {"name": "Room A"}),
    }
    # In the clustering model, occupancy is binary; set to 0 so it differs from recorded=1
    replayed_areas["room_a"].last_motion = 100.0

    replayed_sensors = {
        "motion_a": SensorState("motion_a", {"type": "motion"}, 0.0),
    }
    replayed_sensors["motion_a"].current_state = True
    replayed_sensors["motion_a"].last_changed = 100.0

    verifier = HistoryVerifier()
    result = verifier.verify_history(
        [recorded_snapshot], replayed_areas, replayed_sensors
    )

    assert result is False
    differences = verifier.get_differences()
    assert len(differences) == 1
    assert differences[0].description == "Occupancy mismatch"
    assert differences[0].area_id == "room_a"
    assert differences[0].recorded_value == 1
    assert differences[0].replayed_value == 0


def test_history_verifier_detects_sensor_mismatch(config):
    """Test that sensor state differences are detected."""
    from custom_components.occupancy_tracker.helpers.area_state import AreaState
    from custom_components.occupancy_tracker.helpers.sensor_state import SensorState
    from custom_components.occupancy_tracker.helpers.map_state_recorder import (
        MapSnapshot,
    )

    recorded_snapshot = MapSnapshot(
        timestamp=100.0,
        event_type="sensor",
        description="test",
        areas={"room_a": {"occupancy": 0, "last_motion": 0.0, "is_occupied": False}},
        sensors={
            "motion_a": {"state": True, "last_changed": 100.0},
        },
    )

    replayed_areas = {
        "room_a": AreaState("room_a", {"name": "Room A"}),
    }

    replayed_sensors = {
        "motion_a": SensorState("motion_a", {"type": "motion"}, 0.0),
    }
    replayed_sensors["motion_a"].current_state = False  # Different!
    replayed_sensors["motion_a"].last_changed = 100.0

    verifier = HistoryVerifier()
    result = verifier.verify_history(
        [recorded_snapshot], replayed_areas, replayed_sensors
    )

    assert result is False
    differences = verifier.get_differences()
    assert len(differences) == 1
    assert differences[0].description == "Sensor state mismatch"
    assert differences[0].sensor_id == "motion_a"


def test_history_verifier_summary(config):
    """Test that summary provides useful statistics."""
    from custom_components.occupancy_tracker.helpers.area_state import AreaState
    from custom_components.occupancy_tracker.helpers.sensor_state import SensorState
    from custom_components.occupancy_tracker.helpers.map_state_recorder import (
        MapSnapshot,
    )

    recorded_snapshot = MapSnapshot(
        timestamp=100.0,
        event_type="sensor",
        description="test",
        areas={
            "room_a": {"occupancy": 1, "last_motion": 100.0, "is_occupied": True},
            "room_b": {"occupancy": 2, "last_motion": 100.0, "is_occupied": True},
        },
        sensors={
            "motion_a": {"state": True, "last_changed": 100.0},
        },
    )

    replayed_areas = {
        "room_a": AreaState("room_a", {"name": "Room A"}),
        "room_b": AreaState("room_b", {"name": "Room B"}),
    }
    replayed_areas["room_a"].occupancy = 0  # Diff
    replayed_areas["room_b"].occupancy = 0  # Diff

    replayed_sensors = {
        "motion_a": SensorState("motion_a", {"type": "motion"}, 0.0),
    }
    replayed_sensors["motion_a"].current_state = True
    replayed_sensors["motion_a"].last_changed = 100.0

    verifier = HistoryVerifier()
    verifier.verify_history([recorded_snapshot], replayed_areas, replayed_sensors)

    summary = verifier.get_summary()
    assert summary["passed"] is False
    assert summary["total_differences"] == 4  # 2 occupancy + 2 timestamp mismatches
    assert "room_a" in summary["affected_areas"]
    assert "room_b" in summary["affected_areas"]
    assert summary["differences_by_type"]["Occupancy mismatch"] == 2
    assert summary["differences_by_type"]["Last motion timestamp mismatch"] == 2


@pytest.mark.asyncio
async def test_coordinator_verify_history_deterministic(hass, config):
    """Test that coordinator history verification works on deterministic system."""
    coordinator = OccupancyCoordinator(hass, config)

    # Generate some history
    now = time.time()
    coordinator.process_sensor_event("motion_b", True, now)
    coordinator.process_sensor_event("motion_b", False, now + 1)
    coordinator.process_sensor_event("motion_a", True, now + 2)

    # Verify - should pass because system is deterministic
    result = coordinator.verify_history()
    assert result is True


@pytest.mark.asyncio
async def test_coordinator_verify_history_preserves_state(hass, config):
    """Verification must not mutate any live area, sensor, or latch state."""
    coordinator = OccupancyCoordinator(hass, config)

    # Set up state
    now = time.time()
    coordinator.process_sensor_event("motion_a", True, now)

    area = coordinator.areas["room_a"]
    sensor = coordinator.sensors["motion_a"]
    area.last_off = now - 1
    area.stale_since = now + 1
    area.cleared_by = "test_marker"
    area.activity_history.append((now + 1, "test_marker"))

    original_area = (
        area.occupancy,
        area.last_motion,
        area.last_off,
        area.stale_since,
        area.cleared_by,
        area.last_occupied_at,
        list(area.activity_history),
    )
    original_sensor = (
        sensor.current_state,
        sensor.last_changed,
        sensor.activated_at,
        sensor.last_update_time,
        [(item.state, item.timestamp) for item in sensor.history],
        sensor.is_available,
        sensor.is_reliable,
        sensor.is_stuck,
    )
    original_latched = set(coordinator.occupancy_resolver.indoor_latched)
    original_first_activation = coordinator.occupancy_resolver._first_activation_time

    # Verify (internally resets and replays)
    coordinator.verify_history()

    assert (
        area.occupancy,
        area.last_motion,
        area.last_off,
        area.stale_since,
        area.cleared_by,
        area.last_occupied_at,
        area.activity_history,
    ) == original_area
    assert (
        sensor.current_state,
        sensor.last_changed,
        sensor.activated_at,
        sensor.last_update_time,
        [(item.state, item.timestamp) for item in sensor.history],
        sensor.is_available,
        sensor.is_reliable,
        sensor.is_stuck,
    ) == original_sensor
    assert coordinator.occupancy_resolver.indoor_latched == original_latched
    assert (
        coordinator.occupancy_resolver._first_activation_time
        == original_first_activation
    )


def test_bounded_history_rebuild_cannot_mutate_live_occupancy(hass, config):
    """Evicted restore evidence must never turn a consistency check into a clear."""
    coordinator = OccupancyCoordinator(hass, config, store=Mock())
    coordinator.state_recorder = MapStateRecorder(max_snapshots=2)

    coordinator.process_sensor_event("motion_a", True, 100.0)
    coordinator.process_sensor_event("motion_a", False, 101.0)
    coordinator.process_sensor_event("motion_b", True, 200.0)
    coordinator.process_sensor_event("motion_b", False, 201.0)
    assert not any(
        snapshot.description and "motion_a" in snapshot.description
        for snapshot in coordinator.state_recorder.get_history()
    )
    original_latches = set(coordinator.occupancy_resolver.indoor_latched)

    result = coordinator.rebuild_from_history()

    assert result is False
    assert coordinator.occupancy_resolver.indoor_latched == original_latches
    assert coordinator.get_occupancy("room_a") == 1


def test_state_difference_string_representation():
    """Test that StateDifference has useful string representation."""
    diff = StateDifference(
        snapshot_index=42,
        timestamp=100.0,
        description="Occupancy mismatch",
        area_id="room_a",
        recorded_value=1,
        replayed_value=0,
    )

    str_repr = str(diff)
    assert "42" in str_repr
    assert "Occupancy mismatch" in str_repr
    assert "room_a" in str_repr
    assert "recorded=1" in str_repr
    assert "replayed=0" in str_repr
