"""Tests for integration setup and initialization."""

import pytest
from unittest.mock import patch
import time

from homeassistant.core import HomeAssistant, Event, State
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN

from custom_components.occupancy_tracker import (
    async_setup,
    DOMAIN,
)
from custom_components.occupancy_tracker.coordinator import OccupancyCoordinator


@pytest.fixture
def sample_config():
    """Provide a sample configuration."""
    return {
        DOMAIN: {
            "areas": {
                "living_room": {"name": "Living Room", "indoors": True},
                "kitchen": {"name": "Kitchen", "indoors": True},
            },
            "adjacency": {
                "living_room": ["kitchen"],
            },
            "sensors": {
                "binary_sensor.motion_living": {
                    "area": "living_room",
                    "type": "motion",
                },
                "binary_sensor.motion_kitchen": {"area": "kitchen", "type": "motion"},
            },
        }
    }


class TestAsyncSetup:
    """Test async_setup function."""

    async def test_setup_success(self, hass: HomeAssistant, sample_config):
        """Test successful setup of the integration."""
        result = await async_setup(hass, sample_config)

        assert result is True
        assert DOMAIN in hass.data
        assert "coordinator" in hass.data[DOMAIN]

    async def test_setup_creates_occupancy_coordinator(
        self, hass: HomeAssistant, sample_config
    ):
        """Test that setup creates the occupancy coordinator."""
        await async_setup(hass, sample_config)

        coordinator = hass.data[DOMAIN]["coordinator"]

        assert len(coordinator.areas) == 2
        assert len(coordinator.sensors) == 2
        assert "living_room" in coordinator.areas
        assert "kitchen" in coordinator.areas

    async def test_setup_seeds_current_on_sensor_state(
        self, hass: HomeAssistant, sample_config
    ):
        """An already-active sensor must survive an integration restart."""
        hass.states.async_set("binary_sensor.motion_living", STATE_ON)

        await async_setup(hass, sample_config)

        coordinator = hass.data[DOMAIN]["coordinator"]
        assert coordinator.sensors["binary_sensor.motion_living"].current_state is True
        assert coordinator.get_occupancy("living_room") == 1

    async def test_setup_does_not_replay_open_magnetic_sensor(
        self, hass: HomeAssistant, sample_config
    ):
        """An open door at startup is state, not a fresh door event."""
        entity_id = "binary_sensor.living_room_door"
        sample_config[DOMAIN]["sensors"][entity_id] = {
            "area": "living_room",
            "type": "door",
        }
        hass.states.async_set(entity_id, STATE_ON)

        await async_setup(hass, sample_config)

        coordinator = hass.data[DOMAIN]["coordinator"]
        assert coordinator.sensors[entity_id].current_state is True
        assert coordinator.sensors[entity_id].is_available is True
        assert coordinator.areas["living_room"].last_motion == 0
        assert not any(
            snapshot.event_type == "sensor"
            for snapshot in coordinator.state_recorder.get_history()
        )

        hass.states.async_set(entity_id, STATE_OFF)
        await hass.async_block_till_done()

        assert coordinator.sensors[entity_id].current_state is False
        sensor_history = [
            snapshot
            for snapshot in coordinator.state_recorder.get_history()
            if snapshot.event_type == "sensor"
        ]
        assert len(sensor_history) == 1
        assert sensor_history[0].description == f"sensor:{entity_id}:off"
        assert coordinator.verify_history() is True

    async def test_setup_no_configuration(self, hass: HomeAssistant):
        """Test setup with no configuration."""
        config = {}

        result = await async_setup(hass, config)

        assert result is False

    async def test_setup_initializes_config(self, hass: HomeAssistant, sample_config):
        """Test that configuration is properly initialized."""
        await async_setup(hass, sample_config)

        coordinator = hass.data[DOMAIN]["coordinator"]

        # Check areas
        assert coordinator.config["areas"]["living_room"]["name"] == "Living Room"

        # Check sensors
        assert (
            coordinator.config["sensors"]["binary_sensor.motion_living"]["area"]
            == "living_room"
        )

        # Check adjacency
        assert "kitchen" in coordinator.config["adjacency"]["living_room"]

    @patch("custom_components.occupancy_tracker.async_load_platform")
    async def test_setup_loads_platforms(
        self, mock_load_platform, hass: HomeAssistant, sample_config
    ):
        """Test that setup loads sensor and button platforms."""
        await async_setup(hass, sample_config)

        # Should load binary_sensor, sensor, and button platforms
        assert mock_load_platform.call_count == 3

        calls = [call[0] for call in mock_load_platform.call_args_list]
        platforms = [call[1] for call in calls]

        assert "binary_sensor" in platforms
        assert "sensor" in platforms
        assert "button" in platforms


class TestStateChangeListener:
    """Test state change event handling."""

    async def test_state_change_listener_on_state(
        self, hass: HomeAssistant, sample_config
    ):
        """Test state change listener processes ON state."""
        await async_setup(hass, sample_config)

        coordinator = hass.data[DOMAIN]["coordinator"]

        # Create a state change event
        new_state = State("binary_sensor.motion_living", STATE_ON)
        event_data = {
            "entity_id": "binary_sensor.motion_living",
            "new_state": new_state,
        }

        Event("state_changed", event_data)

        # Manually trigger the listener
        # (In real HA, this would be triggered by async_track_state_change_event)
        timestamp_before = coordinator.last_event_time

        # Simulate processing the event
        coordinator.process_sensor_event(
            "binary_sensor.motion_living", True, time.time()
        )

        # Event should have been processed
        assert coordinator.last_event_time > timestamp_before

    async def test_state_change_listener_off_state(
        self, hass: HomeAssistant, sample_config
    ):
        """Test state change listener processes OFF state."""
        await async_setup(hass, sample_config)

        coordinator = hass.data[DOMAIN]["coordinator"]

        # Set sensor to ON first
        coordinator.process_sensor_event(
            "binary_sensor.motion_living", True, time.time()
        )

        # Then to OFF
        coordinator.process_sensor_event(
            "binary_sensor.motion_living", False, time.time()
        )

        # Sensor state should be False
        assert coordinator.sensors["binary_sensor.motion_living"].current_state is False

    async def test_state_change_listener_unknown_sensor(
        self, hass: HomeAssistant, sample_config
    ):
        """Test state change listener handles unknown sensor gracefully."""
        await async_setup(hass, sample_config)

        coordinator = hass.data[DOMAIN]["coordinator"]

        # Process event from unknown sensor (should not raise error)
        coordinator.process_sensor_event("binary_sensor.unknown", True, time.time())

    @pytest.mark.parametrize("invalid_state", [STATE_UNAVAILABLE, STATE_UNKNOWN])
    @pytest.mark.parametrize(
        ("recovery_state", "expected_active"),
        [(STATE_OFF, False), (STATE_ON, True)],
    )
    async def test_invalid_sensor_state_is_not_trusted_as_active(
        self,
        hass: HomeAssistant,
        sample_config,
        invalid_state,
        recovery_state,
        expected_active,
    ):
        """Unavailable sensors must not remain trusted as active."""
        await async_setup(hass, sample_config)
        coordinator = hass.data[DOMAIN]["coordinator"]
        entity_id = "binary_sensor.motion_living"

        hass.states.async_set(entity_id, STATE_ON)
        await hass.async_block_till_done()
        sensor = coordinator.sensors[entity_id]
        assert sensor.current_state is True
        last_changed = sensor.last_changed
        activated_at = sensor.activated_at
        sensor_history = list(sensor.history)
        snapshot_count = len(coordinator.state_recorder.get_history())

        hass.states.async_set(entity_id, invalid_state)
        await hass.async_block_till_done()

        assert sensor.current_state is True
        assert sensor.activated_at == activated_at
        assert sensor.is_available is False
        assert sensor.is_reliable is True
        assert sensor.is_trusted_active is False
        assert sensor.last_changed == last_changed
        assert sensor.history == sensor_history
        assert coordinator.get_occupancy("living_room") == 1
        snapshots = coordinator.state_recorder.get_history()
        assert len(snapshots) == snapshot_count + 1
        assert snapshots[-1].event_type == "availability"
        assert snapshots[-1].description == (f"availability:{entity_id}:unavailable")

        hass.states.async_set(entity_id, recovery_state)
        await hass.async_block_till_done()

        assert sensor.current_state is expected_active
        assert sensor.is_available is True
        assert sensor.is_reliable is True
        if recovery_state == STATE_ON:
            assert sensor.last_changed == last_changed
        else:
            assert sensor.last_changed > last_changed
        assert coordinator.verify_history() is True

    async def test_unavailable_sensor_keeps_history_replay_deterministic(
        self, hass: HomeAssistant, sample_config
    ):
        """Other sensor events during an outage must replay the same result."""
        await async_setup(hass, sample_config)
        coordinator = hass.data[DOMAIN]["coordinator"]

        hass.states.async_set("binary_sensor.motion_living", STATE_ON)
        await hass.async_block_till_done()
        hass.states.async_set("binary_sensor.motion_living", STATE_UNAVAILABLE)
        await hass.async_block_till_done()
        hass.states.async_set("binary_sensor.motion_kitchen", STATE_ON)
        await hass.async_block_till_done()

        assert coordinator.verify_history() is True

    async def test_repeated_on_from_stuck_sensor_does_not_refresh_evidence(
        self, hass: HomeAssistant, sample_config
    ):
        """An unreliable same-state ON is not a presence keep-alive."""
        await async_setup(hass, sample_config)
        coordinator = hass.data[DOMAIN]["coordinator"]
        entity_id = "binary_sensor.motion_living"

        hass.states.async_set(entity_id, STATE_ON)
        await hass.async_block_till_done()
        sensor = coordinator.sensors[entity_id]
        sensor.is_stuck = True
        sensor.is_reliable = False
        last_motion = coordinator.areas["living_room"].last_motion
        snapshot_count = len(coordinator.state_recorder.get_history())

        hass.states.async_set(entity_id, STATE_ON, {"heartbeat": 1})
        await hass.async_block_till_done()

        assert coordinator.areas["living_room"].last_motion == last_motion
        assert len(coordinator.state_recorder.get_history()) == snapshot_count

    def test_stuck_sensor_trust_state_replays_deterministically(
        self, hass: HomeAssistant, sample_config
    ):
        """A stuck transition must affect live and replayed rebuilds equally."""
        coordinator = OccupancyCoordinator(
            hass,
            sample_config[DOMAIN],
            enable_periodic_updates=False,
        )
        now = time.time()
        old = now - 25 * 3600

        coordinator.process_sensor_event(
            "binary_sensor.motion_living", True, timestamp=old
        )
        coordinator.process_sensor_event(
            "binary_sensor.motion_kitchen", True, timestamp=now
        )
        assert coordinator.sensors["binary_sensor.motion_living"].is_reliable is False

        coordinator.process_sensor_event(
            "binary_sensor.motion_kitchen", False, timestamp=now + 1
        )

        assert coordinator.verify_history() is True


class TestIntegrationConfiguration:
    """Test various configuration scenarios."""

    async def test_minimal_configuration(self, hass: HomeAssistant):
        """Test setup with minimal configuration."""
        config = {
            DOMAIN: {
                "areas": {"room1": {}},
                "adjacency": {},
                "sensors": {},
            }
        }

        result = await async_setup(hass, config)

        assert result is True
        coordinator = hass.data[DOMAIN]["coordinator"]
        assert len(coordinator.areas) == 1

    async def test_configuration_with_multiple_sensor_types(self, hass: HomeAssistant):
        """Test configuration with different sensor types."""
        config = {
            DOMAIN: {
                "areas": {
                    "living_room": {},
                    "hallway": {},
                    "front_porch": {},
                },
                "adjacency": {},
                "sensors": {
                    "binary_sensor.motion_1": {"area": "living_room", "type": "motion"},
                    "binary_sensor.camera_person": {
                        "area": "front_porch",
                        "type": "camera_person",
                    },
                    "binary_sensor.door": {
                        "type": "magnetic",
                        "between_areas": ["living_room", "hallway"],
                    },
                },
            }
        }

        result = await async_setup(hass, config)

        assert result is True
        coordinator = hass.data[DOMAIN]["coordinator"]
        assert len(coordinator.sensors) == 3

    async def test_configuration_with_outdoor_areas(self, hass: HomeAssistant):
        """Test configuration with indoor and outdoor areas."""
        config = {
            DOMAIN: {
                "areas": {
                    "living_room": {"indoors": True},
                    "porch": {"indoors": False, "exit_capable": True},
                },
                "adjacency": {},
                "sensors": {},
            }
        }

        result = await async_setup(hass, config)

        assert result is True
        coordinator = hass.data[DOMAIN]["coordinator"]
        assert coordinator.areas["living_room"].is_indoors is True
        assert coordinator.areas["porch"].is_indoors is False
        assert coordinator.areas["porch"].is_exit_capable is True

    async def test_configuration_with_complex_adjacency(self, hass: HomeAssistant):
        """Test configuration with complex adjacency graph."""
        config = {
            DOMAIN: {
                "areas": {
                    "living_room": {},
                    "kitchen": {},
                    "hallway": {},
                    "bedroom": {},
                },
                "adjacency": {
                    "living_room": ["kitchen", "hallway"],
                    "kitchen": ["living_room", "hallway"],
                    "hallway": ["living_room", "kitchen", "bedroom"],
                    "bedroom": ["hallway"],
                },
                "sensors": {},
            }
        }

        result = await async_setup(hass, config)

        assert result is True
        coordinator = hass.data[DOMAIN]["coordinator"]

        # Check adjacency is properly set up
        assert "kitchen" in coordinator.config["adjacency"]["living_room"]
        assert "bedroom" in coordinator.config["adjacency"]["hallway"]


class TestIntegrationDataFlow:
    """Test data flow through the integration."""

    async def test_sensor_event_updates_area_state(
        self, hass: HomeAssistant, sample_config
    ):
        """Test that sensor events update last_motion for occupied areas."""
        await async_setup(hass, sample_config)

        coordinator = hass.data[DOMAIN]["coordinator"]

        # Bootstrap: seed living_room as occupied
        t0 = time.time()
        coordinator.areas["living_room"].record_entry(t0)
        coordinator.sensors["binary_sensor.motion_living"].update_state(True, t0)
        coordinator.areas["living_room"].last_motion = t0

        # Re-trigger while occupied — last_motion should update
        t1 = t0 + 5
        coordinator.process_sensor_event("binary_sensor.motion_living", False, t0 + 2)
        coordinator.process_sensor_event("binary_sensor.motion_living", True, t1)

        assert coordinator.areas["living_room"].last_motion == t1

    async def test_multiple_sensor_events(self, hass: HomeAssistant, sample_config):
        """Test processing events across adjacent areas."""
        await async_setup(hass, sample_config)

        coordinator = hass.data[DOMAIN]["coordinator"]

        # Bootstrap: seed living_room as occupied
        t1 = time.time()
        coordinator.areas["living_room"].record_entry(t1)
        coordinator.sensors["binary_sensor.motion_living"].update_state(True, t1)
        coordinator.areas["living_room"].last_motion = t1

        # Kitchen motion — living_room is adjacent and occupied, so accepted
        t2 = t1 + 2
        coordinator.process_sensor_event("binary_sensor.motion_kitchen", True, t2)

        assert coordinator.areas["living_room"].last_motion == t1
        assert coordinator.areas["kitchen"].last_motion == t2

    async def test_occupancy_tracking_through_integration(
        self, hass: HomeAssistant, sample_config
    ):
        """Test end-to-end occupancy tracking."""
        await async_setup(hass, sample_config)

        coordinator = hass.data[DOMAIN]["coordinator"]

        # Bootstrap: directly set up occupancy in living_room.
        # The motion-ON entry logic rejects phantom entries in indoor
        # non-exit-capable areas with no plausible adjacent source,
        # so we seed the initial state directly.
        ts = time.time()
        coordinator.areas["living_room"].record_entry(ts)
        coordinator.sensors["binary_sensor.motion_living"].update_state(True, ts)
        coordinator.areas["living_room"].last_motion = ts

        # Should have occupancy
        assert coordinator.get_occupancy("living_room") >= 1
