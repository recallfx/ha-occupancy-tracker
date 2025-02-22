from typing import Dict, Optional
from dataclasses import dataclass
import math
import logging


@dataclass
class AreaState:
    occupancy: int = 0
    probability: float = 0.0
    last_motion: Optional[float] = None
    last_transition: Optional[float] = None


class OccupancyTracker:
    def __init__(self, config):
        self.config = config
        self.areas = config["areas"]
        self.sensors = config["sensors"]
        self.adjacency = config["adjacency"]
        self.current_time = 0.0
        self.states: Dict[str, AreaState] = {area: AreaState() for area in self.areas}
        self.motion_timeout = 5.0  # seconds
        self.min_probability = 0.1
        self.high_confidence = 0.95
        self.low_confidence = 0.70

    def updateTimestamp(self, timestamp: float) -> None:
        """Update the current timestamp and decay probabilities accordingly."""
        self.current_time = timestamp
        for area in self.areas:
            self._decay_probability(area, timestamp)

    def _decay_probability(self, area: str, current_time: float) -> None:
        if area not in self.areas:
            logging.error(f"Invalid area: {area}")
            return

        state = self.states[area]
        if state.last_motion is None:
            if state.occupancy > 0:
                state.probability = self.high_confidence
            else:
                state.probability = self.min_probability
            return

        time_diff = current_time - state.last_motion
        if time_diff > self.motion_timeout:
            if state.occupancy == 0:
                # Fast decay for areas with no occupants
                decay_factor = math.exp(-2.0 * (time_diff - self.motion_timeout))
                state.probability = max(
                    self.min_probability,
                    state.probability * decay_factor
                )
                if state.probability <= self.min_probability:
                    state.last_motion = None
            else:
                # Keep high probability for occupied areas
                state.probability = self.high_confidence

    def _update_adjacent_probabilities(self, area: str, increase: bool = True) -> None:
        if area not in self.areas:
            logging.error(f"Invalid area: {area}")
            return

        if area not in self.adjacency:
            return

        for adj_area in self.adjacency[area]:
            adj_state = self.states[adj_area]
            if increase:
                # Increase probability of adjacent areas on motion
                if adj_state.occupancy > 0:
                    adj_state.probability = self.high_confidence
                else:
                    adj_state.probability = max(
                        adj_state.probability,
                        self.low_confidence
                    )
            else:
                # Decrease probability of adjacent areas on inactivity
                if adj_state.occupancy == 0:
                    adj_state.probability = self.min_probability

    def process_event(self, sensor_id: str, state: bool, timestamp: float) -> None:
        if sensor_id not in self.sensors:
            logging.error(f"Invalid sensor_id: {sensor_id}")
            return

        # Update current time and decay all probabilities
        self.updateTimestamp(timestamp)

        sensor = self.sensors[sensor_id]
        areas = sensor["area"] if isinstance(sensor["area"], list) else [sensor["area"]]
        sensor_type = sensor["type"]

        for area in areas:
            area_state = self.states[area]
            if state:  # Sensor activated
                if sensor_type == "motion":
                    prev_motion = area_state.last_motion
                    area_state.last_motion = timestamp
                    
                    # Find adjacent areas that had recent motion
                    adj_active_areas = []
                    for adj_area in self.adjacency.get(area, []):
                        adj_state = self.states[adj_area]
                        if (
                            adj_state.last_motion is not None
                            and timestamp - adj_state.last_motion < self.motion_timeout
                            and adj_state.occupancy > 0
                        ):
                            adj_active_areas.append((adj_area, adj_state))
                    
                    if adj_active_areas:
                        # Split occupancy for incomplete transitions
                        for adj_area, adj_state in adj_active_areas:
                            if area_state.occupancy == 0:  # Only split if not already split
                                split = adj_state.occupancy / 2
                                area_state.occupancy += split
                                adj_state.occupancy -= split
                                
                                # During transition, destination (newer motion) gets higher probability
                                if adj_state.last_motion < timestamp:  # This is newer motion
                                    area_state.probability = self.high_confidence
                                    adj_state.probability = self.low_confidence  # Original area gets lower confidence
                                else:
                                    area_state.probability = self.low_confidence
                                    adj_state.probability = self.high_confidence
                    else:
                        # No adjacent activity
                        if prev_motion is None:
                            area_state.probability = self.high_confidence if area_state.occupancy > 0 else self.low_confidence
                    
                    self._update_adjacent_probabilities(area)

                elif sensor_type in ["magnetic", "camera_person"]:
                    if self.areas[area].get("indoors", True):
                        area_state.occupancy = max(1, area_state.occupancy)
                    area_state.probability = self.high_confidence
                    area_state.last_transition = timestamp

            else:  # Sensor deactivated
                if sensor_type == "motion":
                    # When motion stops, check for transitions
                    active_adjacents = [
                        adj for adj in self.adjacency.get(area, [])
                        if (
                            self.states[adj].last_motion is not None
                            and timestamp - self.states[adj].last_motion < self.motion_timeout
                        )
                    ]
                    
                    if active_adjacents and area_state.occupancy > 0:
                        # Complete transition - transfer all occupancy to active areas
                        transfer = area_state.occupancy
                        area_state.occupancy = 0
                        area_state.last_motion = None
                        area_state.probability = self.min_probability
                        for adj in active_adjacents:
                            self.states[adj].occupancy += transfer / len(active_adjacents)
                            self.states[adj].probability = self.high_confidence
                    else:
                        # Just motion stopping - lower probability
                        area_state.probability = self.low_confidence

                    self._update_adjacent_probabilities(area, increase=False)

    def get_occupancy(self, area: str) -> int:
        if area not in self.areas:
            logging.error(f"Invalid area: {area}")
            return 0

        return self.states[area].occupancy

    def set_occupancy(self, area: str, value: int) -> None:
        if area not in self.areas:
            logging.error(f"Invalid area: {area}")
            return

        self.states[area].occupancy = value
        if value > 0:
            self.states[area].probability = self.high_confidence

    def get_occupancy_probability(self, area: str) -> float:
        if area not in self.areas:
            logging.error(f"Invalid area: {area}")
            return 0.0

        return self.states[area].probability
