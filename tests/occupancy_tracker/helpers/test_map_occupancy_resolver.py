"""Tests for MapOccupancyResolver claim-based occupancy resolution."""

import time

from custom_components.occupancy_tracker.helpers.map_occupancy_resolver import (
    MapOccupancyResolver,
)
from custom_components.occupancy_tracker.helpers.anomaly_detector import (
    AnomalyDetector,
)
from custom_components.occupancy_tracker.helpers.area_state import AreaState
from custom_components.occupancy_tracker.helpers.sensor_state import SensorState
from custom_components.occupancy_tracker.helpers.map_state_recorder import MapSnapshot


def _sensor_event(sensor_id: str, on: bool, ts: float) -> MapSnapshot:
    return MapSnapshot(
        timestamp=ts,
        event_type="sensor",
        description=f"sensor:{sensor_id}:{'on' if on else 'off'}",
        areas={},
        sensors={},
    )


def _fire(resolver, sensors, areas, sensor_id, on, ts, detector=None):
    """Update sensor state then process snapshot (mirrors coordinator behavior)."""
    sensor = sensors.get(sensor_id)
    if sensor:
        sensor.update_state(on, ts)
    resolver.process_snapshot(
        _sensor_event(sensor_id, on, ts), areas, sensors, detector
    )


def _set_occupancy(area, count):
    """Set area occupancy by adding test claims."""
    area.claims.clear()
    for i in range(count):
        area.claims.add(f"_test_{i}")


def test_unavailable_on_sensor_is_not_active_evidence():
    """Last-known ON is ignored until the sensor becomes available again."""
    now = time.time()
    resolver = MapOccupancyResolver({"areas": {"room": {}}, "sensors": {}})
    sensor = SensorState("s.room", {"area": "room", "type": "motion"}, now)
    sensor.update_state(True, now + 1)
    sensor.mark_unavailable(now + 2)
    sensors = {sensor.id: sensor}

    assert resolver._compute_sensor_active_areas(sensors) == set()
    assert resolver._is_area_active("room", sensors) is False


# ============================================================
# Transfer-on-ON: basic claim transfer tests
# ============================================================


def test_motion_on_does_not_clear_adjacent_source():
    """Destination motion is additive because another person may remain."""
    now = time.time()
    config = {
        "areas": {
            "area_a": {"name": "A", "exit_capable": True},
            "area_b": {"name": "B"},
        },
        "adjacency": {"area_a": ["area_b"]},
        "sensors": {},
    }
    resolver = MapOccupancyResolver(config)

    areas = {
        "area_a": AreaState("area_a", config["areas"]["area_a"]),
        "area_b": AreaState("area_b", config["areas"]["area_b"]),
    }
    sensors = {
        "s.a": SensorState("s.a", {"area": "area_a", "type": "motion"}, now),
        "s.b": SensorState("s.b", {"area": "area_b", "type": "motion"}, now),
    }

    # Person enters at exit-capable area_a
    _fire(resolver, sensors, areas, "s.a", True, now)
    assert areas["area_a"].occupancy == 1
    assert areas["area_b"].occupancy == 0

    # Person walks to area_b, but this cannot prove area_a is empty.
    _fire(resolver, sensors, areas, "s.b", True, now + 1)
    assert areas["area_a"].occupancy == 1
    assert areas["area_b"].occupancy == 1


def test_motion_chain_keeps_all_possible_rooms_occupied():
    """A motion chain cannot prove that earlier rooms are empty."""
    now = time.time()
    config = {
        "areas": {
            "a": {"name": "A", "exit_capable": True},
            "b": {"name": "B"},
            "c": {"name": "C"},
        },
        "adjacency": {"a": ["b"], "b": ["c"]},
        "sensors": {},
    }
    resolver = MapOccupancyResolver(config)

    areas = {
        "a": AreaState("a", config["areas"]["a"]),
        "b": AreaState("b", config["areas"]["b"]),
        "c": AreaState("c", config["areas"]["c"]),
    }
    sensors = {
        "s.a": SensorState("s.a", {"area": "a", "type": "motion"}, now),
        "s.b": SensorState("s.b", {"area": "b", "type": "motion"}, now),
        "s.c": SensorState("s.c", {"area": "c", "type": "motion"}, now),
    }

    _fire(resolver, sensors, areas, "s.a", True, now)
    assert areas["a"].occupancy == 1

    _fire(resolver, sensors, areas, "s.b", True, now + 1)
    assert areas["a"].occupancy == 1
    assert areas["b"].occupancy == 1

    _fire(resolver, sensors, areas, "s.c", True, now + 2)
    assert areas["a"].occupancy == 1
    assert areas["b"].occupancy == 1
    assert areas["c"].occupancy == 1


def test_motion_off_does_not_clear_possible_occupancy():
    """A sensor OFF edge means no motion, not an empty room."""
    now = time.time()
    config = {
        "areas": {
            "a": {"name": "A", "exit_capable": True},
            "b": {"name": "B"},
        },
        "adjacency": {"a": ["b"]},
        "sensors": {},
    }
    resolver = MapOccupancyResolver(config)

    areas = {
        "a": AreaState("a", config["areas"]["a"]),
        "b": AreaState("b", config["areas"]["b"]),
    }
    sensors = {
        "s.a": SensorState("s.a", {"area": "a", "type": "motion"}, now),
        "s.b": SensorState("s.b", {"area": "b", "type": "motion"}, now),
    }

    _fire(resolver, sensors, areas, "s.a", True, now)
    _fire(resolver, sensors, areas, "s.b", True, now + 1)
    assert areas["a"].occupancy == 1
    assert areas["b"].occupancy == 1

    # OFF in area_a cannot prove vacancy.
    _fire(resolver, sensors, areas, "s.a", False, now + 5)
    assert areas["a"].occupancy == 1
    assert areas["b"].occupancy == 1


# ============================================================
# Exit-capable areas
# ============================================================


def test_exit_capable_new_entry():
    """Exit-capable area creates new claim when no adjacent source."""
    now = time.time()
    config = {
        "areas": {"entry": {"name": "Entry", "exit_capable": True}},
        "adjacency": {},
        "sensors": {},
    }
    resolver = MapOccupancyResolver(config)

    areas = {"entry": AreaState("entry", config["areas"]["entry"])}
    sensors = {"s.e": SensorState("s.e", {"area": "entry", "type": "motion"}, now)}

    _fire(resolver, sensors, areas, "s.e", True, now)
    assert areas["entry"].occupancy == 1


def test_indoor_exit_capable_area_does_not_clear_on_off():
    """Even an exit-capable indoor area may still contain someone."""
    now = time.time()
    config = {
        "areas": {
            "entry": {"name": "Entry", "exit_capable": True},
            "hall": {"name": "Hall"},
        },
        "adjacency": {"entry": ["hall"]},
        "sensors": {},
    }
    resolver = MapOccupancyResolver(config)

    areas = {
        "entry": AreaState("entry", config["areas"]["entry"]),
        "hall": AreaState("hall", config["areas"]["hall"]),
    }
    sensors = {
        "s.e": SensorState("s.e", {"area": "entry", "type": "motion"}, now),
        "s.h": SensorState("s.h", {"area": "hall", "type": "motion"}, now),
    }

    # Person appears
    _fire(resolver, sensors, areas, "s.e", True, now)
    assert areas["entry"].occupancy == 1

    # Motion stops, but that is not proof the room is empty.
    _fire(resolver, sensors, areas, "s.e", False, now + 5)
    assert areas["entry"].occupancy == 1


def test_exit_capable_does_not_clear_if_indoor_neighbor_active():
    """Exit-capable area keeps claims when indoor neighbor is recently active."""
    now = time.time()
    config = {
        "areas": {
            "entry": {"name": "Entry", "exit_capable": True},
            "hall": {"name": "Hall"},
        },
        "adjacency": {"entry": ["hall"]},
        "sensors": {},
    }
    resolver = MapOccupancyResolver(config)

    areas = {
        "entry": AreaState("entry", config["areas"]["entry"]),
        "hall": AreaState("hall", config["areas"]["hall"]),
    }
    sensors = {
        "s.e": SensorState("s.e", {"area": "entry", "type": "motion"}, now),
        "s.h": SensorState("s.h", {"area": "hall", "type": "motion"}, now),
    }

    _fire(resolver, sensors, areas, "s.e", True, now)
    # Hall becomes active (neighbor is active)
    _fire(resolver, sensors, areas, "s.h", True, now + 1)

    # Hall activity cannot prove that entry is empty.
    assert areas["entry"].occupancy == 1
    assert areas["hall"].occupancy == 1


# ============================================================
# Phantom rejection
# ============================================================


def test_isolated_area_uses_own_sensor_as_source():
    """An isolated area's own sensor is its only plausible source."""
    now = time.time()
    config = {
        "areas": {"isolated": {"name": "Isolated"}},
        "adjacency": {},
        "sensors": {},
    }
    resolver = MapOccupancyResolver(config)
    detector = AnomalyDetector(config)

    areas = {"isolated": AreaState("isolated", config["areas"]["isolated"])}
    sensors = {"s.i": SensorState("s.i", {"area": "isolated", "type": "motion"}, now)}

    _fire(resolver, sensors, areas, "s.i", True, now, detector)
    assert areas["isolated"].occupancy == 1

    assert detector.get_warnings() == []


def test_phantom_not_rejected_with_recent_neighbor():
    """Indoor non-exit area accepts motion when a neighbor was recently active."""
    now = time.time()
    config = {
        "areas": {
            "hall": {"name": "Hall"},
            "room": {"name": "Room"},
        },
        "adjacency": {"hall": ["room"]},
        "sensors": {},
    }
    resolver = MapOccupancyResolver(config)
    detector = AnomalyDetector(config)

    areas = {
        "hall": AreaState("hall", config["areas"]["hall"]),
        "room": AreaState("room", config["areas"]["room"]),
    }
    sensors = {
        "s.h": SensorState("s.h", {"area": "hall", "type": "motion"}, now),
        "s.r": SensorState("s.r", {"area": "room", "type": "motion"}, now),
    }

    # Hall had recent motion (within ADJACENT_ACTIVITY_WINDOW)
    areas["hall"].last_motion = now - 5  # 5 seconds ago

    _fire(resolver, sensors, areas, "s.r", True, now, detector)
    assert areas["room"].occupancy == 1


def test_phantom_not_rejected_with_magnetic_evidence():
    """Indoor area accepts motion when magnetic sensor (door) recently changed."""
    now = time.time()
    config = {
        "areas": {
            "hall": {"name": "Hall"},
            "outside": {"name": "Outside", "indoors": False},
        },
        "adjacency": {"hall": ["outside"]},
        "sensors": {},
    }
    resolver = MapOccupancyResolver(config)
    detector = AnomalyDetector(config)

    areas = {
        "hall": AreaState("hall", config["areas"]["hall"]),
        "outside": AreaState("outside", config["areas"]["outside"]),
    }
    sensors = {
        "s.h": SensorState("s.h", {"area": "hall", "type": "motion"}, now),
        "s.door": SensorState("s.door", {"area": "hall", "type": "door"}, now),
    }

    # Door opened recently
    sensors["s.door"].update_state(True, now - 10)

    _fire(resolver, sensors, areas, "s.h", True, now, detector)
    assert areas["hall"].occupancy == 1


# ============================================================
# Already occupied: no-op
# ============================================================


def test_motion_on_already_occupied_is_noop():
    """Motion-ON in area that already has claims does nothing."""
    now = time.time()
    config = {
        "areas": {"room": {"name": "Room", "exit_capable": True}},
        "adjacency": {},
        "sensors": {},
    }
    resolver = MapOccupancyResolver(config)

    areas = {"room": AreaState("room", config["areas"]["room"])}
    sensors = {"s.r": SensorState("s.r", {"area": "room", "type": "motion"}, now)}

    _fire(resolver, sensors, areas, "s.r", True, now)
    assert areas["room"].occupancy == 1

    # Fire again - should not add another claim
    _fire(resolver, sensors, areas, "s.r", False, now + 5)
    _fire(resolver, sensors, areas, "s.r", True, now + 6)
    # Exit-capable cleared on OFF, then re-fires ON -> new claim
    assert areas["room"].occupancy == 1


# ============================================================
# Person stays (motion-OFF with no exit)
# ============================================================


def test_person_stays_non_exit_off():
    """Person stays when motion-OFF fires in a non-exit area with no active neighbor."""
    now = time.time()
    config = {
        "areas": {
            "entry": {"name": "Entry", "exit_capable": True},
            "room": {"name": "Room"},
        },
        "adjacency": {"entry": ["room"]},
        "sensors": {},
    }
    resolver = MapOccupancyResolver(config)

    areas = {
        "entry": AreaState("entry", config["areas"]["entry"]),
        "room": AreaState("room", config["areas"]["room"]),
    }
    sensors = {
        "s.e": SensorState("s.e", {"area": "entry", "type": "motion"}, now),
        "s.r": SensorState("s.r", {"area": "room", "type": "motion"}, now),
    }

    # Person enters and moves to room
    _fire(resolver, sensors, areas, "s.e", True, now)
    _fire(resolver, sensors, areas, "s.r", True, now + 1)
    assert areas["room"].occupancy == 1

    # Room sensor OFF -> person stays (non-exit, no active neighbor)
    _fire(resolver, sensors, areas, "s.e", False, now + 5)
    _fire(resolver, sensors, areas, "s.r", False, now + 6)
    assert areas["room"].occupancy == 1


# ============================================================
# Convergence: two people end up in same room
# ============================================================


def test_convergence_sole_active_neighbor():
    """Motion-OFF with sole active neighbor transfers claim (convergence rule)."""
    now = time.time()
    config = {
        "areas": {
            "entry": {"name": "Entry", "exit_capable": True},
            "hall": {"name": "Hall"},
            "room": {"name": "Room"},
        },
        "adjacency": {"entry": ["hall"], "hall": ["room"]},
        "sensors": {},
    }
    resolver = MapOccupancyResolver(config)

    areas = {
        "entry": AreaState("entry", config["areas"]["entry"]),
        "hall": AreaState("hall", config["areas"]["hall"]),
        "room": AreaState("room", config["areas"]["room"]),
    }
    sensors = {
        "s.e": SensorState("s.e", {"area": "entry", "type": "motion"}, now),
        "s.h": SensorState("s.h", {"area": "hall", "type": "motion"}, now),
        "s.r": SensorState("s.r", {"area": "room", "type": "motion"}, now),
    }

    # Person A enters and goes to room
    _fire(resolver, sensors, areas, "s.e", True, now)
    _fire(resolver, sensors, areas, "s.h", True, now + 1)
    _fire(resolver, sensors, areas, "s.r", True, now + 2)
    assert areas["room"].occupancy == 1

    # Person B enters
    _fire(resolver, sensors, areas, "s.e", False, now + 5)
    _fire(resolver, sensors, areas, "s.e", True, now + 10)
    assert areas["entry"].occupancy == 1

    # Person B walks to hall
    _fire(resolver, sensors, areas, "s.h", False, now + 12)
    _fire(resolver, sensors, areas, "s.h", True, now + 13)

    # Now room has 1, hall has 1

    # Person B walks to room: room already occupied so ON is no-op.
    # But room sensor is already on from person A...
    # The transfer happens on hall OFF via convergence.


# ============================================================
# Outdoor -> Indoor transfer
# ============================================================


def test_outdoor_to_indoor_motion_keeps_active_sensor_occupied():
    """An active outdoor sensor is not marked vacant during indoor motion."""
    now = time.time()
    config = {
        "areas": {
            "frontyard": {"name": "Frontyard", "indoors": False, "exit_capable": True},
            "entry": {"name": "Entry"},
        },
        "adjacency": {"frontyard": ["entry"]},
        "sensors": {},
    }
    resolver = MapOccupancyResolver(config)

    areas = {
        "frontyard": AreaState("frontyard", config["areas"]["frontyard"]),
        "entry": AreaState("entry", config["areas"]["entry"]),
    }
    sensors = {
        "s.f": SensorState("s.f", {"area": "frontyard", "type": "motion"}, now),
        "s.e": SensorState("s.e", {"area": "entry", "type": "motion"}, now),
    }

    # Camera detects person outside
    _fire(resolver, sensors, areas, "s.f", True, now)
    assert areas["frontyard"].occupancy == 1

    # Entry motion ON adds indoor evidence without rejecting active outdoor evidence.
    _fire(resolver, sensors, areas, "s.e", True, now + 2)
    assert areas["frontyard"].occupancy == 1
    assert areas["entry"].occupancy == 1


def test_outdoor_occupancy_ends_when_live_evidence_ends():
    """Outdoor detections are not conservatively latched or inferred."""
    now = time.time()
    config = {
        "areas": {"porch": {"name": "Porch", "indoors": False}},
        "adjacency": {},
        "sensors": {},
    }
    resolver = MapOccupancyResolver(config)
    areas = {"porch": AreaState("porch", config["areas"]["porch"])}
    sensors = {
        "s.porch": SensorState("s.porch", {"area": "porch", "type": "motion"}, now)
    }

    _fire(resolver, sensors, areas, "s.porch", True, now)
    assert areas["porch"].occupancy == 1

    _fire(resolver, sensors, areas, "s.porch", False, now + 5)
    assert areas["porch"].occupancy == 0


# ============================================================
# Multi-sensor same room
# ============================================================


def test_multi_sensor_same_room():
    """Multiple sensors in same room: OFF only triggers when ALL sensors are off."""
    now = time.time()
    config = {
        "areas": {"room": {"name": "Room", "exit_capable": True}},
        "adjacency": {},
        "sensors": {},
    }
    resolver = MapOccupancyResolver(config)

    areas = {"room": AreaState("room", config["areas"]["room"])}
    sensors = {
        "s.pir": SensorState("s.pir", {"area": "room", "type": "motion"}, now),
        "s.cam": SensorState("s.cam", {"area": "room", "type": "camera_person"}, now),
    }

    _fire(resolver, sensors, areas, "s.pir", True, now)
    assert areas["room"].occupancy == 1

    _fire(resolver, sensors, areas, "s.cam", True, now + 1)
    assert areas["room"].occupancy == 1

    # PIR off but camera still on -> no exit clearing
    _fire(resolver, sensors, areas, "s.pir", False, now + 5)
    assert areas["room"].occupancy == 1

    # Camera off is still not proof that an indoor room is empty.
    _fire(resolver, sensors, areas, "s.cam", False, now + 10)
    assert areas["room"].occupancy == 1


# ============================================================
# Open-plan group handling
# ============================================================


def test_open_plan_motion_keeps_all_possible_members_occupied():
    """Open-plan clustering cannot clear a member with positive evidence."""
    now = time.time()
    config = {
        "areas": {
            "entry": {"name": "Entry", "exit_capable": True},
            "kitchen": {"name": "Kitchen"},
            "dining": {"name": "Dining"},
            "living": {"name": "Living"},
        },
        "adjacency": {
            "entry": ["kitchen"],
            "kitchen": ["dining"],
            "dining": ["living"],
        },
        "open_plan_groups": {
            "open_plan": {"areas": ["kitchen", "dining", "living"]},
        },
        "sensors": {},
    }
    resolver = MapOccupancyResolver(config)

    areas = {
        "entry": AreaState("entry", config["areas"]["entry"]),
        "kitchen": AreaState("kitchen", config["areas"]["kitchen"]),
        "dining": AreaState("dining", config["areas"]["dining"]),
        "living": AreaState("living", config["areas"]["living"]),
    }
    sensors = {
        "s.e": SensorState("s.e", {"area": "entry", "type": "motion"}, now),
        "s.k": SensorState("s.k", {"area": "kitchen", "type": "motion"}, now),
        "s.d": SensorState("s.d", {"area": "dining", "type": "motion"}, now),
        "s.l": SensorState("s.l", {"area": "living", "type": "motion"}, now),
    }

    # Person enters via entry, moves to kitchen
    _fire(resolver, sensors, areas, "s.e", True, now)
    _fire(resolver, sensors, areas, "s.k", True, now + 1)
    assert areas["kitchen"].occupancy == 1

    # Dining sensor fires: same person, rebalance within group
    _fire(resolver, sensors, areas, "s.d", True, now + 2)
    assert areas["dining"].occupancy == 1
    assert areas["kitchen"].occupancy == 1
    total = sum(a.occupancy for a in areas.values())
    assert total == 3

    # Living sensor fires: rebalance again
    _fire(resolver, sensors, areas, "s.l", True, now + 3)
    assert areas["living"].occupancy == 1
    total = sum(a.occupancy for a in areas.values())
    assert total == 4


def test_open_plan_exit_to_non_group_area():
    """Claim moves out of open-plan group when person walks to non-group area."""
    now = time.time()
    config = {
        "areas": {
            "kitchen": {"name": "Kitchen"},
            "dining": {"name": "Dining"},
            "corridor": {"name": "Corridor"},
            "entry": {"name": "Entry", "exit_capable": True},
        },
        "adjacency": {
            "entry": ["corridor"],
            "corridor": ["kitchen"],
            "kitchen": ["dining"],
        },
        "open_plan_groups": {
            "open_plan": {"areas": ["kitchen", "dining"]},
        },
        "sensors": {},
    }
    resolver = MapOccupancyResolver(config)

    areas = {aid: AreaState(aid, cfg) for aid, cfg in config["areas"].items()}
    sensors = {
        "s.e": SensorState("s.e", {"area": "entry", "type": "motion"}, now),
        "s.c": SensorState("s.c", {"area": "corridor", "type": "motion"}, now),
        "s.k": SensorState("s.k", {"area": "kitchen", "type": "motion"}, now),
        "s.d": SensorState("s.d", {"area": "dining", "type": "motion"}, now),
    }

    # Person enters, walks to kitchen
    _fire(resolver, sensors, areas, "s.e", True, now)
    _fire(resolver, sensors, areas, "s.c", True, now + 1)
    _fire(resolver, sensors, areas, "s.k", True, now + 2)
    assert areas["kitchen"].occupancy == 1

    # Person in kitchen, rebalances to dining
    _fire(resolver, sensors, areas, "s.d", True, now + 3)
    assert areas["dining"].occupancy == 1

    # Person leaves kitchen/dining area back to corridor
    _fire(resolver, sensors, areas, "s.c", False, now + 5)
    _fire(resolver, sensors, areas, "s.c", True, now + 10)
    # Corridor activity does not clear the previously possible rooms.
    assert areas["corridor"].occupancy == 1
    total = sum(a.occupancy for a in areas.values())
    assert total == 4


# ============================================================
# Intrusion with outdoor evidence
# ============================================================


def test_outdoor_evidence_allows_entry():
    """Outdoor activity + motion in adjacent indoor area creates a claim."""
    now = time.time()
    config = {
        "areas": {
            "foyer": {"name": "Foyer"},
            "frontyard": {"name": "Front Yard", "indoors": False, "exit_capable": True},
        },
        "adjacency": {"foyer": ["frontyard"]},
        "sensors": {},
    }
    resolver = MapOccupancyResolver(config)
    detector = AnomalyDetector(config)

    areas = {
        "foyer": AreaState("foyer", config["areas"]["foyer"]),
        "frontyard": AreaState("frontyard", config["areas"]["frontyard"]),
    }
    sensors = {
        "s.f": SensorState("s.f", {"area": "foyer", "type": "motion"}, now),
        "s.fy": SensorState("s.fy", {"area": "frontyard", "type": "motion"}, now),
    }

    # Outdoor motion creates a claim (exit-capable)
    _fire(resolver, sensors, areas, "s.fy", True, now, detector)
    assert areas["frontyard"].occupancy == 1

    # Indoor motion adds evidence; the still-ON outdoor sensor also remains occupied.
    _fire(resolver, sensors, areas, "s.f", True, now + 2, detector)
    assert areas["foyer"].occupancy == 1
    assert areas["frontyard"].occupancy == 1


# ============================================================
# Recalculate from history
# ============================================================


def test_recalculate_from_history():
    """Recalculate produces same state when replayed."""
    now = time.time()
    config = {
        "areas": {
            "a": {"name": "A", "exit_capable": True},
            "b": {"name": "B"},
        },
        "adjacency": {"a": ["b"]},
        "sensors": {},
    }
    resolver = MapOccupancyResolver(config)

    areas = {
        "a": AreaState("a", config["areas"]["a"]),
        "b": AreaState("b", config["areas"]["b"]),
    }
    sensors = {
        "s.a": SensorState("s.a", {"area": "a", "type": "motion"}, now),
        "s.b": SensorState("s.b", {"area": "b", "type": "motion"}, now),
    }

    snapshots = [
        _sensor_event("s.a", True, now),
        _sensor_event("s.b", True, now + 1),
        _sensor_event("s.a", False, now + 5),
    ]

    # First pass
    for snap in snapshots:
        event = resolver._parse_sensor_event(snap)
        if event:
            sid, state = event
            sensors[sid].update_state(state, snap.timestamp)
        resolver.process_snapshot(snap, areas, sensors)

    occ_a = areas["a"].occupancy
    occ_b = areas["b"].occupancy

    # Reset sensors for replay
    for s in sensors.values():
        s.reset()

    # Replay
    resolver.recalculate_from_history(snapshots, areas, sensors)

    assert areas["a"].occupancy == occ_a
    assert areas["b"].occupancy == occ_b


# ============================================================
# Two people in different rooms (separate claims)
# ============================================================


def test_two_people_separate_rooms():
    """Two people entering at different exit-capable areas get separate claims."""
    now = time.time()
    config = {
        "areas": {
            "entry_a": {"name": "Entry A", "exit_capable": True},
            "entry_b": {"name": "Entry B", "exit_capable": True},
            "room": {"name": "Room"},
        },
        "adjacency": {"entry_a": ["room"], "entry_b": ["room"]},
        "sensors": {},
    }
    resolver = MapOccupancyResolver(config)

    areas = {aid: AreaState(aid, cfg) for aid, cfg in config["areas"].items()}
    sensors = {
        "s.ea": SensorState("s.ea", {"area": "entry_a", "type": "motion"}, now),
        "s.eb": SensorState("s.eb", {"area": "entry_b", "type": "motion"}, now),
        "s.r": SensorState("s.r", {"area": "room", "type": "motion"}, now),
    }

    # Person A enters at entry_a
    _fire(resolver, sensors, areas, "s.ea", True, now)
    assert areas["entry_a"].occupancy == 1

    # Person B enters at entry_b
    _fire(resolver, sensors, areas, "s.eb", True, now + 1)
    assert areas["entry_b"].occupancy == 1

    # Total: 2 claims
    total = sum(a.occupancy for a in areas.values())
    assert total == 2


# ============================================================
# Adjacency building
# ============================================================


def test_adjacency_bidirectional():
    """Adjacency map is built bidirectionally."""
    config = {
        "areas": {"a": {}, "b": {}, "c": {}},
        "adjacency": {"a": ["b"]},
        "sensors": {},
    }
    resolver = MapOccupancyResolver(config)

    assert "b" in resolver.adjacency_map["a"]
    assert "a" in resolver.adjacency_map["b"]


# ============================================================
# Magnetic events
# ============================================================


def test_magnetic_event_records_contact_without_faking_motion():
    """A boundary edge is activity, but not fabricated PIR evidence."""
    now = time.time()
    config = {
        "areas": {"hall": {}, "room": {}},
        "adjacency": {"hall": ["room"]},
        "sensors": {},
    }
    resolver = MapOccupancyResolver(config)

    areas = {
        "hall": AreaState("hall", config["areas"]["hall"]),
        "room": AreaState("room", config["areas"]["room"]),
    }
    sensors = {
        "s.door": SensorState(
            "s.door", {"area": ["hall", "room"], "type": "door"}, now
        ),
    }

    _fire(resolver, sensors, areas, "s.door", True, now)

    assert areas["hall"].last_motion == 0
    assert areas["room"].last_motion == 0
    assert areas["hall"].last_contact == now
    assert areas["room"].last_contact == now
    assert areas["hall"].last_activity == now
    assert areas["room"].last_activity == now
    assert areas["hall"].occupancy == 0
    assert areas["room"].occupancy == 0


# ============================================================
# Full walkthrough scenario
# ============================================================


def test_full_walkthrough_preserves_uncertain_room():
    """A late neighboring activation does not erase retained occupancy."""
    now = time.time()
    config = {
        "areas": {
            "entry": {"name": "Entry", "exit_capable": True},
            "hall": {"name": "Hall"},
            "kitchen": {"name": "Kitchen"},
        },
        "adjacency": {"entry": ["hall"], "hall": ["kitchen"]},
        "sensors": {},
    }
    resolver = MapOccupancyResolver(config)

    areas = {aid: AreaState(aid, cfg) for aid, cfg in config["areas"].items()}
    sensors = {
        "s.e": SensorState("s.e", {"area": "entry", "type": "motion"}, now),
        "s.h": SensorState("s.h", {"area": "hall", "type": "motion"}, now),
        "s.k": SensorState("s.k", {"area": "kitchen", "type": "motion"}, now),
    }

    # Enter
    _fire(resolver, sensors, areas, "s.e", True, now)
    assert areas["entry"].occupancy == 1

    # Walk to hall; uncertainty about entry remains.
    _fire(resolver, sensors, areas, "s.h", True, now + 1)
    assert areas["hall"].occupancy == 1
    assert areas["entry"].occupancy == 1

    # Walk to kitchen
    _fire(resolver, sensors, areas, "s.k", True, now + 2)
    assert areas["kitchen"].occupancy == 1
    assert areas["hall"].occupancy == 1

    # Sit in kitchen, sensors turn off
    _fire(resolver, sensors, areas, "s.e", False, now + 5)
    _fire(resolver, sensors, areas, "s.h", False, now + 6)
    _fire(resolver, sensors, areas, "s.k", False, now + 7)

    # Person stays in kitchen (non-exit)
    assert areas["kitchen"].occupancy == 1

    # Walk back to hall
    _fire(resolver, sensors, areas, "s.h", True, now + 60)
    assert areas["hall"].occupancy == 1
    assert areas["kitchen"].occupancy == 1

    # Walk to entry
    _fire(resolver, sensors, areas, "s.e", True, now + 61)
    assert areas["entry"].occupancy == 1
    assert areas["hall"].occupancy == 1

    # Leave via entry
    _fire(resolver, sensors, areas, "s.h", False, now + 65)
    _fire(resolver, sensors, areas, "s.e", False, now + 66)
    assert areas["entry"].occupancy == 1

    # The exit clears, but the kitchen remains uncertain because its motion
    # was not tightly coupled to the later hallway activation.
    total = sum(a.occupancy for a in areas.values())
    assert total == 3
    assert areas["kitchen"].occupancy == 1
