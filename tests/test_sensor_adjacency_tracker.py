import unittest
import time
from custom_components.occupancy_tracker.components.sensor_adjacency_tracker import (
    SensorAdjacencyTracker,
)


class TestSensorAdjacencyTracker(unittest.TestCase):
    """Unit tests for SensorAdjacencyTracker class."""

    def setUp(self):
        """Set up test fixtures."""
        self.tracker = SensorAdjacencyTracker()
        self.current_time = time.time()

    def test_init(self):
        """Test proper initialization of SensorAdjacencyTracker."""
        self.assertEqual(len(self.tracker.adjacency_map), 0)
        self.assertEqual(len(self.tracker.motion_times), 0)

    def test_set_adjacency(self):
        """Test setting adjacency relationships between sensors."""
        sensor_id = "motion.living_room"
        adjacent_sensors = {"motion.hallway", "motion.kitchen"}

        self.tracker.set_adjacency(sensor_id, adjacent_sensors)

        self.assertIn(sensor_id, self.tracker.adjacency_map)
        self.assertEqual(self.tracker.adjacency_map[sensor_id], adjacent_sensors)

        # Test overwriting existing adjacency
        new_adjacent_sensors = {"motion.hallway", "motion.dining_room"}
        self.tracker.set_adjacency(sensor_id, new_adjacent_sensors)
        self.assertEqual(self.tracker.adjacency_map[sensor_id], new_adjacent_sensors)

    def test_record_motion(self):
        """Test recording motion events."""
        area_id = "living_room"
        timestamp = self.current_time

        self.tracker.record_motion(area_id, timestamp)

        self.assertIn(area_id, self.tracker.motion_times)
        self.assertEqual(self.tracker.motion_times[area_id], timestamp)

        # Test updating existing motion time
        new_timestamp = self.current_time + 10
        self.tracker.record_motion(area_id, new_timestamp)
        self.assertEqual(self.tracker.motion_times[area_id], new_timestamp)

    def test_check_adjacent_motion_with_recent_motion(self):
        """Test checking for recent motion in adjacent areas (positive case)."""
        # Set up adjacency relationships
        sensor_id = "motion.living_room"
        adjacent_sensors = {"motion.hallway", "motion.kitchen"}
        self.tracker.set_adjacency(sensor_id, adjacent_sensors)

        # Record recent motion in an adjacent area
        recent_timestamp = self.current_time - 10  # 10 seconds ago
        self.tracker.record_motion("motion.hallway", recent_timestamp)

        # Check for adjacent motion with default timeframe (30 seconds)
        result = self.tracker.check_adjacent_motion(sensor_id, self.current_time)
        self.assertTrue(result)

    def test_check_adjacent_motion_with_old_motion(self):
        """Test checking for motion in adjacent areas that is too old."""
        # Set up adjacency relationships
        sensor_id = "motion.living_room"
        adjacent_sensors = {"motion.hallway", "motion.kitchen"}
        self.tracker.set_adjacency(sensor_id, adjacent_sensors)

        # Record old motion in an adjacent area
        old_timestamp = self.current_time - 40  # 40 seconds ago
        self.tracker.record_motion("motion.hallway", old_timestamp)

        # Check for adjacent motion with default timeframe (30 seconds)
        result = self.tracker.check_adjacent_motion(sensor_id, self.current_time)
        self.assertFalse(result)

    def test_check_adjacent_motion_custom_timeframe(self):
        """Test checking for motion in adjacent areas with custom timeframe."""
        # Set up adjacency relationships
        sensor_id = "motion.living_room"
        adjacent_sensors = {"motion.hallway", "motion.kitchen"}
        self.tracker.set_adjacency(sensor_id, adjacent_sensors)

        # Record motion in an adjacent area
        timestamp = self.current_time - 45  # 45 seconds ago
        self.tracker.record_motion("motion.hallway", timestamp)

        # Check with default timeframe (30 seconds) - should be false
        result = self.tracker.check_adjacent_motion(sensor_id, self.current_time)
        self.assertFalse(result)

        # Check with custom timeframe of 60 seconds - should be true
        result = self.tracker.check_adjacent_motion(
            sensor_id, self.current_time, timeframe=60
        )
        self.assertTrue(result)

    def test_check_adjacent_motion_unknown_sensor(self):
        """Test checking for motion with an unknown sensor ID."""
        unknown_sensor_id = "motion.unknown"

        # Check for adjacent motion for an unknown sensor
        result = self.tracker.check_adjacent_motion(
            unknown_sensor_id, self.current_time
        )
        self.assertFalse(result)

    def test_check_adjacent_motion_no_motion_recorded(self):
        """Test checking for motion when no motion has been recorded."""
        # Set up adjacency relationships
        sensor_id = "motion.living_room"
        adjacent_sensors = {"motion.hallway", "motion.kitchen"}
        self.tracker.set_adjacency(sensor_id, adjacent_sensors)

        # Check for adjacent motion without recording any motion
        result = self.tracker.check_adjacent_motion(sensor_id, self.current_time)
        self.assertFalse(result)

    def test_check_adjacent_motion_multiple_areas(self):
        """Test checking for motion with multiple adjacent areas."""
        # Set up adjacency relationships
        sensor_id = "motion.living_room"
        adjacent_sensors = {"motion.hallway", "motion.kitchen", "motion.dining_room"}
        self.tracker.set_adjacency(sensor_id, adjacent_sensors)

        # Record motion in multiple adjacent areas
        self.tracker.record_motion("motion.hallway", self.current_time - 40)  # too old
        self.tracker.record_motion(
            "motion.kitchen", self.current_time - 10
        )  # recent enough

        # Check for adjacent motion
        result = self.tracker.check_adjacent_motion(sensor_id, self.current_time)
        self.assertTrue(result)


if __name__ == "__main__":
    unittest.main()
