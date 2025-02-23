import unittest

import yaml

from custom_components.occupancy_tracker.occupancy_system import OccupancySystem


class TestOccupancySystem(unittest.TestCase):
    def setUp(self):
        # Load the configuration (areas, adjacency, sensors)
        with open("config.yaml") as f:
            self.config = yaml.safe_load(f)
        # Initialize the system with a long detection threshold of 300 seconds and short threshold of 5 seconds.
        self.system = OccupancySystem(
            self.config, long_detect_threshold=300, short_threshold=5
        )

    def test_simple_transition(self):
        """Verify a simple transition from main_bathroom to main_bedroom.
        Expected: main_bathroom occupancy becomes 0 and main_bedroom becomes 1.
        """
        # Set initial occupancy: occupant in main_bathroom.
        self.system.tracker.set_occupancy("main_bathroom", 1)

        # Simulate the occupant moving:
        # 1. Motion in main_bathroom (start)
        self.system.handle_event("motion_main_bathroom", True, timestamp=0)
        # 2. Motion in main_bedroom (arrival)
        self.system.handle_event("motion_main_bedroom", True, timestamp=2)
        # 3. Clear motion in main_bathroom to finalize the transition.
        self.system.handle_event("motion_main_bathroom", False, timestamp=5)

        self.assertEqual(self.system.get_occupancy("main_bathroom"), 0)
        self.assertEqual(self.system.get_occupancy("main_bedroom"), 1)
        self.assertAlmostEqual(
            self.system.get_occupancy_probability("main_bathroom"), 0.05, delta=0.1
        )
        self.assertAlmostEqual(
            self.system.get_occupancy_probability("main_bedroom"), 0.95, delta=0.1
        )

    def test_long_detection_anomaly(self):
        """Verify that a sensor remaining 'on' longer than the threshold triggers a long_detection anomaly."""
        # Trigger motion_living on at t=0 and off at t=400 (>300 seconds).
        self.system.handle_event("motion_living", True, timestamp=0)
        self.system.handle_event("motion_living", False, timestamp=400)

        anomalies = self.system.get_anomalies()
        long_anomalies = [
            a
            for a in anomalies
            if a["type"] == "long_detection" and a.get("sensor") == "motion_living"
        ]
        self.assertTrue(
            len(long_anomalies) > 0,
            "Expected a long_detection anomaly for motion_living",
        )
        self.assertTrue(long_anomalies[0]["duration"] > 300)

    def test_impossible_appearance(self):
        """Verify that an impossible appearance is flagged in a non-exit area (e.g., main_bathroom)
        when no adjacent area has occupancy.
        """
        # No occupant exists in main_bathroom or its adjacent areas.
        self.system.handle_event("motion_main_bathroom", True, timestamp=10)
        self.system.handle_event("motion_main_bathroom", False, timestamp=12)

        anomalies = self.system.get_anomalies()
        impossible_anoms = [
            a
            for a in anomalies
            if a["type"] == "impossible_appearance" and a.get("area") == "main_bathroom"
        ]
        self.assertTrue(
            len(impossible_anoms) > 0,
            "Expected an impossible_appearance anomaly in main_bathroom",
        )

    def test_exit_detection(self):
        """Verify that when an occupant is in an exit-capable area (frontyard) and the motion stops,
        the system infers the occupant left (occupancy set to 0).
        """
        # Set initial occupancy in frontyard.
        self.system.tracker.set_occupancy("frontyard", 1)
        # Simulate exit sensor events (using a camera sensor for frontyard).
        self.system.handle_event("motion_front_left_camera", True, timestamp=0)
        self.system.handle_event("motion_front_left_camera", False, timestamp=10)

        self.assertEqual(self.system.get_occupancy("frontyard"), 0)
        self.assertAlmostEqual(
            self.system.get_occupancy_probability("frontyard"), 0.05, delta=0.1
        )


if __name__ == "__main__":
    unittest.main()
