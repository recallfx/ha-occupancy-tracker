import unittest
from unittest.mock import patch, MagicMock
import time

from custom_components.occupancy_tracker.occupancy_tracker import OccupancyTracker


class TestOccupancyTracker(unittest.TestCase):
    def setUp(self):
        # Sample configuration for testing
        self.config = {
            "areas": {
                "living_room": {"name": "Living Room", "is_indoors": True},
                "kitchen": {"name": "Kitchen", "is_indoors": True},
                "hallway": {"name": "Hallway", "is_indoors": True},
            },
            "adjacency": {
                "living_room": ["kitchen", "hallway"],
                "kitchen": ["living_room", "hallway"],
                "hallway": ["living_room", "kitchen"],
            },
            "sensors": {
                "motion_sensor_1": {"type": "motion", "area": "living_room"},
                "motion_sensor_2": {"type": "motion", "area": "kitchen"},
                "door_sensor_1": {
                    "type": "magnetic",
                    "between_areas": ["living_room", "hallway"],
                },
            },
        }

        # Create instance to test
        self.tracker = OccupancyTracker(self.config)

    def test_process_unknown_sensor_event(self):
        """Test handling of unknown sensor ID"""
        # Mock the logger to check for warnings
        with patch(
            "custom_components.occupancy_tracker.occupancy_tracker.logger"
        ) as mock_logger:
            # Process event for non-existent sensor
            self.tracker.process_sensor_event("unknown_sensor", True, time.time())
            # Verify warning was logged
            mock_logger.warning.assert_called_once()

    def test_process_motion_sensor_event(self):
        """Test processing motion sensor events"""
        current_time = time.time()

        # Mock internal methods to verify they're called
        self.tracker._process_motion_event = MagicMock()
        self.tracker._check_for_stuck_sensors = MagicMock()

        # Process ON event for motion sensor
        self.tracker.process_sensor_event("motion_sensor_1", True, current_time)

        # Verify internal methods were called
        self.tracker._process_motion_event.assert_called_once_with(
            "motion_sensor_1", current_time
        )
        self.tracker._check_for_stuck_sensors.assert_called_once()

        # Verify sensor state was updated
        self.assertTrue(self.tracker.sensors["motion_sensor_1"].current_state)

    def test_process_magnetic_sensor_event(self):
        """Test processing magnetic sensor events"""
        current_time = time.time()

        # Mock internal methods to verify they're called
        self.tracker._process_magnetic_event = MagicMock()
        self.tracker._check_for_stuck_sensors = MagicMock()

        # Process ON event for magnetic sensor
        self.tracker.process_sensor_event("door_sensor_1", True, current_time)

        # Verify internal methods were called
        self.tracker._process_magnetic_event.assert_called_once_with(
            "door_sensor_1", True, current_time
        )
        self.tracker._check_for_stuck_sensors.assert_called_once()

        # Verify sensor state was updated
        self.assertTrue(self.tracker.sensors["door_sensor_1"].current_state)

    def test_repeated_motion_event(self):
        """Test handling of repeated motion events (same state)"""
        current_time = time.time()

        # Set initial state to ON
        self.tracker.sensors["motion_sensor_1"].current_state = True

        # Mock internal methods
        self.tracker._process_repeated_motion = MagicMock()

        # Process repeated ON event
        self.tracker.process_sensor_event("motion_sensor_1", True, current_time)

        # Verify _process_repeated_motion was called
        self.tracker._process_repeated_motion.assert_called_once_with(
            "motion_sensor_1", current_time
        )


if __name__ == "__main__":
    unittest.main()
