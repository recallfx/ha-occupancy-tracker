from custom_components.occupancy_tracker.anomaly_detector import AnomalyDetector
from custom_components.occupancy_tracker.occupancy_tracker import OccupancyTracker


class OccupancySystem:
    """Combines occupancy tracking and anomaly detection into a single production class.

    This class wraps an OccupancyTracker instance and an AnomalyDetector instance.
    For each sensor event, it:
      1. Notifies the anomaly detector of the sensor state change.
      2. Updates the occupancy tracker.
      3. Checks for occupancy changes that might indicate an anomaly.

    It exposes helper methods to retrieve occupancy and anomaly information.
    """

    def __init__(self, config, long_detect_threshold=300, short_threshold=5):
        """Initialize the OccupancySystem.

        :param config: Configuration dictionary (areas, adjacency, sensors, etc.)
        :param long_detect_threshold: Time (in seconds) a sensor must be active to be flagged.
        :param short_threshold: Maximum allowed gap (in seconds) to consider an adjacent transition.
        """
        self.config = config
        self.tracker = OccupancyTracker(config)
        # Adjust the short threshold to support longer valid transitions
        self.tracker.short_threshold = short_threshold
        self.anomaly_detector = AnomalyDetector(
            self.tracker, config, long_detect_threshold
        )

    def handle_event(self, sensor_name, state, timestamp=0):
        """Process a sensor event.

        The order is:
          1. Process the raw sensor event for anomaly detection.
          2. Update the occupancy tracker.
          3. Check for occupancy changes and log anomalies.

        :param sensor_name: The sensor's name.
        :param state: The sensor state (True for active/detected, False for inactive).
        :param timestamp: The current time (in seconds) for event ordering.
        """
        # 1. Let the anomaly detector record the raw sensor change.
        self.anomaly_detector.process_event(sensor_name, state, timestamp)
        # 2. Process the event for occupancy tracking.
        self.tracker.process_event(sensor_name, state, timestamp)
        # 3. Check for occupancy changes to flag anomalies.
        self.anomaly_detector.check_occupancy_changes(timestamp)

    def get_occupancy(self, area):
        """Retrieve the current occupancy count for an area.

        :param area: The area identifier.
        :return: The number of occupants in the area.
        """
        return self.tracker.get_occupancy(area)

    def get_occupancy_probability(self, area):
        """Retrieve the occupancy probability for an area.

        :param area: The area identifier.
        :return: The probability (between 0.05 and 0.95) of occupancy.
        """
        return self.tracker.get_occupancy_probability(area)

    def get_anomalies(self):
        """Retrieve the list of detected anomalies.

        :return: A list of anomaly records.
        """
        return self.anomaly_detector.get_anomalies()

    def reset_anomalies(self):
        """Reset all detected anomalies."""
        self.anomaly_detector.reset_anomalies()
