import unittest

import yaml

from custom_components.occupancy_tracker.anomaly_detector import AnomalyDetector
from custom_components.occupancy_tracker.occupancy_tracker import OccupancyTracker


class TestAnomalyDetector(unittest.TestCase):
    def setUp(self):
        with open("config.yaml") as f:
            self.config = yaml.safe_load(f)
        self.tracker = OccupancyTracker(self.config)
        # Use a threshold of 300 seconds (5 minutes)
        self.detector = AnomalyDetector(
            self.tracker, self.config, long_detect_threshold=300
        )

    def handle_event(self, sensor_name, state, timestamp):
        """Helper that mimics a typical event handling:
        1. The detector observes the raw sensor event.
        2. The occupancy tracker processes the event.
        3. The detector compares occupancy changes.
        """
        self.detector.process_event(sensor_name, state, timestamp)
        self.tracker.process_event(sensor_name, state, timestamp)
        self.detector.check_occupancy_changes(timestamp)

    def test_long_detection_anomaly(self):
        """Verify that a sensor that remains 'on' longer than the threshold is flagged."""
        # Trigger the sensor on at t=0 and off at t=400 (>300)
        self.handle_event("motion_living", True, 0)
        self.handle_event("motion_living", False, 400)
        anomalies = self.detector.get_anomalies()
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

    def test_no_long_detection(self):
        """Verify that a sensor event with duration less than the threshold does not trigger an anomaly."""
        # Sensor on at t=0 and off at t=200 (<300)
        self.handle_event("motion_living", True, 0)
        self.handle_event("motion_living", False, 200)
        anomalies = self.detector.get_anomalies()
        long_anomalies = [
            a
            for a in anomalies
            if a["type"] == "long_detection" and a.get("sensor") == "motion_living"
        ]
        self.assertEqual(
            len(long_anomalies),
            0,
            "No long_detection anomaly should be flagged for short duration",
        )

    def test_impossible_appearance(self):
        """Verify that an area that goes from 0 to >0 occupancy without adjacent occupants
        is flagged as an impossible appearance in a non-exit area.
        In this case, main_bathroom is not exit_capable.
        """
        # Initially, main_bathroom has 0 occupancy and none of its adjacent areas (main_bedroom, backyard) have occupants.
        # Trigger motion in main_bathroom.
        self.handle_event("motion_main_bathroom", True, 10)
        self.handle_event("motion_main_bathroom", False, 12)
        anomalies = self.detector.get_anomalies()
        impossible_anoms = [
            a
            for a in anomalies
            if a["type"] == "impossible_appearance" and a.get("area") == "main_bathroom"
        ]
        self.assertTrue(
            len(impossible_anoms) > 0,
            "Expected an impossible_appearance anomaly in main_bathroom",
        )

    def test_possible_appearance_no_anomaly(self):
        """Verify that if an area that is exit-capable shows occupancy, it is not flagged.
        For example, frontyard is exit-capable.
        """
        # Trigger motion in frontyard using its camera sensor.
        self.handle_event("motion_front_left_camera", True, 20)
        self.handle_event("motion_front_left_camera", False, 22)
        anomalies = self.detector.get_anomalies()
        impossible_anoms = [
            a
            for a in anomalies
            if a["type"] == "impossible_appearance" and a.get("area") == "frontyard"
        ]
        self.assertEqual(
            len(impossible_anoms),
            0,
            "No impossible_appearance anomaly should be flagged for frontyard",
        )

    def test_impossible_appearance_with_adjacent(self):
        """Verify that if an appearance in a non-exit area is plausible due to adjacent occupancy,
        no anomaly is flagged.
        In this test, main_bedroom (adjacent to main_bathroom) already has an occupant.
        """
        # Manually set occupancy in an adjacent area.
        self.tracker.set_occupancy("main_bedroom", 1)
        # Now trigger motion in main_bathroom.
        self.handle_event("motion_main_bathroom", True, 30)
        self.handle_event("motion_main_bathroom", False, 32)
        anomalies = self.detector.get_anomalies()
        impossible_anoms = [
            a
            for a in anomalies
            if a["type"] == "impossible_appearance" and a.get("area") == "main_bathroom"
        ]
        self.assertEqual(
            len(impossible_anoms),
            0,
            "No impossible_appearance anomaly should be flagged if an adjacent area has occupancy",
        )


if __name__ == "__main__":
    unittest.main()
