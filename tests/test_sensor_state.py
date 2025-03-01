import unittest
import time
from custom_components.occupancy_tracker.components.sensor_state import SensorState
from custom_components.occupancy_tracker.components.constants import MAX_HISTORY_LENGTH

class TestSensorState(unittest.TestCase):
    """Unit tests for SensorState class."""

    def setUp(self):
        """Set up test fixtures."""
        self.sensor_config = {"type": "motion", "location": "living_room"}
        self.sensor_id = "binary_sensor.living_room_motion"
        self.current_time = time.time()
        self.sensor_state = SensorState(self.sensor_id, self.sensor_config, self.current_time)
    
    def test_init(self):
        """Test proper initialization of SensorState."""
        self.assertEqual(self.sensor_state.id, self.sensor_id)
        self.assertEqual(self.sensor_state.config, self.sensor_config)
        self.assertFalse(self.sensor_state.current_state)
        self.assertEqual(self.sensor_state.last_changed, self.current_time)
        self.assertEqual(len(self.sensor_state.history), 0)
        self.assertTrue(self.sensor_state.is_reliable)
        self.assertFalse(self.sensor_state.is_stuck)
    
    def test_update_state_no_change(self):
        """Test updating state with the same value."""
        # Default initial state is False
        timestamp = self.current_time + 10
        self.assertFalse(self.sensor_state.update_state(False, timestamp))
        self.assertEqual(len(self.sensor_state.history), 1)
        self.assertFalse(self.sensor_state.current_state)
    
    def test_update_state_with_change(self):
        """Test updating state with a new value."""
        # Update to True should return True indicating state changed
        timestamp = self.current_time + 10
        self.assertTrue(self.sensor_state.update_state(True, timestamp))
        self.assertEqual(len(self.sensor_state.history), 1)
        self.assertTrue(self.sensor_state.current_state)
        
        # Update back to False should return True indicating state changed
        timestamp = self.current_time + 20
        self.assertTrue(self.sensor_state.update_state(False, timestamp))
        self.assertEqual(len(self.sensor_state.history), 2)
        self.assertFalse(self.sensor_state.current_state)
    
    def test_history_length_limit(self):
        """Test that history is limited to MAX_HISTORY_LENGTH."""
        # Add more than MAX_HISTORY_LENGTH items
        for i in range(MAX_HISTORY_LENGTH + 10):
            state = i % 2 == 0  # Alternate between True and False
            timestamp = self.current_time + i
            self.sensor_state.update_state(state, timestamp)
            
        # Verify history length is capped
        self.assertEqual(len(self.sensor_state.history), MAX_HISTORY_LENGTH)
        
        # Verify oldest items were removed (history[0] should be the 11th item we added)
        self.assertEqual(self.sensor_state.history[0].state, 10 % 2 == 0)
    
    def test_calculate_is_stuck_on_state(self):
        """Test stuck detection when sensor is ON for too long."""
        # Set initial state to ON
        initial_time = self.current_time
        self.sensor_state.update_state(True, initial_time)
        
        # Check if sensor is detected as stuck 24 hours + 1 second later
        check_time = initial_time + 86401
        
        # Check if sensor is detected as stuck
        self.assertTrue(self.sensor_state.calculate_is_stuck(False, check_time))
        self.assertTrue(self.sensor_state.is_stuck)
    
    def test_calculate_is_stuck_with_adjacent_motion(self):
        """Test stuck detection when there's adjacent motion but no state change."""
        # Set initial state
        initial_time = self.current_time
        self.sensor_state.update_state(False, initial_time)
        
        # Check if sensor is detected as stuck 31 seconds later with adjacent motion
        check_time = initial_time + 31
        
        # Check if sensor is detected as stuck when there's adjacent motion
        self.assertTrue(self.sensor_state.calculate_is_stuck(True, check_time))
        self.assertTrue(self.sensor_state.is_stuck)
    
    def test_not_stuck_when_recently_changed(self):
        """Test sensor is not stuck when it changed state recently."""
        # Set initial state
        initial_time = self.current_time
        self.sensor_state.update_state(True, initial_time)
        
        # Check 10 seconds later
        check_time = initial_time + 10
        
        # Check if sensor is not detected as stuck
        self.assertFalse(self.sensor_state.calculate_is_stuck(False, check_time))
        self.assertFalse(self.sensor_state.is_stuck)
    
    def test_not_stuck_off_state(self):
        """Test sensor in OFF state isn't marked as stuck even after 24+ hours."""
        # Set initial state to OFF
        initial_time = self.current_time
        self.sensor_state.update_state(False, initial_time)
        
        # Check 48 hours later
        check_time = initial_time + 86400 * 2
        
        # Check if sensor is not detected as stuck (because it's OFF)
        self.assertFalse(self.sensor_state.calculate_is_stuck(False, check_time))
        self.assertFalse(self.sensor_state.is_stuck)
    
    def test_non_motion_sensor_with_adjacent_motion(self):
        """Test non-motion sensor types aren't affected by adjacent motion."""
        # Create a different sensor type
        current_time = self.current_time
        temp_sensor = SensorState("sensor.temp", {"type": "temperature"}, current_time)
        
        # Set initial state
        temp_sensor.update_state(False, current_time)
        
        # Check 60 seconds later with adjacent motion
        check_time = current_time + 60
        
        # Check if sensor is not detected as stuck despite adjacent motion
        self.assertFalse(temp_sensor.calculate_is_stuck(True, check_time))
        self.assertFalse(temp_sensor.is_stuck)

if __name__ == "__main__":
    unittest.main()