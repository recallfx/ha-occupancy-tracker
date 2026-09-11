import pytest
from unittest.mock import Mock
from homeassistant.core import HomeAssistant
from custom_components.occupancy_tracker.coordinator import OccupancyCoordinator
from custom_components.occupancy_tracker.helpers.room_profiles import ROOM_PROFILES


@pytest.fixture
def coordinator():
    hass = Mock(spec=HomeAssistant)
    config = {
        "areas": {
            "living_room": {"name": "Living Room"},
            "kitchen": {"name": "Kitchen"},
        },
        "adjacency": {},
        "sensors": {},
    }
    return OccupancyCoordinator(hass, config, store=Mock())


def test_probability_decay(coordinator):
    area_id = "living_room"
    start_time = 1000.0

    # Initially empty
    assert coordinator.get_occupancy_probability(area_id, start_time) == 0.0

    # Record entry and motion directly on the area state (since we removed wrappers)
    area = coordinator.areas.get(area_id)
    area.record_entry(start_time)
    area.record_motion(start_time)

    # Check immediate probability (0-60s)
    assert coordinator.get_occupancy_probability(area_id, start_time + 10) == 1.0
    assert coordinator.get_occupancy_probability(area_id, start_time + 59) == 1.0

    # Check medium probability (1-5 mins)
    assert coordinator.get_occupancy_probability(area_id, start_time + 61) == 0.9
    assert coordinator.get_occupancy_probability(area_id, start_time + 299) == 0.9

    # Check decay (> 5 mins)
    # At 5 mins + 1 sec, it should be just below 0.9
    prob_start_decay = coordinator.get_occupancy_probability(area_id, start_time + 301)
    assert 0.8 < prob_start_decay <= 0.9

    # At 30 mins (1800s total, 1500s decay)
    # P = 0.1 + 0.8 * exp(-0.00021 * 1500)
    # P = 0.1 + 0.8 * 0.729 = 0.1 + 0.58 = 0.68
    prob_30_mins = coordinator.get_occupancy_probability(area_id, start_time + 1800)
    assert 0.6 < prob_30_mins < 0.8

    # At 60 mins (3600s total, 3300s decay)
    # P = 0.1 + 0.8 * exp(-0.00021 * 3300)
    # P = 0.1 + 0.8 * 0.5 = 0.5
    prob_60_mins = coordinator.get_occupancy_probability(area_id, start_time + 3600)
    assert 0.45 < prob_60_mins < 0.55

    # New motion should reset probability
    area.record_motion(start_time + 4000)
    assert coordinator.get_occupancy_probability(area_id, start_time + 4001) == 1.0


def test_probability_zero_occupancy(coordinator):
    area_id = "living_room"
    start_time = 1000.0

    # Record motion but no entry (occupancy 0)
    area = coordinator.areas.get(area_id)
    area.record_motion(start_time)

    # Should be 0 because occupancy is 0
    assert coordinator.get_occupancy_probability(area_id, start_time) == 0.0


def test_unknown_area(coordinator):
    assert coordinator.get_occupancy_probability("unknown_area") == 0.0


def test_default_coordinator_has_no_duplicate_periodic_loop(coordinator):
    """The integration-owned 60-second timer is the only periodic loop."""
    assert coordinator.update_interval is None


def test_probability_is_a_compatibility_alias_for_freshness(coordinator):
    area = coordinator.areas["living_room"]
    area.record_entry(1000.0)
    area.record_motion(1000.0)

    assert coordinator.get_occupancy_freshness("living_room", 1061.0) == 0.9
    assert coordinator.get_occupancy_probability("living_room", 1061.0) == 0.9


def test_check_timeouts_retains_a_room_with_no_departure_trail():
    """Uncertainty is retained until the ceiling, then released."""
    hass = Mock(spec=HomeAssistant)
    now = 100000.0
    config = {
        "areas": {
            "bathroom": {"name": "Bathroom"},
            "hallway": {"name": "Hallway"},
        },
        "adjacency": {
            "bathroom": ["hallway"],
        },
        "sensors": {
            "sensor.bathroom_motion": {"area": "bathroom", "type": "motion"},
            "sensor.hallway_motion": {"area": "hallway", "type": "motion"},
        },
    }
    coordinator = OccupancyCoordinator(hass, config, store=Mock())
    profile = ROOM_PROFILES["default"]

    coordinator.process_sensor_event("sensor.bathroom_motion", True, now)
    coordinator.process_sensor_event("sensor.bathroom_motion", False, now + 5)

    # No exit fired, so the hold turns into retention.
    coordinator.check_timeouts(now + 5 + profile.hold_seconds)

    assert coordinator.areas["bathroom"].occupancy == 1
    assert coordinator.get_room_state("bathroom")["state"] == "retained"

    # The retention ceiling still bounds the room.
    coordinator.check_timeouts(now + 5 + profile.retention_ceiling_seconds)

    assert coordinator.areas["bathroom"].occupancy == 0
    assert coordinator.get_room_state("bathroom")["reason"] == "retention_ceiling"


def test_periodic_check_marks_long_running_on_sensor_stuck():
    """Stuck detection cannot depend on a different sensor firing later."""
    hass = Mock(spec=HomeAssistant)
    now = 200_000.0
    config = {
        "areas": {"hall": {}},
        "adjacency": {},
        "sensors": {"sensor.hall": {"area": "hall", "type": "motion"}},
    }
    coordinator = OccupancyCoordinator(hass, config, store=Mock())
    coordinator.process_sensor_event("sensor.hall", True, now - 25 * 3600)

    coordinator.check_timeouts(now)

    assert coordinator.sensors["sensor.hall"].is_stuck is True
    assert coordinator.sensors["sensor.hall"].is_reliable is False
    assert any(w.type == "stuck_sensor" for w in coordinator.get_warnings())
