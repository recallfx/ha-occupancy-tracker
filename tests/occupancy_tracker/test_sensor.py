"""Tests for sensor platform."""

import pytest
from unittest.mock import MagicMock, AsyncMock

from custom_components.occupancy_tracker.sensor import (
    OccupancyCountSensor,
    OccupancyProbabilitySensor,
    AnomalySensor,
    OccupiedInsideAreasSensor,
    OccupiedOutsideAreasSensor,
    TotalOccupantsInsideSensor,
    TotalOccupantsOutsideSensor,
    TotalOccupantsSensor,
    async_setup_platform,
)
from custom_components.occupancy_tracker.occupancy_tracker import OccupancyTracker


@pytest.fixture
def tracker():
    """Create a test occupancy tracker."""
    config = {
        "areas": {
            "living_room": {"name": "Living Room", "indoors": True},
            "bedroom": {"name": "Bedroom", "indoors": True},
            "porch": {"name": "Porch", "indoors": False},
        },
        "adjacency": {},
        "sensors": {},
    }
    return OccupancyTracker(config)


class TestOccupancyCountSensor:
    """Test OccupancyCountSensor class."""

    def test_create_sensor(self, tracker):
        """Test creating an occupancy count sensor."""
        sensor = OccupancyCountSensor(tracker, "living_room")

        assert sensor._area == "living_room"
        assert sensor._attr_name == "Occupancy Count living_room"
        assert sensor._attr_unique_id == "occupancy_count_living_room"

    def test_sensor_state_zero(self, tracker):
        """Test sensor state when occupancy is zero."""
        sensor = OccupancyCountSensor(tracker, "bedroom")

        assert sensor.state == 0

    def test_sensor_state_occupied(self, tracker):
        """Test sensor state when area is occupied."""
        tracker.areas["living_room"].occupancy = 3

        sensor = OccupancyCountSensor(tracker, "living_room")

        assert sensor.state == 3


class TestOccupancyProbabilitySensor:
    """Test OccupancyProbabilitySensor class."""

    def test_create_sensor(self, tracker):
        """Test creating an occupancy probability sensor."""
        sensor = OccupancyProbabilitySensor(tracker, "bedroom")

        assert sensor._area == "bedroom"
        assert sensor._attr_name == "Occupancy Probability bedroom"
        assert sensor._attr_unique_id == "occupancy_probability_bedroom"

    def test_sensor_state(self, tracker):
        """Test sensor state reflects probability."""
        import time

        tracker.areas["living_room"].occupancy = 1
        tracker.areas["living_room"].record_motion(time.time())

        sensor = OccupancyProbabilitySensor(tracker, "living_room")

        assert sensor.state == 0.95


class TestAnomalySensor:
    """Test AnomalySensor class."""

    def test_create_sensor(self, tracker):
        """Test creating an anomaly sensor."""
        sensor = AnomalySensor(tracker)

        assert sensor._attr_name == "Detected Anomalies"
        assert sensor._attr_unique_id == "detected_anomalies"
        assert sensor._attr_icon == "mdi:alert-circle"

    def test_sensor_state_no_anomalies(self, tracker):
        """Test sensor state when no anomalies."""
        sensor = AnomalySensor(tracker)

        assert sensor.state == 0

    def test_sensor_state_with_anomalies(self, tracker):
        """Test sensor state with active anomalies."""
        # Create some warnings
        tracker.anomaly_detector._create_warning("stuck_sensor", "Sensor stuck")
        tracker.anomaly_detector._create_warning("unexpected_motion", "Unexpected")

        sensor = AnomalySensor(tracker)

        assert sensor.state == 2

    def test_sensor_state_only_active_anomalies(self, tracker):
        """Test sensor state counts only active anomalies."""
        w1 = tracker.anomaly_detector._create_warning("type1", "Message 1")
        w2 = tracker.anomaly_detector._create_warning("type2", "Message 2")

        w1.resolve()

        sensor = AnomalySensor(tracker)

        assert sensor.state == 1

    def test_extra_state_attributes(self, tracker):
        """Test extra state attributes."""
        import time

        tracker.anomaly_detector._create_warning(
            "stuck_sensor",
            "Sensor stuck",
            area="bedroom",
            sensor_id="sensor.motion_1",
            timestamp=time.time(),
        )

        sensor = AnomalySensor(tracker)
        attrs = sensor.extra_state_attributes

        assert "anomalies" in attrs
        assert len(attrs["anomalies"]) == 1
        assert attrs["anomalies"][0]["type"] == "stuck_sensor"
        assert attrs["anomalies"][0]["area"] == "bedroom"
        assert attrs["anomalies"][0]["sensor"] == "sensor.motion_1"

    def test_extra_state_attributes_counts(self, tracker):
        """Test anomaly type counts in attributes."""
        tracker.anomaly_detector._create_warning("stuck_sensor", "Sensor 1 stuck")
        tracker.anomaly_detector._create_warning("stuck_sensor", "Sensor 2 stuck")
        tracker.anomaly_detector._create_warning("unexpected_motion", "Unexpected")

        sensor = AnomalySensor(tracker)
        attrs = sensor.extra_state_attributes

        assert attrs["anomaly_counts"]["stuck_sensor"] == 2
        assert attrs["anomaly_counts"]["unexpected_motion"] == 1

    def test_extra_state_attributes_latest(self, tracker):
        """Test latest anomaly in attributes."""
        import time

        tracker.anomaly_detector._create_warning("type1", "First", timestamp=time.time())
        time.sleep(0.01)
        tracker.anomaly_detector._create_warning("type2", "Second", timestamp=time.time())

        sensor = AnomalySensor(tracker)
        attrs = sensor.extra_state_attributes

        assert attrs["latest_anomaly"]["type"] == "type2"

    def test_sensor_available(self, tracker):
        """Test sensor is always available."""
        sensor = AnomalySensor(tracker)

        assert sensor.available is True

    def test_sensor_device_class(self, tracker):
        """Test sensor device class."""
        sensor = AnomalySensor(tracker)

        assert sensor.device_class == "problem"


class TestOccupiedInsideAreasSensor:
    """Test OccupiedInsideAreasSensor class."""

    def test_create_sensor(self, tracker):
        """Test creating sensor."""
        sensor = OccupiedInsideAreasSensor(tracker)

        assert sensor._attr_name == "Occupied Inside Areas"
        assert sensor._attr_unique_id == "occupied_inside_areas"

    def test_sensor_state(self, tracker):
        """Test sensor state shows count of occupied indoor areas."""
        tracker.areas["living_room"].occupancy = 1
        tracker.areas["bedroom"].occupancy = 2
        # porch is outdoor

        sensor = OccupiedInsideAreasSensor(tracker)

        assert sensor.state == 2

    def test_sensor_attributes(self, tracker):
        """Test sensor attributes list occupied areas."""
        tracker.areas["living_room"].occupancy = 1
        tracker.areas["bedroom"].occupancy = 1

        sensor = OccupiedInsideAreasSensor(tracker)
        attrs = sensor.extra_state_attributes

        assert "areas" in attrs
        assert len(attrs["areas"]) == 2
        assert "living_room" in attrs["areas"]
        assert "bedroom" in attrs["areas"]
        assert "porch" not in attrs["areas"]  # outdoor


class TestOccupiedOutsideAreasSensor:
    """Test OccupiedOutsideAreasSensor class."""

    def test_create_sensor(self, tracker):
        """Test creating sensor."""
        sensor = OccupiedOutsideAreasSensor(tracker)

        assert sensor._attr_name == "Occupied Outside Areas"
        assert sensor._attr_unique_id == "occupied_outside_areas"

    def test_sensor_state(self, tracker):
        """Test sensor state shows count of occupied outdoor areas."""
        tracker.areas["porch"].occupancy = 1

        sensor = OccupiedOutsideAreasSensor(tracker)

        assert sensor.state == 1

    def test_sensor_attributes(self, tracker):
        """Test sensor attributes list occupied outdoor areas."""
        tracker.areas["porch"].occupancy = 2

        sensor = OccupiedOutsideAreasSensor(tracker)
        attrs = sensor.extra_state_attributes

        assert "areas" in attrs
        assert "porch" in attrs["areas"]
        assert "living_room" not in attrs["areas"]  # indoor


class TestTotalOccupantsSensors:
    """Test total occupant sensors."""

    def test_total_occupants_inside(self, tracker):
        """Test total occupants inside sensor."""
        tracker.areas["living_room"].occupancy = 2
        tracker.areas["bedroom"].occupancy = 1

        sensor = TotalOccupantsInsideSensor(tracker)

        assert sensor._attr_name == "Total Occupants Inside"
        assert sensor.state == 3

    def test_total_occupants_outside(self, tracker):
        """Test total occupants outside sensor."""
        tracker.areas["porch"].occupancy = 2

        sensor = TotalOccupantsOutsideSensor(tracker)

        assert sensor._attr_name == "Total Occupants Outside"
        assert sensor.state == 2

    def test_total_occupants(self, tracker):
        """Test total occupants sensor."""
        tracker.areas["living_room"].occupancy = 2
        tracker.areas["bedroom"].occupancy = 1
        tracker.areas["porch"].occupancy = 1

        sensor = TotalOccupantsSensor(tracker)

        assert sensor._attr_name == "Total Occupants"
        assert sensor.state == 4


class TestAsyncSetupPlatform:
    """Test async_setup_platform function."""

    async def test_setup_platform(self, hass):
        """Test setting up the sensor platform."""
        from custom_components.occupancy_tracker import DOMAIN

        # Create tracker and add to hass.data
        config = {
            "areas": {
                "living_room": {"name": "Living Room"},
                "bedroom": {"name": "Bedroom"},
            },
            "adjacency": {},
            "sensors": {},
        }
        tracker = OccupancyTracker(config)

        hass.data[DOMAIN] = {"occupancy_tracker": tracker}

        # Mock async_add_entities
        async_add_entities = MagicMock()

        # Call setup
        await async_setup_platform(hass, config, async_add_entities)

        # Should create sensors
        assert async_add_entities.called
        sensors = async_add_entities.call_args[0][0]

        # Should have sensors for both areas plus global sensors
        # 2 areas * 2 sensors + 6 global sensors = 10 total
        assert len(sensors) >= 10

    async def test_setup_platform_creates_all_sensor_types(self, hass):
        """Test that all sensor types are created."""
        from custom_components.occupancy_tracker import DOMAIN

        config = {
            "areas": {"room1": {}},
            "adjacency": {},
            "sensors": {},
        }
        tracker = OccupancyTracker(config)

        hass.data[DOMAIN] = {"occupancy_tracker": tracker}

        async_add_entities = MagicMock()

        await async_setup_platform(hass, config, async_add_entities)

        sensors = async_add_entities.call_args[0][0]

        # Check that different sensor types are present
        sensor_types = {type(s).__name__ for s in sensors}

        assert "OccupancyCountSensor" in sensor_types
        assert "OccupancyProbabilitySensor" in sensor_types
        assert "AnomalySensor" in sensor_types
        assert "OccupiedInsideAreasSensor" in sensor_types
        assert "TotalOccupantsSensor" in sensor_types
