"""Production replay tests: recorded sensor events on the real house layout.

The event sequences come from the 2026-03-16 production logs and from the
observed detector behavior described in the occupancy data analysis: KNX
detectors report OFF about 5 s after the last movement, the kitchen and dining
detectors overlap, and corridor_2 spills into the bedroom doorways within
half a second.

The house layout is read straight from the shipped ``config.yaml``, so the
replay and production cannot drift apart.

What the replay asserts is the exit-gated contract:

- A room that was left releases at its hold deadline, because an exit fired in
  the trail window right after its own activity stopped.
- A room with no departure trail keeps its occupancy, bounded by the retention
  ceiling of its profile.
- Corridors are transition rooms and never retain.
- An activation that starts within the spill window of a neighbour's is not
  evidence anybody entered, and is never retained.
- Where the recorded events genuinely cannot say which room of an overlapping
  pair holds the person, the assertion is on the group, not on the room.
"""

from __future__ import annotations

from pathlib import Path
import time

import pytest
import yaml

from custom_components.occupancy_tracker.helpers.anomaly_detector import (
    AnomalyDetector,
)
from custom_components.occupancy_tracker.helpers.area_state import AreaState
from custom_components.occupancy_tracker.helpers.constants import MOTION_SENSOR_TYPES
from custom_components.occupancy_tracker.helpers.map_occupancy_resolver import (
    MapOccupancyResolver,
)
from custom_components.occupancy_tracker.helpers.map_state_recorder import MapSnapshot
from custom_components.occupancy_tracker.helpers.occupancy_engine import (
    STATE_OCCUPIED,
    STATE_PENDING,
    STATE_RETAINED,
    STATE_VACANT,
)
from custom_components.occupancy_tracker.helpers.room_profiles import ROOM_PROFILES
from custom_components.occupancy_tracker.helpers.sensor_state import SensorState

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"
PRODUCTION_CONFIG = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))

HOLD = ROOM_PROFILES["default"].hold_seconds
TRANSITION_HOLD = ROOM_PROFILES["transition"].hold_seconds
LIVING_CEILING = ROOM_PROFILES["living"].retention_ceiling_seconds
SLEEPING_CEILING = ROOM_PROFILES["sleeping"].retention_ceiling_seconds

OPEN_PLAN = ("kitchen", "dining_room", "living")

#: Every way the engine is allowed to take occupancy away from a room.
RELEASE_REASONS = frozenset(
    {
        "departure_trail",
        "unconfirmed_entry",
        "transition_room",
        "retention_ceiling",
        "manual_clear",
        "manual_service",
        "manual_button",
    }
)


class Replay:
    """Feed recorded sensor events into the resolver on a virtual clock.

    Every timestamp is an offset in seconds from the start of the replay, so
    the recorded gaps stay readable. Sensors start ten minutes in the past so
    that no initial state looks like recent evidence.
    """

    def __init__(self) -> None:
        self.start = time.time()
        self.resolver = MapOccupancyResolver(PRODUCTION_CONFIG)
        self.detector = AnomalyDetector(PRODUCTION_CONFIG)
        self.areas = {
            area_id: AreaState(area_id, area_config)
            for area_id, area_config in PRODUCTION_CONFIG["areas"].items()
        }
        self.sensors = {
            sensor_id: SensorState(sensor_id, sensor_config, self.start - 600)
            for sensor_id, sensor_config in PRODUCTION_CONFIG["sensors"].items()
        }

    # -- driving --------------------------------------------------------

    def fire(self, sensor_id: str, on: bool, at: float) -> None:
        """Replay one sensor edge, the way the coordinator would apply it."""
        timestamp = self.start + at
        before = self._occupied_indoor()
        sensor = self.sensors[sensor_id]
        sensor.update_state(on, timestamp)
        self.resolver.process_snapshot(
            MapSnapshot(
                timestamp=timestamp,
                event_type="sensor",
                description=f"sensor:{sensor_id}:{'on' if on else 'off'}",
                areas={},
                sensors={},
            ),
            self.areas,
            self.sensors,
            self.detector,
        )
        self._check_invariants(before)

    def tick(self, at: float) -> None:
        """Advance the clock with no sensor events, as the 10 s tick does."""
        before = self._occupied_indoor()
        self.resolver.refresh_occupancy(self.start + at, self.areas, self.sensors)
        self._check_invariants(before)

    # -- reading --------------------------------------------------------

    def room(self, area_id: str):
        """Return the engine's decision state for one room."""
        return self.resolver.engine.rooms[area_id]

    def occupied(self) -> set[str]:
        """Return every indoor area that currently reads as occupied."""
        return self._occupied_indoor()

    def open_plan_occupancy(self) -> int:
        """Return how many of the three overlapping rooms read as occupied."""
        return sum(self.areas[area_id].occupancy for area_id in OPEN_PLAN)

    # -- invariants -----------------------------------------------------

    def _occupied_indoor(self) -> set[str]:
        return {
            area_id
            for area_id, area in self.areas.items()
            if area.is_indoors and area.occupied
        }

    def _check_invariants(self, before: set[str]) -> None:
        """Assert what must hold after every single event."""
        for area_id, area in self.areas.items():
            assert area.occupancy in (0, 1), (
                f"{area_id} reports occupancy {area.occupancy}; rooms are boolean"
            )

        active = {
            area_id
            for sensor in self.sensors.values()
            if sensor.is_trusted_active
            and sensor.config.get("type") in MOTION_SENSOR_TYPES
            for area_id in sensor.area_ids
        }
        assert active <= self._occupied_indoor() | {
            area_id for area_id in active if not self.areas[area_id].is_indoors
        }, "a trusted active motion sensor produced a false vacancy"

        for area_id in before - self._occupied_indoor():
            assert self.room(area_id).reason in RELEASE_REASONS, (
                f"{area_id} was released for reason {self.room(area_id).reason!r}, "
                "which is not a decision the state machine is allowed to make"
            )


# ----------------------------------------------------------------------
# The shipped layout
# ----------------------------------------------------------------------


def test_bedroom2_corridor2_overlap_is_modelled():
    """Bedroom 2 can plausibly spill with the rear corridor detector."""
    assert "bedroom_2" in PRODUCTION_CONFIG["adjacency"]["corridor_2"]
    assert "corridor_2" in PRODUCTION_CONFIG["adjacency"]["bedroom_2"]


def test_the_shipped_layout_gives_the_state_machine_what_it_needs():
    """The state machine reads its policy from the shipped configuration."""
    areas = PRODUCTION_CONFIG["areas"]
    assert areas["corridor_1"]["transition"] is True
    assert areas["corridor_2"]["transition"] is True
    assert areas["bedroom_1"]["profile"] == "sleeping"
    assert areas["main_bedroom"]["profile"] == "sleeping"
    for area_id in OPEN_PLAN + ("study",):
        assert areas[area_id]["profile"] == "living"


def test_the_main_bedroom_suite_counts_as_still_inside():
    """An ensuite and a wardrobe are dead ends, so they are not exits."""
    replay = Replay()
    assert replay.resolver.engine.exits_for("main_bedroom") == frozenset({"corridor_2"})
    assert replay.resolver.engine.exits_for("bedroom_1") == frozenset({"corridor_2"})
    assert replay.resolver.engine.exits_for("study") == frozenset({"corridor_1"})


# ----------------------------------------------------------------------
# Recorded walks
# ----------------------------------------------------------------------


def _seed_person_in_study(replay: Replay) -> float:
    """Walk somebody in through the entrance and sit them in the study.

    The first study edge lands 1 s after corridor_1's, which is inside the
    spill window, so it is unconfirmed. The person moving again once they are
    in the room is what confirms it, exactly as the logs show.

    Returns the offset at which the next sequence can start.
    """
    replay.fire("binary_sensor.entrance_motion", True, 0.0)
    replay.fire("binary_sensor.corridor_1_motion", True, 2.0)
    replay.fire("binary_sensor.study_motion", True, 3.0)
    assert replay.room("study").confirmed is False

    replay.fire("binary_sensor.entrance_motion", False, 5.0)
    replay.fire("binary_sensor.corridor_1_motion", False, 7.0)
    replay.fire("binary_sensor.study_motion", False, 8.0)
    replay.fire("binary_sensor.study_motion", True, 15.0)
    replay.fire("binary_sensor.study_motion", False, 20.0)

    assert replay.room("study").confirmed is True
    assert replay.areas["study"].occupancy == 1
    return 45.0


def _seed_person_in_kitchen(replay: Replay) -> float:
    """Walk somebody in through the entrance and into the open plan.

    Returns the offset at which the next sequence can start.
    """
    replay.fire("binary_sensor.entrance_motion", True, 0.0)
    replay.fire("binary_sensor.kitchen_motion", True, 3.7)
    replay.fire("binary_sensor.dining_room_motion", True, 4.5)
    replay.fire("binary_sensor.entrance_motion", False, 5.0)
    replay.fire("binary_sensor.kitchen_motion", False, 10.0)
    replay.fire("binary_sensor.dining_room_motion", False, 11.0)

    # The kitchen edge is 3.7 s after the entrance edge, too slow to be spill.
    # The dining edge is 0.8 s after the kitchen edge, which is the overlap.
    assert replay.room("kitchen").confirmed is True
    assert replay.room("dining_room").confirmed is False
    assert replay.open_plan_occupancy() >= 1
    return 30.0


def test_1812_walk_study_to_kitchen():
    """Replay the 18:12 walk: study, corridor_1, entrance, kitchen.

    Every room the walk passed through releases at its own deadline, each for
    the reason the design gives it. Only the room the walk ended in keeps its
    occupancy.
    """
    replay = Replay()
    t = _seed_person_in_study(replay)

    replay.fire("binary_sensor.corridor_1_motion", True, t)
    replay.fire("binary_sensor.entrance_motion", True, t + 3.7)
    replay.fire("binary_sensor.corridor_1_motion", False, t + 5.0)
    replay.fire("binary_sensor.kitchen_motion", True, t + 7.4)
    replay.fire("binary_sensor.dining_room_motion", True, t + 8.2)
    replay.fire("binary_sensor.entrance_motion", False, t + 8.7)
    replay.fire("binary_sensor.dining_room_motion", False, t + 13.0)
    replay.fire("binary_sensor.kitchen_motion", False, t + 14.0)

    # Mid-walk every room is still held, because no deadline has come due.
    assert {"study", "corridor_1", "entrance"} <= replay.occupied()

    # The person stays in the kitchen and keeps moving there.
    for offset in (60.0, 120.0, 180.0):
        replay.tick(t + offset - 1)
        replay.fire("binary_sensor.kitchen_motion", True, t + offset)
        replay.fire("binary_sensor.kitchen_motion", False, t + offset + 5.0)

    replay.tick(t + 300.0)

    # The study released because corridor_1 fired inside its trail window,
    # and the corridor and the entrance released the same way as the walk
    # moved on. Only the room the walk ended in keeps its occupancy.
    assert replay.room("study").state == STATE_VACANT
    assert replay.room("study").reason == "departure_trail"
    assert replay.room("corridor_1").state == STATE_VACANT
    assert replay.room("entrance").reason == "departure_trail"
    assert replay.occupied() == {"kitchen"}
    assert replay.room("kitchen").state == STATE_RETAINED


def test_1840_return_walk_kitchen_to_bedroom():
    """Replay the 18:40 return walk: kitchen to bedroom_1 through both corridors.

    The bedroom edge lands 0.2 s after corridor_2's, which is the measured
    spill. The person moving again once they are in the room confirms it.
    """
    replay = Replay()
    t = _seed_person_in_kitchen(replay)

    replay.fire("binary_sensor.kitchen_motion", True, t)
    replay.fire("binary_sensor.entrance_motion", True, t + 3.0)
    replay.fire("binary_sensor.kitchen_motion", False, t + 5.0)
    replay.fire("binary_sensor.entrance_motion", False, t + 8.0)

    walk = t + 20.0
    replay.fire("binary_sensor.corridor_1_motion", True, walk)
    replay.fire("binary_sensor.corridor_2_motion", True, walk + 4.4)
    replay.fire("binary_sensor.bedroom_1_motion", True, walk + 4.6)
    assert replay.room("bedroom_1").confirmed is False

    replay.fire("binary_sensor.corridor_1_motion", False, walk + 5.0)
    replay.fire("binary_sensor.corridor_2_motion", False, walk + 10.0)
    replay.fire("binary_sensor.bedroom_1_motion", False, walk + 25.0)
    replay.fire("binary_sensor.bedroom_1_motion", True, walk + 30.0)
    replay.fire("binary_sensor.bedroom_1_motion", False, walk + 35.0)

    assert replay.room("bedroom_1").confirmed is True

    replay.tick(walk + 35.0 + HOLD)

    assert replay.occupied() == {"bedroom_1"}
    assert replay.room("bedroom_1").state == STATE_RETAINED
    # Nothing fired after corridor_2, so it released on being a transition
    # room rather than on a trail. Corridors never retain either way.
    assert replay.room("corridor_1").state == STATE_VACANT
    assert replay.room("corridor_2").reason == "transition_room"
    assert replay.room("kitchen").reason == "departure_trail"


def test_a_sleeping_room_is_bounded_by_its_ceiling():
    """A bedroom held with no departure trail still resolves on its own."""
    replay = Replay()
    replay.fire("binary_sensor.bedroom_1_motion", True, 0.0)
    replay.fire("binary_sensor.bedroom_1_motion", False, 5.0)

    replay.tick(5.0 + HOLD)
    assert replay.room("bedroom_1").state == STATE_RETAINED

    replay.tick(SLEEPING_CEILING)
    assert replay.room("bedroom_1").state == STATE_RETAINED

    replay.tick(5.0 + SLEEPING_CEILING)
    assert replay.occupied() == set()
    assert replay.room("bedroom_1").reason == "retention_ceiling"


# ----------------------------------------------------------------------
# The overlapping open plan
# ----------------------------------------------------------------------


def test_kitchen_dining_oscillation_production():
    """Replay the kitchen and dining oscillation that inflated counts to 7.

    One person stands in the kitchen while the two overlapping detectors
    alternate. Which room holds them is ambiguous, so the assertion is that
    the open plan never reads empty and no room ever counts more than one.
    """
    replay = Replay()
    t = _seed_person_in_kitchen(replay)

    replay.fire("binary_sensor.kitchen_motion", True, t)
    replay.fire("binary_sensor.dining_room_motion", True, t + 0.8)
    replay.fire("binary_sensor.kitchen_motion", False, t + 5.0)
    replay.fire("binary_sensor.kitchen_motion", True, t + 8.0)
    replay.fire("binary_sensor.dining_room_motion", False, t + 9.0)

    replay.fire("binary_sensor.dining_room_motion", True, t + 12.0)
    replay.fire("binary_sensor.kitchen_motion", False, t + 13.0)
    replay.fire("binary_sensor.kitchen_motion", True, t + 16.0)
    replay.fire("binary_sensor.dining_room_motion", False, t + 17.0)

    cycle_start = t + 20.0
    for cycle in range(8):
        replay.fire("binary_sensor.dining_room_motion", True, cycle_start)
        replay.fire("binary_sensor.kitchen_motion", False, cycle_start + 5.0)
        replay.fire("binary_sensor.kitchen_motion", True, cycle_start + 8.0)
        replay.fire("binary_sensor.dining_room_motion", False, cycle_start + 9.0)
        replay.tick(cycle_start + 9.5)

        assert replay.open_plan_occupancy() >= 1, f"open plan empty at cycle {cycle}"
        cycle_start += 10.0

    # The triple fire: dining, kitchen, and living within seconds.
    replay.fire("binary_sensor.kitchen_motion", False, cycle_start)
    replay.fire("binary_sensor.dining_room_motion", True, cycle_start + 3.0)
    replay.fire("binary_sensor.kitchen_motion", True, cycle_start + 3.2)
    replay.fire("binary_sensor.living_room_motion", True, cycle_start + 6.0)
    assert replay.open_plan_occupancy() >= 1

    replay.fire("binary_sensor.living_room_motion", False, cycle_start + 12.0)
    replay.fire("binary_sensor.dining_room_motion", False, cycle_start + 13.0)
    replay.fire("binary_sensor.kitchen_motion", False, cycle_start + 14.0)

    # The room that fired last has no exit edge after its own activity, so
    # the group keeps the person even once every detector is quiet.
    replay.tick(cycle_start + 14.0 + HOLD)
    assert replay.open_plan_occupancy() >= 1


def test_extended_oscillation_20_cycles():
    """Twenty cycles of the same alternation, then a long silence.

    Standing still for ten minutes used to drive the counts to K@6 DR@7.
    """
    replay = Replay()
    t = _seed_person_in_kitchen(replay)

    for cycle in range(20):
        replay.fire("binary_sensor.kitchen_motion", True, t)
        replay.fire("binary_sensor.dining_room_motion", True, t + 0.8)
        replay.fire("binary_sensor.kitchen_motion", False, t + 5.0)
        replay.fire("binary_sensor.dining_room_motion", False, t + 6.0)
        replay.tick(t + 9.0)

        assert replay.open_plan_occupancy() >= 1, f"open plan empty at cycle {cycle}"
        assert replay.open_plan_occupancy() <= len(OPEN_PLAN)
        t += 10.0

    last_off = t - 10.0 + 6.0

    # Which room of the pair the deadline leaves occupied depends on which
    # detector fired last, so the assertion is bounded: the group is still
    # occupied the moment the detectors go quiet, and nothing survives the
    # living-room ceiling.
    replay.tick(last_off + 1.0)
    assert replay.open_plan_occupancy() >= 1

    replay.tick(last_off + LIVING_CEILING)
    assert replay.open_plan_occupancy() == 0
    assert replay.occupied() == set()


def test_guest_room_first_motion_is_accepted_at_once():
    """The first guest-room pulse counts, with the open plan still busy.

    Recorded shape: guest_room_motion fires while the open plan is active and
    the entrance last fired 277.6 s earlier. One person is in the open plan,
    another walks into the guest toilet.
    """
    replay = Replay()
    replay.fire("binary_sensor.entrance_motion", True, 0.0)
    replay.fire("binary_sensor.kitchen_motion", True, 3.7)
    replay.fire("binary_sensor.dining_room_motion", True, 4.5)
    replay.fire("binary_sensor.living_room_motion", True, 5.0)
    replay.fire("binary_sensor.entrance_motion", False, 5.2)

    # The first person keeps the open plan busy for the next five minutes.
    for offset in range(10, 270, 30):
        replay.fire("binary_sensor.kitchen_motion", False, offset)
        replay.fire("binary_sensor.dining_room_motion", False, offset + 1.0)
        replay.fire("binary_sensor.living_room_motion", False, offset + 2.0)
        replay.fire("binary_sensor.kitchen_motion", True, offset + 10.0)
        replay.tick(offset + 12.0)

    guest_time = 277.6
    replay.fire("binary_sensor.guest_room_motion", True, guest_time)

    # The entrance went quiet minutes ago, so this cannot be its spill.
    assert replay.areas["guest_room"].occupancy == 1
    assert replay.room("guest_room").confirmed is True
    assert replay.room("guest_room").reason == "own_motion"
    assert replay.open_plan_occupancy() >= 1
    assert len(replay.occupied()) >= 2
    assert not [
        warning
        for warning in replay.detector.get_warnings()
        if warning.area == "guest_room" and "no_plausible_source" in warning.message
    ]

    # Nobody left the guest toilet, so it keeps its occupancy.
    replay.fire("binary_sensor.guest_room_motion", False, guest_time + 5.0)
    replay.tick(guest_time + 5.0 + HOLD)
    assert replay.room("guest_room").state == STATE_RETAINED


def test_study_persistent_activation_accepted():
    """Nineteen study pulses over twenty minutes are one person, not phantoms.

    The gaps between pulses run to six minutes, which is far longer than the
    hold. The room stays occupied throughout because no exit ever fired.
    """
    replay = Replay()
    t = _seed_person_in_kitchen(replay)

    offsets = [
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
    for offset in offsets:
        replay.fire("binary_sensor.study_motion", True, t + offset)
        assert replay.room("study").state == STATE_OCCUPIED
        replay.fire("binary_sensor.study_motion", False, t + offset + 5.0)
        # Half way to the next pulse the room must still read occupied.
        replay.tick(t + offset + 5.0 + HOLD)
        assert replay.areas["study"].occupancy == 1, (
            f"study went vacant during the silence after {offset:.1f} s"
        )

    last = t + offsets[-1] + 5.0
    assert replay.room("study").state == STATE_RETAINED
    assert replay.room("study").reason == "no_exit_trail"

    # With nobody ever using corridor_1, the ceiling is what ends it.
    replay.tick(last + LIVING_CEILING)
    assert replay.areas["study"].occupancy == 0
    assert replay.room("study").reason == "retention_ceiling"


# ----------------------------------------------------------------------
# End to end
# ----------------------------------------------------------------------


def test_full_production_sequence():
    """Study to kitchen, ten minutes of oscillation, then back to bedroom_1."""
    replay = Replay()
    t = _seed_person_in_study(replay)

    # Phase 1: study, corridor_1, entrance, kitchen.
    replay.fire("binary_sensor.corridor_1_motion", True, t)
    replay.fire("binary_sensor.entrance_motion", True, t + 3.7)
    replay.fire("binary_sensor.corridor_1_motion", False, t + 5.0)
    replay.fire("binary_sensor.kitchen_motion", True, t + 7.4)
    replay.fire("binary_sensor.dining_room_motion", True, t + 8.2)
    replay.fire("binary_sensor.entrance_motion", False, t + 8.7)
    assert replay.open_plan_occupancy() >= 1

    # Phase 2: the overlapping detectors alternate for five cycles.
    t2 = t + 25.0
    for cycle in range(5):
        replay.fire("binary_sensor.kitchen_motion", False, t2)
        replay.fire("binary_sensor.dining_room_motion", False, t2 + 1.0)
        replay.fire("binary_sensor.kitchen_motion", True, t2 + 4.0)
        replay.fire("binary_sensor.dining_room_motion", True, t2 + 4.8)
        replay.tick(t2 + 9.0)
        assert replay.open_plan_occupancy() >= 1, f"open plan empty at cycle {cycle}"
        t2 += 10.0

    # Phase 3: out through the entrance and both corridors to bedroom_1.
    t3 = t2 + 15.0
    replay.fire("binary_sensor.entrance_motion", True, t3)
    replay.fire("binary_sensor.kitchen_motion", False, t3 + 2.0)
    replay.fire("binary_sensor.dining_room_motion", False, t3 + 2.5)
    replay.fire("binary_sensor.corridor_1_motion", True, t3 + 4.5)
    replay.fire("binary_sensor.entrance_motion", False, t3 + 5.0)
    replay.fire("binary_sensor.corridor_2_motion", True, t3 + 8.9)
    replay.fire("binary_sensor.bedroom_1_motion", True, t3 + 9.1)
    replay.fire("binary_sensor.corridor_1_motion", False, t3 + 9.5)
    replay.fire("binary_sensor.corridor_2_motion", False, t3 + 14.0)
    replay.fire("binary_sensor.bedroom_1_motion", False, t3 + 25.0)
    replay.fire("binary_sensor.bedroom_1_motion", True, t3 + 30.0)
    replay.fire("binary_sensor.bedroom_1_motion", False, t3 + 35.0)

    replay.tick(t3 + 35.0 + HOLD)

    assert replay.occupied() == {"bedroom_1"}
    assert replay.room("bedroom_1").state == STATE_RETAINED


def test_no_room_stays_occupied_for_the_whole_replay():
    """The July regression: rooms that were ON from one month to the next.

    After the full sequence, with no further events at all, every room
    resolves within the longest retention ceiling in the house.
    """
    replay = Replay()
    t = _seed_person_in_kitchen(replay)
    replay.fire("binary_sensor.entrance_motion", True, t)
    replay.fire("binary_sensor.corridor_1_motion", True, t + 4.0)
    replay.fire("binary_sensor.entrance_motion", False, t + 5.0)
    replay.fire("binary_sensor.corridor_2_motion", True, t + 8.0)
    replay.fire("binary_sensor.corridor_1_motion", False, t + 9.0)
    replay.fire("binary_sensor.main_bedroom_motion", True, t + 14.0)
    replay.fire("binary_sensor.corridor_2_motion", False, t + 15.0)
    replay.fire("binary_sensor.main_bedroom_motion", False, t + 20.0)

    replay.tick(t + 20.0 + SLEEPING_CEILING + HOLD)

    assert replay.occupied() == set()
    assert all(
        room.state == STATE_VACANT for room in replay.resolver.engine.rooms.values()
    )


def test_outdoor_activity_never_manufactures_an_indoor_departure():
    """Cameras outside must not empty a room somebody is sleeping in."""
    replay = Replay()
    replay.fire("binary_sensor.main_bedroom_motion", True, 0.0)
    replay.fire("binary_sensor.main_bedroom_motion", False, 5.0)
    replay.tick(5.0 + HOLD)
    assert replay.room("main_bedroom").state == STATE_RETAINED

    for sensor_id in (
        "binary_sensor.back_left_motion",
        "binary_sensor.back_right_person_detected",
        "binary_sensor.left_motion",
    ):
        replay.fire(sensor_id, True, 200.0)
        replay.fire(sensor_id, False, 205.0)
    replay.tick(300.0)

    assert replay.room("main_bedroom").state == STATE_RETAINED
    assert replay.areas["backyard"].occupancy == 0


def test_a_dead_end_visit_inside_the_suite_is_not_a_departure():
    """Using the ensuite or the wardrobe is not leaving the main bedroom."""
    replay = Replay()
    replay.fire("binary_sensor.main_bedroom_motion", True, 0.0)
    replay.fire("binary_sensor.main_bedroom_motion", False, 5.0)
    replay.fire("binary_sensor.main_bathroom_motion", True, 8.0)
    replay.fire("binary_sensor.main_bathroom_motion", False, 60.0)
    replay.fire("binary_sensor.wardrobe_motion", True, 65.0)
    replay.fire("binary_sensor.wardrobe_motion", False, 70.0)

    replay.tick(70.0 + HOLD)
    assert replay.room("main_bedroom").state == STATE_RETAINED
    assert {"main_bedroom", "main_bathroom", "wardrobe"} <= replay.occupied()


def test_a_corridor_pass_never_clears_a_retained_bedroom():
    """Somebody else walking the corridor must not evict a sleeper."""
    replay = Replay()
    replay.fire("binary_sensor.bedroom_1_motion", True, 0.0)
    replay.fire("binary_sensor.bedroom_1_motion", False, 5.0)
    replay.tick(5.0 + HOLD)
    assert replay.room("bedroom_1").state == STATE_RETAINED

    replay.fire("binary_sensor.corridor_2_motion", True, 3600.0)
    replay.fire("binary_sensor.corridor_2_motion", False, 3605.0)
    replay.tick(3605.0 + TRANSITION_HOLD)

    assert replay.room("bedroom_1").state == STATE_RETAINED
    assert replay.room("corridor_2").state == STATE_VACANT
    assert replay.room("corridor_2").reason == "transition_room"


def test_a_spilled_bedroom_entry_is_never_retained():
    """A corridor_2 pass that spills into bedroom_2 must not hold the room.

    Twenty percent of the recorded bedroom_2 edges start within 2 s of a
    corridor_2 edge, which is too fast to be somebody walking through a door.
    """
    replay = Replay()
    replay.fire("binary_sensor.corridor_2_motion", True, 0.0)
    replay.fire("binary_sensor.bedroom_2_motion", True, 0.5)
    assert replay.room("bedroom_2").confirmed is False

    replay.fire("binary_sensor.corridor_2_motion", False, 5.0)
    replay.fire("binary_sensor.bedroom_2_motion", False, 5.5)
    replay.tick(5.5 + HOLD)

    assert replay.areas["bedroom_2"].occupancy == 0
    assert replay.room("bedroom_2").reason in {
        "unconfirmed_entry",
        "departure_trail",
    }
    assert replay.room("corridor_2").state == STATE_VACANT


def test_pending_rooms_report_an_explicit_deadline():
    """Vacancy has to be able to happen with no further sensor events."""
    replay = Replay()
    replay.fire("binary_sensor.utility_room_motion", True, 0.0)
    replay.fire("binary_sensor.utility_room_motion", False, 5.0)

    room = replay.room("utility_room")
    assert room.state == STATE_PENDING
    assert room.deadline == pytest.approx(replay.start + 5.0 + HOLD)
