import pytest
from unittest.mock import Mock
from homeassistant.core import HomeAssistant
from custom_components.occupancy_tracker.coordinator import OccupancyCoordinator


def _set_occupancy(area, count):
    """Set area occupancy by adding test claims."""
    area.claims.clear()
    for i in range(count):
        area.claims.add(f"_test_{i}")


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
    return OccupancyCoordinator(hass, config)


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


def test_check_timeouts_removes_phantom_from_resolver_retention():
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
    coordinator = OccupancyCoordinator(hass, config, enable_periodic_updates=False)

    _set_occupancy(coordinator.areas["bathroom"], 1)
    coordinator.areas["bathroom"].last_motion = now - 12000
    coordinator.areas["hallway"].last_motion = now - 3600
    coordinator.occupancy_resolver.retained["bathroom"] = now - 12000

    coordinator.check_timeouts(now)

    assert coordinator.areas["bathroom"].occupancy == 0
    assert "bathroom" not in coordinator.occupancy_resolver.retained
