import unittest
import time
from custom_components.occupancy_tracker.components.warning import Warning

class TestWarning(unittest.TestCase):
    """Unit tests for Warning class."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.current_time = time.time()
        self.warning_type = "sensor_stuck"
        self.message = "Sensor appears to be stuck"
        self.area = "living_room"
        self.sensor_id = "binary_sensor.motion"
        
    def test_init(self):
        """Test proper initialization of Warning."""
        warning = Warning(
            self.warning_type, 
            self.message, 
            self.area, 
            self.sensor_id, 
            self.current_time
        )
        
        self.assertEqual(warning.type, self.warning_type)
        self.assertEqual(warning.message, self.message)
        self.assertEqual(warning.area, self.area)
        self.assertEqual(warning.sensor_id, self.sensor_id)
        self.assertEqual(warning.timestamp, self.current_time)
        self.assertTrue(warning.is_active)
        self.assertEqual(
            warning.id, 
            f"{self.warning_type}_{self.area}_{self.sensor_id}_{self.current_time}"
        )
    
    def test_init_with_optional_fields_none(self):
        """Test initialization with None for optional fields."""
        warning = Warning(
            self.warning_type,
            self.message,
            None,
            None,
            self.current_time
        )
        
        self.assertEqual(warning.type, self.warning_type)
        self.assertEqual(warning.message, self.message)
        self.assertIsNone(warning.area)
        self.assertIsNone(warning.sensor_id)
        self.assertEqual(warning.timestamp, self.current_time)
        self.assertTrue(warning.is_active)
        self.assertEqual(
            warning.id, 
            f"{self.warning_type}___{self.current_time}"
        )
    
    def test_resolve(self):
        """Test resolving a warning."""
        warning = Warning(
            self.warning_type, 
            self.message, 
            self.area, 
            self.sensor_id, 
            self.current_time
        )
        
        self.assertTrue(warning.is_active)
        warning.resolve()
        self.assertFalse(warning.is_active)
    
    def test_str_representation(self):
        """Test the string representation of a warning."""
        warning = Warning(
            self.warning_type, 
            self.message, 
            self.area, 
            self.sensor_id, 
            self.current_time
        )
        
        expected_str = f"Warning[{self.warning_type}]: {self.message}"
        self.assertEqual(str(warning), expected_str)
    
    def test_id_generation(self):
        """Test that warning IDs are generated correctly."""
        # Test with all fields provided
        warning1 = Warning("type1", "msg1", "area1", "sensor1", 1000.0)
        self.assertEqual(warning1.id, "type1_area1_sensor1_1000.0")
        
        # Test with area = None
        warning2 = Warning("type2", "msg2", None, "sensor2", 2000.0)
        self.assertEqual(warning2.id, "type2__sensor2_2000.0")
        
        # Test with sensor_id = None
        warning3 = Warning("type3", "msg3", "area3", None, 3000.0)
        self.assertEqual(warning3.id, "type3_area3__3000.0")
        
        # Test with both area and sensor_id = None
        warning4 = Warning("type4", "msg4", None, None, 4000.0)
        self.assertEqual(warning4.id, "type4___4000.0")

if __name__ == "__main__":
    unittest.main()