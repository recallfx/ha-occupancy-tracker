"""Tests for AnomalyDetector."""

import time
from unittest.mock import MagicMock

from custom_components.occupancy_tracker.components.anomaly_detector import (
    AnomalyDetector,
)
from custom_components.occupancy_tracker.components.area_state import AreaState
from custom_components.occupancy_tracker.components.sensor_state import SensorState
from custom_components.occupancy_tracker.components.sensor_adjacency_tracker import (
    SensorAdjacencyTracker,
)


class TestAnomalyDetector:
    """Test AnomalyDetector class."""

    def test_create_detector(self):
        """Test creating an anomaly detector."""
        config = {
            "areas": {},
            "adjacency": {},
            "sensors": {},
        }
        detector = AnomalyDetector(config)

        assert detector.config == config
        assert detector.warnings == []
        assert detector.recent_motion_window == 120
        assert detector.motion_timeout == 24 * 3600
        assert detector.extended_occupancy_threshold == 12 * 3600

    def test_create_warning(self):
        """Test creating a warning."""
        config = {"areas": {}, "adjacency": {}, "sensors": {}}
        detector = AnomalyDetector(config)
        timestamp = time.time()

        warning = detector._create_warning(
            warning_type="test_warning",
            message="Test message",
            area="living_room",
            sensor_id="sensor.motion_1",
            timestamp=timestamp,
        )

        assert warning.type == "test_warning"
        assert warning.message == "Test message"
        assert warning.area == "living_room"
        assert warning.sensor_id == "sensor.motion_1"
        assert warning.timestamp == timestamp
        assert len(detector.warnings) == 1

    def test_get_warnings_active_only(self):
        """Test getting only active warnings."""
        config = {"areas": {}, "adjacency": {}, "sensors": {}}
        detector = AnomalyDetector(config)

        # Create some warnings
        w1 = detector._create_warning("type1", "Message 1")
        w2 = detector._create_warning("type2", "Message 2")
        w3 = detector._create_warning("type3", "Message 3")

        # Resolve one
        w2.resolve()

        active_warnings = detector.get_warnings(active_only=True)

        assert len(active_warnings) == 2
        assert w2 not in active_warnings

    def test_get_warnings_all(self):
        """Test getting all warnings including resolved."""
        config = {"areas": {}, "adjacency": {}, "sensors": {}}
        detector = AnomalyDetector(config)

        w1 = detector._create_warning("type1", "Message 1")
        w2 = detector._create_warning("type2", "Message 2")

        w1.resolve()

        all_warnings = detector.get_warnings(active_only=False)

        assert len(all_warnings) == 2

    def test_resolve_warning(self):
        """Test resolving a warning by ID."""
        config = {"areas": {}, "adjacency": {}, "sensors": {}}
        detector = AnomalyDetector(config)

        warning = detector._create_warning("stuck_sensor", "Sensor stuck")

        result = detector.resolve_warning(warning.id)

        assert result is True
        assert warning.is_active is False

    def test_resolve_nonexistent_warning(self):
        """Test resolving a warning that doesn't exist."""
        config = {"areas": {}, "adjacency": {}, "sensors": {}}
        detector = AnomalyDetector(config)

        result = detector.resolve_warning("nonexistent_id")

        assert result is False

    def test_check_for_stuck_sensors(self):
        """Test checking for stuck sensors."""
        config = {
            "areas": {
                "living_room": {"name": "Living Room"},
                "kitchen": {"name": "Kitchen"},
            },
            "adjacency": {"living_room": ["kitchen"]},
            "sensors": {},
        }
        detector = AnomalyDetector(config)

        timestamp = time.time()

        # Create sensors - one with very old last update
        sensors = {
            "sensor.motion_living": SensorState(
                "sensor.motion_living",
                {"area": "living_room", "type": "motion"},
                timestamp,
            ),
            "sensor.motion_kitchen": SensorState(
                "sensor.motion_kitchen",
                {"area": "kitchen", "type": "motion"},
                timestamp - 100000,  # Very old - over 24 hours ago
            ),
        }
        
        # Update kitchen sensor to "on" state so it can be detected as stuck
        sensors["sensor.motion_kitchen"].update_state(True, timestamp - 100000)

        areas = {
            "living_room": AreaState("living_room", {"name": "Living Room"}),
            "kitchen": AreaState("kitchen", {"name": "Kitchen"}),
        }

        detector.check_for_stuck_sensors(sensors, areas, "sensor.motion_living")

        # Should create a warning for the stuck sensor
        warnings = detector.get_warnings()
        assert len(warnings) == 1
        assert warnings[0].type == "stuck_sensor"
        assert warnings[0].sensor_id == "sensor.motion_kitchen"
        assert warnings[0].type == "stuck_sensor"
        assert "sensor.motion_kitchen" in warnings[0].message

    def test_handle_unexpected_motion_with_adjacent_occupied(self):
        """Test handling unexpected motion when adjacent area is occupied."""
        config = {
            "areas": {
                "living_room": {"name": "Living Room"},
                "kitchen": {"name": "Kitchen"},
            },
            "adjacency": {"kitchen": ["living_room"]},
            "sensors": {},
        }
        detector = AnomalyDetector(config)

        timestamp = time.time()

        # Setup areas
        areas = {
            "living_room": AreaState("living_room", {"name": "Living Room"}),
            "kitchen": AreaState("kitchen", {"name": "Kitchen"}),
        }

        # Living room is occupied with recent motion
        areas["living_room"].occupancy = 1
        areas["living_room"].record_motion(timestamp - 10)

        sensors = {}
        adjacency_tracker = SensorAdjacencyTracker()

        # Kitchen has unexpected motion but living room (adjacent) is occupied
        result = detector.handle_unexpected_motion(
            areas["kitchen"], areas, sensors, timestamp, adjacency_tracker
        )

        # Should be valid entry (moving from living room to kitchen)
        assert result is True
        # Living room occupancy should decrease
        assert areas["living_room"].occupancy == 0

    def test_handle_unexpected_motion_exit_capable_area(self):
        """Test handling unexpected motion in exit-capable area."""
        config = {
            "areas": {
                "front_porch": {"name": "Front Porch", "exit_capable": True},
            },
            "adjacency": {},
            "sensors": {},
        }
        detector = AnomalyDetector(config)

        timestamp = time.time()

        areas = {
            "front_porch": AreaState(
                "front_porch", {"name": "Front Porch", "exit_capable": True}
            ),
        }

        sensors = {}
        adjacency_tracker = SensorAdjacencyTracker()

        # Entry from outside in exit-capable area is valid
        result = detector.handle_unexpected_motion(
            areas["front_porch"], areas, sensors, timestamp, adjacency_tracker
        )

        # Should still not be considered an anomaly (valid external entry)
        # But warnings may still be created for logging

    def test_handle_unexpected_motion_creates_warning(self):
        """Test that unexpected motion creates a warning when no valid path."""
        config = {
            "areas": {
                "bedroom": {"name": "Bedroom", "exit_capable": False},
            },
            "adjacency": {},
            "sensors": {},
        }
        detector = AnomalyDetector(config)

        timestamp = time.time()

        areas = {
            "bedroom": AreaState("bedroom", {"name": "Bedroom"}),
        }

        sensors = {}
        adjacency_tracker = SensorAdjacencyTracker()

        # Bedroom has unexpected motion with no adjacent occupied areas
        result = detector.handle_unexpected_motion(
            areas["bedroom"], areas, sensors, timestamp, adjacency_tracker
        )

        # Should create warning
        warnings = detector.get_warnings()
        assert len(warnings) == 1
        assert warnings[0].type == "unexpected_motion"
        assert "bedroom" in warnings[0].message

    def test_check_simultaneous_motion_adjacent_areas(self):
        """Test that simultaneous motion in adjacent areas doesn't create warning."""
        config = {
            "areas": {
                "living_room": {"name": "Living Room"},
                "kitchen": {"name": "Kitchen"},
            },
            "adjacency": {"living_room": ["kitchen"]},
            "sensors": {},
        }
        detector = AnomalyDetector(config)

        timestamp = time.time()

        areas = {
            "living_room": AreaState("living_room", {"name": "Living Room"}),
            "kitchen": AreaState("kitchen", {"name": "Kitchen"}),
        }

        # Both have recent motion but they're adjacent
        areas["kitchen"].record_motion(timestamp - 5)

        detector.check_simultaneous_motion("living_room", areas, timestamp)

        # Should not create warning for adjacent areas
        warnings = detector.get_warnings()
        assert len(warnings) == 0

    def test_check_simultaneous_motion_non_adjacent_areas(self):
        """Test that simultaneous motion in non-adjacent areas creates warning."""
        config = {
            "areas": {
                "living_room": {"name": "Living Room"},
                "bedroom": {"name": "Bedroom"},
            },
            "adjacency": {"living_room": []},  # Not adjacent
            "sensors": {},
        }
        detector = AnomalyDetector(config)

        timestamp = time.time()

        areas = {
            "living_room": AreaState("living_room", {"name": "Living Room"}),
            "bedroom": AreaState("bedroom", {"name": "Bedroom"}),
        }

        # Both have recent motion (within 10 seconds) but not adjacent
        areas["bedroom"].record_motion(timestamp - 5)

        detector.check_simultaneous_motion("living_room", areas, timestamp)

        # Should create warning
        warnings = detector.get_warnings()
        assert len(warnings) == 1
        assert warnings[0].type == "simultaneous_motion"

    def test_check_timeouts_inactivity_reset(self):
        """Test that areas are reset after 24 hours of inactivity."""
        config = {"areas": {}, "adjacency": {}, "sensors": {}}
        detector = AnomalyDetector(config)

        timestamp = time.time()

        areas = {
            "bedroom": AreaState("bedroom", {"name": "Bedroom"}),
        }

        # Set up occupied area with old motion
        areas["bedroom"].occupancy = 2
        areas["bedroom"].last_motion = timestamp - (25 * 3600)  # 25 hours ago

        detector.check_timeouts(areas, timestamp)

        # Should reset occupancy
        assert areas["bedroom"].occupancy == 0

        # Should create warning
        warnings = detector.get_warnings()
        assert len(warnings) == 1
        assert warnings[0].type == "inactivity_timeout"

    def test_check_timeouts_extended_occupancy(self):
        """Test warning for extended occupancy (12+ hours)."""
        config = {"areas": {}, "adjacency": {}, "sensors": {}}
        detector = AnomalyDetector(config)

        timestamp = time.time()

        areas = {
            "office": AreaState("office", {"name": "Office"}),
        }

        # Set up occupied area with 13 hours of inactivity
        areas["office"].occupancy = 1
        areas["office"].last_motion = timestamp - (13 * 3600)

        detector.check_timeouts(areas, timestamp)

        # Should not reset (under 24 hours) but should warn
        assert areas["office"].occupancy == 1

        # Should create warning
        warnings = detector.get_warnings()
        assert len(warnings) == 1
        assert warnings[0].type == "extended_occupancy"

    def test_check_timeouts_no_duplicate_extended_occupancy_warning(self):
        """Test that extended occupancy warning is not duplicated."""
        config = {"areas": {}, "adjacency": {}, "sensors": {}}
        detector = AnomalyDetector(config)

        timestamp = time.time()

        areas = {
            "office": AreaState("office", {"name": "Office"}),
        }

        # Set up occupied area with 13 hours of inactivity
        areas["office"].occupancy = 1
        areas["office"].last_motion = timestamp - (13 * 3600)

        # Check timeouts twice
        detector.check_timeouts(areas, timestamp)
        detector.check_timeouts(areas, timestamp + 100)

        # Should only have one warning
        warnings = detector.get_warnings()
        assert len(warnings) == 1

    def test_check_timeouts_recent_activity_no_warning(self):
        """Test that recent activity doesn't trigger warnings."""
        config = {"areas": {}, "adjacency": {}, "sensors": {}}
        detector = AnomalyDetector(config)

        timestamp = time.time()

        areas = {
            "kitchen": AreaState("kitchen", {"name": "Kitchen"}),
        }

        # Occupied with recent motion
        areas["kitchen"].occupancy = 1
        areas["kitchen"].record_motion(timestamp - 60)  # 1 minute ago

        detector.check_timeouts(areas, timestamp)

        # Should not create any warnings
        warnings = detector.get_warnings()
        assert len(warnings) == 0

    def test_multiple_warning_types(self):
        """Test that different warning types can coexist."""
        config = {
            "areas": {"room1": {}, "room2": {}},
            "adjacency": {},
            "sensors": {},
        }
        detector = AnomalyDetector(config)

        # Create different types of warnings
        detector._create_warning("stuck_sensor", "Sensor stuck", sensor_id="sensor.1")
        detector._create_warning("unexpected_motion", "Unexpected", area="room1")
        detector._create_warning("inactivity_timeout", "Timeout", area="room2")

        warnings = detector.get_warnings()

        assert len(warnings) == 3
        warning_types = {w.type for w in warnings}
        assert warning_types == {
            "stuck_sensor",
            "unexpected_motion",
            "inactivity_timeout",
        }
