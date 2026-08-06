"""Room-class behavior must affect decay without weakening safe occupancy."""

from unittest.mock import Mock

from homeassistant.core import HomeAssistant

from custom_components.occupancy_tracker.coordinator import OccupancyCoordinator
from custom_components.occupancy_tracker.helpers.anomaly_detector import AnomalyDetector
from custom_components.occupancy_tracker.sensors import AreaActivityBinarySensor


def _coordinator() -> OccupancyCoordinator:
    config = {
        "areas": {
            "corridor": {"transition": True},
            "living": {"profile": "living"},
            "bedroom": {"profile": "sleeping"},
        },
        "adjacency": {},
        "sensors": {
            "sensor.corridor": {"area": "corridor", "type": "motion"},
            "sensor.living": {"area": "living", "type": "motion"},
            "sensor.bedroom": {"area": "bedroom", "type": "motion"},
        },
    }
    return OccupancyCoordinator(Mock(spec=HomeAssistant), config, store=Mock())


def test_profiles_are_explicit_and_transition_is_backwards_compatible():
    coordinator = _coordinator()

    assert coordinator.areas["corridor"].profile_name == "transition"
    assert coordinator.areas["living"].profile_name == "living"
    assert coordinator.areas["bedroom"].profile_name == "sleeping"


def test_activity_holds_are_ordered_by_room_class(monkeypatch):
    coordinator = _coordinator()
    for area in coordinator.areas.values():
        area.record_motion(1_000.0)

    monkeypatch.setattr(
        "custom_components.occupancy_tracker.sensors.area_sensors.time.time",
        lambda: 1_060.0,
    )

    assert AreaActivityBinarySensor(coordinator, "corridor").is_on is False
    assert AreaActivityBinarySensor(coordinator, "living").is_on is True
    assert AreaActivityBinarySensor(coordinator, "bedroom").is_on is True


def test_freshness_decays_fastest_in_transition_and_slowest_in_sleeping_room():
    coordinator = _coordinator()
    for area_id, area in coordinator.areas.items():
        area.record_entry(1_000.0)
        area.record_motion(1_000.0)
        coordinator.occupancy_resolver.indoor_latched.add(area_id)

    transition = coordinator.get_occupancy_freshness("corridor", 1_600.0)
    living = coordinator.get_occupancy_freshness("living", 1_600.0)
    sleeping = coordinator.get_occupancy_freshness("bedroom", 1_600.0)

    assert transition < living < sleeping
    assert all(area.occupied for area in coordinator.areas.values())


def test_decay_never_clears_transition_area_safe_occupancy():
    coordinator = _coordinator()
    coordinator.process_sensor_event("sensor.corridor", True, 1_000.0)
    coordinator.process_sensor_event("sensor.corridor", False, 1_005.0)

    coordinator.check_timeouts(100_000.0)

    assert coordinator.get_occupancy("corridor") == 1
    assert "corridor" in coordinator.occupancy_resolver.indoor_latched


def test_transition_diagnostics_age_faster_than_living_and_sleeping_rooms():
    coordinator = _coordinator()
    for area in coordinator.areas.values():
        area.record_entry(1_000.0)
        area.record_motion(1_000.0)
    detector = AnomalyDetector(coordinator.config)

    detector.check_timeouts(coordinator.areas, 1_000.0 + 31 * 60)

    extended_areas = {
        warning.area
        for warning in detector.get_warnings()
        if warning.type == "extended_occupancy"
    }
    assert extended_areas == {"corridor"}
