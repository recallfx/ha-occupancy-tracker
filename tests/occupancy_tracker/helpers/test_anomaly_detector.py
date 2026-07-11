"""Tests for AnomalyDetector."""

import time

from custom_components.occupancy_tracker.helpers.anomaly_detector import (
    AnomalyDetector,
)
from custom_components.occupancy_tracker.helpers.area_state import AreaState
from custom_components.occupancy_tracker.helpers.map_occupancy_resolver import (
    MapOccupancyResolver,
)
from custom_components.occupancy_tracker.helpers.map_state_recorder import MapSnapshot
from custom_components.occupancy_tracker.helpers.sensor_state import SensorState


def _set_occupancy(area: AreaState, count: int) -> None:
    """Set area occupancy by adding test claims."""
    area.claims.clear()
    for i in range(count):
        area.claims.add(f"_test_{i}")


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
        detector._create_warning("type1", "Message 1")
        w2 = detector._create_warning("type2", "Message 2")
        detector._create_warning("type3", "Message 3")

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
        detector._create_warning("type2", "Message 2")

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

    def test_check_timeouts_inactivity_reset(self):
        """Test that areas are reset after 24 hours of inactivity."""
        config = {"areas": {}, "adjacency": {}, "sensors": {}}
        detector = AnomalyDetector(config)

        timestamp = time.time()

        areas = {
            "bedroom": AreaState("bedroom", {"name": "Bedroom"}),
        }

        # Set up occupied area with old motion
        _set_occupancy(areas["bedroom"], 2)
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
        _set_occupancy(areas["office"], 1)
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
        _set_occupancy(areas["office"], 1)
        areas["office"].last_motion = timestamp - (13 * 3600)

        # Check timeouts twice
        detector.check_timeouts(areas, timestamp)
        detector.check_timeouts(areas, timestamp + 100)

        # Should only have one warning
        warnings = detector.get_warnings()
        assert len(warnings) == 1

    def test_unexpected_activation_warns_when_no_adjacent_source(self):
        """Unexpected motion in an empty non-exit area creates a warning."""
        timestamp = time.time()
        config = {
            "areas": {"back_hall": {"name": "Back Hall"}},
            "adjacency": {},
            "sensors": {},
        }

        resolver = MapOccupancyResolver(config)
        detector = AnomalyDetector(config)

        areas = {
            "back_hall": AreaState("back_hall", config["areas"]["back_hall"]),
        }
        sensors = {
            "binary_sensor.back": SensorState(
                "binary_sensor.back",
                {"area": "back_hall", "type": "motion"},
                timestamp,
            )
        }

        snapshot = MapSnapshot(
            timestamp=timestamp,
            event_type="sensor",
            description="sensor:binary_sensor.back:on",
            areas={},
            sensors={},
        )

        # Update sensor state before processing (mirrors coordinator behavior)
        sensors["binary_sensor.back"].update_state(True, timestamp)
        resolver.process_snapshot(snapshot, areas, sensors, detector)

        warnings = detector.get_warnings()
        assert len(warnings) == 1
        assert warnings[0].type == "unexpected_motion"
        assert warnings[0].area == "back_hall"

    def test_check_timeouts_recent_activity_no_warning(self):
        """Test that recent activity doesn't trigger warnings."""
        config = {"areas": {}, "adjacency": {}, "sensors": {}}
        detector = AnomalyDetector(config)

        timestamp = time.time()

        areas = {
            "kitchen": AreaState("kitchen", {"name": "Kitchen"}),
        }

        # Occupied with recent motion
        _set_occupancy(areas["kitchen"], 1)
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


class TestPhantomOccupancyCleanup:
    """Tests for evidence-based phantom occupancy cleanup."""

    BASE_TIME = 1_000_000.0

    def _make_config(self):
        return {
            "areas": {
                "bedroom": {"name": "Bedroom"},
                "hallway": {"name": "Hallway"},
                "kitchen": {"name": "Kitchen"},
                "frontyard": {
                    "name": "Frontyard",
                    "indoors": False,
                    "exit_capable": True,
                },
            },
            "adjacency": {
                "bedroom": ["hallway"],
                "hallway": ["kitchen", "frontyard"],
            },
            "sensors": {},
        }

    def _make_areas(self, config):
        return {aid: AreaState(aid, acfg) for aid, acfg in config["areas"].items()}

    def _make_sensors(self, timestamp):
        return {
            "sensor.bedroom_motion": SensorState(
                "sensor.bedroom_motion",
                {"area": "bedroom", "type": "motion"},
                timestamp,
            ),
            "sensor.hallway_motion": SensorState(
                "sensor.hallway_motion",
                {"area": "hallway", "type": "motion"},
                timestamp,
            ),
            "sensor.kitchen_motion": SensorState(
                "sensor.kitchen_motion",
                {"area": "kitchen", "type": "motion"},
                timestamp,
            ),
            "sensor.front_door": SensorState(
                "sensor.front_door",
                {"area": ["hallway", "frontyard"], "type": "door"},
                timestamp,
            ),
        }

    def _low_probability(self, area_id, timestamp):
        """Simulate decayed probability."""
        return 0.12

    def _high_probability(self, area_id, timestamp):
        """Simulate fresh probability."""
        return 0.60

    def test_phantom_cleared_all_conditions_met(self):
        """When all conditions met, phantom occupancy is cleared."""
        config = self._make_config()
        detector = AnomalyDetector(config)
        areas = self._make_areas(config)
        sensors = self._make_sensors(self.BASE_TIME)

        # Bedroom occupied with very old motion
        _set_occupancy(areas["bedroom"], 1)
        areas["bedroom"].last_motion = self.BASE_TIME - 12000  # 200 min ago

        # All neighbors quiet
        areas["hallway"].last_motion = self.BASE_TIME - 3600  # 60 min ago

        now = self.BASE_TIME

        cleared_area_ids = detector.check_timeouts(
            areas, now, sensors=sensors, probability_fn=self._low_probability
        )

        assert cleared_area_ids == ["bedroom"]
        assert areas["bedroom"].occupancy == 0
        warnings = [
            w for w in detector.get_warnings() if w.type == "phantom_occupancy_cleared"
        ]
        assert len(warnings) == 1
        assert warnings[0].area == "bedroom"

    def test_not_cleared_inactivity_too_short(self):
        """Phantom cleanup skipped when inactivity is under threshold."""
        config = self._make_config()
        detector = AnomalyDetector(config)
        areas = self._make_areas(config)
        sensors = self._make_sensors(self.BASE_TIME)

        _set_occupancy(areas["bedroom"], 1)
        areas["bedroom"].last_motion = self.BASE_TIME - 900  # 15 min ago

        detector.check_timeouts(
            areas, self.BASE_TIME, sensors=sensors, probability_fn=self._low_probability
        )

        assert areas["bedroom"].occupancy == 1

    def test_not_cleared_probability_too_high(self):
        """Phantom cleanup skipped when probability is still high."""
        config = self._make_config()
        detector = AnomalyDetector(config)
        areas = self._make_areas(config)
        sensors = self._make_sensors(self.BASE_TIME)

        _set_occupancy(areas["bedroom"], 1)
        areas["bedroom"].last_motion = self.BASE_TIME - 12000

        areas["hallway"].last_motion = self.BASE_TIME - 3600

        detector.check_timeouts(
            areas,
            self.BASE_TIME,
            sensors=sensors,
            probability_fn=self._high_probability,
        )

        assert areas["bedroom"].occupancy == 1

    def test_not_cleared_neighbor_recent_motion(self):
        """Phantom cleanup skipped when neighbor had recent motion (sleeping person)."""
        config = self._make_config()
        detector = AnomalyDetector(config)
        areas = self._make_areas(config)
        sensors = self._make_sensors(self.BASE_TIME)

        _set_occupancy(areas["bedroom"], 1)
        areas["bedroom"].last_motion = self.BASE_TIME - 12000

        # Someone walked through hallway 2 min ago
        areas["hallway"].last_motion = self.BASE_TIME - 120

        detector.check_timeouts(
            areas, self.BASE_TIME, sensors=sensors, probability_fn=self._low_probability
        )

        assert areas["bedroom"].occupancy == 1

    def test_not_cleared_neighbor_sensor_on(self):
        """Phantom cleanup skipped when neighbor has active motion sensor."""
        config = self._make_config()
        detector = AnomalyDetector(config)
        areas = self._make_areas(config)
        sensors = self._make_sensors(self.BASE_TIME)

        _set_occupancy(areas["bedroom"], 1)
        areas["bedroom"].last_motion = self.BASE_TIME - 12000
        areas["hallway"].last_motion = self.BASE_TIME - 3600

        # Hallway motion sensor currently ON
        sensors["sensor.hallway_motion"].update_state(True, self.BASE_TIME - 10)

        detector.check_timeouts(
            areas, self.BASE_TIME, sensors=sensors, probability_fn=self._low_probability
        )

        assert areas["bedroom"].occupancy == 1

    def test_not_cleared_own_sensor_on(self):
        """Phantom cleanup skipped while the area's own sensor is active."""
        config = self._make_config()
        detector = AnomalyDetector(config)
        areas = self._make_areas(config)
        sensors = self._make_sensors(self.BASE_TIME)

        _set_occupancy(areas["bedroom"], 1)
        areas["bedroom"].last_motion = self.BASE_TIME - 12000
        areas["hallway"].last_motion = self.BASE_TIME - 3600
        sensors["sensor.bedroom_motion"].update_state(True, self.BASE_TIME - 10)

        cleared_area_ids = detector.check_timeouts(
            areas,
            self.BASE_TIME,
            sensors=sensors,
            probability_fn=self._low_probability,
        )

        assert cleared_area_ids == []
        assert areas["bedroom"].occupancy == 1
        assert not any(
            warning.type == "phantom_occupancy_cleared"
            for warning in detector.get_warnings()
        )

    def test_unreliable_own_sensor_does_not_block_cleanup(self):
        """An invalidated ON reading is not evidence of continued presence."""
        config = self._make_config()
        detector = AnomalyDetector(config)
        areas = self._make_areas(config)
        sensors = self._make_sensors(self.BASE_TIME)

        _set_occupancy(areas["bedroom"], 1)
        areas["bedroom"].last_motion = self.BASE_TIME - 12000
        areas["hallway"].last_motion = self.BASE_TIME - 3600
        sensor = sensors["sensor.bedroom_motion"]
        sensor.current_state = True
        sensor.is_reliable = False

        cleared_area_ids = detector.check_timeouts(
            areas,
            self.BASE_TIME,
            sensors=sensors,
            probability_fn=self._low_probability,
        )

        assert cleared_area_ids == ["bedroom"]
        assert areas["bedroom"].occupancy == 0

    def test_unavailable_own_sensor_does_not_block_cleanup(self):
        """A last-known ON state is ignored while its sensor is unavailable."""
        config = self._make_config()
        detector = AnomalyDetector(config)
        areas = self._make_areas(config)
        sensors = self._make_sensors(self.BASE_TIME)

        _set_occupancy(areas["bedroom"], 1)
        areas["bedroom"].last_motion = self.BASE_TIME - 12000
        areas["hallway"].last_motion = self.BASE_TIME - 3600
        sensor = sensors["sensor.bedroom_motion"]
        sensor.update_state(True, self.BASE_TIME - 10)
        sensor.mark_unavailable(self.BASE_TIME)

        cleared_area_ids = detector.check_timeouts(
            areas,
            self.BASE_TIME,
            sensors=sensors,
            probability_fn=self._low_probability,
        )

        assert cleared_area_ids == ["bedroom"]
        assert areas["bedroom"].occupancy == 0

    def test_not_cleared_recent_magnetic_event(self):
        """Phantom cleanup skipped when a door sensor on the area changed recently."""
        config = {
            "areas": {
                "hallway": {"name": "Hallway"},
                "kitchen": {"name": "Kitchen"},
            },
            "adjacency": {"hallway": ["kitchen"]},
            "sensors": {},
        }
        detector = AnomalyDetector(config)
        areas = {aid: AreaState(aid, acfg) for aid, acfg in config["areas"].items()}
        sensors = {
            "sensor.hallway_motion": SensorState(
                "sensor.hallway_motion",
                {"area": "hallway", "type": "motion"},
                self.BASE_TIME,
            ),
            "sensor.kitchen_motion": SensorState(
                "sensor.kitchen_motion",
                {"area": "kitchen", "type": "motion"},
                self.BASE_TIME,
            ),
            "sensor.hallway_door": SensorState(
                "sensor.hallway_door",
                {"area": "hallway", "type": "door"},
                self.BASE_TIME,
            ),
        }

        _set_occupancy(areas["hallway"], 1)
        areas["hallway"].last_motion = self.BASE_TIME - 12000
        areas["kitchen"].last_motion = self.BASE_TIME - 3600

        # Door opened 10 min ago
        sensors["sensor.hallway_door"].update_state(True, self.BASE_TIME - 600)

        detector.check_timeouts(
            areas, self.BASE_TIME, sensors=sensors, probability_fn=self._low_probability
        )

        assert areas["hallway"].occupancy == 1

    def test_not_cleared_exit_capable(self):
        """Exit-capable areas are not phantom-cleared (they have their own mechanism)."""
        config = self._make_config()
        detector = AnomalyDetector(config)
        areas = self._make_areas(config)
        sensors = self._make_sensors(self.BASE_TIME)

        _set_occupancy(areas["frontyard"], 1)
        areas["frontyard"].last_motion = self.BASE_TIME - 12000

        # Note: exit-capable auto-clear at 5 min will fire first,
        # but phantom check should also skip it independently
        detector.check_timeouts(
            areas, self.BASE_TIME, sensors=sensors, probability_fn=self._low_probability
        )

        # Frontyard cleared by exit-capable mechanism, not phantom
        phantom_warnings = [
            w for w in detector.get_warnings() if w.type == "phantom_occupancy_cleared"
        ]
        assert len(phantom_warnings) == 0

    def test_multiple_areas_only_phantom_cleared(self):
        """Only the phantom area is cleared; legitimately occupied areas are kept."""
        config = self._make_config()
        detector = AnomalyDetector(config)
        areas = self._make_areas(config)
        sensors = self._make_sensors(self.BASE_TIME)

        # Kitchen: legitimately occupied (recent motion)
        _set_occupancy(areas["kitchen"], 1)
        areas["kitchen"].last_motion = self.BASE_TIME - 60

        # Bedroom: phantom (all conditions met)
        _set_occupancy(areas["bedroom"], 1)
        areas["bedroom"].last_motion = self.BASE_TIME - 12000

        # Hallway quiet
        areas["hallway"].last_motion = self.BASE_TIME - 3600

        def probability(area_id, timestamp):
            if area_id == "bedroom":
                return 0.12
            return 1.0  # Kitchen is fresh

        detector.check_timeouts(
            areas, self.BASE_TIME, sensors=sensors, probability_fn=probability
        )

        assert areas["kitchen"].occupancy == 1  # Kept
        assert areas["bedroom"].occupancy == 0  # Cleared

    def test_backward_compat_no_sensors(self):
        """check_timeouts works without sensors/probability_fn (existing behavior)."""
        config = self._make_config()
        detector = AnomalyDetector(config)
        areas = self._make_areas(config)

        _set_occupancy(areas["bedroom"], 1)
        areas["bedroom"].last_motion = self.BASE_TIME - 12000

        # Call without new params -- should not crash, no phantom cleanup
        detector.check_timeouts(areas, self.BASE_TIME)

        # 24h check won't fire (only 200 min), so bedroom stays
        assert areas["bedroom"].occupancy == 1

    def test_warning_metadata(self):
        """Warning created by phantom cleanup has correct metadata."""
        config = self._make_config()
        detector = AnomalyDetector(config)
        areas = self._make_areas(config)
        sensors = self._make_sensors(self.BASE_TIME)

        _set_occupancy(areas["bedroom"], 1)
        areas["bedroom"].last_motion = self.BASE_TIME - 12000
        areas["hallway"].last_motion = self.BASE_TIME - 3600

        detector.check_timeouts(
            areas, self.BASE_TIME, sensors=sensors, probability_fn=self._low_probability
        )

        warnings = [
            w for w in detector.get_warnings() if w.type == "phantom_occupancy_cleared"
        ]
        assert len(warnings) == 1
        w = warnings[0]
        assert w.area == "bedroom"
        assert "200" in w.message  # ~200 min of inactivity
        assert "12%" in w.message  # probability

    def test_isolated_area_cleared(self):
        """Area with no neighbors is cleared when other conditions met (vacuously true)."""
        config = {
            "areas": {"isolated": {"name": "Isolated Room"}},
            "adjacency": {},
            "sensors": {},
        }
        detector = AnomalyDetector(config)
        areas = {"isolated": AreaState("isolated", config["areas"]["isolated"])}
        sensors = {
            "sensor.isolated_motion": SensorState(
                "sensor.isolated_motion",
                {"area": "isolated", "type": "motion"},
                self.BASE_TIME,
            ),
        }

        _set_occupancy(areas["isolated"], 1)
        areas["isolated"].last_motion = self.BASE_TIME - 12000

        detector.check_timeouts(
            areas, self.BASE_TIME, sensors=sensors, probability_fn=self._low_probability
        )

        assert areas["isolated"].occupancy == 0

    def test_occupancy_greater_than_one_fully_cleared(self):
        """clear_occupancy sets occupancy to 0, not decrement by 1."""
        config = self._make_config()
        detector = AnomalyDetector(config)
        areas = self._make_areas(config)
        sensors = self._make_sensors(self.BASE_TIME)

        _set_occupancy(areas["bedroom"], 3)
        areas["bedroom"].last_motion = self.BASE_TIME - 12000
        areas["hallway"].last_motion = self.BASE_TIME - 3600

        detector.check_timeouts(
            areas, self.BASE_TIME, sensors=sensors, probability_fn=self._low_probability
        )

        assert areas["bedroom"].occupancy == 0
