class AnomalyDetector:
    """Tracks suspicious or impossible events that aren't explainable by normal
    adjacency-based movement or that violate typical sensor usage.

    1) "Impossible Appearances" in non-exit-capable areas with no adjacent occupants.
    2) Sensor 'detected' state persists too long (e.g., stuck motion sensor).
    """

    def __init__(self, occupancy_tracker, config, long_detect_threshold=300):
        """:param occupancy_tracker: Instance of your OccupancyTracker
        :param config: Your loaded config dictionary (areas, adjacency, sensors)
        :param long_detect_threshold: Time in seconds that a sensor can remain
                                      in 'detected' state before we flag it.
        """
        self.tracker = occupancy_tracker
        self.config = config
        self.long_detect_threshold = long_detect_threshold

        # Keep track of which sensors are actively "on" and when they turned on
        # so we can detect if they stay on past the threshold
        self.sensor_states = {}  # sensor_name -> {"state": bool, "since": timestamp}

        # Keep the last occupant_count so we can detect suspicious jumps
        self.prev_occupant_count = {}
        for area in config.get("areas", {}):
            self.prev_occupant_count[area] = self.tracker.get_occupancy(area)

        # Store anomalies here
        # Each anomaly will be a dict describing what happened:
        #   {
        #       "type": "long_detection" / "impossible_appearance",
        #       "sensor": ...,
        #       "area": ...,
        #       "timestamp": ...,
        #       "duration": ...,
        #       ...
        #   }
        self.anomalies = []

    def process_event(self, sensor_name, state, timestamp):
        """Called before the occupancy tracker processes the event.
        Detects when a sensor has turned on/off and logs long detection anomalies.
        """
        sensor_info = self.config.get("sensors", {}).get(sensor_name)
        if not sensor_info:
            # Unknown sensor — ignore or treat as anomaly if desired
            return

        old_state = self.sensor_states.get(sensor_name, {"state": False, "since": None})

        if state and not old_state["state"]:
            # Sensor turned ON
            self.sensor_states[sensor_name] = {"state": True, "since": timestamp}

        elif not state and old_state["state"]:
            # Sensor turned OFF
            on_time = old_state["since"]
            duration = timestamp - on_time
            if duration > self.long_detect_threshold:
                # Record anomaly about this sensor being on for too long
                self.anomalies.append(
                    {
                        "type": "long_detection",
                        "sensor": sensor_name,
                        "timestamp": timestamp,
                        "duration": duration,
                    },
                )

            # Update sensor state to off
            self.sensor_states[sensor_name] = {"state": False, "since": None}

    def check_occupancy_changes(self, timestamp):
        """Called after the occupancy tracker processes the event.
        Compares new occupant counts to previous occupant counts.
        Detects if there's an impossible appearance in a non-exit area with no adjacent occupant.
        """
        for area, old_count in self.prev_occupant_count.items():
            new_count = self.tracker.get_occupancy(area)

            # Detect occupant "appearance" from 0 to > 0 in a non-exit-capable area
            if old_count == 0 and new_count > 0:
                # If area is exit-capable, it's *possible* a new occupant arrived from outside
                area_info = self.config.get("areas", {}).get(area, {})
                if not area_info.get("exit_capable", False):
                    # Check adjacency if it’s at all plausible
                    adjacency_list = self.config.get("adjacency", {}).get(area, [])
                    possible = False
                    for adj_area in adjacency_list:
                        # If an adjacent area had occupant(s), that occupant might have moved in
                        if self.tracker.get_occupancy(adj_area) > 0:
                            possible = True
                            break

                    if not possible:
                        # No adjacency occupant found => suspicious appearance
                        self.anomalies.append(
                            {
                                "type": "impossible_appearance",
                                "area": area,
                                "timestamp": timestamp,
                                "old_count": old_count,
                                "new_count": new_count,
                            },
                        )

            # Update stored occupant count
            self.prev_occupant_count[area] = new_count

    def reset_anomalies(self):
        """Reset all detected anomalies."""
        self.anomalies = []

    def get_anomalies(self):
        """Returns the list of anomalies detected so far."""
        return self.anomalies
