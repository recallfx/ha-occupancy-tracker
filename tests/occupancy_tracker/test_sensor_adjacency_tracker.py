"""Tests for SensorAdjacencyTracker."""

import time

from custom_components.occupancy_tracker.components.sensor_adjacency_tracker import (
    SensorAdjacencyTracker,
)


class TestSensorAdjacencyTracker:
    """Test SensorAdjacencyTracker class."""

    def test_create_tracker(self):
        """Test creating a sensor adjacency tracker."""
        tracker = SensorAdjacencyTracker()

        assert tracker.adjacency_map == {}
        assert tracker.motion_times == {}

    def test_set_adjacency(self):
        """Test setting adjacency for a sensor."""
        tracker = SensorAdjacencyTracker()
        adjacent_sensors = {"sensor.motion_2", "sensor.motion_3"}

        tracker.set_adjacency("sensor.motion_1", adjacent_sensors)

        assert tracker.adjacency_map["sensor.motion_1"] == adjacent_sensors

    def test_get_adjacency(self):
        """Test getting adjacency for a sensor."""
        tracker = SensorAdjacencyTracker()
        adjacent_sensors = {"sensor.motion_2", "sensor.motion_3"}

        tracker.set_adjacency("sensor.motion_1", adjacent_sensors)
        result = tracker.get_adjacency("sensor.motion_1")

        assert result == adjacent_sensors

    def test_get_adjacency_not_set(self):
        """Test getting adjacency for a sensor without adjacency set."""
        tracker = SensorAdjacencyTracker()

        result = tracker.get_adjacency("sensor.unknown")

        assert result == set()

    def test_record_motion(self):
        """Test recording motion in an area."""
        tracker = SensorAdjacencyTracker()
        timestamp = time.time()

        tracker.record_motion("living_room", timestamp)

        assert tracker.motion_times["living_room"] == timestamp

    def test_record_multiple_motions(self):
        """Test recording multiple motion events."""
        tracker = SensorAdjacencyTracker()
        t1 = time.time()
        t2 = t1 + 10

        tracker.record_motion("living_room", t1)
        tracker.record_motion("kitchen", t2)

        assert tracker.motion_times["living_room"] == t1
        assert tracker.motion_times["kitchen"] == t2

    def test_record_motion_update(self):
        """Test updating motion time for an area."""
        tracker = SensorAdjacencyTracker()
        t1 = time.time()
        t2 = t1 + 20

        tracker.record_motion("bedroom", t1)
        tracker.record_motion("bedroom", t2)

        assert tracker.motion_times["bedroom"] == t2

    def test_check_adjacent_motion_true(self):
        """Test checking for recent adjacent motion returns True."""
        tracker = SensorAdjacencyTracker()
        timestamp = time.time()

        # Set up sensor-to-area mappings
        tracker.set_sensor_area("sensor.motion_1", "hallway")
        tracker.set_sensor_area("sensor.motion_2", "living_room")
        tracker.set_sensor_area("sensor.motion_3", "kitchen")

        # Set up adjacency: sensor1 is adjacent to sensor2 and sensor3
        tracker.set_adjacency("sensor.motion_1", {"sensor.motion_2", "sensor.motion_3"})

        # Record motion in living_room (where sensor2 is)
        tracker.record_motion("living_room", timestamp)

        # Check 20 seconds later - should find recent motion
        result = tracker.check_adjacent_motion(
            "sensor.motion_1", timestamp + 20, timeframe=30
        )

        assert result is True

    def test_check_adjacent_motion_false_old(self):
        """Test checking for adjacent motion when motion is too old."""
        tracker = SensorAdjacencyTracker()
        timestamp = time.time()

        # Set up sensor-to-area mappings
        tracker.set_sensor_area("sensor.motion_1", "hallway")
        tracker.set_sensor_area("sensor.motion_2", "living_room")

        # Set up adjacency
        tracker.set_adjacency("sensor.motion_1", {"sensor.motion_2"})

        # Record motion in living_room
        tracker.record_motion("living_room", timestamp)

        # Check 60 seconds later with 30 second timeframe - too old
        result = tracker.check_adjacent_motion(
            "sensor.motion_1", timestamp + 60, timeframe=30
        )

        assert result is False

    def test_check_adjacent_motion_false_no_motion(self):
        """Test checking for adjacent motion when no motion recorded."""
        tracker = SensorAdjacencyTracker()
        timestamp = time.time()

        # Set up adjacency but don't record any motion
        tracker.set_adjacency("sensor.motion_1", {"living_room", "kitchen"})

        result = tracker.check_adjacent_motion(
            "sensor.motion_1", timestamp, timeframe=30
        )

        assert result is False

    def test_check_adjacent_motion_no_adjacency(self):
        """Test checking adjacent motion for sensor with no adjacency."""
        tracker = SensorAdjacencyTracker()
        timestamp = time.time()

        # Record motion somewhere
        tracker.record_motion("living_room", timestamp)

        # But sensor has no adjacency set
        result = tracker.check_adjacent_motion(
            "sensor.motion_unknown", timestamp + 5, timeframe=30
        )

        assert result is False

    def test_check_adjacent_motion_multiple_areas(self):
        """Test checking adjacent motion with multiple adjacent areas."""
        tracker = SensorAdjacencyTracker()
        timestamp = time.time()

        # Set up sensor-to-area mappings
        tracker.set_sensor_area("sensor.motion_hallway", "hallway")
        tracker.set_sensor_area("sensor.motion_living", "living_room")
        tracker.set_sensor_area("sensor.motion_kitchen", "kitchen")
        tracker.set_sensor_area("sensor.motion_bedroom", "bedroom")

        # Set up adjacency with multiple sensors
        tracker.set_adjacency(
            "sensor.motion_hallway",
            {"sensor.motion_living", "sensor.motion_kitchen", "sensor.motion_bedroom"},
        )

        # Record motion in kitchen only
        tracker.record_motion("kitchen", timestamp)

        # Should find motion in kitchen
        result = tracker.check_adjacent_motion(
            "sensor.motion_hallway", timestamp + 10, timeframe=30
        )

        assert result is True

    def test_check_adjacent_motion_exact_timeframe_boundary(self):
        """Test adjacent motion check at exact timeframe boundary."""
        tracker = SensorAdjacencyTracker()
        timestamp = time.time()

        tracker.set_sensor_area("sensor.motion_1", "hallway")
        tracker.set_sensor_area("sensor.motion_living", "living_room")
        tracker.set_adjacency("sensor.motion_1", {"sensor.motion_living"})
        tracker.record_motion("living_room", timestamp)

        # Check exactly at the timeframe boundary
        result = tracker.check_adjacent_motion(
            "sensor.motion_1", timestamp + 30, timeframe=30
        )

        # Should be False because timestamp - motion_time = 30, not < 30
        assert result is False

    def test_check_adjacent_motion_just_within_timeframe(self):
        """Test adjacent motion check just within timeframe."""
        tracker = SensorAdjacencyTracker()
        timestamp = time.time()

        tracker.set_sensor_area("sensor.motion_1", "hallway")
        tracker.set_sensor_area("sensor.motion_living", "living_room")
        tracker.set_adjacency("sensor.motion_1", {"sensor.motion_living"})
        tracker.record_motion("living_room", timestamp)

        # Check just before the boundary
        result = tracker.check_adjacent_motion(
            "sensor.motion_1", timestamp + 29.9, timeframe=30
        )

        assert result is True

    def test_complex_adjacency_scenario(self):
        """Test complex scenario with multiple sensors and areas."""
        tracker = SensorAdjacencyTracker()
        base_time = time.time()

        # Set up sensor-to-area mappings
        tracker.set_sensor_area("sensor.living_room", "living_room")
        tracker.set_sensor_area("sensor.hallway", "hallway")
        tracker.set_sensor_area("sensor.kitchen_1", "kitchen")
        tracker.set_sensor_area("sensor.kitchen_2", "kitchen")
        tracker.set_sensor_area("sensor.dining", "dining_room")
        tracker.set_sensor_area("sensor.bedroom", "bedroom")
        tracker.set_sensor_area("sensor.bathroom", "bathroom")

        # Set up multiple sensors with different adjacencies
        tracker.set_adjacency(
            "sensor.living_room", {"sensor.hallway", "sensor.kitchen_1"}
        )
        tracker.set_adjacency(
            "sensor.kitchen_2", {"sensor.living_room", "sensor.dining"}
        )
        tracker.set_adjacency("sensor.bedroom", {"sensor.hallway", "sensor.bathroom"})

        # Record motion in various areas at different times
        tracker.record_motion("hallway", base_time)
        tracker.record_motion("kitchen", base_time + 10)
        tracker.record_motion("bathroom", base_time + 50)

        # Check living_room sensor - should find motion in hallway and kitchen
        assert (
            tracker.check_adjacent_motion(
                "sensor.living_room", base_time + 15, timeframe=30
            )
            is True
        )

        # Check bedroom sensor - hallway is too old, but bathroom is recent
        assert (
            tracker.check_adjacent_motion(
                "sensor.bedroom", base_time + 60, timeframe=30
            )
            is True
        )

        # Check kitchen sensor - should not find recent motion in dining_room
        assert (
            tracker.check_adjacent_motion(
                "sensor.kitchen_2", base_time + 100, timeframe=30
            )
            is False
        )
