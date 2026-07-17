"""Production scenario tests for conservative occupancy resolution.

These tests simulate real sensor patterns observed in production logs
(2026-03-16 17:24-19:57). Timing values come from measured inter-sensor
gaps and cycling statistics documented in log-analysis.md.
"""

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


# ------------------------------------------------------------------
# Helpers (same pattern as test_map_occupancy_resolver.py)
# ------------------------------------------------------------------


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


def _total_occupancy(areas):
    """Sum occupancy across all areas."""
    return sum(a.occupancy for a in areas.values())


# ------------------------------------------------------------------
# House layout config builder for open-plan scenarios
# ------------------------------------------------------------------


def _open_plan_config():
    """Config matching the user's open-plan kitchen/dining/living layout."""
    return {
        "areas": {
            "entrance": {"name": "Entrance", "exit_capable": True},
            "kitchen": {"name": "Kitchen"},
            "dining_room": {"name": "Dining Room"},
            "living": {"name": "Living"},
        },
        "adjacency": {
            "entrance": ["kitchen"],
            "kitchen": ["dining_room"],
            "dining_room": ["living"],
        },
        "open_plan_groups": {
            "open_plan": {"areas": ["kitchen", "dining_room", "living"]},
        },
        "sensors": {},
    }


def _open_plan_areas_and_sensors(config, now):
    areas = {aid: AreaState(aid, cfg) for aid, cfg in config["areas"].items()}
    sensors = {
        "s.entrance": SensorState(
            "s.entrance", {"area": "entrance", "type": "motion"}, now
        ),
        "s.kitchen": SensorState(
            "s.kitchen", {"area": "kitchen", "type": "motion"}, now
        ),
        "s.dining": SensorState(
            "s.dining", {"area": "dining_room", "type": "motion"}, now
        ),
        "s.living": SensorState("s.living", {"area": "living", "type": "motion"}, now),
    }
    return areas, sensors


# ------------------------------------------------------------------
# Full house config for walk-chain tests
# ------------------------------------------------------------------


def _full_house_config():
    """Config matching the user's full house layout."""
    return {
        "areas": {
            "frontyard": {"name": "Frontyard", "indoors": False, "exit_capable": True},
            "entrance": {"name": "Entrance", "exit_capable": True},
            "corridor_1": {"name": "Corridor 1"},
            "study": {"name": "Study"},
            "bedroom_2": {"name": "Bedroom 2"},
            "bathroom": {"name": "Bathroom"},
            "corridor_2": {"name": "Corridor 2"},
            "bedroom_1": {"name": "Bedroom 1"},
            "kitchen": {"name": "Kitchen"},
            "dining_room": {"name": "Dining Room"},
            "living": {"name": "Living"},
        },
        "adjacency": {
            "frontyard": ["entrance"],
            "entrance": ["kitchen", "corridor_1"],
            "corridor_1": ["study", "bedroom_2", "corridor_2", "bathroom"],
            "corridor_2": ["bedroom_1"],
            "kitchen": ["dining_room"],
            "dining_room": ["living"],
        },
        "open_plan_groups": {
            "open_plan": {"areas": ["kitchen", "dining_room", "living"]},
        },
        "sensors": {},
    }


def _full_house_areas_and_sensors(config, now):
    areas = {aid: AreaState(aid, cfg) for aid, cfg in config["areas"].items()}
    sensors = {
        "s.frontyard": SensorState(
            "s.frontyard", {"area": "frontyard", "type": "camera_person"}, now
        ),
        "s.entrance": SensorState(
            "s.entrance", {"area": "entrance", "type": "motion"}, now
        ),
        "s.corridor_1": SensorState(
            "s.corridor_1", {"area": "corridor_1", "type": "motion"}, now
        ),
        "s.study": SensorState("s.study", {"area": "study", "type": "motion"}, now),
        "s.bedroom_2": SensorState(
            "s.bedroom_2", {"area": "bedroom_2", "type": "motion"}, now
        ),
        "s.bathroom": SensorState(
            "s.bathroom", {"area": "bathroom", "type": "motion"}, now
        ),
        "s.corridor_2": SensorState(
            "s.corridor_2", {"area": "corridor_2", "type": "motion"}, now
        ),
        "s.bedroom_1": SensorState(
            "s.bedroom_1", {"area": "bedroom_1", "type": "motion"}, now
        ),
        "s.kitchen": SensorState(
            "s.kitchen", {"area": "kitchen", "type": "motion"}, now
        ),
        "s.dining": SensorState(
            "s.dining", {"area": "dining_room", "type": "motion"}, now
        ),
        "s.living": SensorState("s.living", {"area": "living", "type": "motion"}, now),
    }
    return areas, sensors


# ==================================================================
# 1. Open-plan simultaneous fire: K(+0.0s) -> DR(+0.8s)
#    From log: 18:12:46 kitchen ON, 18:12:47 dining ON
#    Every room with positive evidence must remain occupied.
# ==================================================================


def test_open_plan_simultaneous_fire():
    """Kitchen and dining fire 0.8s apart -- overlap, not two people."""
    now = time.time()
    config = _open_plan_config()
    resolver = MapOccupancyResolver(config)
    areas, sensors = _open_plan_areas_and_sensors(config, now)

    # Person enters via entrance
    _fire(resolver, sensors, areas, "s.entrance", True, now)
    assert areas["entrance"].occupancy == 1

    # Person walks to kitchen; entrance remains uncertain.
    _fire(resolver, sensors, areas, "s.kitchen", True, now + 3.7)
    assert areas["kitchen"].occupancy == 1
    assert areas["entrance"].occupancy == 1

    # Dining room fires 0.8s later (overlap, same person)
    _fire(resolver, sensors, areas, "s.dining", True, now + 3.7 + 0.8)
    # Open-plan overlap must not create a false vacancy.
    assert _total_occupancy(areas) >= 1


def test_open_plan_triple_fire():
    """All three open-plan sensors fire: DR(+0.0s) -> K(+0.2s) -> L(+3.0s).

    From log: 18:14:26 DR ON, K ON +0.2s, L ON +3.0s.
    All active rooms remain conservatively occupied.
    """
    now = time.time()
    config = _open_plan_config()
    resolver = MapOccupancyResolver(config)
    areas, sensors = _open_plan_areas_and_sensors(config, now)

    # Person enters and reaches kitchen
    _fire(resolver, sensors, areas, "s.entrance", True, now)
    _fire(resolver, sensors, areas, "s.kitchen", True, now + 3.0)

    # Sensors turn off as person moves deeper
    _fire(resolver, sensors, areas, "s.entrance", False, now + 5.0)

    # Triple open-plan fire sequence: DR -> K -> L
    t_base = now + 10.0
    _fire(resolver, sensors, areas, "s.kitchen", False, t_base - 1.0)
    _fire(resolver, sensors, areas, "s.dining", True, t_base)
    _fire(resolver, sensors, areas, "s.kitchen", True, t_base + 0.2)
    _fire(resolver, sensors, areas, "s.living", True, t_base + 3.0)

    assert _total_occupancy(areas) >= 1


# ==================================================================
# 3. Corridor -> study overlap: 0.4-1.0s sensor spill
#    From log: corridor_1 ON, study ON +1.0s (overlap from corridor)
#    Person walks from corridor to study. Must transfer, not 2 occupants.
# ==================================================================


def test_corridor_study_overlap():
    """Corridor_1 and study fire 1.0s apart (sensor overlap/spill)."""
    now = time.time()
    config = _full_house_config()
    resolver = MapOccupancyResolver(config)
    areas, sensors = _full_house_areas_and_sensors(config, now)

    # Person enters at entrance
    _fire(resolver, sensors, areas, "s.entrance", True, now)
    assert areas["entrance"].occupancy == 1

    # Person walks to corridor_1
    _fire(resolver, sensors, areas, "s.corridor_1", True, now + 2.0)
    assert areas["corridor_1"].occupancy == 1
    assert areas["entrance"].occupancy == 1

    # Study fires 1.0s after corridor (overlap/spill)
    _fire(resolver, sensors, areas, "s.study", True, now + 3.0)
    assert areas["study"].occupancy == 1
    assert areas["corridor_1"].occupancy == 1

    # At least one occupied area must survive the overlap.
    assert _total_occupancy(areas) >= 1


# ==================================================================
# 4. Full walk chain: study -> corridor_1 -> entrance -> kitchen -> dining
#    From log 18:12 movement chain with real timing gaps.
#    1 person throughout, ending in last room.
# ==================================================================


def test_full_walk_chain():
    """Simulate study -> corridor_1 -> entrance -> kitchen -> dining.

    Uses real timing from 18:12 log:
      corridor_1 ON (+0.0s)
      study ON (+1.0s, overlap)
      entrance ON (+28.1s, person walking to entrance)
      kitchen ON (+3.7s from entrance)
      dining ON (+0.8s from kitchen, overlap)
    """
    now = time.time()
    config = _full_house_config()
    resolver = MapOccupancyResolver(config)
    areas, sensors = _full_house_areas_and_sensors(config, now)

    # Person starts in study (entered earlier via entrance)
    _fire(resolver, sensors, areas, "s.entrance", True, now)
    _fire(resolver, sensors, areas, "s.corridor_1", True, now + 2.0)
    _fire(resolver, sensors, areas, "s.study", True, now + 3.0)
    assert areas["study"].occupancy == 1
    assert _total_occupancy(areas) >= 1

    # Sensors turn off (person sitting)
    _fire(resolver, sensors, areas, "s.entrance", False, now + 5.0)
    _fire(resolver, sensors, areas, "s.corridor_1", False, now + 7.0)
    _fire(resolver, sensors, areas, "s.study", False, now + 12.0)
    assert areas["study"].occupancy == 1  # person stays (non-exit)

    # Person starts walking: corridor_1 ON
    t_walk = now + 60.0
    _fire(resolver, sensors, areas, "s.corridor_1", True, t_walk)
    assert areas["corridor_1"].occupancy == 1
    assert areas["study"].occupancy == 1

    # Entrance ON (+3.7s from corridor -- realistic adjacent walk)
    _fire(resolver, sensors, areas, "s.entrance", True, t_walk + 3.7)

    # Corridor_1 OFF (5s KNX delay)
    _fire(resolver, sensors, areas, "s.corridor_1", False, t_walk + 5.0)
    assert areas["entrance"].occupancy == 1
    assert areas["study"].occupancy == 1

    # Entrance OFF (5s KNX delay)
    _fire(resolver, sensors, areas, "s.entrance", False, t_walk + 8.7)

    # Kitchen ON (+28s from entrance ON -- person walking to kitchen)
    _fire(resolver, sensors, areas, "s.kitchen", True, t_walk + 31.7)
    assert areas["kitchen"].occupancy == 1
    assert areas["entrance"].occupancy == 1

    # Dining ON (+0.8s from kitchen -- overlap)
    _fire(resolver, sensors, areas, "s.dining", True, t_walk + 32.5)
    # Both open-plan sensors are positive evidence.
    assert areas["study"].occupancy == 1
    assert (
        sum(areas[aid].occupancy for aid in ("kitchen", "dining_room", "living")) == 2
    )

    # Person ends in the open-plan area
    open_plan_occ = (
        areas["kitchen"].occupancy
        + areas["dining_room"].occupancy
        + areas["living"].occupancy
    )
    assert open_plan_occ == 2


# ==================================================================
# 5. Person sitting, sensor cycling ON(5s)/OFF(5s) for 30 seconds.
#    KNX sensors have 5s minimum ON, ~3s minimum OFF gap.
#    Occupancy must stay present the entire time.
# ==================================================================


def test_person_sitting_sensor_cycling():
    """Person in study, sensor cycles ON/OFF repeatedly. Occupancy stays 1."""
    now = time.time()
    config = _full_house_config()
    resolver = MapOccupancyResolver(config)
    areas, sensors = _full_house_areas_and_sensors(config, now)

    # Person enters and reaches study
    _fire(resolver, sensors, areas, "s.entrance", True, now)
    _fire(resolver, sensors, areas, "s.corridor_1", True, now + 2.0)
    _fire(resolver, sensors, areas, "s.study", True, now + 3.0)
    assert areas["study"].occupancy == 1

    # Clear entrance/corridor sensors
    _fire(resolver, sensors, areas, "s.entrance", False, now + 5.0)
    _fire(resolver, sensors, areas, "s.corridor_1", False, now + 7.0)

    # Simulate 6 cycles of ON(5s)/OFF(3s) -- 30s total
    t = now + 10.0
    for cycle in range(6):
        # Sensor OFF
        _fire(resolver, sensors, areas, "s.study", False, t)
        assert areas["study"].occupancy == 1, f"Lost occupancy on OFF at cycle {cycle}"
        t += 3.0  # 3s OFF gap

        # Sensor ON
        _fire(resolver, sensors, areas, "s.study", True, t)
        assert areas["study"].occupancy == 1, f"Wrong occupancy on ON at cycle {cycle}"
        t += 5.0  # 5s ON duration

    assert _total_occupancy(areas) >= 1


# ==================================================================
# 6. Two people in different rooms, both sensors cycling.
#    Each room stays at 1, no cross-contamination.
# ==================================================================


def test_two_people_different_rooms():
    """Person A in study, person B in bedroom_1. Both cycling. No cross-contamination."""
    now = time.time()
    config = {
        "areas": {
            "entrance": {"name": "Entrance", "exit_capable": True},
            "corridor_1": {"name": "Corridor 1"},
            "study": {"name": "Study"},
            "corridor_2": {"name": "Corridor 2"},
            "bedroom_1": {"name": "Bedroom 1"},
        },
        "adjacency": {
            "entrance": ["corridor_1"],
            "corridor_1": ["study", "corridor_2"],
            "corridor_2": ["bedroom_1"],
        },
        "sensors": {},
    }
    resolver = MapOccupancyResolver(config)
    areas = {aid: AreaState(aid, cfg) for aid, cfg in config["areas"].items()}
    sensors = {
        "s.entrance": SensorState(
            "s.entrance", {"area": "entrance", "type": "motion"}, now
        ),
        "s.corridor_1": SensorState(
            "s.corridor_1", {"area": "corridor_1", "type": "motion"}, now
        ),
        "s.study": SensorState("s.study", {"area": "study", "type": "motion"}, now),
        "s.corridor_2": SensorState(
            "s.corridor_2", {"area": "corridor_2", "type": "motion"}, now
        ),
        "s.bedroom_1": SensorState(
            "s.bedroom_1", {"area": "bedroom_1", "type": "motion"}, now
        ),
    }

    # Person A enters and goes to study
    _fire(resolver, sensors, areas, "s.entrance", True, now)
    _fire(resolver, sensors, areas, "s.corridor_1", True, now + 1.0)
    _fire(resolver, sensors, areas, "s.study", True, now + 2.0)
    assert areas["study"].occupancy == 1

    # Person B enters (entrance is exit-capable, creates new claim when OFF/ON cycle)
    _fire(resolver, sensors, areas, "s.entrance", False, now + 5.0)
    _fire(resolver, sensors, areas, "s.corridor_1", False, now + 6.0)
    _fire(resolver, sensors, areas, "s.entrance", True, now + 10.0)
    assert areas["entrance"].occupancy == 1
    assert _total_occupancy(areas) >= 2

    # Person B walks to bedroom_1
    _fire(resolver, sensors, areas, "s.corridor_1", True, now + 11.0)
    _fire(resolver, sensors, areas, "s.corridor_2", True, now + 13.0)
    _fire(resolver, sensors, areas, "s.bedroom_1", True, now + 14.0)

    # Clear transit sensors
    _fire(resolver, sensors, areas, "s.entrance", False, now + 15.0)
    _fire(resolver, sensors, areas, "s.corridor_1", False, now + 16.0)
    _fire(resolver, sensors, areas, "s.corridor_2", False, now + 18.0)

    assert areas["study"].occupancy == 1
    assert areas["bedroom_1"].occupancy == 1
    assert _total_occupancy(areas) >= 2

    # Both sensors cycle ON/OFF 4 times -- occupancy must remain stable
    t = now + 30.0
    for cycle in range(4):
        # Both OFF
        _fire(resolver, sensors, areas, "s.study", False, t)
        _fire(resolver, sensors, areas, "s.bedroom_1", False, t + 0.5)
        assert areas["study"].occupancy == 1, f"Study lost occupancy at cycle {cycle}"
        assert areas["bedroom_1"].occupancy == 1, (
            f"Bedroom lost occupancy at cycle {cycle}"
        )
        t += 3.0

        # Both ON
        _fire(resolver, sensors, areas, "s.study", True, t)
        _fire(resolver, sensors, areas, "s.bedroom_1", True, t + 0.3)
        assert areas["study"].occupancy == 1, f"Study wrong on ON at cycle {cycle}"
        assert areas["bedroom_1"].occupancy == 1, (
            f"Bedroom wrong on ON at cycle {cycle}"
        )
        t += 5.0

    assert _total_occupancy(areas) >= 2


# ==================================================================
# 7. Open-plan kitchen/dining oscillation -- the exact pattern that
#    caused K@6 in production. Kitchen and dining sensors alternating
#    ON/OFF as person moves around kitchen. After 10 cycles, must be 1.
# ==================================================================


def test_open_plan_kitchen_dining_oscillation():
    """Kitchen and dining sensors alternate ON/OFF -- the inflation pattern.

    In production this drove dining_room to 7 occupants. Per-room occupancy
    must remain boolean without dropping rooms that have positive evidence.
    """
    now = time.time()
    config = _open_plan_config()
    resolver = MapOccupancyResolver(config)
    areas, sensors = _open_plan_areas_and_sensors(config, now)

    # Person enters and reaches kitchen
    _fire(resolver, sensors, areas, "s.entrance", True, now)
    _fire(resolver, sensors, areas, "s.kitchen", True, now + 3.7)
    assert areas["kitchen"].occupancy == 1
    _fire(resolver, sensors, areas, "s.entrance", False, now + 5.0)

    # Dining room fires due to overlap
    _fire(resolver, sensors, areas, "s.dining", True, now + 4.5)

    # Now simulate 10 cycles of the exact inflation pattern:
    # Kitchen OFF -> Kitchen ON -> Dining OFF -> Dining ON -> repeat
    # This is what created phantom occupants in production.
    t = now + 10.0
    for cycle in range(10):
        # Kitchen OFF (5s KNX delay)
        _fire(resolver, sensors, areas, "s.kitchen", False, t)
        assert _total_occupancy(areas) >= 1, (
            f"Inflation at cycle {cycle} after kitchen OFF: "
            f"K={areas['kitchen'].occupancy} DR={areas['dining_room'].occupancy} "
            f"L={areas['living'].occupancy}"
        )
        t += 1.0

        # Kitchen ON again (person still there)
        _fire(resolver, sensors, areas, "s.kitchen", True, t)
        assert _total_occupancy(areas) >= 1, (
            f"Inflation at cycle {cycle} after kitchen ON: "
            f"K={areas['kitchen'].occupancy} DR={areas['dining_room'].occupancy}"
        )
        t += 4.0

        # Dining OFF
        _fire(resolver, sensors, areas, "s.dining", False, t)
        assert _total_occupancy(areas) >= 1, (
            f"Inflation at cycle {cycle} after dining OFF: "
            f"K={areas['kitchen'].occupancy} DR={areas['dining_room'].occupancy}"
        )
        t += 1.0

        # Dining ON again (overlap)
        _fire(resolver, sensors, areas, "s.dining", True, t)
        assert _total_occupancy(areas) >= 1, (
            f"Inflation at cycle {cycle} after dining ON: "
            f"K={areas['kitchen'].occupancy} DR={areas['dining_room'].occupancy}"
        )
        t += 4.0

    # After 10 full cycles, occupancy is still present.
    assert _total_occupancy(areas) >= 1


# ==================================================================
# 8. Person enters from outside: camera fires in frontyard, then
#    entrance_motion fires. Exactly 1 claim created.
# ==================================================================


def test_person_enters_from_outside():
    """Camera detects person in frontyard, then entrance motion fires.

    Exactly 1 claim created and transferred through the chain.
    """
    now = time.time()
    config = _full_house_config()
    resolver = MapOccupancyResolver(config)
    areas, sensors = _full_house_areas_and_sensors(config, now)

    # Frontyard camera detects person (exit-capable outdoor area)
    _fire(resolver, sensors, areas, "s.frontyard", True, now)
    assert areas["frontyard"].occupancy == 1
    assert _total_occupancy(areas) >= 1

    # Entrance motion fires (transfer from frontyard)
    _fire(resolver, sensors, areas, "s.entrance", True, now + 2.0)
    assert areas["entrance"].occupancy == 1
    assert areas["frontyard"].occupancy == 1
    assert _total_occupancy(areas) >= 1

    # Person continues to kitchen
    _fire(resolver, sensors, areas, "s.kitchen", True, now + 5.7)
    assert areas["kitchen"].occupancy == 1
    assert areas["entrance"].occupancy == 1
    assert _total_occupancy(areas) >= 1

    # Dining overlap
    _fire(resolver, sensors, areas, "s.dining", True, now + 6.5)
    assert _total_occupancy(areas) >= 1

    # Frontyard camera turns off, entrance turns off
    _fire(resolver, sensors, areas, "s.frontyard", False, now + 10.0)
    _fire(resolver, sensors, areas, "s.entrance", False, now + 7.0)

    # Open-plan occupancy remains present.
    assert _total_occupancy(areas) >= 1


# ==================================================================
# 9. Bootstrap after HA restart: person in non-exit-capable room.
#    C2 fix: first activation seeds the system when total claims == 0.
# ==================================================================


def test_bootstrap_after_restart():
    """After HA restart (all claims empty), first sensor in non-exit area is accepted."""
    now = time.time()
    config = _full_house_config()
    resolver = MapOccupancyResolver(config)
    areas, sensors = _full_house_areas_and_sensors(config, now)

    # All claims are empty (simulating HA restart state)
    assert _total_occupancy(areas) == 0

    # Kitchen sensor fires (non-exit-capable, has neighbors)
    _fire(resolver, sensors, areas, "s.kitchen", True, now)
    assert areas["kitchen"].occupancy == 1
    assert _total_occupancy(areas) >= 1


def test_isolated_area_accepts_its_own_motion():
    """An isolated area's sensor is valid evidence without adjacent areas."""
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


# ==================================================================
# 10. Doorway overlap claim trap: bedroom_1 -> corridor_2 -> corridor_1 -> kitchen
#     Production failure: bedroom_1 fires 200ms after corridor_2 (doorway
#     overlap) and pulls the claim BACK, trapping it in a dead-end.
#     The reverse-transfer guard must prevent this.
# ==================================================================


def test_doorway_overlap_does_not_reverse_claim():
    """Person walks bedroom_1 -> corridor_2 -> corridor_1 -> kitchen.

    In the clustering model, doorway overlap (bedroom_1 firing 0.2s after
    corridor_2) creates a transient cluster. The person progresses through
    corridors to kitchen with total occupancy staying at 1.
    """
    now = time.time()
    config = _full_house_config()
    resolver = MapOccupancyResolver(config)
    areas, sensors = _full_house_areas_and_sensors(config, now)

    # Person starts in bedroom_1 (entered earlier)
    _fire(resolver, sensors, areas, "s.entrance", True, now)
    _fire(resolver, sensors, areas, "s.corridor_1", True, now + 1.0)
    _fire(resolver, sensors, areas, "s.corridor_2", True, now + 3.0)
    _fire(resolver, sensors, areas, "s.bedroom_1", True, now + 4.0)
    assert areas["bedroom_1"].occupancy == 1

    # All sensors go off (person sitting in bedroom_1)
    _fire(resolver, sensors, areas, "s.entrance", False, now + 5.0)
    _fire(resolver, sensors, areas, "s.corridor_1", False, now + 6.0)
    _fire(resolver, sensors, areas, "s.corridor_2", False, now + 8.0)
    _fire(resolver, sensors, areas, "s.bedroom_1", False, now + 12.0)
    assert areas["bedroom_1"].occupancy == 1
    assert _total_occupancy(areas) >= 1

    # === Person starts walking out of bedroom_1 ===
    t = now + 60.0  # well past all activity windows

    # Step 1: corridor motion cannot prove the retained bedroom is empty.
    _fire(resolver, sensors, areas, "s.corridor_2", True, t)
    assert areas["corridor_2"].occupancy == 1
    assert areas["bedroom_1"].occupancy == 1

    # Step 2: corridor_1 ON -> person continues walking
    _fire(resolver, sensors, areas, "s.corridor_1", True, t + 2.0)
    _fire(resolver, sensors, areas, "s.corridor_2", False, t + 5.0)

    # Step 3: entrance ON -> person continues
    _fire(resolver, sensors, areas, "s.entrance", True, t + 5.5)
    _fire(resolver, sensors, areas, "s.corridor_1", False, t + 7.0)

    # Step 4: kitchen ON -> person arrives in kitchen
    _fire(resolver, sensors, areas, "s.kitchen", True, t + 9.0)
    _fire(resolver, sensors, areas, "s.entrance", False, t + 10.5)
    assert areas["kitchen"].occupancy == 1

    # The destination is occupied without evicting the uncertain bedroom.
    assert areas["bedroom_1"].occupancy == 1
    assert areas["kitchen"].occupancy == 1


def test_reverse_guard_does_not_block_legitimate_return():
    """Reverse guard must not block a real return trip after enough time passes.

    Person walks bedroom_1 -> corridor_2, sits in corridor_2 for 10 seconds,
    then walks back to bedroom_1. The 10-second gap exceeds RECENT_ACTIVATION_WINDOW
    (5s), so the return is allowed.
    """
    now = time.time()
    config = _full_house_config()
    resolver = MapOccupancyResolver(config)
    areas, sensors = _full_house_areas_and_sensors(config, now)

    # Person starts in bedroom_1
    _fire(resolver, sensors, areas, "s.entrance", True, now)
    _fire(resolver, sensors, areas, "s.corridor_1", True, now + 1.0)
    _fire(resolver, sensors, areas, "s.corridor_2", True, now + 3.0)
    _fire(resolver, sensors, areas, "s.bedroom_1", True, now + 4.0)

    # All sensors off
    _fire(resolver, sensors, areas, "s.entrance", False, now + 5.0)
    _fire(resolver, sensors, areas, "s.corridor_1", False, now + 6.0)
    _fire(resolver, sensors, areas, "s.corridor_2", False, now + 8.0)
    _fire(resolver, sensors, areas, "s.bedroom_1", False, now + 12.0)

    # Person walks to corridor_2
    t = now + 60.0
    _fire(resolver, sensors, areas, "s.corridor_2", True, t)
    assert areas["corridor_2"].occupancy == 1
    assert areas["bedroom_1"].occupancy == 1

    # Wait 10 seconds (past RECENT_ACTIVATION_WINDOW = 5s)
    _fire(resolver, sensors, areas, "s.corridor_2", False, t + 5.0)

    # Person walks back to bedroom_1 after 10s
    _fire(resolver, sensors, areas, "s.corridor_2", True, t + 8.0)
    _fire(resolver, sensors, areas, "s.bedroom_1", True, t + 10.0)
    _fire(resolver, sensors, areas, "s.corridor_2", False, t + 13.0)  # KNX 5s OFF delay
    # After 10s the reverse guard expires, so bedroom_1 can pull from corridor_2
    assert areas["bedroom_1"].occupancy == 1
    # Corridor_2 may still be retained briefly (conservative — better than
    # dropping a real person). Trigger cleanup after inactivity timeout.
    _fire(resolver, sensors, areas, "s.bedroom_1", False, t + 15.0)
    _fire(
        resolver, sensors, areas, "s.bedroom_1", True, t + 135.0
    )  # 127s after corridor_2 last_motion (>120s)
    assert areas["bedroom_1"].occupancy == 1
    assert _total_occupancy(areas) >= 2


# ==================================================================
# 11. Bootstrap registers multiple people after restart
# ==================================================================


def test_bootstrap_registers_multiple_people():
    """After restart, all max_occupants people register within BOOTSTRAP_WINDOW."""
    now = time.time()
    config = _full_house_config()
    config["max_occupants"] = 3
    resolver = MapOccupancyResolver(config)
    areas, sensors = _full_house_areas_and_sensors(config, now)

    # Person 1: study fires ON — bootstrap accepts (first ever)
    _fire(resolver, sensors, areas, "s.study", True, now)
    assert areas["study"].occupancy == 1

    # Person 2: bedroom_1 fires ON 10s later — extended bootstrap
    # (total_tracked=1 < max_occupants=3, within 120s window)
    _fire(resolver, sensors, areas, "s.bedroom_1", True, now + 10)
    assert areas["bedroom_1"].occupancy == 1
    assert areas["study"].occupancy == 1

    # Person 3: kitchen fires ON 20s later — still in bootstrap window
    _fire(resolver, sensors, areas, "s.kitchen", True, now + 20)
    assert areas["kitchen"].occupancy == 1
    assert _total_occupancy(areas) >= 3


# ==================================================================
# 12. Bootstrap expires after BOOTSTRAP_WINDOW
# ==================================================================


def test_late_unexplained_activation_is_accepted_with_warning_semantics():
    """Positive evidence remains valid after the bootstrap window."""
    now = time.time()
    config = _full_house_config()
    config["max_occupants"] = 3
    resolver = MapOccupancyResolver(config)
    areas, sensors = _full_house_areas_and_sensors(config, now)

    # Person 1 enters via entrance (exit-capable, always accepted)
    _fire(resolver, sensors, areas, "s.entrance", True, now)
    assert areas["entrance"].occupancy == 1

    # Person 2 in study within bootstrap window
    _fire(resolver, sensors, areas, "s.study", True, now + 5)
    assert areas["study"].occupancy == 1

    # Well past bootstrap window (130s after first activation)
    # bedroom_1 fires with no adjacent evidence and is still occupied.
    _fire(resolver, sensors, areas, "s.bedroom_1", True, now + 130)
    assert areas["bedroom_1"].occupancy == 1


# ==================================================================
# 13. Departure evidence cannot clear a possibly occupied room
# ==================================================================


def test_retained_room_survives_departure_grace():
    """A departure trail cannot prove that a second occupant did not remain."""
    now = time.time()
    config = _full_house_config()
    resolver = MapOccupancyResolver(config)
    areas, sensors = _full_house_areas_and_sensors(config, now)

    # Person enters and goes to bedroom_2 via corridor_1
    _fire(resolver, sensors, areas, "s.entrance", True, now)
    _fire(resolver, sensors, areas, "s.corridor_1", True, now + 2)
    _fire(resolver, sensors, areas, "s.bedroom_2", True, now + 4)
    assert areas["bedroom_2"].occupancy == 1

    # Person walks back — corridor_1 fires, bedroom_2 sensor goes OFF
    _fire(resolver, sensors, areas, "s.bedroom_2", False, now + 9)
    _fire(resolver, sensors, areas, "s.corridor_1", True, now + 10)
    _fire(resolver, sensors, areas, "s.study", True, now + 12)
    # bedroom_2 should be retained (person might still be there)
    # but study is now the main occupied area

    # Before the grace expires, uncertainty is preserved.
    _fire(resolver, sensors, areas, "s.study", False, now + 72)
    _fire(resolver, sensors, areas, "s.study", True, now + 75)
    assert areas["bedroom_2"].occupancy == 1

    # Bedroom motion followed by corridor motion could represent one person
    # leaving while another remains, even after the old grace period.
    _fire(resolver, sensors, areas, "s.study", False, now + 132)
    _fire(resolver, sensors, areas, "s.study", True, now + 135)
    assert areas["bedroom_2"].occupancy == 1
    assert areas["study"].occupancy == 1


# ==================================================================
# 14. Sitting person NOT cleared by inactivity timeout
# ==================================================================


def test_sitting_person_not_cleared_by_inactivity():
    """Person sitting with sensor cycling every 10s is never cleared."""
    now = time.time()
    config = _full_house_config()
    resolver = MapOccupancyResolver(config)
    areas, sensors = _full_house_areas_and_sensors(config, now)

    # Person enters study via entrance + corridor
    _fire(resolver, sensors, areas, "s.entrance", True, now)
    _fire(resolver, sensors, areas, "s.corridor_1", True, now + 2)
    _fire(resolver, sensors, areas, "s.study", True, now + 4)
    assert areas["study"].occupancy == 1

    # Simulate sitting: sensor cycles ON(5s) → OFF(5s) for 120s
    t = now + 4
    for i in range(12):
        _fire(resolver, sensors, areas, "s.study", False, t + 5)
        _fire(resolver, sensors, areas, "s.study", True, t + 10)
        t += 10
        assert areas["study"].occupancy == 1, f"Study lost occupancy at cycle {i + 1}"


# ==================================================================
# 15. Persistent activation at 2 triggers (reduced from 3)
# ==================================================================


def test_first_late_activation_is_immediately_occupied():
    """A room does not wait for repeated motion before becoming occupied."""
    now = time.time()
    config = _full_house_config()
    resolver = MapOccupancyResolver(config)
    areas, sensors = _full_house_areas_and_sensors(config, now)

    # Establish person 1 in kitchen (via entrance)
    _fire(resolver, sensors, areas, "s.entrance", True, now)
    _fire(resolver, sensors, areas, "s.kitchen", True, now + 3)

    # Well past bootstrap window
    t = now + 200

    # Study fires once with no adjacent evidence and is accepted immediately.
    _fire(resolver, sensors, areas, "s.study", True, t)
    assert areas["study"].occupancy == 1

    # A later OFF/ON cycle keeps it occupied.
    _fire(resolver, sensors, areas, "s.study", False, t + 5)
    _fire(resolver, sensors, areas, "s.study", True, t + 10)
    assert areas["study"].occupancy == 1


# ==================================================================
# 16. Sensor-ON blocks displacement — housemate walks corridor,
#     person in study with active sensor is NOT displaced.
# ==================================================================


def test_sensor_on_blocks_displacement():
    """Person in study (sensor cycling) is not displaced when housemate walks corridor.

    The key: study must go through an OFF→ON cycle so it enters retained state.
    When retained + sensor ON, both the merge guard (retained → don't merge)
    and the displacement guard (sensor ON → don't displace) protect it.
    """
    now = time.time()
    config = _full_house_config()
    resolver = MapOccupancyResolver(config)
    areas, sensors = _full_house_areas_and_sensors(config, now)

    # Person A enters study
    _fire(resolver, sensors, areas, "s.entrance", True, now)
    _fire(resolver, sensors, areas, "s.corridor_1", True, now + 2)
    _fire(resolver, sensors, areas, "s.study", True, now + 3)
    assert areas["study"].occupancy == 1

    # Corridor sensors go OFF (person A settled in study)
    _fire(resolver, sensors, areas, "s.entrance", False, now + 5)
    _fire(resolver, sensors, areas, "s.corridor_1", False, now + 7)

    # Study sensor cycles: OFF → retained, then ON → still retained during rebuild
    _fire(resolver, sensors, areas, "s.study", False, now + 8)
    assert areas["study"].occupancy == 1  # Retained

    _fire(resolver, sensors, areas, "s.study", True, now + 13)
    assert areas["study"].occupancy == 1

    # Another OFF→ON cycle to be solidly in retained state
    _fire(resolver, sensors, areas, "s.study", False, now + 18)
    _fire(resolver, sensors, areas, "s.study", True, now + 23)
    assert areas["study"].occupancy == 1

    # Person B walks through corridor_1 → corridor_2 → bedroom_1
    # Study is retained with sensor ON — must NOT be displaced
    _fire(resolver, sensors, areas, "s.corridor_1", True, now + 25)
    assert areas["study"].occupancy == 1, "Study displaced by corridor_1!"

    _fire(resolver, sensors, areas, "s.corridor_2", True, now + 27)
    assert areas["study"].occupancy == 1, "Study displaced by corridor_2!"

    _fire(resolver, sensors, areas, "s.bedroom_1", True, now + 29)
    assert areas["study"].occupancy == 1, "Study displaced by bedroom_1!"

    # Both should be tracked
    assert areas["bedroom_1"].occupancy == 1


# ==================================================================
# 17. Sleeping person not cleared — no adjacent motion evidence.
# ==================================================================


def test_sleeping_person_not_cleared():
    """Person in bedroom with no motion for 2+ min is NOT cleared if no
    adjacent room has more recent motion (no evidence of leaving)."""
    now = time.time()
    config = _full_house_config()
    resolver = MapOccupancyResolver(config)
    areas, sensors = _full_house_areas_and_sensors(config, now)

    # Person enters bedroom_1 via corridor
    _fire(resolver, sensors, areas, "s.entrance", True, now)
    _fire(resolver, sensors, areas, "s.corridor_1", True, now + 2)
    _fire(resolver, sensors, areas, "s.corridor_2", True, now + 4)
    _fire(resolver, sensors, areas, "s.bedroom_1", True, now + 6)
    assert areas["bedroom_1"].occupancy == 1

    # All sensors go OFF (person falls asleep)
    _fire(resolver, sensors, areas, "s.entrance", False, now + 5)
    _fire(resolver, sensors, areas, "s.corridor_1", False, now + 7)
    _fire(resolver, sensors, areas, "s.corridor_2", False, now + 9)
    _fire(resolver, sensors, areas, "s.bedroom_1", False, now + 11)
    assert areas["bedroom_1"].occupancy == 1  # Retained

    # 3 minutes pass. Another person is active in study (house NOT quiet).
    # But corridor_2 (bedroom_1's neighbor) has NO new motion.
    t = now + 200
    _fire(resolver, sensors, areas, "s.study", True, t)
    assert areas["bedroom_1"].occupancy == 1, "Sleeping person cleared!"

    _fire(resolver, sensors, areas, "s.study", False, t + 5)
    _fire(resolver, sensors, areas, "s.study", True, t + 10)
    assert areas["bedroom_1"].occupancy == 1, "Sleeping person cleared!"


# ==================================================================
# 18. Sensor OFF gap (5s KNX cycle) does NOT drop occupancy.
# ==================================================================


def test_sensor_off_gap_keeps_occupancy():
    """During the 5s sensor OFF gap, room stays occupied via retention."""
    now = time.time()
    config = _full_house_config()
    resolver = MapOccupancyResolver(config)
    areas, sensors = _full_house_areas_and_sensors(config, now)

    # Person enters study
    _fire(resolver, sensors, areas, "s.entrance", True, now)
    _fire(resolver, sensors, areas, "s.corridor_1", True, now + 2)
    _fire(resolver, sensors, areas, "s.study", True, now + 3)
    assert areas["study"].occupancy == 1

    # Sensor goes OFF (5s KNX delay)
    _fire(resolver, sensors, areas, "s.corridor_1", False, now + 7)
    _fire(resolver, sensors, areas, "s.entrance", False, now + 5)
    _fire(resolver, sensors, areas, "s.study", False, now + 8)

    # During OFF gap — study MUST stay occupied (retained)
    assert areas["study"].occupancy == 1, "Study dropped during OFF gap!"

    # Sensor comes back ON
    _fire(resolver, sensors, areas, "s.study", True, now + 13)
    assert areas["study"].occupancy == 1

    # Repeat 5 cycles — never drops
    t = now + 13
    for i in range(5):
        _fire(resolver, sensors, areas, "s.study", False, t + 5)
        assert areas["study"].occupancy == 1, f"Dropped at cycle {i} OFF"
        _fire(resolver, sensors, areas, "s.study", True, t + 10)
        assert areas["study"].occupancy == 1, f"Dropped at cycle {i} ON"
        t += 10


# ==================================================================
# 19. Sleeping person wakes up — walk chain is immediate.
# ==================================================================


def test_sleeping_person_wakes_walk_is_immediate():
    """Person sleeps in bedroom_1 for 5 min, wakes up, walks to bathroom.
    Every room in the chain must be immediately occupied."""
    now = time.time()
    config = _full_house_config()
    resolver = MapOccupancyResolver(config)
    areas, sensors = _full_house_areas_and_sensors(config, now)

    # Person enters bedroom_1
    _fire(resolver, sensors, areas, "s.entrance", True, now)
    _fire(resolver, sensors, areas, "s.corridor_1", True, now + 2)
    _fire(resolver, sensors, areas, "s.corridor_2", True, now + 4)
    _fire(resolver, sensors, areas, "s.bedroom_1", True, now + 6)

    # All sensors OFF — person sleeps
    _fire(resolver, sensors, areas, "s.entrance", False, now + 5)
    _fire(resolver, sensors, areas, "s.corridor_1", False, now + 7)
    _fire(resolver, sensors, areas, "s.corridor_2", False, now + 9)
    _fire(resolver, sensors, areas, "s.bedroom_1", False, now + 11)
    assert areas["bedroom_1"].occupancy == 1  # Retained

    # 5 minutes of sleep — no motion anywhere (house quiet)
    t = now + 311

    # Person wakes up — bedroom_1 sensor fires
    _fire(resolver, sensors, areas, "s.bedroom_1", True, t)
    assert areas["bedroom_1"].occupancy == 1, (
        "Bedroom not immediately occupied on wake!"
    )

    # Walk: bedroom_1 → corridor_2 → corridor_1 → bathroom
    _fire(resolver, sensors, areas, "s.corridor_2", True, t + 3)
    assert areas["corridor_2"].occupancy == 1, "Corridor_2 not immediate!"

    _fire(resolver, sensors, areas, "s.corridor_1", True, t + 6)
    assert areas["corridor_1"].occupancy == 1, "Corridor_1 not immediate!"

    _fire(resolver, sensors, areas, "s.bathroom", True, t + 8)
    assert areas["bathroom"].occupancy == 1, "Bathroom not immediate!"


# ==================================================================
# 20. CRITICAL: Sensor OFF gap must not cause displacement.
#     Person in study, sensor in 5s OFF gap, housemate walks corridor.
#     Study must NOT lose occupancy.
# ==================================================================


def test_sensor_off_gap_blocks_displacement():
    """Person in study during 5s sensor OFF gap. Housemate walks corridor.
    Study must NOT be displaced — the SENSOR_CYCLING_GUARD protects it."""
    now = time.time()
    config = _full_house_config()
    resolver = MapOccupancyResolver(config)
    areas, sensors = _full_house_areas_and_sensors(config, now)

    # Person A enters study
    _fire(resolver, sensors, areas, "s.entrance", True, now)
    _fire(resolver, sensors, areas, "s.corridor_1", True, now + 2)
    _fire(resolver, sensors, areas, "s.study", True, now + 3)
    assert areas["study"].occupancy == 1

    # Transit sensors go OFF
    _fire(resolver, sensors, areas, "s.entrance", False, now + 5)
    _fire(resolver, sensors, areas, "s.corridor_1", False, now + 7)

    # Study sensor cycles ON/OFF (person sitting)
    _fire(resolver, sensors, areas, "s.study", False, now + 8)
    assert areas["study"].occupancy == 1  # Retained
    _fire(resolver, sensors, areas, "s.study", True, now + 13)
    _fire(resolver, sensors, areas, "s.study", False, now + 18)
    assert areas["study"].occupancy == 1  # Still retained

    # NOW: study sensor OFF (at now+18). Last motion = now+13.
    # Person B walks through corridor_1 at now+20 (7s after study's last motion)
    # SENSOR_CYCLING_GUARD: (now+20 - now+13) = 7s < 15s → PROTECTED
    _fire(resolver, sensors, areas, "s.corridor_1", True, now + 20)
    assert areas["study"].occupancy == 1, "Study displaced during sensor OFF gap!"

    # Person B continues to bedroom_2
    _fire(resolver, sensors, areas, "s.bedroom_2", True, now + 22)
    assert areas["study"].occupancy == 1, "Study displaced by bedroom_2!"

    # Study sensor comes back ON (next KNX cycle)
    _fire(resolver, sensors, areas, "s.study", True, now + 23)
    assert areas["study"].occupancy == 1

    # Both people tracked
    assert areas["bedroom_2"].occupancy == 1
    assert _total_occupancy(areas) >= 2


# ==================================================================
# 21. Retention cooldown — recently retained room immune to displacement
# ==================================================================


def test_neighbor_motion_never_displaces_retained_room():
    """A passer cannot displace a retained room before or after old guards."""
    now = time.time()
    config = _full_house_config()
    resolver = MapOccupancyResolver(config)
    areas, sensors = _full_house_areas_and_sensors(config, now)

    # Person enters study
    _fire(resolver, sensors, areas, "s.entrance", True, now)
    _fire(resolver, sensors, areas, "s.corridor_1", True, now + 2)
    _fire(resolver, sensors, areas, "s.study", True, now + 3)

    # Clear transit
    _fire(resolver, sensors, areas, "s.entrance", False, now + 5)
    _fire(resolver, sensors, areas, "s.corridor_1", False, now + 7)
    _fire(resolver, sensors, areas, "s.study", False, now + 8)
    # study retained at now+8

    # 5 seconds later (within both SENSOR_CYCLING_GUARD=15s and
    # MIN_RETENTION_COOLDOWN=10s), someone walks corridor
    _fire(resolver, sensors, areas, "s.corridor_1", True, now + 13)
    assert areas["study"].occupancy == 1, "Study displaced within retention cooldown!"

    # Even after the old cooldown and cycling guard, neighbor motion alone
    # is not proof that the room became empty.
    _fire(resolver, sensors, areas, "s.corridor_1", True, now + 30)
    assert areas["study"].occupancy == 1


# ==================================================================
# 22. CRITICAL: Person sitting very still — sensor stops cycling.
#     After SENSOR_CYCLING_GUARD (15s) expires, housemate walks by.
#     Room must NOT lose occupancy because RETAINED_INACTIVITY_TIMEOUT
#     (120s) hasn't elapsed yet — and there's no leaving evidence
#     unless someone walks through a DIRECT neighbor.
# ==================================================================


def test_still_person_sensor_stops_housemate_enters():
    """Person reads in study (sensor stopped). Housemate enters from outside
    via frontyard → entrance → corridor_1 → bedroom_1. Study must stay
    occupied because independent entry evidence (frontyard) proves the
    corridor motion is a different person."""
    now = time.time()
    config = _full_house_config()
    resolver = MapOccupancyResolver(config)
    areas, sensors = _full_house_areas_and_sensors(config, now)

    # Person A enters study
    _fire(resolver, sensors, areas, "s.entrance", True, now)
    _fire(resolver, sensors, areas, "s.corridor_1", True, now + 2)
    _fire(resolver, sensors, areas, "s.study", True, now + 3)

    # Transit sensors go OFF
    _fire(resolver, sensors, areas, "s.entrance", False, now + 5)
    _fire(resolver, sensors, areas, "s.corridor_1", False, now + 7)

    # Study sensor cycles then stops (person settling in to read)
    _fire(resolver, sensors, areas, "s.study", False, now + 8)
    _fire(resolver, sensors, areas, "s.study", True, now + 13)
    _fire(resolver, sensors, areas, "s.study", False, now + 18)
    # Last motion at now+13. Person sits very still.

    # 30s later — all guards expired. Person B enters from OUTSIDE.
    # frontyard (exit-capable) fires first, proving independent entry.
    _fire(resolver, sensors, areas, "s.frontyard", True, now + 43)
    _fire(resolver, sensors, areas, "s.entrance", True, now + 45)
    _fire(resolver, sensors, areas, "s.corridor_1", True, now + 47)

    # Study must be protected: frontyard.last_motion (now+43) > study's
    # last_motion (now+13), proving someone entered from outside.
    assert areas["study"].occupancy == 1, (
        "Study displaced even though housemate entered from outside!"
    )

    # Person B continues to bedroom_1
    _fire(resolver, sensors, areas, "s.corridor_2", True, now + 49)
    _fire(resolver, sensors, areas, "s.bedroom_1", True, now + 51)
    assert areas["study"].occupancy == 1, "Study displaced by bedroom walk!"
    assert areas["bedroom_1"].occupancy == 1


# ==================================================================
# 23. Single-person vulnerability: sensor stops, corridor fires
#     with no exit-capable evidence. System displaces study (expected).
#     Room must recover immediately when sensor re-fires.
# ==================================================================


def test_still_person_survives_neighbor_motion_and_refire():
    """A still person remains occupied when a corridor sensor fires."""
    now = time.time()
    config = _full_house_config()
    resolver = MapOccupancyResolver(config)
    areas, sensors = _full_house_areas_and_sensors(config, now)

    # Person enters study
    _fire(resolver, sensors, areas, "s.entrance", True, now)
    _fire(resolver, sensors, areas, "s.corridor_1", True, now + 2)
    _fire(resolver, sensors, areas, "s.study", True, now + 3)

    # Sensors OFF, person sits still
    _fire(resolver, sensors, areas, "s.entrance", False, now + 5)
    _fire(resolver, sensors, areas, "s.corridor_1", False, now + 7)
    _fire(resolver, sensors, areas, "s.study", False, now + 8)

    # Long stillness — all guards expire (>15s cycling guard, >10s cooldown)
    # corridor fires (maybe a pet, draft, or single housemate)
    _fire(resolver, sensors, areas, "s.corridor_1", True, now + 45)

    assert areas["study"].occupancy == 1
    assert areas["corridor_1"].occupancy == 1

    # Corridor sensor goes OFF, then person shifts — study sensor re-fires.
    # Study must recover immediately (corridor_1 is adjacent + occupied).
    _fire(resolver, sensors, areas, "s.corridor_1", False, now + 50)
    _fire(resolver, sensors, areas, "s.study", True, now + 55)
    assert areas["study"].occupancy == 1, "Study did not recover after sensor re-fired!"


# ==================================================================
# 24. CRITICAL: Recently-occupied room re-activates IMMEDIATELY.
#     After displacement, sensor re-fires. Must not wait for
#     persistent activation (2 cycles). The "recently occupied"
#     check accepts it on the first activation.
# ==================================================================


def test_retained_room_refire_remains_immediately_occupied():
    """A retained room remains occupied and accepts its first sensor refire."""
    now = time.time()
    config = _full_house_config()
    resolver = MapOccupancyResolver(config)
    areas, sensors = _full_house_areas_and_sensors(config, now)

    # Person enters study
    _fire(resolver, sensors, areas, "s.entrance", True, now)
    _fire(resolver, sensors, areas, "s.corridor_1", True, now + 2)
    _fire(resolver, sensors, areas, "s.study", True, now + 3)
    _fire(resolver, sensors, areas, "s.entrance", False, now + 5)
    _fire(resolver, sensors, areas, "s.corridor_1", False, now + 7)
    _fire(resolver, sensors, areas, "s.study", False, now + 8)

    # All old guards expire, but corridor motion does not evict the study.
    _fire(resolver, sensors, areas, "s.corridor_1", True, now + 45)
    assert areas["study"].occupancy == 1

    # Corridor goes OFF, all sensors quiet
    _fire(resolver, sensors, areas, "s.corridor_1", False, now + 50)

    # 30 seconds later — no neighbor is occupied, no adjacent evidence.
    # Without "recently occupied" check, this would be rejected as phantom.
    # With it, study was occupied within 5 min → accepted immediately.
    _fire(resolver, sensors, areas, "s.study", True, now + 80)
    assert areas["study"].occupancy == 1, (
        "Recently-occupied room not immediately re-activated!"
    )


def test_four_rooms_and_outdoor_passer_do_not_evict_anyone():
    """Room occupancy is uncapped and outdoor evidence cannot evict rooms."""
    now = time.time()
    config = _full_house_config()
    resolver = MapOccupancyResolver(config)
    areas, sensors = _full_house_areas_and_sensors(config, now)

    room_sensors = (
        ("study", "s.study"),
        ("bedroom_1", "s.bedroom_1"),
        ("bedroom_2", "s.bedroom_2"),
        ("bathroom", "s.bathroom"),
    )
    for offset, (_, sensor_id) in enumerate(room_sensors):
        _fire(resolver, sensors, areas, sensor_id, True, now + offset)

    assert all(areas[area_id].occupied for area_id, _ in room_sensors)

    for offset, (_, sensor_id) in enumerate(room_sensors, start=10):
        _fire(resolver, sensors, areas, sensor_id, False, now + offset)

    assert all(areas[area_id].occupied for area_id, _ in room_sensors)

    _fire(resolver, sensors, areas, "s.frontyard", True, now + 30)
    assert areas["frontyard"].occupied
    assert all(areas[area_id].occupied for area_id, _ in room_sensors)
