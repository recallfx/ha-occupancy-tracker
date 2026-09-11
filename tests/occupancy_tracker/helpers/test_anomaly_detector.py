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

    def test_check_timeouts_inactivity_warns_without_reset(self):
        """Indoor inactivity is reported without claiming that the room is empty."""
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

        assert areas["bedroom"].occupancy == 1

        # Should create warning
        warnings = detector.get_warnings()
        assert len(warnings) == 1
        assert warnings[0].type == "extended_occupancy"

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

    def test_extended_occupancy_warning_resolves_when_motion_returns(self):
        """Warnings describe current predicates rather than stale history."""
        detector = AnomalyDetector({"areas": {}, "adjacency": {}, "sensors": {}})
        timestamp = time.time()
        area = AreaState("office", {"name": "Office"})
        _set_occupancy(area, 1)
        area.last_motion = timestamp - (13 * 3600)

        detector.check_timeouts({"office": area}, timestamp)
        warning = detector.get_warnings()[0]
        assert warning.type == "extended_occupancy"

        area.record_motion(timestamp + 1)
        detector.check_timeouts({"office": area}, timestamp + 2)

        assert warning.is_active is False
        assert detector.get_warnings() == []

    def test_sticky_safety_latch_is_not_a_plausible_motion_source(self):
        """Possible presence cannot explain a new unexplained activation forever."""
        timestamp = 10_000.0
        config = {
            "areas": {"hall": {}, "room": {}},
            "adjacency": {"hall": ["room"]},
            "sensors": {},
        }
        detector = AnomalyDetector(config)
        areas = {
            "hall": AreaState("hall", {}),
            "room": AreaState("room", {}),
        }
        areas["room"].occupied = True
        areas["hall"].occupied = True
        sensors = {
            "sensor.room": SensorState(
                "sensor.room", {"area": "room", "type": "motion"}, 0
            )
        }
        sensors["sensor.room"].update_state(True, timestamp)

        detector.report_unexpected_active_areas(
            timestamp, areas, sensors, first_activation_time=timestamp - 1_000
        )

        assert any(w.type == "unexpected_motion" for w in detector.get_warnings())

    def test_check_timeouts_skips_unknown_inactivity_duration(self):
        """Restored occupancy without a motion timestamp is not infinite inactivity."""
        detector = AnomalyDetector({"areas": {}, "adjacency": {}, "sensors": {}})
        area = AreaState("bedroom", {"name": "Bedroom"})
        _set_occupancy(area, 1)

        detector.check_timeouts({"bedroom": area}, time.time())

        assert detector.get_warnings() == []

    def test_isolated_area_motion_is_not_unexpected(self):
        """An isolated area's own sensor is valid occupancy evidence."""
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

        assert areas["back_hall"].occupancy == 1
        assert detector.get_warnings() == []

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

    def test_phantom_warned_all_conditions_met(self):
        """Ambiguous indoor inactivity produces a warning, not vacancy."""
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

        result = detector.check_timeouts(
            areas, now, sensors=sensors, freshness_fn=self._low_probability
        )

        assert result is None
        assert areas["bedroom"].occupancy == 1
        warnings = [
            w
            for w in detector.get_warnings()
            if w.type == "phantom_occupancy_suspected"
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
            areas, self.BASE_TIME, sensors=sensors, freshness_fn=self._low_probability
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
            freshness_fn=self._high_probability,
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
            areas, self.BASE_TIME, sensors=sensors, freshness_fn=self._low_probability
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
            areas, self.BASE_TIME, sensors=sensors, freshness_fn=self._low_probability
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

        result = detector.check_timeouts(
            areas,
            self.BASE_TIME,
            sensors=sensors,
            freshness_fn=self._low_probability,
        )

        assert result is None
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

        result = detector.check_timeouts(
            areas,
            self.BASE_TIME,
            sensors=sensors,
            freshness_fn=self._low_probability,
        )

        assert result is None
        assert areas["bedroom"].occupancy == 1

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

        result = detector.check_timeouts(
            areas,
            self.BASE_TIME,
            sensors=sensors,
            freshness_fn=self._low_probability,
        )

        assert result is None
        assert areas["bedroom"].occupancy == 1

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

        # Door opened 10 min ago; area-level evidence is restart-safe.
        sensors["sensor.hallway_door"].update_state(True, self.BASE_TIME - 600)
        areas["hallway"].record_contact(self.BASE_TIME - 600, is_open=True)

        detector.check_timeouts(
            areas, self.BASE_TIME, sensors=sensors, freshness_fn=self._low_probability
        )

        assert areas["hallway"].occupancy == 1
        assert not any(
            warning.type == "phantom_occupancy_suspected" and warning.area == "hallway"
            for warning in detector.get_warnings()
        )

    def test_exit_capable_timeout_is_diagnostic_only(self):
        """Exit-capable inactivity warns without mutating occupancy."""
        config = self._make_config()
        detector = AnomalyDetector(config)
        areas = self._make_areas(config)
        sensors = self._make_sensors(self.BASE_TIME)

        _set_occupancy(areas["frontyard"], 1)
        areas["frontyard"].last_motion = self.BASE_TIME - 12000

        detector.check_timeouts(
            areas, self.BASE_TIME, sensors=sensors, freshness_fn=self._low_probability
        )

        assert areas["frontyard"].occupancy == 1
        assert any(
            warning.type == "exit_area_stale" for warning in detector.get_warnings()
        )

    def test_multiple_areas_keep_possible_occupancy(self):
        """Stale-looking indoor occupancy remains alongside fresh occupancy."""
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
            areas, self.BASE_TIME, sensors=sensors, freshness_fn=probability
        )

        assert areas["kitchen"].occupancy == 1  # Kept
        assert areas["bedroom"].occupancy == 1

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
            areas, self.BASE_TIME, sensors=sensors, freshness_fn=self._low_probability
        )

        warnings = [
            w
            for w in detector.get_warnings()
            if w.type == "phantom_occupancy_suspected"
        ]
        assert len(warnings) == 1
        w = warnings[0]
        assert w.area == "bedroom"
        assert "200" in w.message  # ~200 min of inactivity
        assert "12%" in w.message  # probability

    def test_isolated_indoor_area_is_not_cleared(self):
        """Silence in an isolated room is still not proof of vacancy."""
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
            areas, self.BASE_TIME, sensors=sensors, freshness_fn=self._low_probability
        )

        assert areas["isolated"].occupancy == 1

    def test_legacy_count_remains_conservatively_occupied(self):
        """Legacy count input remains a boolean occupied state."""
        config = self._make_config()
        detector = AnomalyDetector(config)
        areas = self._make_areas(config)
        sensors = self._make_sensors(self.BASE_TIME)

        _set_occupancy(areas["bedroom"], 3)
        areas["bedroom"].last_motion = self.BASE_TIME - 12000
        areas["hallway"].last_motion = self.BASE_TIME - 3600

        detector.check_timeouts(
            areas, self.BASE_TIME, sensors=sensors, freshness_fn=self._low_probability
        )

        assert areas["bedroom"].occupancy == 1
