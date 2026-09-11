"""Conformance tests: the shipped engine against `BEHAVIOR.md`, rule by rule.

Every test here names the section of `BEHAVIOR.md` it pins, so a rule cannot
be reworded without a test moving with it. The engine is built from the
production `config.yaml`, so the topology, profiles and exits are the real
ones.

`BEHAVIOR.md` section 14 points at this file for its invariants. The scope is
the engine: ingestion decisions and audit records are coordinator concerns and
are pinned in `tests/occupancy_tracker/test_init.py` and
`tests/occupancy_tracker/test_audit.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from custom_components.occupancy_tracker.helpers.constants import (
    UNAVAILABLE_GRACE_SECONDS,
)
from custom_components.occupancy_tracker.helpers.occupancy_engine import (
    STATE_OCCUPIED,
    STATE_PENDING,
    STATE_RETAINED,
    STATE_UNKNOWN,
    STATE_VACANT,
    OccupancyEngine,
)
from custom_components.occupancy_tracker.helpers.room_profiles import ROOM_PROFILES

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"
PRODUCTION_CONFIG = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))

T0 = 1_000_000.0

LIVING_HOLD = ROOM_PROFILES["living"].hold_seconds
LIVING_CEILING = ROOM_PROFILES["living"].retention_ceiling_seconds
TRANSITION_HOLD = ROOM_PROFILES["transition"].hold_seconds


@pytest.fixture
def engine() -> OccupancyEngine:
    """Return an engine on the production topology and a virtual clock."""
    return OccupancyEngine(PRODUCTION_CONFIG, clock=lambda: T0)


def run(engine: OccupancyEngine, steps) -> OccupancyEngine:
    """Apply a list of ``(timestamp, active areas)`` steps in order."""
    for timestamp, active in steps:
        engine.apply(timestamp, set(active))
    return engine


def enter_study_and_fall_silent(engine: OccupancyEngine) -> None:
    """Occupy the study at T0 and let its detector go quiet at T0 + 45."""
    engine.apply(T0, {"study"})
    engine.apply(T0 + 45, set())


# ----------------------------------------------------------------------
# Section 4: topology
# ----------------------------------------------------------------------

#: Every derived exit set of the current house, as section 4 tabulates them.
EXPECTED_EXITS = {
    "entrance": {"garage", "corridor_1", "kitchen", "workshop"},
    "garage": {"entrance", "workshop"},
    "guest_room": {"entrance"},
    "workshop": {"entrance", "garage"},
    "corridor_1": {"entrance", "corridor_2", "bedroom_2"},
    "corridor_2": {"corridor_1", "bedroom_2", "main_bedroom"},
    "kitchen": {"entrance", "dining_room", "living"},
    "dining_room": {"kitchen", "living"},
    "living": {"kitchen", "dining_room"},
    "study": {"corridor_1"},
    "bedroom_2": {"corridor_1", "corridor_2"},
    "bathroom": {"corridor_1"},
    "bedroom_1": {"corridor_2"},
    "utility_room": {"corridor_2"},
    "main_bedroom": {"corridor_2"},
    "main_bathroom": {"main_bedroom"},
    "wardrobe": {"main_bedroom"},
}


def test_the_exit_table_matches_the_production_topology(
    engine: OccupancyEngine,
) -> None:
    """BEHAVIOR.md section 4: the full table of derived exits."""
    derived = {
        area_id: set(engine.exits_for(area_id))
        for area_id in engine.topology
        if engine.topology[area_id].is_indoors
    }
    assert derived == EXPECTED_EXITS


@pytest.mark.parametrize(
    "outdoor", ["frontyard", "backyard", "left_side", "right_side"]
)
def test_no_outdoor_area_is_ever_an_exit(engine: OccupancyEngine, outdoor: str) -> None:
    """BEHAVIOR.md section 4: outdoor activity never manufactures a departure."""
    assert [
        area_id for area_id in engine.topology if outdoor in engine.exits_for(area_id)
    ] == []


# ----------------------------------------------------------------------
# Section 6.4: the deadline evaluation table, row by row
# ----------------------------------------------------------------------


def test_6_4_row_1_a_trail_inside_the_window_releases_the_room(
    engine: OccupancyEngine,
) -> None:
    """BEHAVIOR.md section 6.4 row 1: `pending`, trail seen, `departure_trail`."""
    enter_study_and_fall_silent(engine)
    run(engine, [(T0 + 47, {"corridor_1"}), (T0 + 52, set())])

    engine.apply(T0 + 45 + LIVING_HOLD, set())

    room = engine.rooms["study"]
    assert (room.state, room.reason) == (STATE_VACANT, "departure_trail")


def test_6_4_row_2_an_unconfirmed_entry_releases_the_room(
    engine: OccupancyEngine,
) -> None:
    """BEHAVIOR.md section 6.4 row 2: `pending`, entry never confirmed."""
    # A spill-timed entry into a vacant room is never confirmed.
    engine.apply(T0, {"corridor_1"})
    engine.apply(T0 + 0.5, {"corridor_1", "study"})
    assert engine.rooms["study"].confirmed is False
    engine.apply(T0 + 5, {"corridor_1"})

    engine.apply(T0 + 5 + LIVING_HOLD + 1, {"corridor_1"})

    room = engine.rooms["study"]
    assert (room.state, room.reason) == (STATE_VACANT, "unconfirmed_entry")


def test_6_4_row_3_a_zero_ceiling_releases_the_room(
    engine: OccupancyEngine,
) -> None:
    """BEHAVIOR.md section 6.4 row 3: `pending`, profile ceiling 0."""
    run(engine, [(T0, {"corridor_1"}), (T0 + 10, set())])

    engine.apply(T0 + 10 + TRANSITION_HOLD + 1, set())

    room = engine.rooms["corridor_1"]
    assert (room.state, room.reason) == (STATE_VACANT, "transition_room")


def test_6_4_row_4_no_trail_retains_the_room(engine: OccupancyEngine) -> None:
    """BEHAVIOR.md section 6.4 row 4: `pending` with no trail, `no_exit_trail`."""
    enter_study_and_fall_silent(engine)

    engine.apply(T0 + 45 + LIVING_HOLD, set())

    room = engine.rooms["study"]
    assert (room.state, room.reason) == (STATE_RETAINED, "no_exit_trail")
    # The ceiling is measured from the room's own OFF edge, not from the hold.
    assert room.deadline == pytest.approx(T0 + 45 + LIVING_CEILING)


def test_6_4_row_5_the_ceiling_releases_a_retained_room(
    engine: OccupancyEngine,
) -> None:
    """BEHAVIOR.md section 6.4 row 5: `retained`, ceiling due."""
    enter_study_and_fall_silent(engine)
    engine.apply(T0 + 45 + LIVING_HOLD, set())
    assert engine.rooms["study"].state == STATE_RETAINED

    engine.apply(T0 + 45 + LIVING_CEILING, set())

    room = engine.rooms["study"]
    assert (room.state, room.reason) == (STATE_VACANT, "retention_ceiling")


def test_6_4_a_ceiling_already_in_the_past_releases_in_the_same_pass(
    engine: OccupancyEngine,
) -> None:
    """BEHAVIOR.md section 6.4: a long outage resolves both deadlines at once."""
    enter_study_and_fall_silent(engine)

    engine.apply(T0 + 45 + LIVING_CEILING + 1, set())

    room = engine.rooms["study"]
    assert (room.state, room.reason) == (STATE_VACANT, "retention_ceiling")


# ----------------------------------------------------------------------
# Section 14: the invariants, one test each
# ----------------------------------------------------------------------


def test_invariant_1_a_trusted_sensor_occupies_every_area_it_maps_to(
    engine: OccupancyEngine,
) -> None:
    """BEHAVIOR.md section 14, invariant 1."""
    # `binary_sensor.workshop_magnet` aside, the front sensors map to two areas.
    engine.apply(T0, {"entrance", "frontyard"})

    assert engine.rooms["entrance"].state == STATE_OCCUPIED
    assert engine.rooms["frontyard"].state == STATE_OCCUPIED


def test_invariant_2_neighbor_motion_alone_never_changes_a_room(
    engine: OccupancyEngine,
) -> None:
    """BEHAVIOR.md section 14, invariant 2."""
    run(engine, [(T0, {"corridor_1"}), (T0 + 5, set()), (T0 + 600, set())])

    assert engine.rooms["study"].state == STATE_VACANT
    assert engine.rooms["bathroom"].state == STATE_VACANT


def test_invariant_3_a_room_is_held_for_its_hold_before_any_decision(
    engine: OccupancyEngine,
) -> None:
    """BEHAVIOR.md section 14, invariant 3."""
    enter_study_and_fall_silent(engine)
    # A trail is already visible, and it still cannot shorten the hold.
    run(engine, [(T0 + 47, {"corridor_1"}), (T0 + 52, set())])

    engine.apply(T0 + 45 + LIVING_HOLD - 1, set())
    assert engine.rooms["study"].state == STATE_PENDING

    engine.apply(T0 + 45 + LIVING_HOLD, set())
    assert engine.rooms["study"].state == STATE_VACANT


def test_invariant_4_a_room_with_no_release_evidence_retains(
    engine: OccupancyEngine,
) -> None:
    """BEHAVIOR.md section 14, invariant 4."""
    enter_study_and_fall_silent(engine)
    # An exit edge later than the trail window is not release evidence.
    run(engine, [(T0 + 90, {"corridor_1"}), (T0 + 95, set())])

    engine.apply(T0 + 45 + LIVING_HOLD, set())

    assert engine.rooms["study"].state == STATE_RETAINED


def test_invariant_5_only_own_motion_or_the_ceiling_releases_a_retained_room(
    engine: OccupancyEngine,
) -> None:
    """BEHAVIOR.md section 14, invariant 5."""
    enter_study_and_fall_silent(engine)
    engine.apply(T0 + 45 + LIVING_HOLD, set())
    assert engine.rooms["study"].state == STATE_RETAINED

    # A corridor pass long after the hold is somebody else walking past.
    run(engine, [(T0 + 900, {"corridor_1"}), (T0 + 905, set())])
    assert engine.rooms["study"].state == STATE_RETAINED
    assert engine.rooms["study"].first_exit_edge is None

    engine.apply(T0 + 1000, {"study"})
    assert engine.rooms["study"].state == STATE_OCCUPIED


def test_invariant_6_a_spill_timed_edge_neither_confirms_nor_re_anchors(
    engine: OccupancyEngine,
) -> None:
    """BEHAVIOR.md section 14, invariant 6."""
    enter_study_and_fall_silent(engine)
    engine.apply(T0 + 50, {"corridor_1"})  # the trail
    engine.apply(T0 + 50.5, {"corridor_1", "study"})  # spill, inside the window

    room = engine.rooms["study"]
    assert room.state == STATE_PENDING
    assert room.first_exit_edge == T0 + 50

    # The spilled edge's own OFF must not re-anchor the hold either.
    engine.apply(T0 + 55, {"corridor_1"})
    assert room.last_own_off == T0 + 45

    engine.apply(T0 + 45 + LIVING_HOLD, set())
    assert (room.state, room.reason) == (STATE_VACANT, "departure_trail")


def test_invariant_7_unavailable_is_never_treated_as_off(
    engine: OccupancyEngine,
) -> None:
    """BEHAVIOR.md section 14, invariant 7."""
    enter_study_and_fall_silent(engine)
    engine.apply(T0 + 50, set(), {"study"})

    # Inside the grace the room still answers, and its deadline is frozen.
    assert engine.is_known("study", T0 + 50 + UNAVAILABLE_GRACE_SECONDS - 1) is True
    assert engine.rooms["study"].state == STATE_PENDING
    # Past the grace it publishes unknown rather than a confident vacancy.
    assert (
        engine.published_state("study", T0 + 50 + UNAVAILABLE_GRACE_SECONDS)
        == STATE_UNKNOWN
    )
    assert engine.rooms["study"].state == STATE_PENDING

    # Recovery resolves the frozen deadline against now.
    engine.apply(T0 + 500, set())
    assert engine.rooms["study"].state == STATE_RETAINED


def test_invariant_7_a_resumption_is_not_a_new_edge(
    engine: OccupancyEngine,
) -> None:
    """BEHAVIOR.md section 14, invariant 7: a still-ON return resumes."""
    engine.apply(T0, {"study"})
    engine.apply(T0 + 45, set(), {"study"})
    engine.apply(T0 + 51, {"study"})

    room = engine.rooms["study"]
    assert (room.state, room.reason) == (STATE_OCCUPIED, "own_motion_resumed")
    assert room.last_own_on == T0
    # No exit edge is noted for the neighbors of a resuming room.
    assert engine.rooms["corridor_1"].first_exit_edge is None


def test_invariant_8_a_restart_never_grants_a_fresh_hold(
    engine: OccupancyEngine,
) -> None:
    """BEHAVIOR.md section 14, invariant 8.

    The storage version 1 half of the invariant is a store concern and is
    pinned by `test_init.py` and `test_home_assistant_level.py`.
    """
    restarted = OccupancyEngine(PRODUCTION_CONFIG, clock=lambda: T0 + 300)
    restarted.restore(
        {
            "study": {
                "state": STATE_OCCUPIED,
                "state_since": T0,
                "last_own_on": T0,
                "confirmed": True,
                "deadline": None,
            }
        },
        T0 + 300,
        set(),
    )

    room = restarted.rooms["study"]
    # The hold that the last live activation had earned, already expired, so
    # the room resolves in the restore pass rather than starting over.
    assert room.state == STATE_RETAINED
    assert room.deadline == pytest.approx(T0 + LIVING_CEILING)


def test_invariant_8_an_expired_stored_deadline_resolves_in_the_restore_pass() -> None:
    """BEHAVIOR.md section 14, invariant 8: expired deadlines resolve."""
    restarted = OccupancyEngine(PRODUCTION_CONFIG, clock=lambda: T0 + 100_000)
    restarted.restore(
        {
            "study": {
                "state": STATE_RETAINED,
                "state_since": T0,
                "last_own_on": T0 - 10,
                "last_own_off": T0,
                "confirmed": True,
                "deadline": T0 + LIVING_CEILING,
            }
        },
        T0 + 100_000,
        set(),
    )

    room = restarted.rooms["study"]
    assert (room.state, room.reason) == (STATE_VACANT, "retention_ceiling")


def test_invariant_9_outdoor_areas_mirror_their_sensors(
    engine: OccupancyEngine,
) -> None:
    """BEHAVIOR.md section 14, invariant 9."""
    engine.apply(T0, {"frontyard"})
    room = engine.rooms["frontyard"]
    assert (room.state, room.reason) == (STATE_OCCUPIED, "outdoor_active")

    engine.apply(T0 + 10, set())
    assert (room.state, room.reason) == (STATE_VACANT, "outdoor_clear")


def test_invariant_10_manual_clear_refuses_active_motion_and_clears_no_other_room(
    engine: OccupancyEngine,
) -> None:
    """BEHAVIOR.md section 14, invariant 10."""
    enter_study_and_fall_silent(engine)
    engine.apply(T0 + 45 + LIVING_HOLD, set())
    engine.apply(T0 + 200, {"kitchen"})

    assert engine.force_vacant(["study"], T0 + 300, "manual_clear") == ["study"]
    assert engine.rooms["kitchen"].state == STATE_OCCUPIED
    # Live positive evidence outranks a stale human assertion.
    assert engine.force_vacant(["kitchen"], T0 + 301, "manual_clear") == []
    # An outdoor area has nothing durable to clear.
    assert engine.force_vacant(["frontyard"], T0 + 302, "manual_clear") == []


def test_invariant_11_a_rewound_event_never_changes_a_decision(
    engine: OccupancyEngine,
) -> None:
    """BEHAVIOR.md section 14, invariant 11.

    The engine clamps time forward; the ingestion rules that drop late,
    duplicate and invalid events with an audited decision are section 3.3 and
    live in the coordinator.
    """
    engine.apply(T0, {"study"})

    engine.apply(T0 - 100, set())

    assert engine.rooms["study"].last_own_off == T0
    assert engine.rooms["study"].state == STATE_PENDING


def test_invariant_12_only_evidence_and_deadlines_move_a_room(
    engine: OccupancyEngine,
) -> None:
    """BEHAVIOR.md section 14, invariant 12.

    Activity expiry, freshness and the warnings are not engine inputs at all:
    a room with live own motion stays occupied however long it goes without a
    new edge, and no amount of ticking decays it.
    """
    engine.apply(T0, {"study"})

    for step in range(1, 200):
        engine.apply(T0 + step * 300, {"study"})

    assert engine.rooms["study"].state == STATE_OCCUPIED
    assert engine.rooms["study"].reason == "own_motion"


def test_invariant_13_the_same_events_produce_the_same_states() -> None:
    """BEHAVIOR.md section 14, invariant 13."""
    steps = [
        (T0, {"study"}),
        (T0 + 45, set()),
        (T0 + 50, {"corridor_1"}),
        (T0 + 55, set()),
        (T0 + 45 + LIVING_HOLD, set()),
    ]

    first = run(OccupancyEngine(PRODUCTION_CONFIG, clock=lambda: T0), steps)
    second = run(OccupancyEngine(PRODUCTION_CONFIG, clock=lambda: T0), steps)

    assert {area_id: room.as_dict() for area_id, room in first.rooms.items()} == {
        area_id: room.as_dict() for area_id, room in second.rooms.items()
    }


# ----------------------------------------------------------------------
# Section 5: a decision the code has not caught up with
# ----------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="BEHAVIOR.md section 15, item 1: the `transient` profile is not "
    "implemented, so bathroom and utility_room still retain for 2 h",
)
def test_the_transient_profile_bounds_the_low_motion_rooms(
    engine: OccupancyEngine,
) -> None:
    """BEHAVIOR.md section 5: the 2026-09-09 decision, 90 s hold, 15 min ceiling."""
    assert "transient" in ROOM_PROFILES
    for area_id in ("bathroom", "utility_room"):
        profile = engine.profile_for(area_id)
        assert (profile.name, profile.hold_seconds) == ("transient", 90)
        assert profile.retention_ceiling_seconds == 900
