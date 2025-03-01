import unittest
from custom_components.occupancy_tracker.components.area_state import AreaState
from custom_components.occupancy_tracker.components.constants import MAX_HISTORY_LENGTH


class TestAreaState(unittest.TestCase):
    """Unit tests for AreaState class."""

    def setUp(self):
        """Set up test environment before each test."""
        self.basic_config = {"name": "Test Area", "indoors": True}
        self.outdoor_config = {
            "name": "Outdoor Area",
            "indoors": False,
            "exit_capable": True,
        }
        self.area = AreaState("test_area", self.basic_config)
        self.current_time = 1000.0  # Base timestamp for tests

    def test_initialization(self):
        """Test that the AreaState is correctly initialized."""
        # Test basic initialization
        self.assertEqual(self.area.id, "test_area")
        self.assertEqual(self.area.config, self.basic_config)
        self.assertEqual(self.area.occupancy, 0)
        self.assertEqual(self.area.last_motion, 0)
        self.assertEqual(self.area.activity_history, [])
        self.assertTrue(self.area.is_indoors)
        self.assertFalse(self.area.is_exit_capable)

        # Test initialization with different config
        outdoor_area = AreaState("outdoor", self.outdoor_config)
        self.assertFalse(outdoor_area.is_indoors)
        self.assertTrue(outdoor_area.is_exit_capable)

    def test_record_motion(self):
        """Test recording motion in an area."""
        self.area.record_motion(self.current_time)

        # Check that motion is recorded correctly
        self.assertEqual(self.area.last_motion, self.current_time)
        self.assertEqual(len(self.area.activity_history), 1)
        self.assertEqual(self.area.activity_history[0], (self.current_time, "motion"))

    def test_record_entry(self):
        """Test recording entry into an area."""
        initial_occupancy = self.area.occupancy

        self.area.record_entry(self.current_time)

        # Check that entry is recorded correctly
        self.assertEqual(self.area.occupancy, initial_occupancy + 1)
        self.assertEqual(len(self.area.activity_history), 1)
        self.assertEqual(self.area.activity_history[0], (self.current_time, "entry"))

    def test_record_exit(self):
        """Test recording exit from an area."""
        # First record entry to increment occupancy
        self.area.record_entry(self.current_time - 100)

        # Then record exit
        result = self.area.record_exit(self.current_time)

        # Check that exit is recorded correctly
        self.assertTrue(result)  # Should return True when successful
        self.assertEqual(self.area.occupancy, 0)
        self.assertEqual(len(self.area.activity_history), 2)
        self.assertEqual(self.area.activity_history[1], (self.current_time, "exit"))

    def test_record_exit_no_occupancy(self):
        """Test recording exit when there's no occupancy."""
        # Ensure area has zero occupancy
        self.area.occupancy = 0

        # Try to record exit
        result = self.area.record_exit(self.current_time)

        # Check that exit failed
        self.assertFalse(result)  # Should return False when no occupancy
        self.assertEqual(len(self.area.activity_history), 0)  # No history recorded

    def test_get_inactivity_duration(self):
        """Test getting inactivity duration."""
        # Record motion
        self.area.record_motion(self.current_time - 100)

        # Check inactivity duration
        duration = self.area.get_inactivity_duration(self.current_time)
        self.assertEqual(duration, 100)

    def test_has_recent_motion_true(self):
        """Test has_recent_motion returns True for recent motion."""
        # Record motion
        self.area.record_motion(self.current_time - 60)

        # Check with 120 seconds window (default)
        self.assertTrue(self.area.has_recent_motion(self.current_time))

        # Check with custom window
        self.assertTrue(
            self.area.has_recent_motion(self.current_time, within_seconds=90)
        )

    def test_has_recent_motion_false(self):
        """Test has_recent_motion returns False for old motion."""
        # Record motion
        self.area.record_motion(self.current_time - 150)

        # Check with 120 seconds window (default)
        self.assertFalse(self.area.has_recent_motion(self.current_time))

        # Check with custom window
        self.assertTrue(
            self.area.has_recent_motion(self.current_time, within_seconds=200)
        )

    def test_has_recent_motion_no_motion(self):
        """Test has_recent_motion returns False when no motion recorded."""
        # No motion recorded
        self.assertFalse(self.area.has_recent_motion(self.current_time))

    def test_history_length_limit(self):
        """Test that activity history is limited to MAX_HISTORY_LENGTH."""
        # Record more than MAX_HISTORY_LENGTH entries
        for i in range(MAX_HISTORY_LENGTH + 10):
            self.area.record_motion(self.current_time + i)

        # Check that history is limited
        self.assertEqual(len(self.area.activity_history), MAX_HISTORY_LENGTH)

        # Check that oldest entries were removed (FIFO)
        self.assertEqual(self.area.activity_history[0][0], self.current_time + 10)

    def test_multiple_occupancy(self):
        """Test multiple entries and exits."""
        # Record multiple entries
        self.area.record_entry(self.current_time - 200)
        self.area.record_entry(self.current_time - 150)
        self.area.record_entry(self.current_time - 100)

        self.assertEqual(self.area.occupancy, 3)

        # Record multiple exits
        self.area.record_exit(self.current_time - 50)
        self.area.record_exit(self.current_time)

        self.assertEqual(self.area.occupancy, 1)

        # Check history length
        self.assertEqual(len(self.area.activity_history), 5)


if __name__ == "__main__":
    unittest.main()
