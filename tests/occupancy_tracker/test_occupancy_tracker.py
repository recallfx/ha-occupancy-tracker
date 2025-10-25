"""Tests for OccupancyTracker core functionality."""

import time

from custom_components.occupancy_tracker.occupancy_tracker import OccupancyTracker


class TestOccupancyTrackerInit:
    """Test OccupancyTracker initialization."""

    def test_create_tracker(self):
        """Test creating an occupancy tracker."""
        config = {
            "areas": {
                "living_room": {"name": "Living Room", "indoors": True},
                "kitchen": {"name": "Kitchen"},
            },
            "adjacency": {"living_room": ["kitchen"]},
            "sensors": {
                "sensor.motion_living": {"area": "living_room", "type": "motion"},
            },
        }

        tracker = OccupancyTracker(config)

        assert tracker.config == config
        assert len(tracker.areas) == 2
        assert len(tracker.sensors) == 1
        assert "living_room" in tracker.areas
        assert "sensor.motion_living" in tracker.sensors

    def test_initialize_areas(self):
        """Test area initialization from config."""
        config = {
            "areas": {
                "bedroom": {"name": "Bedroom", "indoors": True, "exit_capable": False},
                "porch": {"name": "Porch", "indoors": False, "exit_capable": True},
            },
            "adjacency": {},
            "sensors": {},
        }

        tracker = OccupancyTracker(config)

        assert tracker.areas["bedroom"].is_indoors is True
        assert tracker.areas["bedroom"].is_exit_capable is False
        assert tracker.areas["porch"].is_indoors is False
        assert tracker.areas["porch"].is_exit_capable is True

    def test_initialize_sensors(self):
        """Test sensor initialization from config."""
        config = {
            "areas": {"room1": {}},
            "adjacency": {},
            "sensors": {
                "sensor.motion_1": {"area": "room1", "type": "motion"},
                "sensor.door_1": {
                    "type": "magnetic",
                    "between_areas": ["room1", "room2"],
                },
            },
        }

        tracker = OccupancyTracker(config)

        assert tracker.sensors["sensor.motion_1"].config["type"] == "motion"
        assert tracker.sensors["sensor.door_1"].config["type"] == "magnetic"

    def test_initialize_adjacency(self):
        """Test adjacency initialization."""
        config = {
            "areas": {
                "living_room": {},
                "kitchen": {},
                "hallway": {},
            },
            "adjacency": {
                "living_room": ["kitchen", "hallway"],
                "kitchen": ["living_room"],
            },
            "sensors": {
                "sensor.motion_living": {"area": "living_room", "type": "motion"},
                "sensor.motion_kitchen": {"area": "kitchen", "type": "motion"},
                "sensor.motion_hallway": {"area": "hallway", "type": "motion"},
            },
        }

        tracker = OccupancyTracker(config)

        # Living room sensor should know about kitchen and hallway sensors
        adjacent = tracker.adjacency_tracker.get_adjacency("sensor.motion_living")
        assert "sensor.motion_kitchen" in adjacent
        assert "sensor.motion_hallway" in adjacent


class TestOccupancyTrackerSensorEvents:
    """Test sensor event processing."""

    def test_process_motion_event(self):
        """Test processing a motion sensor event."""
        config = {
            "areas": {"living_room": {"name": "Living Room", "exit_capable": True}},
            "adjacency": {},
            "sensors": {
                "sensor.motion_living": {"area": "living_room", "type": "motion"},
            },
        }

        tracker = OccupancyTracker(config)
        timestamp = time.time()

        tracker.process_sensor_event("sensor.motion_living", True, timestamp)

        # Motion should be recorded
        assert tracker.areas["living_room"].last_motion == timestamp
        # Entry should be recorded (from outside via exit_capable)
        assert tracker.areas["living_room"].occupancy == 1

    def test_process_motion_event_occupied_room(self):
        """Test motion in already occupied room."""
        config = {
            "areas": {"kitchen": {}},
            "adjacency": {},
            "sensors": {
                "sensor.motion_kitchen": {"area": "kitchen", "type": "motion"},
            },
        }

        tracker = OccupancyTracker(config)
        timestamp = time.time()

        # First motion
        tracker.process_sensor_event("sensor.motion_kitchen", True, timestamp)
        occupancy_after_first = tracker.areas["kitchen"].occupancy

        # Second motion (repeated)
        tracker.process_sensor_event("sensor.motion_kitchen", True, timestamp + 10)

        # Occupancy shouldn't increase again
        assert tracker.areas["kitchen"].occupancy == occupancy_after_first

    def test_process_unknown_sensor(self):
        """Test processing event from unknown sensor."""
        config = {"areas": {}, "adjacency": {}, "sensors": {}}

        tracker = OccupancyTracker(config)

        # Should not raise error
        tracker.process_sensor_event("sensor.unknown", True, time.time())

    def test_process_magnetic_sensor(self):
        """Test processing magnetic (door/window) sensor."""
        config = {
            "areas": {"room1": {}, "room2": {}},
            "adjacency": {"room1": ["room2"]},
            "sensors": {
                "sensor.door_12": {
                    "type": "magnetic",
                    "between_areas": ["room1", "room2"],
                },
            },
        }

        tracker = OccupancyTracker(config)

        # Process door open/close
        tracker.process_sensor_event("sensor.door_12", True, time.time())
        tracker.process_sensor_event("sensor.door_12", False, time.time() + 5)

        # Door events are recorded but don't directly change occupancy

    def test_process_camera_motion_sensor(self):
        """Test processing camera motion sensor."""
        config = {
            "areas": {"front_porch": {"exit_capable": True}},
            "adjacency": {},
            "sensors": {
                "sensor.camera_motion": {
                    "area": "front_porch",
                    "type": "camera_motion",
                },
            },
        }

        tracker = OccupancyTracker(config)
        timestamp = time.time()

        tracker.process_sensor_event("sensor.camera_motion", True, timestamp)

        assert tracker.areas["front_porch"].last_motion == timestamp

    def test_process_camera_person_sensor(self):
        """Test processing camera person detection sensor."""
        config = {
            "areas": {"driveway": {"exit_capable": True}},
            "adjacency": {},
            "sensors": {
                "sensor.camera_person": {"area": "driveway", "type": "camera_person"},
            },
        }

        tracker = OccupancyTracker(config)
        timestamp = time.time()

        tracker.process_sensor_event("sensor.camera_person", True, timestamp)

        assert tracker.areas["driveway"].last_motion == timestamp

    def test_sensor_state_change_tracking(self):
        """Test that sensor state changes are properly tracked."""
        config = {
            "areas": {"room": {}},
            "adjacency": {},
            "sensors": {
                "sensor.motion": {"area": "room", "type": "motion"},
            },
        }

        tracker = OccupancyTracker(config)
        t1 = time.time()

        tracker.process_sensor_event("sensor.motion", True, t1)
        assert tracker.sensors["sensor.motion"].current_state is True

        tracker.process_sensor_event("sensor.motion", False, t1 + 10)
        assert tracker.sensors["sensor.motion"].current_state is False


class TestOccupancyTrackerTransitions:
    """Test occupancy transitions between areas."""

    def test_transition_between_adjacent_rooms(self):
        """Test person transitioning between adjacent rooms."""
        config = {
            "areas": {
                "living_room": {},
                "kitchen": {},
            },
            "adjacency": {"living_room": ["kitchen"], "kitchen": ["living_room"]},
            "sensors": {
                "sensor.motion_living": {"area": "living_room", "type": "motion"},
                "sensor.motion_kitchen": {"area": "kitchen", "type": "motion"},
            },
        }

        tracker = OccupancyTracker(config)
        timestamp = time.time()

        # Start in living room (entry from outside - both are not exit_capable)
        # This will create an unexpected motion warning but still increment
        tracker.process_sensor_event("sensor.motion_living", True, timestamp)
        assert tracker.areas["living_room"].occupancy >= 1

        # Move to kitchen (adjacent, recent motion in living room)
        tracker.process_sensor_event("sensor.motion_kitchen", True, timestamp + 10)

        # Kitchen should have person
        assert tracker.areas["kitchen"].occupancy >= 1
        # Living room should have decremented if transition detected
        # (This depends on the exact logic in handle_unexpected_motion)

    def test_entry_from_outside(self):
        """Test person entering from outside through exit-capable area."""
        config = {
            "areas": {
                "front_door": {"exit_capable": True},
            },
            "adjacency": {},
            "sensors": {
                "sensor.motion_door": {"area": "front_door", "type": "motion"},
            },
        }

        tracker = OccupancyTracker(config)

        tracker.process_sensor_event("sensor.motion_door", True, time.time())

        # Should register entry
        assert tracker.areas["front_door"].occupancy == 1


class TestOccupancyTrackerQueries:
    """Test occupancy query methods."""

    def test_get_occupancy(self):
        """Test getting occupancy count for an area."""
        config = {
            "areas": {"bedroom": {}},
            "adjacency": {},
            "sensors": {},
        }

        tracker = OccupancyTracker(config)

        # Initially zero
        assert tracker.get_occupancy("bedroom") == 0

        # Manually set occupancy
        tracker.areas["bedroom"].occupancy = 2

        assert tracker.get_occupancy("bedroom") == 2

    def test_get_occupancy_unknown_area(self):
        """Test getting occupancy for unknown area."""
        config = {"areas": {}, "adjacency": {}, "sensors": {}}

        tracker = OccupancyTracker(config)

        assert tracker.get_occupancy("unknown") == 0

    def test_get_occupancy_probability_occupied(self):
        """Test probability calculation for occupied area with recent motion."""
        config = {
            "areas": {"office": {}},
            "adjacency": {},
            "sensors": {},
        }

        tracker = OccupancyTracker(config)
        timestamp = time.time()

        tracker.areas["office"].occupancy = 1
        tracker.areas["office"].record_motion(timestamp)

        probability = tracker.get_occupancy_probability("office")

        # Recent motion + occupied = high probability
        assert probability == 0.95

    def test_get_occupancy_probability_occupied_no_recent_motion(self):
        """Test probability for occupied area without recent motion."""
        config = {
            "areas": {"garage": {}},
            "adjacency": {},
            "sensors": {},
        }

        tracker = OccupancyTracker(config)
        timestamp = time.time()

        tracker.areas["garage"].occupancy = 1
        tracker.areas["garage"].last_motion = timestamp - 600  # 10 minutes ago

        probability = tracker.get_occupancy_probability("garage")

        # Occupied but no recent motion = medium probability
        assert probability == 0.75

    def test_get_occupancy_probability_unoccupied(self):
        """Test probability for unoccupied area."""
        config = {
            "areas": {"basement": {}},
            "adjacency": {},
            "sensors": {},
        }

        tracker = OccupancyTracker(config)

        probability = tracker.get_occupancy_probability("basement")

        assert probability == 0.05

    def test_get_occupancy_probability_unknown_area(self):
        """Test probability for unknown area."""
        config = {"areas": {}, "adjacency": {}, "sensors": {}}

        tracker = OccupancyTracker(config)

        probability = tracker.get_occupancy_probability("unknown")

        assert probability == 0.05

    def test_get_area_status(self):
        """Test getting detailed area status."""
        config = {
            "areas": {
                "living_room": {"name": "Living Room"},
            },
            "adjacency": {"living_room": ["kitchen"]},
            "sensors": {},
        }

        tracker = OccupancyTracker(config)
        timestamp = time.time()

        tracker.areas["living_room"].occupancy = 2
        tracker.areas["living_room"].record_motion(timestamp)

        status = tracker.get_area_status("living_room")

        assert status["id"] == "living_room"
        assert status["name"] == "Living Room"
        assert status["occupancy"] == 2
        assert status["last_motion"] == timestamp
        assert status["adjacent_areas"] == ["kitchen"]

    def test_get_area_status_unknown(self):
        """Test getting status for unknown area."""
        config = {"areas": {}, "adjacency": {}, "sensors": {}}

        tracker = OccupancyTracker(config)

        status = tracker.get_area_status("unknown")

        assert "error" in status

    def test_get_system_status(self):
        """Test getting overall system status."""
        config = {
            "areas": {
                "room1": {},
                "room2": {},
                "room3": {},
            },
            "adjacency": {},
            "sensors": {},
        }

        tracker = OccupancyTracker(config)

        tracker.areas["room1"].occupancy = 2
        tracker.areas["room2"].occupancy = 1

        status = tracker.get_system_status()

        assert status["total_occupancy"] == 3
        assert len(status["occupied_areas"]) == 2
        assert status["occupied_areas"]["room1"] == 2
        assert status["occupied_areas"]["room2"] == 1


class TestOccupancyTrackerWarnings:
    """Test warning management."""

    def test_get_warnings(self):
        """Test getting warnings from tracker."""
        config = {"areas": {}, "adjacency": {}, "sensors": {}}

        tracker = OccupancyTracker(config)

        # Initially no warnings
        assert len(tracker.get_warnings()) == 0

    def test_resolve_warning(self):
        """Test resolving a warning."""
        config = {"areas": {}, "adjacency": {}, "sensors": {}}

        tracker = OccupancyTracker(config)

        # Create a warning
        warning = tracker.anomaly_detector._create_warning("test", "Test warning")

        result = tracker.resolve_warning(warning.id)

        assert result is True
        assert warning.is_active is False


class TestOccupancyTrackerReset:
    """Test reset functionality."""

    def test_reset(self):
        """Test full system reset."""
        config = {
            "areas": {"room1": {}, "room2": {}},
            "adjacency": {"room1": ["room2"]},
            "sensors": {
                "sensor.motion_1": {"area": "room1", "type": "motion"},
            },
        }

        tracker = OccupancyTracker(config)
        timestamp = time.time()

        # Set up some state
        tracker.areas["room1"].occupancy = 2
        tracker.areas["room1"].record_motion(timestamp)
        tracker.process_sensor_event("sensor.motion_1", True, timestamp)

        # Reset
        tracker.reset()

        # Everything should be cleared
        assert tracker.areas["room1"].occupancy == 0
        assert tracker.areas["room1"].last_motion == 0
        assert tracker.sensors["sensor.motion_1"].current_state is False
        assert len(tracker.get_warnings()) == 0

    def test_reset_anomalies(self):
        """Test resetting only anomalies (not occupancy state)."""
        config = {
            "areas": {"room1": {}},
            "adjacency": {},
            "sensors": {},
        }

        tracker = OccupancyTracker(config)

        # Set up state and warnings
        tracker.areas["room1"].occupancy = 1
        tracker.anomaly_detector._create_warning("test", "Test")

        # Reset anomalies only
        tracker.reset_anomalies()

        # Occupancy preserved, warnings cleared
        assert tracker.areas["room1"].occupancy == 1
        assert len(tracker.get_warnings()) == 0


class TestOccupancyTrackerTimeouts:
    """Test timeout checking."""

    def test_check_timeouts(self):
        """Test checking for timeout conditions."""
        config = {
            "areas": {"bedroom": {}},
            "adjacency": {},
            "sensors": {},
        }

        tracker = OccupancyTracker(config)
        timestamp = time.time()

        # Set up occupied area with old motion
        tracker.areas["bedroom"].occupancy = 1
        tracker.areas["bedroom"].last_motion = timestamp - (25 * 3600)

        tracker.check_timeouts(timestamp)

        # Should reset due to inactivity
        assert tracker.areas["bedroom"].occupancy == 0


class TestOccupancyTrackerDiagnostics:
    """Test diagnostic methods."""

    def test_diagnose_motion_issues_single_sensor(self):
        """Test diagnosing issues with a specific sensor."""
        config = {
            "areas": {"living_room": {}},
            "adjacency": {},
            "sensors": {
                "sensor.motion_living": {"area": "living_room", "type": "motion"},
            },
        }

        tracker = OccupancyTracker(config)

        result = tracker.diagnose_motion_issues("sensor.motion_living")

        assert "sensor.motion_living" in result
        assert result["sensor.motion_living"]["is_motion_sensor"] is True
        assert result["sensor.motion_living"]["sensor_type"] == "motion"
        assert result["sensor.motion_living"]["area_exists"] is True

    def test_diagnose_motion_issues_all_sensors(self):
        """Test diagnosing all sensors."""
        config = {
            "areas": {"room1": {}, "room2": {}},
            "adjacency": {},
            "sensors": {
                "sensor.motion_1": {"area": "room1", "type": "motion"},
                "sensor.motion_2": {"area": "room2", "type": "camera_motion"},
            },
        }

        tracker = OccupancyTracker(config)

        result = tracker.diagnose_motion_issues()

        assert len(result) == 2
        assert "sensor.motion_1" in result
        assert "sensor.motion_2" in result

    def test_diagnose_motion_issues_unknown_sensor(self):
        """Test diagnosing unknown sensor."""
        config = {"areas": {}, "adjacency": {}, "sensors": {}}

        tracker = OccupancyTracker(config)

        result = tracker.diagnose_motion_issues("sensor.unknown")

        assert "sensor.unknown" in result
        assert "error" in result["sensor.unknown"]
