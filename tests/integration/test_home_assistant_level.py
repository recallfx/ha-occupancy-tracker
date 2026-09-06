"""Home Assistant level tests for the occupancy tracker.

These tests go through Home Assistant for real. They set the integration up
with ``async_setup_component``, feed state-change events with
``hass.states.async_set``, and move the clock so that the integration's own
ten-second interval fires and retires deadlines. Nothing here reaches into the
resolver: every assertion is on the state and the attributes of
``binary_sensor.<area>_occupancy``, on the storage the integration writes, or
on the services and buttons it registers.

The layout is a cut-down version of the shipped ``config.yaml``: an entrance,
a rear corridor marked as a transition room, two bedrooms with the sleeping
profile, the wardrobe as a dead end inside the main-bedroom suite, a kitchen
with the living profile, and one outdoor area.
"""

from __future__ import annotations

from datetime import timedelta
import time

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import (
    EVENT_STATE_CHANGED,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.occupancy_tracker.const import (
    DOMAIN,
    SERVICE_CLEAR_STALE_OCCUPANCY,
)
from custom_components.occupancy_tracker.coordinator import (
    PUBLISH_INTERVAL_SECONDS,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from custom_components.occupancy_tracker.helpers.constants import (
    UNAVAILABLE_GRACE_SECONDS,
)
from custom_components.occupancy_tracker.helpers.room_profiles import ROOM_PROFILES

HOLD = ROOM_PROFILES["default"].hold_seconds
TRANSITION_HOLD = ROOM_PROFILES["transition"].hold_seconds
LIVING_CEILING = ROOM_PROFILES["living"].retention_ceiling_seconds
SLEEPING_CEILING = ROOM_PROFILES["sleeping"].retention_ceiling_seconds

#: The integration retires deadlines on this interval.
TICK = 10

HOUSE = {
    DOMAIN: {
        "areas": {
            "entrance": {"name": "Entrance", "indoors": True},
            "corridor_2": {"name": "Corridor 2", "indoors": True, "transition": True},
            "bedroom_1": {"name": "Bedroom 1", "indoors": True, "profile": "sleeping"},
            "main_bedroom": {
                "name": "Main Bedroom",
                "indoors": True,
                "profile": "sleeping",
            },
            "wardrobe": {"name": "Wardrobe", "indoors": True},
            "kitchen": {"name": "Kitchen", "indoors": True, "profile": "living"},
            "frontyard": {
                "name": "Frontyard",
                "indoors": False,
                "exit_capable": True,
            },
        },
        "adjacency": {
            "entrance": ["frontyard", "corridor_2", "kitchen"],
            "corridor_2": ["entrance", "bedroom_1", "main_bedroom"],
            "main_bedroom": ["corridor_2", "wardrobe"],
            "bedroom_1": ["corridor_2"],
            "kitchen": ["entrance"],
        },
        "sensors": {
            "binary_sensor.entrance_motion": {"area": "entrance", "type": "motion"},
            "binary_sensor.corridor_2_motion": {"area": "corridor_2", "type": "motion"},
            "binary_sensor.bedroom_1_motion": {"area": "bedroom_1", "type": "motion"},
            "binary_sensor.main_bedroom_motion": {
                "area": "main_bedroom",
                "type": "motion",
            },
            "binary_sensor.wardrobe_motion": {"area": "wardrobe", "type": "motion"},
            "binary_sensor.kitchen_motion": {"area": "kitchen", "type": "motion"},
            "binary_sensor.front_motion": {
                "area": "frontyard",
                "type": "camera_motion",
            },
            "binary_sensor.bedroom_1_magnet": {
                "area": ["bedroom_1", "frontyard"],
                "type": "magnetic",
            },
        },
    }
}

INDOOR_AREAS = (
    "entrance",
    "corridor_2",
    "bedroom_1",
    "main_bedroom",
    "wardrobe",
    "kitchen",
)
SENSORS = tuple(HOUSE[DOMAIN]["sensors"])


def occupancy(area_id: str) -> str:
    """Return the entity ID of one area's occupancy sensor."""
    return f"binary_sensor.{area_id}_occupancy"


async def set_sensor(hass: HomeAssistant, sensor_id: str, state: str) -> None:
    """Feed one sensor state change and let the integration handle it."""
    hass.states.async_set(sensor_id, state)
    await hass.async_block_till_done()


async def advance(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float
) -> None:
    """Move the clock, then let the integration's interval tick fire."""
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def setup_house(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Set the integration up with every sensor reporting a quiet house."""
    for sensor_id in SENSORS:
        hass.states.async_set(sensor_id, STATE_OFF)
    await hass.async_block_till_done()

    assert await async_setup_component(hass, DOMAIN, HOUSE)
    await hass.async_block_till_done()

    # The first tick publishes the state machine's opening answer.
    await advance(hass, freezer, TICK + 1)


@pytest.fixture
async def house(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> HomeAssistant:
    """Return Home Assistant with the integration set up and quiet."""
    await setup_house(hass, freezer)
    return hass


def attribute(hass: HomeAssistant, area_id: str, name: str):
    """Return one attribute of an area's occupancy sensor."""
    return hass.states.get(occupancy(area_id)).attributes.get(name)


async def enter_and_settle(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, area_id: str
) -> None:
    """Put somebody in a room and let its detector fall silent.

    Nothing else fires, so the room has no departure trail.
    """
    sensor_id = f"binary_sensor.{area_id}_motion"
    await set_sensor(hass, sensor_id, STATE_ON)
    freezer.tick(timedelta(seconds=5))
    await set_sensor(hass, sensor_id, STATE_OFF)


# ----------------------------------------------------------------------
# Setup
# ----------------------------------------------------------------------


async def test_setup_creates_an_occupancy_sensor_for_every_area(
    house: HomeAssistant,
):
    for area_id in (*INDOOR_AREAS, "frontyard"):
        assert house.states.get(occupancy(area_id)) is not None
    assert house.services.has_service(DOMAIN, SERVICE_CLEAR_STALE_OCCUPANCY)
    assert house.states.get("button.clear_stale_occupancy") is not None


async def test_every_room_publishes_a_state_before_the_first_tick(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
):
    """Setting up must not leave the rooms unavailable until the first tick.

    Seeding an OFF startup baseline publishes nothing by itself, so setup has
    to refresh once it has read every sensor. Consumers otherwise see
    ``unavailable`` rather than ``off`` for ten seconds after every restart.
    """
    for sensor_id in SENSORS:
        hass.states.async_set(sensor_id, STATE_OFF)
    await hass.async_block_till_done()

    assert await async_setup_component(hass, DOMAIN, HOUSE)
    await hass.async_block_till_done()

    for area_id in INDOOR_AREAS:
        state = hass.states.get(occupancy(area_id))
        assert state.state == STATE_OFF, area_id


async def test_a_quiet_house_never_starts_occupied(house: HomeAssistant):
    # The old storage held permanent latches. A room now starts from live
    # sensor evidence, and every detector is reporting off.
    for area_id in INDOOR_AREAS:
        state = house.states.get(occupancy(area_id))
        assert state.state == STATE_OFF, area_id
        assert state.attributes["state"] == "vacant"


async def test_the_attributes_explain_the_decision(
    house: HomeAssistant, freezer: FrozenDateTimeFactory
):
    await set_sensor(house, "binary_sensor.bedroom_1_motion", STATE_ON)
    freezer.tick(timedelta(seconds=5))
    await set_sensor(house, "binary_sensor.bedroom_1_motion", STATE_OFF)

    state = house.states.get(occupancy("bedroom_1"))
    assert state.state == STATE_ON
    assert state.attributes["state"] == "pending"
    assert state.attributes["reason"] == "hold"
    assert state.attributes["confirmed"] is True
    assert state.attributes["exits"] == ["corridor_2"]
    assert state.attributes["deadline"] == pytest.approx(time.time() + HOLD)
    # The hold started on the off edge; the evidence behind it is 5 s old.
    assert state.attributes["state_since"] == pytest.approx(time.time(), abs=1)
    assert state.attributes["evidence_age"] == pytest.approx(5, abs=1)


# ----------------------------------------------------------------------
# Releasing and retaining
# ----------------------------------------------------------------------


async def test_a_room_goes_vacant_through_a_departure_trail(
    house: HomeAssistant, freezer: FrozenDateTimeFactory
):
    await set_sensor(house, "binary_sensor.bedroom_1_motion", STATE_ON)

    # The corridor fires while the bedroom detector is still on: somebody is
    # walking out.
    freezer.tick(timedelta(seconds=3))
    await set_sensor(house, "binary_sensor.corridor_2_motion", STATE_ON)
    freezer.tick(timedelta(seconds=2))
    await set_sensor(house, "binary_sensor.bedroom_1_motion", STATE_OFF)
    freezer.tick(timedelta(seconds=3))
    await set_sensor(house, "binary_sensor.corridor_2_motion", STATE_OFF)

    # The hold has to run out before the room releases.
    await advance(house, freezer, HOLD - 10)
    assert house.states.get(occupancy("bedroom_1")).state == STATE_ON

    await advance(house, freezer, TICK + 1)
    state = house.states.get(occupancy("bedroom_1"))
    assert state.state == STATE_OFF
    assert state.attributes["state"] == "vacant"
    assert state.attributes["reason"] == "departure_trail"
    assert state.attributes["deadline"] is None


async def test_a_room_with_no_trail_is_retained(
    house: HomeAssistant, freezer: FrozenDateTimeFactory
):
    await enter_and_settle(house, freezer, "main_bedroom")
    last_off = time.time()

    await advance(house, freezer, HOLD + TICK)

    state = house.states.get(occupancy("main_bedroom"))
    assert state.state == STATE_ON
    assert state.attributes["state"] == "retained"
    assert state.attributes["reason"] == "no_exit_trail"
    assert state.attributes["confirmed"] is True
    assert state.attributes["deadline"] == pytest.approx(last_off + SLEEPING_CEILING)


async def test_a_retained_room_is_released_by_its_ceiling(
    house: HomeAssistant, freezer: FrozenDateTimeFactory
):
    await enter_and_settle(house, freezer, "kitchen")
    await advance(house, freezer, HOLD + TICK)
    assert attribute(house, "kitchen", "state") == "retained"

    await advance(house, freezer, LIVING_CEILING)
    state = house.states.get(occupancy("kitchen"))
    assert state.state == STATE_OFF
    assert state.attributes["reason"] == "retention_ceiling"


async def test_a_corridor_releases_after_its_hold(
    house: HomeAssistant, freezer: FrozenDateTimeFactory
):
    await enter_and_settle(house, freezer, "corridor_2")

    await advance(house, freezer, TRANSITION_HOLD - 15)
    state = house.states.get(occupancy("corridor_2"))
    assert state.state == STATE_ON
    assert state.attributes["state"] == "pending"

    await advance(house, freezer, TICK + 5)
    state = house.states.get(occupancy("corridor_2"))
    assert state.state == STATE_OFF
    assert state.attributes["reason"] == "transition_room"


async def test_walking_into_the_wardrobe_is_not_leaving_the_bedroom(
    house: HomeAssistant, freezer: FrozenDateTimeFactory
):
    await enter_and_settle(house, freezer, "main_bedroom")
    freezer.tick(timedelta(seconds=3))
    await set_sensor(house, "binary_sensor.wardrobe_motion", STATE_ON)
    freezer.tick(timedelta(seconds=5))
    await set_sensor(house, "binary_sensor.wardrobe_motion", STATE_OFF)

    await advance(house, freezer, HOLD + TICK)

    assert house.states.get(occupancy("main_bedroom")).state == STATE_ON
    assert attribute(house, "main_bedroom", "reason") == "no_exit_trail"
    assert attribute(house, "main_bedroom", "exits") == ["corridor_2"]


async def test_a_spilled_entry_is_never_retained(
    house: HomeAssistant, freezer: FrozenDateTimeFactory
):
    # The corridor detector sees into the doorway: the bedroom edge lands half
    # a second later, far too fast to be somebody walking in.
    await set_sensor(house, "binary_sensor.corridor_2_motion", STATE_ON)
    freezer.tick(timedelta(seconds=0.5))
    await set_sensor(house, "binary_sensor.bedroom_1_motion", STATE_ON)
    assert attribute(house, "bedroom_1", "confirmed") is False

    freezer.tick(timedelta(seconds=4.5))
    await set_sensor(house, "binary_sensor.corridor_2_motion", STATE_OFF)
    await set_sensor(house, "binary_sensor.bedroom_1_motion", STATE_OFF)

    await advance(house, freezer, HOLD + TICK)
    state = house.states.get(occupancy("bedroom_1"))
    assert state.state == STATE_OFF
    assert state.attributes["reason"] in ("unconfirmed_entry", "departure_trail")


async def test_a_corridor_pass_never_clears_a_retained_room(
    house: HomeAssistant, freezer: FrozenDateTimeFactory
):
    await enter_and_settle(house, freezer, "main_bedroom")
    await advance(house, freezer, HOLD + TICK)
    assert attribute(house, "main_bedroom", "state") == "retained"

    # Somebody else walks the corridor an hour later.
    await advance(house, freezer, 3600)
    await set_sensor(house, "binary_sensor.corridor_2_motion", STATE_ON)
    freezer.tick(timedelta(seconds=5))
    await set_sensor(house, "binary_sensor.corridor_2_motion", STATE_OFF)
    await advance(house, freezer, TRANSITION_HOLD + TICK)

    assert house.states.get(occupancy("main_bedroom")).state == STATE_ON
    assert house.states.get(occupancy("corridor_2")).state == STATE_OFF


async def test_an_outdoor_area_mirrors_its_camera(
    house: HomeAssistant, freezer: FrozenDateTimeFactory
):
    await set_sensor(house, "binary_sensor.front_motion", STATE_ON)
    assert house.states.get(occupancy("frontyard")).state == STATE_ON

    freezer.tick(timedelta(seconds=5))
    await set_sensor(house, "binary_sensor.front_motion", STATE_OFF)
    assert house.states.get(occupancy("frontyard")).state == STATE_OFF


# ----------------------------------------------------------------------
# Faults
# ----------------------------------------------------------------------


async def test_an_unavailable_sensor_makes_the_entity_unavailable_not_off(
    house: HomeAssistant, freezer: FrozenDateTimeFactory
):
    await set_sensor(house, "binary_sensor.kitchen_motion", STATE_ON)
    freezer.tick(timedelta(seconds=5))
    await set_sensor(house, "binary_sensor.kitchen_motion", STATE_UNAVAILABLE)

    # Inside the grace the last answer still stands.
    assert house.states.get(occupancy("kitchen")).state == STATE_ON

    await advance(house, freezer, UNAVAILABLE_GRACE_SECONDS + TICK)
    assert house.states.get(occupancy("kitchen")).state == STATE_UNAVAILABLE

    # The transport comes back and the room answers again.
    await set_sensor(house, "binary_sensor.kitchen_motion", STATE_OFF)
    state = house.states.get(occupancy("kitchen"))
    assert state.state == STATE_ON
    assert state.attributes["state"] == "pending"


async def test_a_short_transport_blip_changes_nothing(
    house: HomeAssistant, freezer: FrozenDateTimeFactory
):
    # Every indoor entity went unavailable for eight seconds once, on
    # 2026-09-02. That is a transport fault, not a departure.
    await set_sensor(house, "binary_sensor.bedroom_1_motion", STATE_ON)
    freezer.tick(timedelta(seconds=2))
    await set_sensor(house, "binary_sensor.bedroom_1_motion", STATE_UNAVAILABLE)
    freezer.tick(timedelta(seconds=8))
    await set_sensor(house, "binary_sensor.bedroom_1_motion", STATE_ON)

    state = house.states.get(occupancy("bedroom_1"))
    assert state.state == STATE_ON
    assert state.attributes["state"] == "occupied"


# ----------------------------------------------------------------------
# Storage
# ----------------------------------------------------------------------


def _stored(rooms: dict, version: int = STORAGE_VERSION) -> dict:
    """Return a storage payload in the on-disk shape."""
    return {
        "version": version,
        "minor_version": 1,
        "key": STORAGE_KEY,
        "data": {"initialized": True, "rooms": rooms},
    }


async def test_a_retained_room_survives_a_restart(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, hass_storage
):
    now = time.time()
    hass_storage[STORAGE_KEY] = _stored(
        {
            "main_bedroom": {
                "state": "retained",
                "state_since": now - 600,
                "last_own_on": now - 700,
                "last_own_off": now - 690,
                "confirmed": True,
                "deadline": now - 690 + SLEEPING_CEILING,
                "reason": "no_exit_trail",
                "first_exit_edge": None,
            }
        }
    )

    await setup_house(hass, freezer)

    state = hass.states.get(occupancy("main_bedroom"))
    assert state.state == STATE_ON
    assert state.attributes["state"] == "retained"
    # A restart never grants a fresh ceiling.
    assert state.attributes["deadline"] == pytest.approx(now - 690 + SLEEPING_CEILING)


async def test_a_restart_releases_a_room_whose_ceiling_expired(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, hass_storage
):
    now = time.time()
    hass_storage[STORAGE_KEY] = _stored(
        {
            "kitchen": {
                "state": "retained",
                "state_since": now - 5 * 3600,
                "last_own_on": now - 5 * 3600,
                "last_own_off": now - 5 * 3600,
                "confirmed": True,
                "deadline": now - 5 * 3600 + LIVING_CEILING,
                "reason": "no_exit_trail",
                "first_exit_edge": None,
            }
        }
    )

    await setup_house(hass, freezer)

    state = hass.states.get(occupancy("kitchen"))
    assert state.state == STATE_OFF
    assert state.attributes["reason"] == "retention_ceiling"


async def test_a_restart_discards_the_old_permanent_latches(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, hass_storage
):
    # Storage version 1 held the latches that kept every indoor room on for
    # months. They are not occupancy evidence and must never be imported.
    hass_storage[STORAGE_KEY] = {
        "version": 1,
        "key": STORAGE_KEY,
        "data": {"indoor_latched": ["main_bedroom", "bedroom_1", "kitchen"]},
    }

    await setup_house(hass, freezer)

    for area_id in INDOOR_AREAS:
        assert hass.states.get(occupancy(area_id)).state == STATE_OFF, area_id
    assert hass_storage[STORAGE_KEY]["version"] == STORAGE_VERSION


async def test_the_decision_state_is_persisted_for_the_next_restart(
    house: HomeAssistant, freezer: FrozenDateTimeFactory, hass_storage
):
    await enter_and_settle(house, freezer, "bedroom_1")
    await advance(house, freezer, HOLD + TICK)

    stored = hass_storage[STORAGE_KEY]
    assert stored["version"] == STORAGE_VERSION
    room = stored["data"]["rooms"]["bedroom_1"]
    assert room["state"] == "retained"
    assert room["confirmed"] is True
    assert room["deadline"] == pytest.approx(room["last_own_off"] + SLEEPING_CEILING)
    # Outdoor areas mirror their sensors and have nothing durable to keep.
    assert "frontyard" not in stored["data"]["rooms"]


# ----------------------------------------------------------------------
# Manual clear
# ----------------------------------------------------------------------


async def test_the_clear_service_empties_one_room_only(
    house: HomeAssistant, freezer: FrozenDateTimeFactory
):
    await enter_and_settle(house, freezer, "main_bedroom")
    await enter_and_settle(house, freezer, "bedroom_1")
    await advance(house, freezer, HOLD + TICK)
    assert house.states.get(occupancy("main_bedroom")).state == STATE_ON
    assert house.states.get(occupancy("bedroom_1")).state == STATE_ON

    await house.services.async_call(
        DOMAIN,
        SERVICE_CLEAR_STALE_OCCUPANCY,
        {"area_id": "main_bedroom"},
        blocking=True,
    )
    await house.async_block_till_done()

    cleared = house.states.get(occupancy("main_bedroom"))
    assert cleared.state == STATE_OFF
    assert cleared.attributes["reason"] == "manual_service"
    assert house.states.get(occupancy("bedroom_1")).state == STATE_ON


async def test_the_clear_service_refuses_a_room_with_live_motion(
    house: HomeAssistant,
):
    await set_sensor(house, "binary_sensor.bedroom_1_motion", STATE_ON)

    await house.services.async_call(
        DOMAIN,
        SERVICE_CLEAR_STALE_OCCUPANCY,
        {"area_id": "bedroom_1"},
        blocking=True,
    )
    await house.async_block_till_done()

    state = house.states.get(occupancy("bedroom_1"))
    assert state.state == STATE_ON
    assert state.attributes["state"] == "occupied"


async def test_the_clear_button_empties_the_stale_rooms(
    house: HomeAssistant, freezer: FrozenDateTimeFactory
):
    await enter_and_settle(house, freezer, "main_bedroom")
    await advance(house, freezer, HOLD + TICK)
    assert house.states.get(occupancy("main_bedroom")).state == STATE_ON

    await house.services.async_call(
        "button",
        "press",
        {"entity_id": "button.clear_stale_occupancy"},
        blocking=True,
    )
    await house.async_block_till_done()

    state = house.states.get(occupancy("main_bedroom"))
    assert state.state == STATE_OFF
    assert state.attributes["reason"] == "manual_button"


async def test_a_quiet_tick_does_not_republish_every_entity(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
):
    """Time-derived attributes refresh once a minute, not on every tick.

    Every publish is a recorder row for every entity, and the tick moved from
    60 s to 10 s when it became the path that retires deadlines. A transition
    still publishes at once; a quiet tick waits for the minute.
    """
    await setup_house(hass, freezer)
    await enter_and_settle(hass, freezer, "kitchen")
    await advance(hass, freezer, TICK)

    events: list[str] = []

    @callback
    def record(event) -> None:
        if event.data["entity_id"] == occupancy("kitchen"):
            events.append(event.data["new_state"].state)

    hass.bus.async_listen(EVENT_STATE_CHANGED, record)

    for _ in range(3):
        await advance(hass, freezer, TICK)
    assert events == []

    await advance(hass, freezer, PUBLISH_INTERVAL_SECONDS)
    assert events == [STATE_ON]
