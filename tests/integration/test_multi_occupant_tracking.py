"""Integration tests for occupancy tracking under the exit-gated contract.

The scenarios are the ones this suite has always covered: single walks, two
people, hub and loop and T-junction topologies, a room with two sensor types,
and an open plan whose detectors overlap. What changed is the expectation.
A traversed room is no longer occupied forever. At its hold deadline a room
goes vacant when a departure trail says somebody left, keeps its occupancy
when no exit fired, and releases an entry that looks like detector spill.
Transition rooms never retain, and a retention ceiling bounds every room that
is kept without a trail.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant

from custom_components.occupancy_tracker import DOMAIN, async_setup
from custom_components.occupancy_tracker.helpers.occupancy_engine import (
    STATE_OCCUPIED,
    STATE_PENDING,
    STATE_RETAINED,
    STATE_VACANT,
)
from custom_components.occupancy_tracker.helpers.room_profiles import ROOM_PROFILES
from tests.integration.conftest import SensorEventHelper

HOLD = ROOM_PROFILES["default"].hold_seconds
TRANSITION_HOLD = ROOM_PROFILES["transition"].hold_seconds
DEFAULT_CEILING = ROOM_PROFILES["default"].retention_ceiling_seconds
LIVING_CEILING = ROOM_PROFILES["living"].retention_ceiling_seconds

# Walking pace in the recorded production logs: about 4 s between two rooms.
STEP = 4.0
# The KNX detectors report OFF about 5 s after the last movement they saw.
KNX_OFF = 5.0


# ----------------------------------------------------------------------
# A line of rooms: an entrance, a corridor, and a room at the end.
# ----------------------------------------------------------------------


@pytest.fixture
def linear_config():
    """Return a three-room line with the middle room as the corridor."""
    return {
        DOMAIN: {
            "areas": {
                "area_a": {"name": "A", "exit_capable": True},
                "area_b": {"name": "B", "transition": True},
                "area_c": {"name": "C"},
            },
            "adjacency": {
                "area_a": ["area_b"],
                "area_b": ["area_a", "area_c"],
                "area_c": ["area_b"],
            },
            "sensors": {
                "binary_sensor.motion_a": {"area": "area_a", "type": "motion"},
                "binary_sensor.motion_b": {"area": "area_b", "type": "motion"},
                "binary_sensor.motion_c": {"area": "area_c", "type": "motion"},
            },
        }
    }


@pytest.fixture
async def linear(hass: HomeAssistant, linear_config):
    """Set up the integration on the three-room line."""
    assert await async_setup(hass, linear_config)
    return hass


def _helper(hass: HomeAssistant) -> tuple[object, SensorEventHelper]:
    """Return the coordinator and an event helper for it."""
    coordinator = hass.data[DOMAIN]["coordinator"]
    return coordinator, SensorEventHelper(coordinator)


class TestSingleOccupant:
    """One person, one room at a time."""

    async def test_first_motion_makes_the_room_occupied(self, linear: HomeAssistant):
        coordinator, helper = _helper(linear)
        helper.trigger_sensor("binary_sensor.motion_a", True)

        assert coordinator.get_occupancy("area_a") == 1
        assert helper.room("area_a").state == STATE_OCCUPIED
        assert helper.room("area_a").confirmed is True

    async def test_silence_holds_the_room_until_its_deadline(
        self, linear: HomeAssistant
    ):
        coordinator, helper = _helper(linear)
        helper.trigger_sensor("binary_sensor.motion_a", True)
        helper.trigger_sensor("binary_sensor.motion_a", False, delay=KNX_OFF)

        room = helper.room("area_a")
        assert coordinator.get_occupancy("area_a") == 1
        assert room.state == STATE_PENDING
        assert room.deadline == pytest.approx(helper.current_time + HOLD)

        helper.tick(HOLD - 1)
        assert helper.room("area_a").state == STATE_PENDING

    async def test_a_room_with_no_trail_is_retained_then_released_by_its_ceiling(
        self, linear: HomeAssistant
    ):
        coordinator, helper = _helper(linear)
        helper.trigger_sensor("binary_sensor.motion_a", True)
        helper.trigger_sensor("binary_sensor.motion_a", False, delay=KNX_OFF)

        # Nothing else fired, so nobody can have left through the corridor.
        helper.tick(HOLD)
        assert coordinator.get_occupancy("area_a") == 1
        assert helper.room("area_a").state == STATE_RETAINED
        assert helper.room("area_a").reason == "no_exit_trail"

        # The ceiling bounds how long a missed exit can hold the room.
        helper.tick(DEFAULT_CEILING)
        assert coordinator.get_occupancy("area_a") == 0
        assert helper.room("area_a").reason == "retention_ceiling"

    async def test_walking_the_line_releases_the_rooms_that_were_left(
        self, linear: HomeAssistant
    ):
        coordinator, helper = _helper(linear)
        helper.trigger_sensor("binary_sensor.motion_a", True)
        helper.trigger_sensor("binary_sensor.motion_b", True, delay=STEP)
        helper.trigger_sensor("binary_sensor.motion_a", False, delay=1)
        helper.trigger_sensor("binary_sensor.motion_c", True, delay=STEP)
        helper.trigger_sensor("binary_sensor.motion_b", False, delay=1)
        helper.trigger_sensor("binary_sensor.motion_c", False, delay=KNX_OFF)

        # Every room is still held while its own hold runs.
        assert helper.occupied("area_a", "area_b", "area_c") == {
            "area_a",
            "area_b",
            "area_c",
        }

        helper.tick(HOLD)

        # The corridor fired right after A, so A released on a departure
        # trail. The corridor is a transition room and never retains. Only
        # the room the walk ended in keeps its occupancy.
        assert helper.room("area_a").reason == "departure_trail"
        assert helper.room("area_b").reason == "transition_room"
        assert helper.occupied("area_a", "area_b", "area_c") == {"area_c"}
        assert helper.room("area_c").state == STATE_RETAINED

    async def test_a_person_who_stays_keeps_the_room_occupied(
        self, linear: HomeAssistant
    ):
        coordinator, helper = _helper(linear)
        helper.trigger_sensor("binary_sensor.motion_c", True)
        helper.trigger_sensor("binary_sensor.motion_c", False, delay=KNX_OFF)

        # A person who moves once a minute re-arms the hold every time.
        for _ in range(3):
            helper.tick(60)
            helper.trigger_sensor("binary_sensor.motion_c", True)
            assert helper.room("area_c").state == STATE_OCCUPIED
            helper.trigger_sensor("binary_sensor.motion_c", False, delay=KNX_OFF)
            assert coordinator.get_occupancy("area_c") == 1

        helper.tick(HOLD)
        assert coordinator.get_occupancy("area_c") == 1
        assert helper.room("area_c").state == STATE_RETAINED


class TestMultiOccupant:
    """Two people at once, including the accepted one-of-two error."""

    async def test_one_of_two_leaves_and_the_room_reoccupies_on_the_next_motion(
        self, linear: HomeAssistant
    ):
        coordinator, helper = _helper(linear)
        # Two people are in C. One walks out through the corridor.
        helper.trigger_sensor("binary_sensor.motion_c", True)
        helper.trigger_sensor("binary_sensor.motion_b", True, delay=STEP)
        helper.trigger_sensor("binary_sensor.motion_c", False, delay=1)
        helper.trigger_sensor("binary_sensor.motion_b", False, delay=KNX_OFF)

        # The trail belongs to the leaver, so the room reads empty. This is
        # the accepted error: it self-heals on the next movement.
        helper.tick(HOLD)
        assert coordinator.get_occupancy("area_c") == 0
        assert helper.room("area_c").reason == "departure_trail"

        helper.trigger_sensor("binary_sensor.motion_c", True, delay=600)
        assert coordinator.get_occupancy("area_c") == 1
        assert helper.room("area_c").state == STATE_OCCUPIED

    async def test_two_people_in_different_rooms_are_both_kept(
        self, linear: HomeAssistant
    ):
        coordinator, helper = _helper(linear)
        # The first person settles at the end of the line.
        helper.trigger_sensor("binary_sensor.motion_c", True)
        helper.trigger_sensor("binary_sensor.motion_c", False, delay=KNX_OFF)
        helper.tick(HOLD)
        assert helper.room("area_c").state == STATE_RETAINED

        # The second person comes in and stays in the entrance room.
        helper.trigger_sensor("binary_sensor.motion_a", True, delay=600)
        helper.trigger_sensor("binary_sensor.motion_a", False, delay=KNX_OFF)
        helper.tick(HOLD)

        assert helper.occupied("area_a", "area_b", "area_c") == {"area_a", "area_c"}
        assert coordinator.get_occupancy("area_b") == 0

    async def test_a_corridor_pass_never_clears_a_retained_room(
        self, linear: HomeAssistant
    ):
        coordinator, helper = _helper(linear)
        helper.trigger_sensor("binary_sensor.motion_c", True)
        helper.trigger_sensor("binary_sensor.motion_c", False, delay=KNX_OFF)
        helper.tick(HOLD)
        assert helper.room("area_c").state == STATE_RETAINED

        # Somebody else walks the corridor ten minutes later. In July this
        # evicted the settled occupant; it must not.
        helper.trigger_sensor("binary_sensor.motion_b", True, delay=600)
        helper.trigger_sensor("binary_sensor.motion_b", False, delay=KNX_OFF)
        helper.tick(TRANSITION_HOLD)

        assert coordinator.get_occupancy("area_c") == 1
        assert helper.room("area_c").state == STATE_RETAINED
        assert coordinator.get_occupancy("area_b") == 0


class TestEdgeCases:
    """Spill, re-entry, and a period with no events at all."""

    async def test_a_rapid_neighbour_edge_is_spill_and_is_never_retained(
        self, linear: HomeAssistant
    ):
        coordinator, helper = _helper(linear)
        # The corridor detector sees into the doorway: C's edge lands half a
        # second after the corridor's, far too fast to be somebody walking in.
        helper.trigger_sensor("binary_sensor.motion_b", True)
        helper.trigger_sensor("binary_sensor.motion_c", True, delay=0.5)
        assert coordinator.get_occupancy("area_c") == 1
        assert helper.room("area_c").confirmed is False
        assert helper.room("area_c").reason == "own_motion_spill"

        helper.trigger_sensor("binary_sensor.motion_b", False, delay=KNX_OFF)
        helper.trigger_sensor("binary_sensor.motion_c", False, delay=0.5)
        helper.tick(HOLD)

        assert coordinator.get_occupancy("area_c") == 0
        assert helper.room("area_c").reason == "unconfirmed_entry"

    async def test_a_second_quiet_activation_confirms_a_spilled_entry(
        self, linear: HomeAssistant
    ):
        coordinator, helper = _helper(linear)
        helper.trigger_sensor("binary_sensor.motion_b", True)
        helper.trigger_sensor("binary_sensor.motion_c", True, delay=0.5)
        helper.trigger_sensor("binary_sensor.motion_b", False, delay=KNX_OFF)
        helper.trigger_sensor("binary_sensor.motion_c", False, delay=0.5)

        # The corridor has gone quiet, so this activation cannot be its spill.
        helper.trigger_sensor("binary_sensor.motion_c", True, delay=40)
        assert helper.room("area_c").confirmed is True

        helper.trigger_sensor("binary_sensor.motion_c", False, delay=KNX_OFF)
        helper.tick(HOLD)
        assert coordinator.get_occupancy("area_c") == 1
        assert helper.room("area_c").state == STATE_RETAINED

    async def test_leaving_and_coming_back_never_reads_vacant(
        self, linear: HomeAssistant
    ):
        coordinator, helper = _helper(linear)
        helper.trigger_sensor("binary_sensor.motion_a", True)
        helper.trigger_sensor("binary_sensor.motion_a", False, delay=KNX_OFF)
        assert coordinator.get_occupancy("area_a") == 1

        helper.trigger_sensor("binary_sensor.motion_a", True, delay=10)
        room = helper.room("area_a")
        assert room.state == STATE_OCCUPIED
        assert room.reason == "own_motion"
        assert room.deadline is None

    async def test_an_hour_with_no_events_leaves_no_room_occupied(
        self, linear: HomeAssistant
    ):
        coordinator, helper = _helper(linear)
        helper.trigger_sensor("binary_sensor.motion_a", True)
        helper.trigger_sensor("binary_sensor.motion_b", True, delay=STEP)
        helper.trigger_sensor("binary_sensor.motion_c", True, delay=STEP)
        helper.trigger_sensor("binary_sensor.motion_a", False, delay=1)
        helper.trigger_sensor("binary_sensor.motion_b", False, delay=1)
        helper.trigger_sensor("binary_sensor.motion_c", False, delay=1)

        # The July regression was rooms that stayed ON for months. With no
        # further events at all, every room resolves.
        helper.tick(DEFAULT_CEILING + HOLD)
        assert helper.occupied("area_a", "area_b", "area_c") == set()
        assert all(
            room.state == STATE_VACANT
            for room in coordinator.occupancy_resolver.engine.rooms.values()
        )


class TestStaleOccupancyDiagnostics:
    """Stale-looking occupancy is reported, never silently cleared."""

    async def test_stale_occupancy_is_warned_but_not_cleared(
        self, linear: HomeAssistant
    ):
        coordinator, helper = _helper(linear)
        helper.trigger_sensor("binary_sensor.motion_c", True)
        helper.trigger_sensor("binary_sensor.motion_c", False, delay=KNX_OFF)
        helper.tick(HOLD)
        assert helper.room("area_c").state == STATE_RETAINED

        # 45 minutes of silence is past the phantom diagnostic threshold but
        # still inside the retention ceiling.
        helper.advance_time(45 * 60)
        coordinator.anomaly_detector.check_timeouts(
            coordinator.areas,
            helper.current_time,
            sensors=coordinator.sensors,
            freshness_fn=lambda area_id, timestamp: 0.12,
        )

        assert coordinator.get_occupancy("area_c") == 1
        assert any(
            warning.type == "phantom_occupancy_suspected"
            for warning in coordinator.anomaly_detector.get_warnings()
        )

    async def test_an_active_neighbour_suppresses_the_warning(
        self, linear: HomeAssistant
    ):
        coordinator, helper = _helper(linear)
        helper.trigger_sensor("binary_sensor.motion_c", True)
        helper.trigger_sensor("binary_sensor.motion_c", False, delay=KNX_OFF)
        helper.tick(HOLD)

        helper.advance_time(45 * 60)
        # The corridor fired a minute ago, so somebody is plainly about.
        helper.trigger_sensor("binary_sensor.motion_b", True)
        helper.trigger_sensor("binary_sensor.motion_b", False, delay=KNX_OFF)
        helper.advance_time(60)
        coordinator.anomaly_detector.check_timeouts(
            coordinator.areas,
            helper.current_time,
            sensors=coordinator.sensors,
            freshness_fn=lambda area_id, timestamp: 0.12,
        )

        assert coordinator.get_occupancy("area_c") == 1
        assert not [
            warning
            for warning in coordinator.anomaly_detector.get_warnings()
            if warning.type == "phantom_occupancy_suspected"
            and warning.area == "area_c"
        ]

    async def test_the_ceiling_is_what_finally_clears_a_stale_room(
        self, linear: HomeAssistant
    ):
        coordinator, helper = _helper(linear)
        helper.trigger_sensor("binary_sensor.motion_c", True)
        helper.trigger_sensor("binary_sensor.motion_c", False, delay=KNX_OFF)
        helper.tick(HOLD)

        helper.tick(DEFAULT_CEILING)
        assert coordinator.get_occupancy("area_c") == 0
        assert helper.room("area_c").reason == "retention_ceiling"


# ----------------------------------------------------------------------
# A hub: a hall with three rooms off it.
# ----------------------------------------------------------------------


@pytest.fixture
def hub_config():
    """Return a hall with a kitchen, a bedroom, and a bathroom off it."""
    return {
        DOMAIN: {
            "areas": {
                "hall": {"name": "Hall", "exit_capable": True, "transition": True},
                "kitchen": {"name": "Kitchen", "profile": "living"},
                "bedroom": {"name": "Bedroom", "profile": "sleeping"},
                "bathroom": {"name": "Bathroom"},
            },
            "adjacency": {"hall": ["kitchen", "bedroom", "bathroom"]},
            "sensors": {
                "binary_sensor.motion_hall": {"area": "hall", "type": "motion"},
                "binary_sensor.motion_kitchen": {"area": "kitchen", "type": "motion"},
                "binary_sensor.motion_bedroom": {"area": "bedroom", "type": "motion"},
                "binary_sensor.motion_bathroom": {"area": "bathroom", "type": "motion"},
            },
        }
    }


@pytest.fixture
async def hub(hass: HomeAssistant, hub_config):
    """Set up the integration on the hub topology."""
    assert await async_setup(hass, hub_config)
    return hass


class TestHub:
    """Rooms reached through a shared hall."""

    async def test_entering_a_room_releases_the_hall(self, hub: HomeAssistant):
        coordinator, helper = _helper(hub)
        helper.trigger_sensor("binary_sensor.motion_hall", True)
        helper.trigger_sensor("binary_sensor.motion_kitchen", True, delay=STEP)
        assert helper.occupied("hall", "kitchen") == {"hall", "kitchen"}

        helper.trigger_sensor("binary_sensor.motion_hall", False, delay=1)
        helper.trigger_sensor("binary_sensor.motion_kitchen", False, delay=KNX_OFF)
        helper.tick(HOLD)

        assert helper.occupied("hall", "kitchen", "bedroom", "bathroom") == {"kitchen"}
        assert helper.room("kitchen").state == STATE_RETAINED
        assert helper.room("kitchen").deadline == pytest.approx(
            helper.room("kitchen").last_own_off + LIVING_CEILING
        )

    async def test_a_room_visit_that_ends_in_the_hall_releases_the_room(
        self, hub: HomeAssistant
    ):
        coordinator, helper = _helper(hub)
        helper.trigger_sensor("binary_sensor.motion_hall", True)
        helper.trigger_sensor("binary_sensor.motion_bedroom", True, delay=STEP)
        helper.trigger_sensor("binary_sensor.motion_hall", False, delay=1)
        helper.trigger_sensor("binary_sensor.motion_bedroom", False, delay=KNX_OFF)

        # Back out through the hall, then into the kitchen.
        helper.trigger_sensor("binary_sensor.motion_hall", True, delay=3)
        helper.trigger_sensor("binary_sensor.motion_kitchen", True, delay=STEP)
        helper.trigger_sensor("binary_sensor.motion_hall", False, delay=1)
        helper.trigger_sensor("binary_sensor.motion_kitchen", False, delay=KNX_OFF)
        helper.tick(HOLD)

        assert helper.room("bedroom").reason == "departure_trail"
        assert helper.occupied("hall", "kitchen", "bedroom", "bathroom") == {"kitchen"}

    async def test_two_people_settle_in_two_rooms(self, hub: HomeAssistant):
        coordinator, helper = _helper(hub)
        # The first person walks hall to kitchen and stays.
        helper.trigger_sensor("binary_sensor.motion_hall", True)
        helper.trigger_sensor("binary_sensor.motion_kitchen", True, delay=STEP)
        helper.trigger_sensor("binary_sensor.motion_hall", False, delay=1)
        helper.trigger_sensor("binary_sensor.motion_kitchen", False, delay=KNX_OFF)
        helper.tick(HOLD)
        assert helper.room("kitchen").state == STATE_RETAINED

        # The second person comes in and goes to the bedroom.
        helper.trigger_sensor("binary_sensor.motion_hall", True, delay=300)
        helper.trigger_sensor("binary_sensor.motion_bedroom", True, delay=STEP)
        helper.trigger_sensor("binary_sensor.motion_hall", False, delay=1)
        helper.trigger_sensor("binary_sensor.motion_bedroom", False, delay=KNX_OFF)
        helper.tick(HOLD)

        assert helper.occupied("hall", "kitchen", "bedroom", "bathroom") == {
            "kitchen",
            "bedroom",
        }


# ----------------------------------------------------------------------
# A loop of four rooms, so every room has two real exits.
# ----------------------------------------------------------------------


@pytest.fixture
def loop_config():
    """Return four rooms connected in a ring."""
    return {
        DOMAIN: {
            "areas": {
                "area_a": {"name": "A", "exit_capable": True},
                "area_b": {"name": "B"},
                "area_c": {"name": "C"},
                "area_d": {"name": "D"},
            },
            "adjacency": {
                "area_a": ["area_b", "area_d"],
                "area_b": ["area_c"],
                "area_c": ["area_d"],
            },
            "sensors": {
                "binary_sensor.motion_a": {"area": "area_a", "type": "motion"},
                "binary_sensor.motion_b": {"area": "area_b", "type": "motion"},
                "binary_sensor.motion_c": {"area": "area_c", "type": "motion"},
                "binary_sensor.motion_d": {"area": "area_d", "type": "motion"},
            },
        }
    }


@pytest.fixture
async def loop(hass: HomeAssistant, loop_config):
    """Set up the integration on the ring topology."""
    assert await async_setup(hass, loop_config)
    return hass


class TestLoop:
    """A ring gives every room a real exit, so transit rooms all clear."""

    async def test_half_traversal_leaves_only_the_last_room_occupied(
        self, loop: HomeAssistant
    ):
        coordinator, helper = _helper(loop)
        helper.trigger_sensor("binary_sensor.motion_a", True)
        helper.trigger_sensor("binary_sensor.motion_b", True, delay=STEP)
        helper.trigger_sensor("binary_sensor.motion_a", False, delay=1)
        helper.trigger_sensor("binary_sensor.motion_c", True, delay=STEP)
        helper.trigger_sensor("binary_sensor.motion_b", False, delay=1)
        helper.trigger_sensor("binary_sensor.motion_c", False, delay=KNX_OFF)
        helper.tick(HOLD)

        assert helper.room("area_a").reason == "departure_trail"
        assert helper.room("area_b").reason == "departure_trail"
        assert helper.occupied("area_a", "area_b", "area_c", "area_d") == {"area_c"}
        assert helper.room("area_c").state == STATE_RETAINED


# ----------------------------------------------------------------------
# A T-junction: a corridor with two rooms at the far end.
# ----------------------------------------------------------------------


@pytest.fixture
def t_config():
    """Return a corridor with a room behind it and two rooms off its end."""
    return {
        DOMAIN: {
            "areas": {
                "area_a": {"name": "A", "exit_capable": True},
                "area_b": {"name": "B", "transition": True},
                "area_c": {"name": "C"},
                "area_d": {"name": "D"},
            },
            "adjacency": {"area_a": ["area_b"], "area_b": ["area_c", "area_d"]},
            "sensors": {
                "binary_sensor.motion_a": {"area": "area_a", "type": "motion"},
                "binary_sensor.motion_b": {"area": "area_b", "type": "motion"},
                "binary_sensor.motion_c": {"area": "area_c", "type": "motion"},
                "binary_sensor.motion_d": {"area": "area_d", "type": "motion"},
            },
        }
    }


@pytest.fixture
async def t_junction(hass: HomeAssistant, t_config):
    """Set up the integration on the T-junction topology."""
    assert await async_setup(hass, t_config)
    return hass


class TestTJunction:
    """Which branch the walk took decides which room keeps its occupancy."""

    @pytest.mark.parametrize(
        ("sensor", "destination", "other"),
        [
            ("binary_sensor.motion_c", "area_c", "area_d"),
            ("binary_sensor.motion_d", "area_d", "area_c"),
        ],
    )
    async def test_the_walk_ends_in_one_branch_only(
        self, t_junction: HomeAssistant, sensor: str, destination: str, other: str
    ):
        coordinator, helper = _helper(t_junction)
        helper.trigger_sensor("binary_sensor.motion_a", True)
        helper.trigger_sensor("binary_sensor.motion_b", True, delay=STEP)
        helper.trigger_sensor("binary_sensor.motion_a", False, delay=1)
        helper.trigger_sensor(sensor, True, delay=STEP)
        helper.trigger_sensor("binary_sensor.motion_b", False, delay=1)
        helper.trigger_sensor(sensor, False, delay=KNX_OFF)
        helper.tick(HOLD)

        assert helper.occupied("area_a", "area_b", "area_c", "area_d") == {destination}
        assert coordinator.get_occupancy(other) == 0


# ----------------------------------------------------------------------
# One room with two kinds of motion input.
# ----------------------------------------------------------------------


@pytest.fixture
def multi_sensor_config():
    """Return a room covered by both a PIR and a camera person detector."""
    return {
        DOMAIN: {
            "areas": {
                "entry": {"name": "Entry", "exit_capable": True, "transition": True},
                "room": {"name": "Room"},
            },
            "adjacency": {"entry": ["room"]},
            "sensors": {
                "binary_sensor.pir": {"area": "room", "type": "motion"},
                "binary_sensor.camera": {"area": "room", "type": "camera_person"},
                "binary_sensor.entry_motion": {"area": "entry", "type": "motion"},
            },
        }
    }


@pytest.fixture
async def multi_sensor(hass: HomeAssistant, multi_sensor_config):
    """Set up the integration on the two-input room."""
    assert await async_setup(hass, multi_sensor_config)
    return hass


class TestMultiSensor:
    """Any trusted motion input in the room counts as its own activity."""

    async def test_a_camera_still_on_keeps_the_room_occupied(
        self, multi_sensor: HomeAssistant
    ):
        coordinator, helper = _helper(multi_sensor)
        helper.trigger_sensor("binary_sensor.entry_motion", True)
        helper.trigger_sensor("binary_sensor.pir", True, delay=STEP)
        helper.trigger_sensor("binary_sensor.camera", True, delay=0.5)
        helper.trigger_sensor("binary_sensor.entry_motion", False, delay=1)
        helper.trigger_sensor("binary_sensor.pir", False, delay=KNX_OFF)

        # The PIR reported OFF but the camera still sees a person, so no
        # hold starts at all.
        helper.tick(HOLD * 2)
        assert coordinator.get_occupancy("room") == 1
        assert helper.room("room").state == STATE_OCCUPIED

        helper.trigger_sensor("binary_sensor.camera", False, delay=5)
        assert helper.room("room").state == STATE_PENDING

    async def test_a_later_entry_activation_never_clears_the_retained_room(
        self, multi_sensor: HomeAssistant
    ):
        coordinator, helper = _helper(multi_sensor)
        helper.trigger_sensor("binary_sensor.entry_motion", True)
        helper.trigger_sensor("binary_sensor.pir", True, delay=STEP)
        helper.trigger_sensor("binary_sensor.entry_motion", False, delay=1)
        helper.trigger_sensor("binary_sensor.pir", False, delay=KNX_OFF)
        helper.tick(HOLD)
        assert helper.room("room").state == STATE_RETAINED

        helper.trigger_sensor("binary_sensor.entry_motion", True, delay=600)
        helper.trigger_sensor("binary_sensor.entry_motion", False, delay=KNX_OFF)
        helper.tick(TRANSITION_HOLD)

        assert coordinator.get_occupancy("room") == 1
        assert coordinator.get_occupancy("entry") == 0

    async def test_a_delayed_camera_confirms_the_same_room(
        self, multi_sensor: HomeAssistant
    ):
        coordinator, helper = _helper(multi_sensor)
        helper.trigger_sensor("binary_sensor.entry_motion", True)
        helper.trigger_sensor("binary_sensor.pir", True, delay=STEP)
        assert coordinator.get_occupancy("room") == 1

        helper.trigger_sensor("binary_sensor.camera", True, delay=5)
        assert helper.room("room").state == STATE_OCCUPIED
        assert helper.room("room").confirmed is True


# ----------------------------------------------------------------------
# An open plan whose three detectors overlap.
# ----------------------------------------------------------------------


@pytest.fixture
def open_plan_config():
    """Return an open plan reached through an entry and a corridor."""
    return {
        DOMAIN: {
            "areas": {
                "entry": {"name": "Entry", "exit_capable": True},
                "corridor": {"name": "Corridor", "transition": True},
                "kitchen": {"name": "Kitchen", "profile": "living"},
                "dining": {"name": "Dining", "profile": "living"},
                "living": {"name": "Living", "profile": "living"},
            },
            "adjacency": {
                "entry": ["corridor"],
                "corridor": ["kitchen"],
                "kitchen": ["dining"],
                "dining": ["living"],
            },
            "sensors": {
                "binary_sensor.entry": {"area": "entry", "type": "motion"},
                "binary_sensor.corridor": {"area": "corridor", "type": "motion"},
                "binary_sensor.kitchen": {"area": "kitchen", "type": "motion"},
                "binary_sensor.dining": {"area": "dining", "type": "motion"},
                "binary_sensor.living": {"area": "living", "type": "motion"},
            },
            "open_plan_groups": {
                "open_plan": {"areas": ["kitchen", "dining", "living"]}
            },
        }
    }


@pytest.fixture
async def open_plan(hass: HomeAssistant, open_plan_config):
    """Set up the integration on the open-plan topology."""
    assert await async_setup(hass, open_plan_config)
    return hass


class TestOpenPlan:
    """Overlapping detectors make the exact room ambiguous, not the presence."""

    async def test_the_open_plan_stays_occupied_while_somebody_is_in_it(
        self, open_plan: HomeAssistant
    ):
        coordinator, helper = _helper(open_plan)
        helper.trigger_sensor("binary_sensor.entry", True)
        helper.trigger_sensor("binary_sensor.corridor", True, delay=STEP)
        helper.trigger_sensor("binary_sensor.kitchen", True, delay=STEP)
        helper.trigger_sensor("binary_sensor.entry", False, delay=1)
        helper.trigger_sensor("binary_sensor.corridor", False, delay=1)

        # One person stands in the kitchen; the neighbouring detectors keep
        # picking them up. Which of the three rooms is occupied is genuinely
        # ambiguous, so assert the group, not the room.
        for _ in range(10):
            helper.trigger_sensor("binary_sensor.dining", True, delay=1)
            helper.trigger_sensor("binary_sensor.kitchen", False, delay=KNX_OFF)
            helper.trigger_sensor("binary_sensor.kitchen", True, delay=3)
            helper.trigger_sensor("binary_sensor.dining", False, delay=1)
            helper.tick()
            assert helper.occupied("kitchen", "dining", "living"), (
                "the open plan read empty while somebody was standing in it"
            )
            assert all(
                coordinator.get_occupancy(area_id) <= 1
                for area_id in ("kitchen", "dining", "living")
            )

        helper.trigger_sensor("binary_sensor.kitchen", False, delay=KNX_OFF)
        helper.tick(HOLD)
        assert helper.occupied("kitchen", "dining", "living")

    async def test_walking_out_through_the_corridor_empties_the_open_plan(
        self, open_plan: HomeAssistant
    ):
        coordinator, helper = _helper(open_plan)
        # Somebody is settled in the dining area.
        helper.trigger_sensor("binary_sensor.dining", True)
        helper.trigger_sensor("binary_sensor.dining", False, delay=KNX_OFF)
        helper.tick(HOLD)
        assert helper.occupied("kitchen", "dining", "living") == {"dining"}

        # They walk out: dining, kitchen, corridor, and away.
        helper.trigger_sensor("binary_sensor.dining", True, delay=600)
        helper.trigger_sensor("binary_sensor.kitchen", True, delay=3)
        helper.trigger_sensor("binary_sensor.corridor", True, delay=STEP)
        helper.trigger_sensor("binary_sensor.dining", False, delay=1)
        helper.trigger_sensor("binary_sensor.kitchen", False, delay=2)
        helper.trigger_sensor("binary_sensor.corridor", False, delay=2)
        helper.tick(HOLD)

        assert helper.room("dining").reason == "departure_trail"
        assert helper.room("kitchen").reason == "departure_trail"
        assert helper.occupied("entry", "corridor", "kitchen", "dining", "living") == (
            set()
        )

    async def test_the_open_plan_is_bounded_by_the_living_ceiling(
        self, open_plan: HomeAssistant
    ):
        coordinator, helper = _helper(open_plan)
        helper.trigger_sensor("binary_sensor.living", True)
        helper.trigger_sensor("binary_sensor.living", False, delay=KNX_OFF)
        helper.tick(HOLD)
        assert helper.room("living").state == STATE_RETAINED

        helper.tick(LIVING_CEILING)
        assert helper.occupied("kitchen", "dining", "living") == set()
        assert helper.room("living").reason == "retention_ceiling"
