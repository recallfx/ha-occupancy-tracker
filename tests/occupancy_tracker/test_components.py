"""Tests for component models (AreaState, SensorState, Warning, SensorHistoryItem)."""

import pytest
import time

from custom_components.occupancy_tracker.components.area_state import AreaState
from custom_components.occupancy_tracker.components.sensor_state import SensorState
from custom_components.occupancy_tracker.components.warning import Warning
from custom_components.occupancy_tracker.components.sensor_history_item import (
    SensorHistoryItem,
)


class TestSensorHistoryItem:
    """Test SensorHistoryItem class."""

    def test_create_history_item(self):
        """Test creating a sensor history item."""
        timestamp = time.time()
        item = SensorHistoryItem(state=True, timestamp=timestamp)

        assert item.state is True
        assert item.timestamp == timestamp

    def test_create_false_state(self):
        """Test creating a history item with False state."""
        timestamp = time.time()
        item = SensorHistoryItem(state=False, timestamp=timestamp)

        assert item.state is False
        assert item.timestamp == timestamp


class TestWarning:
    """Test Warning class."""

    def test_create_warning(self):
        """Test creating a basic warning."""
        timestamp = time.time()
        warning = Warning(
            warning_type="test_warning",
            message="Test message",
            area="living_room",
            sensor_id="sensor.motion_1",
            timestamp=timestamp,
        )

        assert warning.type == "test_warning"
        assert warning.message == "Test message"
        assert warning.area == "living_room"
        assert warning.sensor_id == "sensor.motion_1"
        assert warning.timestamp == timestamp
        assert warning.is_active is True
        assert "test_warning" in warning.id
        assert "living_room" in warning.id
        assert "sensor.motion_1" in warning.id

    def test_warning_without_area_or_sensor(self):
        """Test creating a warning without area or sensor."""
        timestamp = time.time()
        warning = Warning(
            warning_type="general_warning",
            message="General message",
            area=None,
            sensor_id=None,
            timestamp=timestamp,
        )

        assert warning.area is None
        assert warning.sensor_id is None
        assert warning.is_active is True

    def test_resolve_warning(self):
        """Test resolving a warning."""
        warning = Warning(
            warning_type="test",
            message="Test",
            area=None,
            sensor_id=None,
            timestamp=time.time(),
        )

        assert warning.is_active is True
        warning.resolve()
        assert warning.is_active is False

    def test_warning_string_representation(self):
        """Test warning string representation."""
        warning = Warning(
            warning_type="stuck_sensor",
            message="Sensor appears stuck",
            area="bedroom",
            sensor_id="sensor.motion_2",
            timestamp=time.time(),
        )

        str_repr = str(warning)
        assert "Warning[stuck_sensor]" in str_repr
        assert "Sensor appears stuck" in str_repr


class TestAreaState:
    """Test AreaState class."""

    def test_create_area_state(self):
        """Test creating an area state."""
        config = {
            "name": "Living Room",
            "indoors": True,
            "exit_capable": False,
        }
        area = AreaState("living_room", config)

        assert area.id == "living_room"
        assert area.config == config
        assert area.occupancy == 0
        assert area.last_motion == 0
        assert area.activity_history == []
        assert area.is_indoors is True
        assert area.is_exit_capable is False

    def test_area_defaults(self):
        """Test area state with default values."""
        area = AreaState("bedroom", {})

        assert area.is_indoors is True  # Default
        assert area.is_exit_capable is False  # Default

    def test_outdoor_exit_capable_area(self):
        """Test outdoor exit-capable area."""
        config = {"indoors": False, "exit_capable": True}
        area = AreaState("front_porch", config)

        assert area.is_indoors is False
        assert area.is_exit_capable is True

    def test_record_motion(self):
        """Test recording motion in an area."""
        area = AreaState("kitchen", {})
        timestamp = time.time()

        area.record_motion(timestamp)

        assert area.last_motion == timestamp
        assert len(area.activity_history) == 1
        assert area.activity_history[0] == (timestamp, "motion")

    def test_record_multiple_motions(self):
        """Test recording multiple motion events."""
        area = AreaState("kitchen", {})
        timestamp1 = time.time()
        timestamp2 = timestamp1 + 10

        area.record_motion(timestamp1)
        area.record_motion(timestamp2)

        assert area.last_motion == timestamp2
        assert len(area.activity_history) == 2

    def test_record_entry(self):
        """Test recording entry into an area."""
        area = AreaState("bathroom", {})
        timestamp = time.time()

        area.record_entry(timestamp)

        assert area.occupancy == 1
        assert len(area.activity_history) == 1
        assert area.activity_history[0] == (timestamp, "entry")

    def test_multiple_entries(self):
        """Test multiple entries increase occupancy."""
        area = AreaState("office", {})
        timestamp = time.time()

        area.record_entry(timestamp)
        area.record_entry(timestamp + 5)

        assert area.occupancy == 2

    def test_record_exit(self):
        """Test recording exit from an area."""
        area = AreaState("garage", {})
        timestamp = time.time()

        area.record_entry(timestamp)
        result = area.record_exit(timestamp + 10)

        assert result is True
        assert area.occupancy == 0
        assert len(area.activity_history) == 2

    def test_exit_without_occupancy(self):
        """Test exit when area is unoccupied."""
        area = AreaState("hallway", {})
        timestamp = time.time()

        result = area.record_exit(timestamp)

        assert result is False
        assert area.occupancy == 0

    def test_get_inactivity_duration(self):
        """Test calculating inactivity duration."""
        area = AreaState("bedroom", {})
        start_time = time.time()

        area.record_motion(start_time)

        # Check inactivity after 60 seconds
        current_time = start_time + 60
        duration = area.get_inactivity_duration(current_time)

        assert duration == 60

    def test_has_recent_motion_true(self):
        """Test has_recent_motion returns True when motion is recent."""
        area = AreaState("kitchen", {})
        timestamp = time.time()

        area.record_motion(timestamp)

        # Check 30 seconds later
        assert area.has_recent_motion(timestamp + 30, within_seconds=120) is True

    def test_has_recent_motion_false(self):
        """Test has_recent_motion returns False when motion is old."""
        area = AreaState("kitchen", {})
        timestamp = time.time()

        area.record_motion(timestamp)

        # Check 150 seconds later (outside default 120 second window)
        assert area.has_recent_motion(timestamp + 150, within_seconds=120) is False

    def test_has_recent_motion_no_motion(self):
        """Test has_recent_motion when no motion recorded."""
        area = AreaState("basement", {})
        timestamp = time.time()

        assert area.has_recent_motion(timestamp) is False

    def test_activity_history_max_length(self):
        """Test that activity history maintains max length."""
        from custom_components.occupancy_tracker.components.constants import (
            MAX_HISTORY_LENGTH,
        )

        area = AreaState("test_area", {})
        timestamp = time.time()

        # Record more events than max length
        for i in range(MAX_HISTORY_LENGTH + 50):
            area.record_motion(timestamp + i)

        assert len(area.activity_history) == MAX_HISTORY_LENGTH


class TestSensorState:
    """Test SensorState class."""

    def test_create_sensor_state(self):
        """Test creating a sensor state."""
        config = {"area": "living_room", "type": "motion"}
        timestamp = time.time()

        sensor = SensorState("sensor.motion_1", config, timestamp)

        assert sensor.id == "sensor.motion_1"
        assert sensor.config == config
        assert sensor.current_state is False
        assert sensor.last_changed == timestamp
        assert sensor.history == []
        assert sensor.is_reliable is True
        assert sensor.is_stuck is False

    def test_update_state_change(self):
        """Test updating sensor state when state changes."""
        sensor = SensorState("sensor.motion_1", {}, time.time())
        timestamp = time.time()

        result = sensor.update_state(True, timestamp)

        assert result is True
        assert sensor.current_state is True
        assert sensor.last_changed == timestamp
        assert len(sensor.history) == 1
        assert sensor.history[0].state is True

    def test_update_state_no_change(self):
        """Test updating sensor when state doesn't change."""
        timestamp = time.time()
        sensor = SensorState("sensor.motion_1", {}, timestamp)

        # State starts as False, update to False again
        result = sensor.update_state(False, timestamp + 10)

        assert result is False
        assert sensor.current_state is False
        assert sensor.last_changed == timestamp  # Unchanged

    def test_state_transitions(self):
        """Test multiple state transitions."""
        sensor = SensorState("sensor.door_1", {}, time.time())
        t1 = time.time()

        sensor.update_state(True, t1)
        sensor.update_state(False, t1 + 5)
        sensor.update_state(True, t1 + 10)

        assert sensor.current_state is True
        assert len(sensor.history) == 3

    def test_history_max_length(self):
        """Test that sensor history maintains max length."""
        from custom_components.occupancy_tracker.components.constants import (
            MAX_HISTORY_LENGTH,
        )

        sensor = SensorState("sensor.test", {}, time.time())
        timestamp = time.time()

        # Add more history items than max
        for i in range(MAX_HISTORY_LENGTH + 50):
            sensor.update_state(i % 2 == 0, timestamp + i)

        assert len(sensor.history) == MAX_HISTORY_LENGTH

    def test_calculate_is_stuck_long_on_state(self):
        """Test detecting sensor stuck in ON state for 24 hours."""
        sensor = SensorState("sensor.motion_stuck", {"type": "motion"}, time.time())
        timestamp = time.time()

        sensor.update_state(True, timestamp)

        # Check 25 hours later
        is_stuck = sensor.calculate_is_stuck(False, timestamp + 25 * 3600)

        assert is_stuck is True
        assert sensor.is_stuck is True

    def test_calculate_is_stuck_with_adjacent_motion(self):
        """Test detecting stuck sensor when adjacent area has motion."""
        config = {"type": "motion", "area": "bedroom"}
        sensor = SensorState("sensor.motion_1", config, time.time())
        timestamp = time.time()

        sensor.update_state(False, timestamp)

        # Check with recent adjacent motion but sensor hasn't changed for 60 seconds
        is_stuck = sensor.calculate_is_stuck(
            has_recent_adjacent_motion=True, timestamp=timestamp + 60
        )

        assert is_stuck is True
        assert sensor.is_stuck is True

    def test_calculate_is_stuck_not_stuck(self):
        """Test sensor that is not stuck."""
        sensor = SensorState("sensor.motion_1", {"type": "motion"}, time.time())
        timestamp = time.time()

        sensor.update_state(True, timestamp)

        # Check only 1 hour later
        is_stuck = sensor.calculate_is_stuck(False, timestamp + 3600)

        assert is_stuck is False
        assert sensor.is_stuck is False

    def test_magnetic_sensor_type(self):
        """Test creating a magnetic sensor."""
        config = {"type": "magnetic", "between_areas": ["hallway", "bedroom"]}
        sensor = SensorState("sensor.door_bedroom", config, time.time())

        assert sensor.config["type"] == "magnetic"
        assert len(sensor.config["between_areas"]) == 2

    def test_camera_person_sensor_type(self):
        """Test creating a camera person sensor."""
        config = {"type": "camera_person", "area": "front_porch"}
        sensor = SensorState("sensor.camera_person", config, time.time())

        assert sensor.config["type"] == "camera_person"
