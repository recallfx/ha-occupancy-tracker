"""Tests for SensorState."""

import time
from custom_components.occupancy_tracker.helpers.sensor_state import SensorState


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
        assert sensor.last_changed == 0  # Only set by real state changes
        assert sensor.last_update_time == timestamp
        assert sensor.last_source_timestamp == 0
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
        assert sensor.last_changed == 0  # Never changed from init

    def test_mark_unavailable_does_not_create_false_edge(self):
        """Availability loss invalidates state without changing event history."""
        timestamp = time.time()
        sensor = SensorState("sensor.motion_1", {}, timestamp)
        sensor.update_state(True, timestamp + 1)
        last_changed = sensor.last_changed
        activated_at = sensor.activated_at
        history = list(sensor.history)

        sensor.mark_unavailable(timestamp + 2)

        assert sensor.current_state is True
        assert sensor.activated_at == activated_at
        assert sensor.is_available is False
        assert sensor.is_reliable is True
        assert sensor.is_trusted_active is False
        assert sensor.last_changed == last_changed
        assert sensor.history == history

    def test_valid_unchanged_state_restores_availability(self):
        """The first valid state after an outage restores availability."""
        timestamp = time.time()
        sensor = SensorState("sensor.motion_1", {}, timestamp)
        sensor.mark_unavailable(timestamp + 1)

        changed = sensor.update_state(False, timestamp + 2)

        assert changed is False
        assert sensor.current_state is False
        assert sensor.is_available is True
        assert sensor.is_reliable is True

    def test_repeated_state_does_not_restore_stuck_sensor(self):
        """Availability recovery must not re-trust an unchanged stuck sensor."""
        timestamp = time.time()
        sensor = SensorState("sensor.motion_1", {}, timestamp)
        sensor.update_state(True, timestamp + 1)
        sensor.is_stuck = True
        sensor.is_reliable = False

        changed = sensor.update_state(True, timestamp + 2)

        assert changed is False
        assert sensor.is_stuck is True
        assert sensor.is_reliable is False

    def test_state_transitions(self):
        """Test multiple state transitions."""
        sensor = SensorState("sensor.door_1", {}, time.time())
        t1 = time.time()

        sensor.update_state(True, t1)
        sensor.update_state(False, t1 + 5)
        sensor.update_state(True, t1 + 10)

        assert sensor.current_state is True
        assert len(sensor.history) == 3

    def test_reset_clears_all_runtime_timestamps_and_history(self):
        """A full reset must not leave stale transition metadata behind."""
        timestamp = time.time()
        sensor = SensorState("sensor.motion_1", {}, timestamp)
        sensor.update_state(True, timestamp + 1)
        sensor.mark_unavailable(timestamp + 2)

        sensor.reset()

        assert sensor.current_state is False
        assert sensor.last_changed == 0
        assert sensor.activated_at is None
        assert sensor.last_update_time == 0
        assert sensor.last_source_timestamp == 0
        assert sensor.history == []
        assert sensor.is_available is True

    def test_history_max_length(self):
        """Test that sensor history maintains max length."""
        from custom_components.occupancy_tracker.helpers.constants import (
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
        is_stuck = sensor.calculate_is_stuck(timestamp + 25 * 3600)

        assert is_stuck is True
        assert sensor.is_stuck is True

    def test_calculate_is_stuck_with_adjacent_motion(self):
        """Test detecting stuck sensor when adjacent area has motion."""
        config = {"type": "motion", "area": "bedroom"}
        sensor = SensorState("sensor.motion_1", config, time.time())
        timestamp = time.time()

        sensor.update_state(False, timestamp)

        # Check with recent adjacent motion but sensor hasn't changed for 60 seconds
        # This logic was removed as it caused false positives
        is_stuck = sensor.calculate_is_stuck(timestamp=timestamp + 60)

        assert is_stuck is False
        assert sensor.is_stuck is False

    def test_calculate_is_stuck_not_stuck(self):
        """Test sensor that is not stuck."""
        sensor = SensorState("sensor.motion_1", {"type": "motion"}, time.time())
        timestamp = time.time()

        sensor.update_state(True, timestamp)

        # Check only 1 hour later
        is_stuck = sensor.calculate_is_stuck(timestamp + 3600)

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
