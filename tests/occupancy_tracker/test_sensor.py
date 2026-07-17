"""Tests for sensor and binary_sensor platforms."""

import time

import pytest
from unittest.mock import MagicMock, Mock

from homeassistant.core import HomeAssistant
from custom_components.occupancy_tracker.sensors import (
    AnomalySensor,
    AreaActivityBinarySensor,
    AreaOccupancyBinarySensor,
)
from custom_components.occupancy_tracker.sensor import (
    async_setup_platform as async_setup_sensor_platform,
)
from custom_components.occupancy_tracker.binary_sensor import (
    async_setup_platform as async_setup_binary_sensor_platform,
)
from custom_components.occupancy_tracker.coordinator import OccupancyCoordinator


def _set_occupancy(area, count):
    """Set area occupancy by adding test claims."""
    area.claims.clear()
    for i in range(count):
        area.claims.add(f"_test_{i}")


@pytest.fixture
def coordinator():
    """Create a test occupancy coordinator."""
    hass = Mock(spec=HomeAssistant)
    config = {
        "areas": {
            "living_room": {"name": "Living Room", "indoors": True},
            "bedroom": {"name": "Bedroom", "indoors": True},
            "porch": {"name": "Porch", "indoors": False},
        },
        "adjacency": {},
        "sensors": {
            "binary_sensor.living_room_motion": {
                "area": "living_room",
                "type": "motion",
            }
        },
    }
    return OccupancyCoordinator(hass, config, store=Mock())


class TestAreaOccupancyBinarySensor:
    """Test AreaOccupancyBinarySensor class."""

    def test_create_sensor(self, coordinator):
        """Test creating a binary occupancy sensor."""
        sensor = AreaOccupancyBinarySensor(coordinator, "living_room")

        assert sensor._area == "living_room"
        assert sensor._attr_name == "Living Room Occupancy"
        assert sensor._attr_unique_id == "occupancy_living_room"

    def test_is_on_when_occupied(self, coordinator):
        """Test sensor is ON when area is occupied."""
        _set_occupancy(coordinator.areas["living_room"], 1)

        sensor = AreaOccupancyBinarySensor(coordinator, "living_room")

        assert sensor.is_on is True

    def test_is_off_when_empty(self, coordinator):
        """Test sensor is OFF when area is empty."""
        sensor = AreaOccupancyBinarySensor(coordinator, "bedroom")

        assert sensor.is_on is False

    def test_attributes_include_count(self, coordinator):
        """Test attributes include occupancy count."""
        _set_occupancy(coordinator.areas["living_room"], 1)

        sensor = AreaOccupancyBinarySensor(coordinator, "living_room")
        attrs = sensor.extra_state_attributes

        assert attrs["occupancy_count"] == 1

    def test_attributes_include_probability(self, coordinator):
        """Test attributes expose freshness with a compatibility alias."""
        _set_occupancy(coordinator.areas["living_room"], 1)
        coordinator.areas["living_room"].record_motion(time.time())

        sensor = AreaOccupancyBinarySensor(coordinator, "living_room")
        attrs = sensor.extra_state_attributes

        assert attrs["freshness"] == 1.0
        assert attrs["probability"] == 1.0

    def test_attributes_explain_occupancy_evidence(self, coordinator):
        """Area attributes distinguish live evidence from a stale latch."""
        area = coordinator.areas["living_room"]
        sensor_state = coordinator.sensors["binary_sensor.living_room_motion"]
        sensor = AreaOccupancyBinarySensor(coordinator, "living_room")

        area.occupied = True
        area.record_motion(1000.0)
        sensor_state.current_state = True
        assert sensor.extra_state_attributes["evidence_state"] == "active"
        assert sensor.extra_state_attributes["active_sensors"] == [
            "binary_sensor.living_room_motion"
        ]

        sensor_state.current_state = False
        area.stale_since = 1010.0
        coordinator.occupancy_resolver.indoor_latched.add("living_room")
        attrs = sensor.extra_state_attributes
        assert attrs["evidence_state"] == "stale"
        assert attrs["last_positive_evidence"] == 1000.0
        assert attrs["stale_since"] == 1010.0

    def test_attributes_explain_inferred_and_vacant_states(self, coordinator):
        """Non-latched occupancy remains visibly distinct from vacancy."""
        sensor = AreaOccupancyBinarySensor(coordinator, "porch")

        assert sensor.extra_state_attributes["evidence_state"] == "vacant"

        coordinator.areas["porch"].occupied = True
        assert sensor.extra_state_attributes["evidence_state"] == "inferred"

    def test_attributes_include_area_properties(self, coordinator):
        """Test attributes include indoors and exit_capable."""
        sensor = AreaOccupancyBinarySensor(coordinator, "living_room")
        attrs = sensor.extra_state_attributes

        assert attrs["is_indoors"] is True
        assert attrs["is_exit_capable"] is False

    def test_attributes_time_since_motion(self, coordinator):
        """Test time_since_motion is None when no motion recorded."""
        sensor = AreaOccupancyBinarySensor(coordinator, "bedroom")
        attrs = sensor.extra_state_attributes

        assert attrs["last_motion"] is None
        assert attrs["time_since_motion_s"] is None

    def test_device_class(self, coordinator):
        """Test device class is occupancy."""
        sensor = AreaOccupancyBinarySensor(coordinator, "living_room")

        assert sensor.device_class == "occupancy"


class TestAreaActivityBinarySensor:
    """Test the short-lived, non-authoritative activity signal."""

    def test_recent_activity_expires_without_clearing_safe_occupancy(self, coordinator):
        area = coordinator.areas["living_room"]
        area.record_motion(1000.0)
        area.occupied = True
        coordinator.occupancy_resolver.indoor_latched.add("living_room")
        sensor = AreaActivityBinarySensor(coordinator, "living_room")

        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(
                "custom_components.occupancy_tracker.sensors.area_sensors.time.time",
                lambda: 1119.0,
            )
            assert sensor.is_on is True
            assert sensor.extra_state_attributes["activity_source"] == "recent"

            monkeypatch.setattr(
                "custom_components.occupancy_tracker.sensors.area_sensors.time.time",
                lambda: 1121.0,
            )
            assert sensor.is_on is False

        assert coordinator.get_occupancy("living_room") == 1

    def test_live_trusted_sensor_is_activity_even_with_old_timestamp(self, coordinator):
        sensor_state = coordinator.sensors["binary_sensor.living_room_motion"]
        sensor_state.current_state = True
        sensor = AreaActivityBinarySensor(coordinator, "living_room")

        assert sensor.is_on is True
        assert sensor.extra_state_attributes["activity_source"] == "live"

    def test_unavailable_sensor_is_not_live_activity(self, coordinator):
        sensor_state = coordinator.sensors["binary_sensor.living_room_motion"]
        sensor_state.current_state = True
        sensor_state.mark_unavailable(time.time())
        sensor = AreaActivityBinarySensor(coordinator, "living_room")

        assert sensor.is_on is False
        assert sensor.extra_state_attributes["activity_source"] == "none"

    def test_identity_and_device_class(self, coordinator):
        sensor = AreaActivityBinarySensor(coordinator, "living_room")

        assert sensor._attr_name == "Living Room Activity"
        assert sensor._attr_unique_id == "activity_living_room"
        assert sensor.device_class == "motion"


class TestAnomalySensor:
    """Test AnomalySensor class."""

    def test_create_sensor(self, coordinator):
        """Test creating an anomaly sensor."""
        sensor = AnomalySensor(coordinator)

        assert sensor._attr_name == "Detected Anomalies"
        assert sensor._attr_unique_id == "detected_anomalies"
        assert sensor._attr_icon == "mdi:alert-circle"

    def test_sensor_state_no_anomalies(self, coordinator):
        """Test sensor state when no anomalies."""
        sensor = AnomalySensor(coordinator)

        assert sensor.state == 0

    def test_sensor_state_with_anomalies(self, coordinator):
        """Test sensor state with active anomalies."""
        coordinator.anomaly_detector._create_warning("stuck_sensor", "Sensor stuck")
        coordinator.anomaly_detector._create_warning("unexpected_motion", "Unexpected")

        sensor = AnomalySensor(coordinator)

        assert sensor.state == 2

    def test_sensor_state_only_active_anomalies(self, coordinator):
        """Test sensor state counts only active anomalies."""
        w1 = coordinator.anomaly_detector._create_warning("type1", "Message 1")
        coordinator.anomaly_detector._create_warning("type2", "Message 2")

        w1.resolve()

        sensor = AnomalySensor(coordinator)

        assert sensor.state == 1

    def test_extra_state_attributes(self, coordinator):
        """Test extra state attributes."""
        coordinator.anomaly_detector._create_warning(
            "stuck_sensor",
            "Sensor stuck",
            area="bedroom",
            sensor_id="sensor.motion_1",
            timestamp=time.time(),
        )

        sensor = AnomalySensor(coordinator)
        attrs = sensor.extra_state_attributes

        assert "anomalies" in attrs
        assert len(attrs["anomalies"]) == 1
        assert attrs["anomalies"][0]["type"] == "stuck_sensor"
        assert attrs["anomalies"][0]["area"] == "bedroom"
        assert attrs["anomalies"][0]["sensor"] == "sensor.motion_1"

    def test_extra_state_attributes_counts(self, coordinator):
        """Test anomaly type counts in attributes."""
        coordinator.anomaly_detector._create_warning("stuck_sensor", "Sensor 1 stuck")
        coordinator.anomaly_detector._create_warning("stuck_sensor", "Sensor 2 stuck")
        coordinator.anomaly_detector._create_warning("unexpected_motion", "Unexpected")

        sensor = AnomalySensor(coordinator)
        attrs = sensor.extra_state_attributes

        assert attrs["anomaly_counts"]["stuck_sensor"] == 2
        assert attrs["anomaly_counts"]["unexpected_motion"] == 1

    def test_sensor_available(self, coordinator):
        """Test sensor is always available."""
        sensor = AnomalySensor(coordinator)

        assert sensor.available is True

    def test_sensor_device_class(self, coordinator):
        """Test sensor device class."""
        sensor = AnomalySensor(coordinator)

        assert sensor.device_class == "problem"


class TestAsyncSetupPlatforms:
    """Test platform setup functions."""

    async def test_binary_sensor_platform(self, hass):
        """Test binary sensor platform creates one entity per area."""
        from custom_components.occupancy_tracker.const import DOMAIN

        config = {
            "areas": {
                "living_room": {"name": "Living Room"},
                "bedroom": {"name": "Bedroom"},
            },
            "adjacency": {},
            "sensors": {},
        }
        coordinator = OccupancyCoordinator(hass, config)
        hass.data[DOMAIN] = {"coordinator": coordinator}

        async_add_entities = MagicMock()
        await async_setup_binary_sensor_platform(hass, config, async_add_entities)

        assert async_add_entities.called
        entities = async_add_entities.call_args[0][0]

        # One safe-occupancy and one short-lived activity sensor per area.
        assert len(entities) == 4
        assert sum(isinstance(e, AreaOccupancyBinarySensor) for e in entities) == 2
        assert sum(isinstance(e, AreaActivityBinarySensor) for e in entities) == 2

    async def test_sensor_platform(self, hass):
        """Test sensor platform creates only anomaly sensor."""
        from custom_components.occupancy_tracker.const import DOMAIN

        config = {
            "areas": {"room1": {}},
            "adjacency": {},
            "sensors": {},
        }
        coordinator = OccupancyCoordinator(hass, config)
        hass.data[DOMAIN] = {"coordinator": coordinator}

        async_add_entities = MagicMock()
        await async_setup_sensor_platform(hass, config, async_add_entities)

        entities = async_add_entities.call_args[0][0]

        assert len(entities) == 1
        assert isinstance(entities[0], AnomalySensor)
