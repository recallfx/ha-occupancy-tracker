"""Safety invariants for conservative room occupancy."""

import time

from custom_components.occupancy_tracker.helpers.anomaly_detector import (
    AnomalyDetector,
)
from custom_components.occupancy_tracker.helpers.area_state import AreaState
from custom_components.occupancy_tracker.helpers.map_occupancy_resolver import (
    MapOccupancyResolver,
)
from custom_components.occupancy_tracker.helpers.map_state_recorder import MapSnapshot
from custom_components.occupancy_tracker.helpers.occupancy_engine import STATE_VACANT
from custom_components.occupancy_tracker.helpers.sensor_state import SensorState


def _event(sensor_id: str, on: bool, timestamp: float) -> MapSnapshot:
    return MapSnapshot(
        timestamp=timestamp,
        event_type="sensor",
        description=f"sensor:{sensor_id}:{'on' if on else 'off'}",
        areas={},
        sensors={},
    )


def _fire(resolver, areas, sensors, sensor_id, on, timestamp, detector=None):
    sensors[sensor_id].update_state(on, timestamp)
    resolver.process_snapshot(
        _event(sensor_id, on, timestamp), areas, sensors, detector
    )


def test_unexplained_indoor_motion_is_occupied_immediately():
    """Adjacency plausibility may warn, but must not reject positive evidence."""
    now = time.time()
    config = {
        "areas": {"entrance": {"exit_capable": True}, "hall": {}, "bedroom": {}},
        "adjacency": {"entrance": ["hall"], "hall": ["bedroom"]},
        "sensors": {},
    }
    resolver = MapOccupancyResolver(config)
    detector = AnomalyDetector(config)
    areas = {
        area_id: AreaState(area_id, value) for area_id, value in config["areas"].items()
    }
    sensors = {
        "s.entrance": SensorState(
            "s.entrance", {"area": "entrance", "type": "motion"}, now
        ),
        "s.bedroom": SensorState(
            "s.bedroom", {"area": "bedroom", "type": "motion"}, now
        ),
    }

    _fire(resolver, areas, sensors, "s.entrance", True, now, detector)
    _fire(resolver, areas, sensors, "s.entrance", False, now + 5, detector)
    _fire(resolver, areas, sensors, "s.bedroom", True, now + 200, detector)

    assert areas["bedroom"].occupied
    assert any(w.type == "unexpected_motion" for w in detector.get_warnings())


def test_all_simultaneously_active_rooms_are_occupied():
    """A movement cluster cannot turn ON sensors into vacant rooms."""
    now = time.time()
    config = {
        "areas": {
            "bedroom_1": {},
            "corridor": {"transition": True},
            "bedroom_2": {},
        },
        "adjacency": {"bedroom_1": ["corridor"], "corridor": ["bedroom_2"]},
        "sensors": {},
    }
    resolver = MapOccupancyResolver(config)
    areas = {
        area_id: AreaState(area_id, value) for area_id, value in config["areas"].items()
    }
    sensors = {
        f"s.{area_id}": SensorState(
            f"s.{area_id}", {"area": area_id, "type": "motion"}, now
        )
        for area_id in areas
    }

    _fire(resolver, areas, sensors, "s.bedroom_1", True, now)
    _fire(resolver, areas, sensors, "s.corridor", True, now + 1)
    _fire(resolver, areas, sensors, "s.bedroom_2", True, now + 2)

    assert all(area.occupied for area in areas.values())


def test_departure_trail_releases_the_room_at_its_deadline():
    """An exit activation right after the room falls silent is a departure.

    The accepted error is the two-person case: if one occupant leaves and
    another stays without moving, the room reads empty until the person who
    stayed triggers the sensor again.
    """
    now = time.time()
    config = {
        "areas": {"bedroom": {}, "corridor": {"transition": True}, "kitchen": {}},
        "adjacency": {"bedroom": ["corridor"], "corridor": ["kitchen"]},
        "sensors": {},
    }
    resolver = MapOccupancyResolver(config)
    areas = {
        area_id: AreaState(area_id, value) for area_id, value in config["areas"].items()
    }
    sensors = {
        f"s.{area_id}": SensorState(
            f"s.{area_id}", {"area": area_id, "type": "motion"}, now
        )
        for area_id in areas
    }

    _fire(resolver, areas, sensors, "s.bedroom", True, now)
    _fire(resolver, areas, sensors, "s.bedroom", False, now + 5)
    _fire(resolver, areas, sensors, "s.corridor", True, now + 10)
    _fire(resolver, areas, sensors, "s.corridor", False, now + 15)

    # The hold has not expired yet, so the room stays occupied for now.
    assert areas["bedroom"].occupied

    _fire(resolver, areas, sensors, "s.kitchen", True, now + 200)

    assert not areas["bedroom"].occupied
    assert resolver.engine.rooms["bedroom"].reason == "departure_trail"


def test_long_quiet_indoor_room_is_warned_but_not_cleared():
    """A timeout cannot distinguish an empty bedroom from a sleeping occupant."""
    now = time.time()
    config = {"areas": {"bedroom": {}}, "adjacency": {}, "sensors": {}}
    detector = AnomalyDetector(config)
    bedroom = AreaState("bedroom", {})
    bedroom.occupied = True
    bedroom.last_motion = now - (25 * 3600)

    result = detector.check_timeouts({"bedroom": bedroom}, now)

    assert result is None
    assert bedroom.occupied
    assert detector.get_warnings()


def test_explicit_clear_removes_only_stale_indoor_occupancy():
    """Manual cleanup clears stale rooms while preserving active evidence."""
    now = time.time()
    config = {
        "areas": {"stale": {}, "active": {}},
        "adjacency": {},
        "sensors": {},
    }
    resolver = MapOccupancyResolver(config)
    areas = {
        area_id: AreaState(area_id, value) for area_id, value in config["areas"].items()
    }
    sensors = {
        f"s.{area_id}": SensorState(
            f"s.{area_id}", {"area": area_id, "type": "motion"}, now
        )
        for area_id in areas
    }

    _fire(resolver, areas, sensors, "s.stale", True, now)
    _fire(resolver, areas, sensors, "s.stale", False, now + 5)
    _fire(resolver, areas, sensors, "s.active", True, now + 10)

    cleared = resolver.clear_stale_indoor_occupancy(now + 20, areas, sensors)

    assert cleared == ["stale"]
    assert not areas["stale"].occupied
    assert areas["stale"].cleared_by == "manual_clear"
    assert areas["active"].occupied


def test_targeted_clear_does_not_clear_other_stale_rooms():
    """Per-room cleanup must not turn into a whole-house absence assertion."""
    now = time.time()
    config = {
        "areas": {"study": {}, "bedroom": {}},
        "adjacency": {},
        "sensors": {},
    }
    resolver = MapOccupancyResolver(config)
    areas = {
        area_id: AreaState(area_id, value) for area_id, value in config["areas"].items()
    }
    sensors = {
        f"s.{area_id}": SensorState(
            f"s.{area_id}", {"area": area_id, "type": "motion"}, now
        )
        for area_id in areas
    }
    for index, area_id in enumerate(areas):
        _fire(resolver, areas, sensors, f"s.{area_id}", True, now + index)
        _fire(resolver, areas, sensors, f"s.{area_id}", False, now + index + 0.5)

    cleared = resolver.clear_stale_indoor_occupancy(
        now + 10,
        areas,
        sensors,
        area_ids={"study"},
    )

    assert cleared == ["study"]
    assert not areas["study"].occupied
    assert areas["bedroom"].occupied


def test_explicit_clear_survives_history_replay():
    """Replaying sensor history must not resurrect manually cleared occupancy."""
    now = time.time()
    config = {"areas": {"room": {}}, "adjacency": {}, "sensors": {}}
    resolver = MapOccupancyResolver(config)
    areas = {"room": AreaState("room", {})}
    sensors = {"s.room": SensorState("s.room", {"area": "room", "type": "motion"}, now)}
    history = [
        _event("s.room", True, now),
        _event("s.room", False, now + 5),
        MapSnapshot(
            timestamp=now + 10,
            event_type="clear",
            description="clear:room",
            areas={},
            sensors={},
        ),
    ]

    resolver.recalculate_from_history(history, areas, sensors)

    assert not areas["room"].occupied
    assert resolver.engine.rooms["room"].state == STATE_VACANT
