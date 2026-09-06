"""Room-class behavior must affect decay without weakening safe occupancy."""

from unittest.mock import Mock

from homeassistant.core import HomeAssistant

from custom_components.occupancy_tracker.coordinator import OccupancyCoordinator
from custom_components.occupancy_tracker.helpers.anomaly_detector import AnomalyDetector
from custom_components.occupancy_tracker.helpers.room_profiles import ROOM_PROFILES
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


def test_an_unclassified_room_falls_back_to_the_default_profile():
    """A room without a profile gets the two-hour ceiling, not the living one."""
    coordinator = OccupancyCoordinator(
        Mock(spec=HomeAssistant),
        {"areas": {"utility": {}}, "adjacency": {}, "sensors": {}},
        store=Mock(),
    )

    assert coordinator.areas["utility"].profile_name == "default"
    engine = coordinator.occupancy_resolver.engine
    assert engine.profile_for("utility") == ROOM_PROFILES["default"]


def test_holds_and_retention_ceilings_are_per_room_class():
    """The ceiling is a policy number bounding a missed-exit ghost."""
    assert ROOM_PROFILES["transition"].hold_seconds == 30
    assert ROOM_PROFILES["default"].hold_seconds == 90
    assert ROOM_PROFILES["living"].hold_seconds == 90
    assert ROOM_PROFILES["sleeping"].hold_seconds == 90

    assert ROOM_PROFILES["transition"].retention_ceiling_seconds == 0
    assert ROOM_PROFILES["default"].retention_ceiling_seconds == 2 * 3600
    assert ROOM_PROFILES["living"].retention_ceiling_seconds == 4 * 3600
    assert ROOM_PROFILES["sleeping"].retention_ceiling_seconds == 12 * 3600


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
    for area_id in coordinator.areas:
        coordinator.process_sensor_event(f"sensor.{area_id}", True, 1_000.0)
        coordinator.process_sensor_event(f"sensor.{area_id}", False, 1_005.0)

    transition = coordinator.get_occupancy_freshness("corridor", 1_600.0)
    living = coordinator.get_occupancy_freshness("living", 1_600.0)
    sleeping = coordinator.get_occupancy_freshness("bedroom", 1_600.0)

    assert transition < living < sleeping
    assert all(area.occupied for area in coordinator.areas.values())


def test_transition_area_releases_at_its_hold_deadline():
    """A corridor has a 0 s retention ceiling, so its hold is final."""
    coordinator = _coordinator()
    hold = ROOM_PROFILES["transition"].hold_seconds
    coordinator.process_sensor_event("sensor.corridor", True, 1_000.0)
    coordinator.process_sensor_event("sensor.corridor", False, 1_005.0)

    coordinator.check_timeouts(1_005.0 + hold - 1)

    assert coordinator.get_occupancy("corridor") == 1

    coordinator.check_timeouts(1_005.0 + hold)

    assert coordinator.get_occupancy("corridor") == 0
    assert coordinator.get_room_state("corridor")["reason"] == "transition_room"


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
