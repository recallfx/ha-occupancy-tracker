import unittest
import time
from unittest.mock import MagicMock

from custom_components.occupancy_tracker.components.anomaly_detector import (
    AnomalyDetector,
)
from custom_components.occupancy_tracker.components.area_state import AreaState
from custom_components.occupancy_tracker.components.sensor_state import SensorState
from custom_components.occupancy_tracker.components.warning import Warning


class TestAnomalyDetector(unittest.TestCase):
    """Unit tests for AnomalyDetector class."""

    def setUp(self):
        """Set up test fixtures."""
        self.current_time = time.time()
        self.config = {
            "adjacency": {
                "living_room": ["hallway", "kitchen"],
                "hallway": ["living_room", "bedroom"],
                "kitchen": ["living_room"],
                "bedroom": ["hallway"],
            }
        }
        self.anomaly_detector = AnomalyDetector(self.config)

        # Setup mock areas
        self.areas = {
            "living_room": MagicMock(spec=AreaState),
            "hallway": MagicMock(spec=AreaState),
            "kitchen": MagicMock(spec=AreaState),
            "bedroom": MagicMock(spec=AreaState),
        }

        for area_id, area in self.areas.items():
            area.id = area_id
            area.occupancy = 0
            area.has_recent_motion = MagicMock(return_value=False)
            area.record_exit = MagicMock()
            area.is_exit_capable = False
            area.get_inactivity_duration = MagicMock(return_value=0)

        # Setup mock sensors
        self.sensors = {
            "sensor.living_room_motion": MagicMock(spec=SensorState),
            "sensor.hallway_motion": MagicMock(spec=SensorState),
            "sensor.kitchen_motion": MagicMock(spec=SensorState),
            "sensor.bedroom_motion": MagicMock(spec=SensorState),
        }

        for sensor_id, sensor in self.sensors.items():
            sensor.id = sensor_id
            sensor.config = {"area": sensor_id.split(".")[1].replace("_motion", "")}
            sensor.is_stuck = MagicMock(return_value=False)
            sensor.is_reliable = True
            sensor.record_adjacent_motion = MagicMock()
            sensor.last_update_time = self.current_time  # Add missing attribute

    def test_init(self):
        """Test proper initialization of AnomalyDetector."""
        self.assertEqual(self.anomaly_detector.config, self.config)
        self.assertEqual(len(self.anomaly_detector.warnings), 0)
        self.assertEqual(self.anomaly_detector.recent_motion_window, 120)
        self.assertEqual(self.anomaly_detector.motion_timeout, 24 * 3600)
        self.assertEqual(self.anomaly_detector.extended_occupancy_threshold, 12 * 3600)

    def test_check_for_stuck_sensors(self):
        """Test checking for stuck sensors."""
        # Set up a stuck sensor
        self.sensors["sensor.living_room_motion"].is_stuck.return_value = True

        # Call method
        self.anomaly_detector.check_for_stuck_sensors(
            self.sensors, self.areas, "sensor.hallway_motion"
        )

        # Verify adjacent motion was recorded
        self.sensors[
            "sensor.living_room_motion"
        ].record_adjacent_motion.assert_called_once_with("hallway")

        # Verify stuck sensor was marked as unreliable and a warning was created
        self.assertFalse(self.sensors["sensor.living_room_motion"].is_reliable)
        self.assertEqual(len(self.anomaly_detector.warnings), 1)
        self.assertEqual(self.anomaly_detector.warnings[0].type, "stuck_sensor")
        self.assertEqual(self.anomaly_detector.warnings[0].area, "living_room")

    def test_handle_unexpected_motion_from_adjacent(self):
        """Test handling motion with valid movement from adjacent area."""
        # Set up an occupied adjacent area with recent motion
        self.areas["living_room"].occupancy = 1
        self.areas["living_room"].has_recent_motion.return_value = True

        # Call method
        result = self.anomaly_detector.handle_unexpected_motion(
            self.areas["hallway"],
            self.areas,
            self.sensors,
            self.current_time,
            MagicMock(),
        )

        # Verify result and that exit was recorded
        self.assertTrue(result)  # Valid entry
        self.areas["living_room"].record_exit.assert_called_once()
        self.assertEqual(len(self.anomaly_detector.warnings), 0)  # No warnings

    def test_handle_unexpected_motion_via_adjacency_tracker(self):
        """Test handling motion with valid movement via adjacency tracker."""
        # Set up mock adjacency tracker
        adjacency_tracker = MagicMock()
        adjacency_tracker.check_adjacent_motion.return_value = True

        # Call method
        result = self.anomaly_detector.handle_unexpected_motion(
            self.areas["hallway"],
            self.areas,
            self.sensors,
            self.current_time,
            adjacency_tracker,
        )

        # Verify result
        self.assertTrue(result)  # Valid entry
        self.assertEqual(len(self.anomaly_detector.warnings), 0)  # No warnings

    def test_handle_unexpected_motion_exit_capable(self):
        """Test handling motion in an exit-capable area."""
        # Mark area as exit capable
        self.areas["hallway"].is_exit_capable = True

        # Call method
        result = self.anomaly_detector.handle_unexpected_motion(
            self.areas["hallway"],
            self.areas,
            self.sensors,
            self.current_time,
            MagicMock(),
        )

        # Verify result - exit capable areas are allowed to have unexpected motion
        self.assertTrue(result)  # Exit capable areas return True
        self.assertEqual(len(self.anomaly_detector.warnings), 0)  # No warnings

    def test_handle_unexpected_motion_true_anomaly(self):
        """Test handling motion that is a genuine anomaly."""
        # Mock adjacency tracker to return False (no recent adjacent motion)
        adjacency_tracker = MagicMock()
        adjacency_tracker.check_adjacent_motion.return_value = False

        # Make sure no areas are occupied with recent motion
        for area in self.areas.values():
            area.occupancy = 0
            area.has_recent_motion.return_value = False

        # Call method with non-exit capable area
        result = self.anomaly_detector.handle_unexpected_motion(
            self.areas["bedroom"],
            self.areas,
            self.sensors,
            self.current_time,
            adjacency_tracker,
        )

        # Verify result and warning
        self.assertFalse(result)  # Not a valid entry
        self.assertEqual(len(self.anomaly_detector.warnings), 1)  # Warning generated
        self.assertEqual(self.anomaly_detector.warnings[0].type, "unexpected_motion")
        self.assertEqual(self.anomaly_detector.warnings[0].area, "bedroom")

    def test_check_simultaneous_motion_no_motion(self):
        """Test checking for simultaneous motion when there is none."""
        # Call method
        self.anomaly_detector.check_simultaneous_motion(
            "living_room", self.areas, self.current_time
        )

        # Verify no warnings
        self.assertEqual(len(self.anomaly_detector.warnings), 0)

    def test_check_simultaneous_motion_adjacent_areas(self):
        """Test checking for simultaneous motion in adjacent areas (no warning)."""
        # Set up recent motion in adjacent area
        self.areas["hallway"].has_recent_motion.return_value = True

        # Call method
        self.anomaly_detector.check_simultaneous_motion(
            "living_room", self.areas, self.current_time
        )

        # Verify no warnings
        self.assertEqual(len(self.anomaly_detector.warnings), 0)

    def test_check_simultaneous_motion_non_adjacent_areas(self):
        """Test checking for simultaneous motion in non-adjacent areas (warning)."""
        # Set up recent motion in non-adjacent area
        self.areas["bedroom"].has_recent_motion.return_value = True

        # Call method
        self.anomaly_detector.check_simultaneous_motion(
            "kitchen", self.areas, self.current_time
        )

        # Verify warning
        self.assertEqual(len(self.anomaly_detector.warnings), 1)
        self.assertEqual(self.anomaly_detector.warnings[0].type, "simultaneous_motion")

    def test_check_timeouts_inactivity(self):
        """Test checking for inactivity timeouts."""
        # Set up occupied area with long inactivity
        self.areas["bedroom"].occupancy = 1
        self.areas["bedroom"].get_inactivity_duration.return_value = (
            25 * 3600
        )  # 25 hours

        # Call method
        self.anomaly_detector.check_timeouts(self.areas, self.current_time)

        # Verify area was reset and warning created
        self.assertEqual(self.areas["bedroom"].occupancy, 0)
        self.assertEqual(len(self.anomaly_detector.warnings), 1)
        self.assertEqual(self.anomaly_detector.warnings[0].type, "inactivity_timeout")
        self.assertEqual(self.anomaly_detector.warnings[0].area, "bedroom")

    def test_check_timeouts_extended_occupancy(self):
        """Test checking for extended occupancy."""
        # Set up occupied area with medium-long inactivity
        self.areas["bedroom"].occupancy = 1
        self.areas["bedroom"].get_inactivity_duration.return_value = (
            13 * 3600
        )  # 13 hours

        # Call method
        self.anomaly_detector.check_timeouts(self.areas, self.current_time)

        # Verify warning created but area not reset
        self.assertEqual(self.areas["bedroom"].occupancy, 1)  # Still occupied
        self.assertEqual(len(self.anomaly_detector.warnings), 1)
        self.assertEqual(self.anomaly_detector.warnings[0].type, "extended_occupancy")
        self.assertEqual(self.anomaly_detector.warnings[0].area, "bedroom")

    def test_check_timeouts_no_warning_for_duplicate(self):
        """Test no duplicate warnings for extended occupancy."""
        # Set up occupied area with medium-long inactivity
        self.areas["bedroom"].occupancy = 1
        self.areas["bedroom"].get_inactivity_duration.return_value = (
            13 * 3600
        )  # 13 hours

        # Create existing warning
        existing_warning = Warning(
            "extended_occupancy", "test", "bedroom", None, self.current_time
        )
        self.anomaly_detector.warnings.append(existing_warning)

        # Call method
        self.anomaly_detector.check_timeouts(self.areas, self.current_time)

        # Verify no new warning created
        self.assertEqual(len(self.anomaly_detector.warnings), 1)

    def test_get_warnings_all(self):
        """Test getting all warnings."""
        # Create warnings
        warning1 = Warning("test1", "msg1", "area1", None, self.current_time)
        warning2 = Warning("test2", "msg2", "area2", None, self.current_time)
        warning2.resolve()  # Mark as inactive

        self.anomaly_detector.warnings = [warning1, warning2]

        # Get all warnings
        all_warnings = self.anomaly_detector.get_warnings(active_only=False)

        # Verify both warnings returned
        self.assertEqual(len(all_warnings), 2)
        self.assertIn(warning1, all_warnings)
        self.assertIn(warning2, all_warnings)

    def test_get_warnings_active_only(self):
        """Test getting active warnings only."""
        # Create warnings
        warning1 = Warning("test1", "msg1", "area1", None, self.current_time)
        warning2 = Warning("test2", "msg2", "area2", None, self.current_time)
        warning2.resolve()  # Mark as inactive

        self.anomaly_detector.warnings = [warning1, warning2]

        # Get active warnings
        active_warnings = self.anomaly_detector.get_warnings()

        # Verify only active warning returned
        self.assertEqual(len(active_warnings), 1)
        self.assertIn(warning1, active_warnings)
        self.assertNotIn(warning2, active_warnings)

    def test_resolve_warning(self):
        """Test resolving a warning."""
        # Create warning
        warning = Warning("test", "msg", "area", None, self.current_time)
        self.anomaly_detector.warnings.append(warning)
        warning_id = warning.id

        # Resolve warning
        result = self.anomaly_detector.resolve_warning(warning_id)

        # Verify warning was resolved
        self.assertTrue(result)
        self.assertFalse(warning.is_active)

    def test_resolve_warning_nonexistent(self):
        """Test resolving a nonexistent warning."""
        # Attempt to resolve nonexistent warning
        result = self.anomaly_detector.resolve_warning("fake_id")

        # Verify operation failed
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
