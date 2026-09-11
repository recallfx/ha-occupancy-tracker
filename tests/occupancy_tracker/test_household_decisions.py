"""The household decisions of `BEHAVIOR.md` section 13, pinned in configuration.

Each decision that is visible in `config.yaml` or in the room profiles gets a
test here, so changing the configuration cannot silently reverse a decision
the household made. Decisions that are consumer policy, or that live in
another repository, are not checkable here and are not tested.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from custom_components.occupancy_tracker.helpers.constants import (
    MAGNETIC_SENSOR_TYPES,
)
from custom_components.occupancy_tracker.helpers.occupancy_engine import OccupancyEngine

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"
PRODUCTION_CONFIG = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))

TWO_HOURS = 2 * 3600


@pytest.fixture
def engine() -> OccupancyEngine:
    """Return an engine built from the production configuration."""
    return OccupancyEngine(PRODUCTION_CONFIG)


def test_bedroom_2_keeps_a_ceiling_long_enough_for_a_daytime_nap(
    engine: OccupancyEngine,
) -> None:
    """BEHAVIOR.md section 13, 2026-09-06: nursery.

    The occupant cannot leave unaided, so the room must not release before the
    nap plausibly ends. Any profile with a ceiling of at least two hours
    satisfies the decision; the `sleeping` profile was rejected as too long.
    """
    profile = engine.profile_for("bedroom_2")

    assert profile is not None
    assert profile.retention_ceiling_seconds >= TWO_HOURS


def test_the_corridors_are_transition_rooms(engine: OccupancyEngine) -> None:
    """BEHAVIOR.md section 13: corridors never retain, so they never ghost."""
    for area_id in ("corridor_1", "corridor_2"):
        profile = engine.profile_for(area_id)
        assert profile is not None, area_id
        assert profile.name == "transition", area_id
        assert profile.retention_ceiling_seconds == 0, area_id


def test_the_slept_in_bedrooms_use_the_sleeping_profile(
    engine: OccupancyEngine,
) -> None:
    """BEHAVIOR.md section 13: a sleeping person is invisible to a PIR."""
    for area_id in ("bedroom_1", "main_bedroom"):
        profile = engine.profile_for(area_id)
        assert profile is not None, area_id
        assert profile.name == "sleeping", area_id


def test_every_contact_is_configured_as_a_magnetic_sensor() -> None:
    """BEHAVIOR.md section 13, 2026-09-06: the contact walk-through.

    All ten contacts were verified as 5 s pulses and are trusted as departure
    evidence. They are departure evidence only because their type is a contact
    class, never occupancy evidence.
    """
    contacts = {
        sensor_id: sensor_config
        for sensor_id, sensor_config in PRODUCTION_CONFIG["sensors"].items()
        if sensor_id.endswith("_magnet")
    }

    assert len(contacts) == 10
    assert all(
        sensor_config.get("type") == "magnetic" for sensor_config in contacts.values()
    )
    assert all(
        sensor_config.get("type") in MAGNETIC_SENSOR_TYPES
        for sensor_config in contacts.values()
    )


def test_the_workshop_magnet_is_mapped_to_the_entrance() -> None:
    """BEHAVIOR.md section 13, 2026-09-07: `workshop_magnet` is the front door.

    Home Assistant names the entity after the ETS label, not after the door it
    is on, so the mapping is the decision: it counts as departure evidence for
    the entrance.
    """
    areas = PRODUCTION_CONFIG["sensors"]["binary_sensor.workshop_magnet"]["area"]

    assert "entrance" in areas
    assert "workshop" not in areas
