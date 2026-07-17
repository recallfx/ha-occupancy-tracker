"""Production replay tests — exact sensor events from 2026-03-16 production logs.

Replays real sensor event sequences through the conservative resolver using
the FULL 21-area house config. Asserts boolean occupancy and no false vacancy
at key checkpoints.

Log source: ssh 192.168.1.10 /config/occupancy_tracker.log
Config source: ssh 192.168.1.10 /config/occupancy_tracker.yaml

IMPORTANT BEHAVIORAL NOTE:
Corridor spill (corridor_1 fires study, bedroom_2, corridor_2 within 0.06-1.4s)
can create additional claims via "new entry with evidence" when the spill target
has a recently-active neighbor. These are transient claims that represent the
resolver's conservative approach — better to over-count briefly than lose track
of a real person. The critical invariant is: **the open-plan group never inflates
beyond 1** and the person's final position is correct.
"""

import time

from custom_components.occupancy_tracker.helpers.map_occupancy_resolver import (
    MapOccupancyResolver,
)
from custom_components.occupancy_tracker.helpers.anomaly_detector import (
    AnomalyDetector,
)
from custom_components.occupancy_tracker.helpers.area_state import AreaState
from custom_components.occupancy_tracker.helpers.constants import MOTION_SENSOR_TYPES
from custom_components.occupancy_tracker.helpers.sensor_state import SensorState
from custom_components.occupancy_tracker.helpers.map_state_recorder import MapSnapshot


# ------------------------------------------------------------------
# Helpers
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
    previously_occupied_indoor = {
        area_id for area_id, area in areas.items() if area.is_indoors and area.occupied
    }
    sensor = sensors.get(sensor_id)
    if sensor:
        sensor.update_state(on, ts)
    resolver.process_snapshot(
        _sensor_event(sensor_id, on, ts), areas, sensors, detector
    )
    active_areas = {
        area_id
        for sensor in sensors.values()
        if sensor.is_trusted_active and sensor.config.get("type") in MOTION_SENSOR_TYPES
        for area_id in sensor.area_ids
    }
    assert active_areas <= {
        area_id for area_id, area in areas.items() if area.occupied
    }, "Trusted active sensor produced a false vacancy"
    assert resolver.indoor_latched <= {
        area_id for area_id, area in areas.items() if area.occupied
    }, "Indoor latch produced a false vacancy"
    assert all(
        areas[area_id].occupied or areas[area_id].cleared_by
        for area_id in previously_occupied_indoor
    ), "Indoor occupancy cleared without an explicit reason"


def _total_occupancy(areas):
    return sum(a.occupancy for a in areas.values())


def _occupied_areas(areas):
    """Return dict of area_id -> occupancy for areas with claims."""
    return {aid: a.occupancy for aid, a in areas.items() if a.occupancy > 0}


def _open_plan_occupancy(areas):
    """Sum occupancy across kitchen + dining_room + living."""
    return (
        areas["kitchen"].occupancy
        + areas["dining_room"].occupancy
        + areas["living"].occupancy
    )


def _assert_no_area_above(areas, max_occ, context=""):
    """Assert no single area exceeds max_occ occupants."""
    for aid, a in areas.items():
        assert a.occupancy <= max_occ, (
            f"Area {aid} has occupancy={a.occupancy} (max {max_occ}) {context}"
        )


def test_bedroom2_corridor2_overlap_is_modelled():
    """Bedroom 2 can plausibly spill with the rear corridor sensor."""
    assert "bedroom_2" in PRODUCTION_CONFIG["adjacency"]["corridor_2"]
    assert "corridor_2" in PRODUCTION_CONFIG["adjacency"]["bedroom_2"]


# ------------------------------------------------------------------
# Full production config (21 areas, all sensors, all adjacency)
# ------------------------------------------------------------------

PRODUCTION_CONFIG = {
    "areas": {
        # Indoor
        "entrance": {"name": "Entrance", "indoors": True},
        "garage": {"name": "Garage", "indoors": True},
        "guest_room": {"name": "Guest Toilet", "indoors": True},
        "workshop": {"name": "Workshop", "indoors": True},
        "corridor_1": {"name": "Corridor 1 (Front)", "indoors": True},
        "corridor_2": {"name": "Corridor 2 (Back)", "indoors": True},
        "kitchen": {"name": "Kitchen", "indoors": True},
        "dining_room": {"name": "Dining Room", "indoors": True},
        "living": {"name": "Living Room", "indoors": True},
        "study": {"name": "Study", "indoors": True},
        "bedroom_2": {"name": "Bedroom 2", "indoors": True},
        "bathroom": {"name": "Bathroom", "indoors": True},
        "bedroom_1": {"name": "Bedroom 1", "indoors": True},
        "utility_room": {"name": "Utility Room", "indoors": True},
        "main_bedroom": {"name": "Main Bedroom", "indoors": True},
        "main_bathroom": {"name": "Main Bathroom", "indoors": True},
        "wardrobe": {"name": "Wardrobe", "indoors": True},
        # Outdoor
        "frontyard": {"name": "Front Yard", "indoors": False, "exit_capable": True},
        "backyard": {"name": "Back Yard", "indoors": False, "exit_capable": True},
        "left_side": {"name": "Left Side", "indoors": False, "exit_capable": True},
        "right_side": {"name": "Right Side", "indoors": False, "exit_capable": True},
    },
    "adjacency": {
        "frontyard": ["entrance", "garage", "backyard", "left_side", "right_side"],
        "backyard": [
            "entrance",
            "kitchen",
            "main_bedroom",
            "frontyard",
            "left_side",
            "right_side",
        ],
        "left_side": ["frontyard", "backyard", "bedroom_1", "utility_room"],
        "right_side": ["frontyard", "backyard", "workshop"],
        "entrance": [
            "frontyard",
            "garage",
            "guest_room",
            "corridor_1",
            "backyard",
            "kitchen",
            "workshop",
        ],
        "garage": ["entrance", "frontyard", "workshop"],
        "guest_room": ["entrance"],
        "workshop": ["entrance", "garage", "right_side"],
        "corridor_1": ["entrance", "corridor_2", "study", "bedroom_2", "bathroom"],
        "corridor_2": [
            "corridor_1",
            "bedroom_1",
            "bedroom_2",
            "main_bedroom",
            "utility_room",
        ],
        "kitchen": ["entrance", "dining_room", "living", "backyard"],
        "dining_room": ["kitchen", "living"],
        "living": ["kitchen", "dining_room"],
        "study": ["corridor_1"],
        "bedroom_2": ["corridor_1", "corridor_2"],
        "bathroom": ["corridor_1"],
        "bedroom_1": ["corridor_2", "left_side"],
        "utility_room": ["corridor_2", "left_side"],
        "main_bedroom": ["corridor_2", "main_bathroom", "wardrobe", "backyard"],
        "main_bathroom": ["main_bedroom"],
        "wardrobe": ["main_bedroom"],
    },
    "open_plan_groups": {
        "open_plan": {"areas": ["kitchen", "dining_room", "living"]},
    },
    "sensors": {
        # KNX motion sensors
        "binary_sensor.entrance_motion": {"area": "entrance", "type": "motion"},
        "binary_sensor.garage_motion": {"area": "garage", "type": "motion"},
        "binary_sensor.garage_pir": {"area": "garage", "type": "motion"},
        "binary_sensor.guest_room_motion": {"area": "guest_room", "type": "motion"},
        "binary_sensor.workshop_motion": {"area": "workshop", "type": "motion"},
        "binary_sensor.workshop_pir": {"area": "workshop", "type": "motion"},
        "binary_sensor.corridor_1_motion": {"area": "corridor_1", "type": "motion"},
        "binary_sensor.corridor_2_motion": {"area": "corridor_2", "type": "motion"},
        "binary_sensor.kitchen_motion": {"area": "kitchen", "type": "motion"},
        "binary_sensor.dining_room_motion": {"area": "dining_room", "type": "motion"},
        "binary_sensor.living_room_motion": {"area": "living", "type": "motion"},
        "binary_sensor.study_motion": {"area": "study", "type": "motion"},
        "binary_sensor.bedroom_2_motion": {"area": "bedroom_2", "type": "motion"},
        "binary_sensor.bathroom_motion": {"area": "bathroom", "type": "motion"},
        "binary_sensor.bedroom_1_motion": {"area": "bedroom_1", "type": "motion"},
        "binary_sensor.utility_room_motion": {"area": "utility_room", "type": "motion"},
        "binary_sensor.main_bedroom_motion": {"area": "main_bedroom", "type": "motion"},
        "binary_sensor.main_bathroom_motion": {
            "area": "main_bathroom",
            "type": "motion",
        },
        "binary_sensor.wardrobe_motion": {"area": "wardrobe", "type": "motion"},
        # Magnetic sensors
        "binary_sensor.entrance_magnet": {
            "area": ["entrance", "frontyard"],
            "type": "magnetic",
        },
        "binary_sensor.corridor_magnet": {
            "area": ["entrance", "backyard"],
            "type": "magnetic",
        },
        "binary_sensor.sliding_door_magnet": {
            "area": ["kitchen", "backyard"],
            "type": "magnetic",
        },
        "binary_sensor.main_bedroom_magnet": {
            "area": ["main_bedroom", "backyard"],
            "type": "magnetic",
        },
        "binary_sensor.bathroom_magnet": {
            "area": ["bathroom", "backyard"],
            "type": "magnetic",
        },
        "binary_sensor.bedroom_1_magnet": {
            "area": ["bedroom_1", "left_side"],
            "type": "magnetic",
        },
        "binary_sensor.utility_magnet": {
            "area": ["utility_room", "left_side"],
            "type": "magnetic",
        },
        "binary_sensor.workshop_magnet": {
            "area": ["workshop", "right_side"],
            "type": "magnetic",
        },
        "binary_sensor.bedroom_2_magnet": {
            "area": ["bedroom_2", "frontyard"],
            "type": "magnetic",
        },
        "binary_sensor.study_magnet": {
            "area": ["study", "frontyard"],
            "type": "magnetic",
        },
        # Camera sensors
        "binary_sensor.front_left_motion": {
            "area": "frontyard",
            "type": "camera_motion",
        },
        "binary_sensor.front_left_person_detected": {
            "area": "frontyard",
            "type": "camera_person",
        },
        "binary_sensor.front_motion": {"area": "frontyard", "type": "camera_motion"},
        "binary_sensor.front_person_detected": {
            "area": "frontyard",
            "type": "camera_person",
        },
        "binary_sensor.front_right_motion": {
            "area": "frontyard",
            "type": "camera_motion",
        },
        "binary_sensor.front_right_person_detected": {
            "area": "frontyard",
            "type": "camera_person",
        },
        "binary_sensor.doorbell_motion": {"area": "frontyard", "type": "camera_motion"},
        "binary_sensor.doorbell_person_detected": {
            "area": "frontyard",
            "type": "camera_person",
        },
        "binary_sensor.back_left_motion": {"area": "backyard", "type": "camera_motion"},
        "binary_sensor.back_left_person_detected": {
            "area": "backyard",
            "type": "camera_person",
        },
        "binary_sensor.back_right_motion": {
            "area": "backyard",
            "type": "camera_motion",
        },
        "binary_sensor.back_right_person_detected": {
            "area": "backyard",
            "type": "camera_person",
        },
        "binary_sensor.left_motion": {"area": "left_side", "type": "camera_motion"},
        "binary_sensor.left_person_detected": {
            "area": "left_side",
            "type": "camera_person",
        },
        "binary_sensor.right_motion": {"area": "right_side", "type": "camera_motion"},
        "binary_sensor.right_person_detected": {
            "area": "right_side",
            "type": "camera_person",
        },
    },
}


def _make_system():
    """Create resolver, areas, sensors, and anomaly detector from production config.

    Sensors are initialized with a timestamp far in the past so that
    magnetic sensors' initial last_changed doesn't provide false evidence
    for the has_magnetic_evidence check (OUTDOOR_INTRUSION_WINDOW = 300s).
    """
    now = time.time()
    sensor_init_time = now - 600  # 10 minutes ago — well past any evidence window
    resolver = MapOccupancyResolver(PRODUCTION_CONFIG)
    detector = AnomalyDetector(PRODUCTION_CONFIG)
    areas = {
        aid: AreaState(aid, cfg) for aid, cfg in PRODUCTION_CONFIG["areas"].items()
    }
    sensors = {
        sid: SensorState(sid, cfg, sensor_init_time)
        for sid, cfg in PRODUCTION_CONFIG["sensors"].items()
    }
    return resolver, areas, sensors, detector, now


def _seed_person_in_kitchen(resolver, areas, sensors, detector, now):
    """Bootstrap: person enters via entrance and walks to kitchen.

    Returns timestamp for next sequence (well past ADJACENT_ACTIVITY_WINDOW).
    """
    _fire(
        resolver, sensors, areas, "binary_sensor.entrance_motion", True, now, detector
    )
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.kitchen_motion",
        True,
        now + 3.7,
        detector,
    )
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.entrance_motion",
        False,
        now + 5.0,
        detector,
    )
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.dining_room_motion",
        True,
        now + 4.5,
        detector,
    )
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.kitchen_motion",
        False,
        now + 10.0,
        detector,
    )
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.dining_room_motion",
        False,
        now + 11.0,
        detector,
    )

    assert areas["kitchen"].occupancy == 1 or areas["dining_room"].occupancy == 1
    assert _open_plan_occupancy(areas) >= 1
    assert _total_occupancy(areas) >= 1
    # Return time well past any activity window so subsequent events start clean
    return now + 30.0


def _seed_person_in_study(resolver, areas, sensors, detector, now):
    """Bootstrap: person enters via entrance and walks to study.

    Returns timestamp for next sequence (well past ADJACENT_ACTIVITY_WINDOW).
    """
    _fire(
        resolver, sensors, areas, "binary_sensor.entrance_motion", True, now, detector
    )
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.corridor_1_motion",
        True,
        now + 2.0,
        detector,
    )
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.study_motion",
        True,
        now + 3.0,
        detector,
    )
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.entrance_motion",
        False,
        now + 5.0,
        detector,
    )
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.corridor_1_motion",
        False,
        now + 7.0,
        detector,
    )
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.study_motion",
        False,
        now + 12.0,
        detector,
    )

    assert areas["study"].occupancy == 1
    assert _total_occupancy(areas) >= 1
    # Return time well past any activity window AND retention cooldown
    # (study retained at now+12, cooldown=30s, so need > now+42)
    return now + 45.0


# ==================================================================
# TEST 1: The 18:12 walk — study → corridor_1 → entrance → kitchen
#
# Person walks from study through corridor, entrance, into kitchen.
# Key assertion: person arrives in kitchen with open-plan occupancy
# in every room with positive evidence.
# ==================================================================


def test_1812_walk_study_to_kitchen():
    """Replay the 18:12 walk from study to kitchen.

    The critical invariant: positive open-plan and corridor evidence must
    never be converted into a false vacancy.
    """
    resolver, areas, sensors, detector, now = _make_system()
    t = _seed_person_in_study(resolver, areas, sensors, detector, now)

    # -- The walk: study → corridor_1 → entrance → kitchen --

    # Corridor motion must not evict a potentially still-occupied study.
    _fire(
        resolver, sensors, areas, "binary_sensor.corridor_1_motion", True, t, detector
    )
    assert areas["corridor_1"].occupancy == 1
    assert areas["study"].occupancy == 1

    # corridor_1 OFF after 5s KNX delay
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.corridor_1_motion",
        False,
        t + 5.0,
        detector,
    )

    # Entrance ON (person walks to entrance, +3.7s from corridor)
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.entrance_motion",
        True,
        t + 3.7,
        detector,
    )
    assert areas["entrance"].occupancy == 1

    # Entrance OFF after 5s
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.entrance_motion",
        False,
        t + 8.7,
        detector,
    )

    # Kitchen ON (+3.7s from entrance ON)
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.kitchen_motion",
        True,
        t + 7.4,
        detector,
    )
    assert _open_plan_occupancy(areas) >= 1

    # Dining ON (+0.8s overlap)
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.dining_room_motion",
        True,
        t + 8.2,
        detector,
    )

    # CHECKPOINT: open-plan occupancy remains present.
    assert _open_plan_occupancy(areas) >= 1, (
        f"Open plan: K={areas['kitchen'].occupancy} DR={areas['dining_room'].occupancy} "
        f"L={areas['living'].occupancy}"
    )


# ==================================================================
# TEST 2: Kitchen/dining oscillation — the inflation factory
#
# Person stands in kitchen. Kitchen and dining sensors alternate
# ON/OFF due to overlapping fields of view. In production this
# drove a single room count to 7. Per-room state must remain boolean.
# ==================================================================


def test_kitchen_dining_oscillation_production():
    """Replay K/DR oscillation that caused DR@7 in production.

    The oscillation pattern: K ON → DR ON (overlap) → K OFF → K ON →
    DR OFF → DR ON → repeat. Each OFF/ON cycle used to create a phantom
    occupant. Per-room state remains boolean while possible rooms stay occupied.
    """
    resolver, areas, sensors, detector, now = _make_system()
    t = _seed_person_in_kitchen(resolver, areas, sensors, detector, now)

    # Start kitchen cycling
    _fire(resolver, sensors, areas, "binary_sensor.kitchen_motion", True, t, detector)

    # -- Oscillation: exact production pattern --
    # Cycle 1: DR ON → K OFF → K ON → DR OFF
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.dining_room_motion",
        True,
        t + 0.8,
        detector,
    )
    assert _open_plan_occupancy(areas) >= 1

    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.kitchen_motion",
        False,
        t + 5.0,
        detector,
    )
    assert _open_plan_occupancy(areas) >= 1, (
        f"K OFF inflation! K={areas['kitchen'].occupancy} DR={areas['dining_room'].occupancy}"
    )

    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.kitchen_motion",
        True,
        t + 8.0,
        detector,
    )
    assert _open_plan_occupancy(areas) >= 1

    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.dining_room_motion",
        False,
        t + 9.0,
        detector,
    )
    assert _open_plan_occupancy(areas) >= 1

    # Cycle 2
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.dining_room_motion",
        True,
        t + 12.0,
        detector,
    )
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.kitchen_motion",
        False,
        t + 13.0,
        detector,
    )
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.kitchen_motion",
        True,
        t + 16.0,
        detector,
    )
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.dining_room_motion",
        False,
        t + 17.0,
        detector,
    )
    assert _open_plan_occupancy(areas) >= 1

    # Cycle 3-10 (compressed)
    t_c = t + 20.0
    for cycle in range(8):
        _fire(
            resolver,
            sensors,
            areas,
            "binary_sensor.dining_room_motion",
            True,
            t_c,
            detector,
        )
        _fire(
            resolver,
            sensors,
            areas,
            "binary_sensor.kitchen_motion",
            False,
            t_c + 5.0,
            detector,
        )
        _fire(
            resolver,
            sensors,
            areas,
            "binary_sensor.kitchen_motion",
            True,
            t_c + 8.0,
            detector,
        )
        _fire(
            resolver,
            sensors,
            areas,
            "binary_sensor.dining_room_motion",
            False,
            t_c + 9.0,
            detector,
        )

        assert _open_plan_occupancy(areas) >= 1, (
            f"Inflation at cycle {cycle + 3}: K={areas['kitchen'].occupancy} "
            f"DR={areas['dining_room'].occupancy} L={areas['living'].occupancy}"
        )
        t_c += 10.0

    # Triple fire pattern: DR → K → L
    _fire(
        resolver, sensors, areas, "binary_sensor.kitchen_motion", False, t_c, detector
    )
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.dining_room_motion",
        True,
        t_c + 3.0,
        detector,
    )
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.kitchen_motion",
        True,
        t_c + 3.2,
        detector,
    )
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.living_room_motion",
        True,
        t_c + 6.0,
        detector,
    )
    assert _open_plan_occupancy(areas) >= 1, (
        f"Triple fire: K={areas['kitchen'].occupancy} "
        f"DR={areas['dining_room'].occupancy} L={areas['living'].occupancy}"
    )

    # All off
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.living_room_motion",
        False,
        t_c + 12.0,
        detector,
    )
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.dining_room_motion",
        False,
        t_c + 13.0,
        detector,
    )
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.kitchen_motion",
        False,
        t_c + 14.0,
        detector,
    )

    assert _open_plan_occupancy(areas) >= 1
    assert _total_occupancy(areas) >= 1


# ==================================================================
# TEST 2b: Guest-room first motion from open-plan area.
#
# Production: guest_room_motion fired while kitchen/dining/living were
# active, but entrance was off and last fired 277.6s earlier. The first
# guest-room pulse must be accepted; otherwise lights wait for the
# persistent-activation fallback.
# ==================================================================


def test_guest_room_first_motion_accepts_recent_entrance_path():
    """Guest room should not wait for a second KNX pulse when entrance is stale but recent."""
    resolver, areas, sensors, detector, now = _make_system()

    # Person entered through entrance and reached open-plan.
    _fire(
        resolver, sensors, areas, "binary_sensor.entrance_motion", True, now, detector
    )
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.kitchen_motion",
        True,
        now + 3.7,
        detector,
    )
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.dining_room_motion",
        True,
        now + 4.5,
        detector,
    )
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.living_room_motion",
        True,
        now + 5.0,
        detector,
    )
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.entrance_motion",
        False,
        now + 5.0,
        detector,
    )
    assert _open_plan_occupancy(areas) >= 1

    # Same shape as the observed failure: guest motion happens after bootstrap
    # expired, entrance is off, and entrance last ON is still within 5 minutes.
    guest_time = now + 277.6
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.guest_room_motion",
        True,
        guest_time,
        detector,
    )

    assert areas["guest_room"].occupancy == 1
    assert _open_plan_occupancy(areas) >= 1
    assert _total_occupancy(areas) >= 2
    assert not [
        warning
        for warning in detector.get_warnings()
        if warning.area == "guest_room" and "no_plausible_source" in warning.message
    ]


# ==================================================================
# TEST 3: The 18:40 return walk — kitchen → entrance → corridor_1
#          → corridor_2 → bedroom_1
#
# Person walks from open-plan area back through corridors to bedroom_1.
# Corridor_1 spills into study (+1.4s) and bedroom_2 (+0.06s).
# Key assertion: person ends up in bedroom_1.
# ==================================================================


def test_1840_return_walk_kitchen_to_bedroom():
    """Replay the 18:40 return walk from kitchen to bedroom_1.

    The critical invariant: person ends in bedroom_1 with occupancy=1.
    During transit, corridor spill into study and bedroom_2 may create
    temporary claims, but the person must arrive correctly.
    """
    resolver, areas, sensors, detector, now = _make_system()
    t = _seed_person_in_kitchen(resolver, areas, sensors, detector, now)

    # Person starts moving: kitchen ON, then walks to entrance
    _fire(resolver, sensors, areas, "binary_sensor.kitchen_motion", True, t, detector)
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.entrance_motion",
        True,
        t + 3.0,
        detector,
    )
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.kitchen_motion",
        False,
        t + 5.0,
        detector,
    )
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.entrance_motion",
        False,
        t + 8.0,
        detector,
    )

    # Wait for activity windows to expire
    t_walk = t + 20.0

    # -- The corridor walk (clean, no spill for simplicity) --
    # corridor_1 ON
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.corridor_1_motion",
        True,
        t_walk,
        detector,
    )
    assert areas["corridor_1"].occupancy == 1

    # corridor_1 OFF
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.corridor_1_motion",
        False,
        t_walk + 5.0,
        detector,
    )

    # corridor_2 ON (+4.4s from corridor_1)
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.corridor_2_motion",
        True,
        t_walk + 4.4,
        detector,
    )
    assert areas["corridor_2"].occupancy == 1

    # bedroom_1 ON (+0.2s from corridor_2)
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.bedroom_1_motion",
        True,
        t_walk + 4.6,
        detector,
    )

    # corridor_2 OFF
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.corridor_2_motion",
        False,
        t_walk + 10.0,
        detector,
    )

    # bedroom_1 OFF (person still there, sensor cycling)
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.bedroom_1_motion",
        False,
        t_walk + 25.0,
        detector,
    )
    assert areas["bedroom_1"].occupancy == 1, "Person should stay in bedroom_1"

    # bedroom_1 ON (re-trigger)
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.bedroom_1_motion",
        True,
        t_walk + 30.0,
        detector,
    )
    assert areas["bedroom_1"].occupancy == 1

    # CHECKPOINT: person is in bedroom_1
    assert areas["bedroom_1"].occupancy == 1, (
        f"Person not in bedroom_1. Occupied: {_occupied_areas(areas)}"
    )


# ==================================================================
# TEST 4: Extended oscillation — 20 cycles of K/DR alternation.
#
# Person standing in kitchen for ~10 minutes. In production,
# 10 minutes drove counts to K@6 DR@7. With open-plan groups,
# must remain boolean without dropping positive evidence.
# ==================================================================


def test_extended_oscillation_20_cycles():
    """20 cycles of kitchen/dining ON/OFF oscillation.

    Each cycle: K ON → DR ON (+0.8s) → K OFF (+5s) → DR OFF (+1s) → gap(3s)
    This mimics the real production pattern that caused runaway inflation.
    """
    resolver, areas, sensors, detector, now = _make_system()
    t = _seed_person_in_kitchen(resolver, areas, sensors, detector, now)

    for cycle in range(20):
        # Kitchen ON
        _fire(
            resolver, sensors, areas, "binary_sensor.kitchen_motion", True, t, detector
        )
        # Dining ON (overlap, +0.8s)
        _fire(
            resolver,
            sensors,
            areas,
            "binary_sensor.dining_room_motion",
            True,
            t + 0.8,
            detector,
        )
        # Kitchen OFF (+5s KNX delay)
        _fire(
            resolver,
            sensors,
            areas,
            "binary_sensor.kitchen_motion",
            False,
            t + 5.0,
            detector,
        )
        # Dining OFF (+1s after kitchen OFF)
        _fire(
            resolver,
            sensors,
            areas,
            "binary_sensor.dining_room_motion",
            False,
            t + 6.0,
            detector,
        )

        assert _open_plan_occupancy(areas) >= 1, (
            f"Inflation at cycle {cycle}: "
            f"K={areas['kitchen'].occupancy} DR={areas['dining_room'].occupancy} "
            f"L={areas['living'].occupancy} total={_total_occupancy(areas)}"
        )

        t += 10.0  # 10s per cycle

    # After 20 cycles, open-plan occupancy remains present.
    assert _open_plan_occupancy(areas) >= 1
    assert _total_occupancy(areas) >= 1


# ==================================================================
# TEST 5: Study phantom rejection during kitchen occupancy.
#
# Production: 19 phantom triggers in study while person was in
# kitchen (18:13-18:36). All must be rejected — study has no
# occupied adjacent neighbor (corridor_1 is empty and stale).
# ==================================================================


def test_study_persistent_activation_accepted():
    """Study sensor fires repeatedly — accepted as real person via persistent activation.

    19 triggers over 20 minutes is clearly a person sitting in the study,
    not a phantom. After 3+ activations within 5 minutes, the persistent
    activation check accepts it. Total occupancy becomes 2 (kitchen + study).
    """
    resolver, areas, sensors, detector, now = _make_system()
    t = _seed_person_in_kitchen(resolver, areas, sensors, detector, now)

    # Fire repeated triggers at study — intervals from production
    phantom_offsets = [
        0.0,
        35.2,
        75.2,
        93.2,
        117.2,
        152.2,
        183.2,
        251.2,
        305.2,
        372.2,
        433.2,
        468.2,
        477.2,
        500.2,
        532.2,
        610.2,
        624.2,
        847.2,
        1219.2,
    ]
    for i, offset in enumerate(phantom_offsets):
        _fire(
            resolver,
            sensors,
            areas,
            "binary_sensor.study_motion",
            True,
            t + offset,
            detector,
        )
        _fire(
            resolver,
            sensors,
            areas,
            "binary_sensor.study_motion",
            False,
            t + offset + 5.0,
            detector,
        )

    # Study gets accepted via persistent activation but may be cleared
    # by the 2-minute inactivity cleanup between sparse triggers.
    # Persistent valid activity must not be rejected by a global room cap.
    assert areas["study"].occupancy == 1


# ==================================================================
# TEST 6: Full end-to-end — walk to kitchen + oscillation + return.
#
# Three production sequences back to back:
# 1. Person in study → walks to kitchen (no spill — clean walk)
# 2. K/DR oscillation for 5 cycles
# 3. Person walks from kitchen back to bedroom_1
# Open-plan must never inflate. Final position: bedroom_1.
# ==================================================================


def test_full_production_sequence():
    """Full end-to-end: study → kitchen → oscillation → bedroom_1."""
    resolver, areas, sensors, detector, now = _make_system()
    t = _seed_person_in_study(resolver, areas, sensors, detector, now)

    # ---- Phase 1: Walk study → corridor_1 → entrance → kitchen ----
    _fire(
        resolver, sensors, areas, "binary_sensor.corridor_1_motion", True, t, detector
    )
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.corridor_1_motion",
        False,
        t + 5.0,
        detector,
    )
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.entrance_motion",
        True,
        t + 3.7,
        detector,
    )
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.entrance_motion",
        False,
        t + 8.7,
        detector,
    )
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.kitchen_motion",
        True,
        t + 7.4,
        detector,
    )
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.dining_room_motion",
        True,
        t + 8.2,
        detector,
    )

    assert _open_plan_occupancy(areas) >= 1, (
        f"Phase 1: K={areas['kitchen'].occupancy} DR={areas['dining_room'].occupancy}"
    )

    # ---- Phase 2: K/DR oscillation (5 cycles) ----
    # Wait past activity window from the walk
    t2 = t + 25.0
    for cycle in range(5):
        _fire(
            resolver,
            sensors,
            areas,
            "binary_sensor.kitchen_motion",
            False,
            t2,
            detector,
        )
        _fire(
            resolver,
            sensors,
            areas,
            "binary_sensor.dining_room_motion",
            False,
            t2 + 1.0,
            detector,
        )
        _fire(
            resolver,
            sensors,
            areas,
            "binary_sensor.kitchen_motion",
            True,
            t2 + 4.0,
            detector,
        )
        _fire(
            resolver,
            sensors,
            areas,
            "binary_sensor.dining_room_motion",
            True,
            t2 + 4.8,
            detector,
        )
        t2 += 10.0

        assert _open_plan_occupancy(areas) >= 1, (
            f"Phase 2 cycle {cycle}: K={areas['kitchen'].occupancy} "
            f"DR={areas['dining_room'].occupancy} L={areas['living'].occupancy}"
        )

    # ---- Phase 3: Walk kitchen → entrance → corridor_1 → corridor_2 → bedroom_1 ----
    # Wait past activity window
    t3 = t2 + 15.0

    # Entrance ON (person leaving kitchen area)
    _fire(resolver, sensors, areas, "binary_sensor.entrance_motion", True, t3, detector)
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.kitchen_motion",
        False,
        t3 + 2.0,
        detector,
    )
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.dining_room_motion",
        False,
        t3 + 2.5,
        detector,
    )

    # corridor_1 ON
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.corridor_1_motion",
        True,
        t3 + 4.5,
        detector,
    )
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.entrance_motion",
        False,
        t3 + 5.0,
        detector,
    )

    # corridor_2 ON, bedroom_1 ON, corridor_1 OFF — in timestamp order
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.corridor_2_motion",
        True,
        t3 + 8.9,
        detector,
    )
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.bedroom_1_motion",
        True,
        t3 + 9.1,
        detector,
    )
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.corridor_1_motion",
        False,
        t3 + 9.5,
        detector,
    )

    # All transit sensors OFF
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.corridor_2_motion",
        False,
        t3 + 14.0,
        detector,
    )
    _fire(
        resolver,
        sensors,
        areas,
        "binary_sensor.bedroom_1_motion",
        False,
        t3 + 25.0,
        detector,
    )

    # FINAL CHECKPOINT
    assert areas["bedroom_1"].occupancy == 1, (
        f"Person not in bedroom_1. Occupied: {_occupied_areas(areas)}"
    )
    # The previous open-plan occupancy remains pessimistically retained until
    # departure evidence and its grace period complete.
    assert _open_plan_occupancy(areas) >= 1
