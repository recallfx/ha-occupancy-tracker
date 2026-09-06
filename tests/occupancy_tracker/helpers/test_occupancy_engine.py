"""Contract tests for the exit-gated occupancy state machine.

Every transition and every deadline boundary is driven by an explicit virtual
clock, so vacancy has to happen with no further sensor events.
"""

from __future__ import annotations

import pytest

from custom_components.occupancy_tracker.helpers.occupancy_engine import (
    STATE_OCCUPIED,
    STATE_PENDING,
    STATE_RETAINED,
    STATE_UNKNOWN,
    STATE_VACANT,
    OccupancyEngine,
)
from custom_components.occupancy_tracker.helpers.room_profiles import ROOM_PROFILES

HOLD = ROOM_PROFILES["default"].hold_seconds
TRANSITION_HOLD = ROOM_PROFILES["transition"].hold_seconds
SLEEPING_CEILING = ROOM_PROFILES["sleeping"].retention_ceiling_seconds
LIVING_CEILING = ROOM_PROFILES["living"].retention_ceiling_seconds
DEFAULT_CEILING = ROOM_PROFILES["default"].retention_ceiling_seconds

T0 = 1_000_000.0


CONFIG = {
    "areas": {
        "corridor": {"indoors": True, "transition": True},
        "bedroom": {"indoors": True, "profile": "sleeping"},
        "ensuite": {"indoors": True},
        "living": {"indoors": True, "profile": "living"},
        "utility": {"indoors": True},
        "yard": {"indoors": False},
    },
    "adjacency": {
        "corridor": ["bedroom", "living", "utility"],
        "living": ["corridor", "utility", "yard"],
        "utility": ["corridor", "living"],
        "bedroom": ["corridor", "ensuite", "yard"],
        "ensuite": ["bedroom"],
    },
    "sensors": {
        "binary_sensor.corridor_motion": {"area": "corridor", "type": "motion"},
        "binary_sensor.bedroom_motion": {"area": "bedroom", "type": "motion"},
        "binary_sensor.ensuite_motion": {"area": "ensuite", "type": "motion"},
        "binary_sensor.living_motion": {"area": "living", "type": "motion"},
        "binary_sensor.utility_motion": {"area": "utility", "type": "motion"},
        "binary_sensor.yard_motion": {"area": "yard", "type": "camera_motion"},
        "binary_sensor.bedroom_door": {"area": ["bedroom", "yard"], "type": "magnetic"},
    },
}


@pytest.fixture
def engine() -> OccupancyEngine:
    """Return an engine whose clock never moves unless a test moves it."""
    return OccupancyEngine(CONFIG, clock=lambda: T0)


def occupy(engine: OccupancyEngine, area: str, at: float, *, others=()) -> None:
    """Turn an area's motion ON at ``at``."""
    engine.apply(at, {area, *others})


def quiet(engine: OccupancyEngine, at: float, *, active=()) -> None:
    """Turn everything except ``active`` OFF at ``at``."""
    engine.apply(at, set(active))


# ----------------------------------------------------------------------
# Topology
# ----------------------------------------------------------------------


def test_dead_end_rooms_are_not_exits(engine: OccupancyEngine) -> None:
    # An ensuite has no way out except back through the bedroom, so walking
    # into it is not leaving the bedroom.
    assert engine.exits_for("bedroom") == frozenset({"corridor"})
    assert engine.exits_for("ensuite") == frozenset({"bedroom"})


def test_outdoor_areas_are_never_exits(engine: OccupancyEngine) -> None:
    assert "yard" not in engine.exits_for("bedroom")
    assert "yard" not in engine.exits_for("living")


# ----------------------------------------------------------------------
# Activity and hold
# ----------------------------------------------------------------------


def test_motion_makes_a_room_occupied(engine: OccupancyEngine) -> None:
    occupy(engine, "bedroom", T0)
    assert engine.rooms["bedroom"].state == STATE_OCCUPIED
    assert engine.rooms["bedroom"].confirmed is True


def test_silence_starts_a_hold_with_an_explicit_deadline(
    engine: OccupancyEngine,
) -> None:
    occupy(engine, "bedroom", T0)
    quiet(engine, T0 + 5)
    room = engine.rooms["bedroom"]
    assert room.state == STATE_PENDING
    assert room.deadline == pytest.approx(T0 + 5 + HOLD)


def test_hold_survives_up_to_its_deadline_and_not_past_it(
    engine: OccupancyEngine,
) -> None:
    occupy(engine, "bedroom", T0)
    quiet(engine, T0 + 5)
    engine.tick(T0 + 5 + HOLD - 0.001)
    assert engine.rooms["bedroom"].state == STATE_PENDING
    engine.tick(T0 + 5 + HOLD)
    assert engine.rooms["bedroom"].state == STATE_RETAINED


def test_duplicate_off_reports_never_extend_a_hold(engine: OccupancyEngine) -> None:
    occupy(engine, "bedroom", T0)
    quiet(engine, T0 + 5)
    deadline = engine.rooms["bedroom"].deadline
    for offset in (10, 20, 30):
        quiet(engine, T0 + offset)
    assert engine.rooms["bedroom"].deadline == deadline


def test_own_motion_cancels_a_hold(engine: OccupancyEngine) -> None:
    occupy(engine, "bedroom", T0)
    quiet(engine, T0 + 5)
    occupy(engine, "bedroom", T0 + 30)
    room = engine.rooms["bedroom"]
    assert room.state == STATE_OCCUPIED
    assert room.deadline is None


def test_a_zero_event_period_still_retires_the_hold(engine: OccupancyEngine) -> None:
    occupy(engine, "corridor", T0)
    quiet(engine, T0 + 5)
    # No further sensor events at all, only the clock.
    engine.tick(T0 + 5 + TRANSITION_HOLD)
    assert engine.rooms["corridor"].state == STATE_VACANT


def test_a_healthy_continuously_on_input_never_expires(
    engine: OccupancyEngine,
) -> None:
    occupy(engine, "living", T0)
    engine.apply(T0 + 10 * 3600, {"living"})
    assert engine.rooms["living"].state == STATE_OCCUPIED


# ----------------------------------------------------------------------
# Exit-gated release
# ----------------------------------------------------------------------


def test_a_departure_trail_releases_the_room(engine: OccupancyEngine) -> None:
    occupy(engine, "bedroom", T0)
    # Walks out: the corridor fires while the bedroom detector is still ON.
    engine.apply(T0 + 3, {"bedroom", "corridor"})
    engine.apply(T0 + 5, {"corridor"})
    engine.apply(T0 + 10, set())
    engine.tick(T0 + 5 + HOLD)
    assert engine.rooms["bedroom"].state == STATE_VACANT
    assert engine.rooms["bedroom"].reason == "departure_trail"


def test_no_trail_retains_the_room(engine: OccupancyEngine) -> None:
    occupy(engine, "bedroom", T0)
    quiet(engine, T0 + 5)
    engine.tick(T0 + 5 + HOLD)
    room = engine.rooms["bedroom"]
    assert room.state == STATE_RETAINED
    assert room.reason == "no_exit_trail"
    assert room.deadline == pytest.approx(T0 + 5 + SLEEPING_CEILING)


def test_trail_window_boundary(engine: OccupancyEngine) -> None:
    trail_window = engine.trail_window
    for offset, expected in ((trail_window, STATE_VACANT), (trail_window + 1, STATE_RETAINED)):
        engine = OccupancyEngine(CONFIG, clock=lambda: T0)
        occupy(engine, "bedroom", T0)
        quiet(engine, T0 + 5)
        occupy(engine, "corridor", T0 + 5 + offset)
        quiet(engine, T0 + 10 + offset)
        engine.tick(T0 + 5 + HOLD + 1)
        assert engine.rooms["bedroom"].state == expected, offset


def test_walking_into_a_dead_end_is_not_a_departure(engine: OccupancyEngine) -> None:
    # A partner using the ensuite must not empty the bedroom.
    occupy(engine, "bedroom", T0)
    quiet(engine, T0 + 5)
    occupy(engine, "ensuite", T0 + 8)
    engine.tick(T0 + 5 + HOLD)
    assert engine.rooms["bedroom"].state == STATE_RETAINED


def test_a_transition_room_never_retains(engine: OccupancyEngine) -> None:
    occupy(engine, "corridor", T0)
    quiet(engine, T0 + 5)
    engine.tick(T0 + 5 + TRANSITION_HOLD)
    room = engine.rooms["corridor"]
    assert room.state == STATE_VACANT
    assert room.reason == "transition_room"


def test_a_later_exit_activation_never_clears_a_retained_room(
    engine: OccupancyEngine,
) -> None:
    occupy(engine, "bedroom", T0)
    quiet(engine, T0 + 5)
    engine.tick(T0 + 5 + HOLD)
    assert engine.rooms["bedroom"].state == STATE_RETAINED

    # Somebody else walks the corridor an hour later.
    occupy(engine, "corridor", T0 + 3600)
    quiet(engine, T0 + 3605)
    engine.tick(T0 + 3700)
    assert engine.rooms["bedroom"].state == STATE_RETAINED


def test_own_motion_ends_retention(engine: OccupancyEngine) -> None:
    occupy(engine, "bedroom", T0)
    quiet(engine, T0 + 5)
    engine.tick(T0 + 5 + HOLD)
    occupy(engine, "bedroom", T0 + 4000)
    assert engine.rooms["bedroom"].state == STATE_OCCUPIED


def test_retention_ceiling_releases_the_room(engine: OccupancyEngine) -> None:
    occupy(engine, "bedroom", T0)
    quiet(engine, T0 + 5)
    engine.tick(T0 + 5 + HOLD)
    engine.tick(T0 + 5 + SLEEPING_CEILING - 1)
    assert engine.rooms["bedroom"].state == STATE_RETAINED
    engine.tick(T0 + 5 + SLEEPING_CEILING)
    assert engine.rooms["bedroom"].state == STATE_VACANT
    assert engine.rooms["bedroom"].reason == "retention_ceiling"


@pytest.mark.parametrize(
    ("area", "ceiling"),
    [("bedroom", SLEEPING_CEILING), ("living", LIVING_CEILING), ("utility", DEFAULT_CEILING)],
)
def test_ceilings_come_from_the_room_profile(area: str, ceiling: int) -> None:
    engine = OccupancyEngine(CONFIG, clock=lambda: T0)
    occupy(engine, area, T0)
    quiet(engine, T0 + 5)
    engine.tick(T0 + 5 + HOLD)
    assert engine.rooms[area].deadline == pytest.approx(T0 + 5 + ceiling)


# ----------------------------------------------------------------------
# Qualified entry (detector spill)
# ----------------------------------------------------------------------


def test_a_spilled_entry_is_unconfirmed_and_never_retained(
    engine: OccupancyEngine,
) -> None:
    # The corridor detector sees into the bedroom doorway: the bedroom edge
    # lands half a second later, far too fast to be someone walking in.
    occupy(engine, "corridor", T0)
    engine.apply(T0 + 0.5, {"corridor", "bedroom"})
    assert engine.rooms["bedroom"].confirmed is False

    engine.apply(T0 + 5, set())
    engine.tick(T0 + 5 + HOLD)
    assert engine.rooms["bedroom"].state == STATE_VACANT
    assert engine.rooms["bedroom"].reason == "unconfirmed_entry"


def test_a_later_quiet_activation_confirms_a_spilled_entry(
    engine: OccupancyEngine,
) -> None:
    occupy(engine, "corridor", T0)
    engine.apply(T0 + 0.5, {"corridor", "bedroom"})
    engine.apply(T0 + 5, {"bedroom"})
    engine.apply(T0 + 10, set())
    # The corridor has gone quiet; this activation cannot be its spill.
    occupy(engine, "bedroom", T0 + 40)
    assert engine.rooms["bedroom"].confirmed is True

    quiet(engine, T0 + 45)
    engine.tick(T0 + 45 + HOLD)
    assert engine.rooms["bedroom"].state == STATE_RETAINED


def test_spill_window_boundary() -> None:
    for offset, confirmed in ((2.0, False), (2.5, True)):
        engine = OccupancyEngine(CONFIG, clock=lambda: T0)
        occupy(engine, "corridor", T0)
        engine.apply(T0 + offset, {"corridor", "bedroom"})
        assert engine.rooms["bedroom"].confirmed is confirmed, offset


def test_an_entry_while_the_neighbour_is_already_quiet_is_confirmed(
    engine: OccupancyEngine,
) -> None:
    occupy(engine, "corridor", T0)
    engine.apply(T0 + 1, set())
    # Only 0.5 s after the corridor went OFF, but the corridor edge itself is
    # older than the spill window.
    occupy(engine, "bedroom", T0 + 1.5)
    assert engine.rooms["bedroom"].confirmed is True


# ----------------------------------------------------------------------
# Contacts
# ----------------------------------------------------------------------


def test_a_contact_pulse_is_a_departure_trail(engine: OccupancyEngine) -> None:
    occupy(engine, "bedroom", T0)
    quiet(engine, T0 + 5)
    engine.record_contact(["bedroom", "yard"], T0 + 8)
    engine.tick(T0 + 5 + HOLD)
    assert engine.rooms["bedroom"].state == STATE_VACANT
    assert engine.rooms["bedroom"].reason == "departure_trail"


def test_a_contact_pulse_after_the_trail_window_is_not_a_departure(
    engine: OccupancyEngine,
) -> None:
    occupy(engine, "bedroom", T0)
    quiet(engine, T0 + 5)
    engine.record_contact(["bedroom", "yard"], T0 + 5 + engine.trail_window + 1)
    engine.tick(T0 + 5 + HOLD)
    assert engine.rooms["bedroom"].state == STATE_RETAINED


def test_a_contact_pulse_before_the_room_was_entered_is_ignored(
    engine: OccupancyEngine,
) -> None:
    engine.record_contact(["bedroom", "yard"], T0)
    occupy(engine, "bedroom", T0 + 10)
    quiet(engine, T0 + 15)
    engine.tick(T0 + 15 + HOLD)
    assert engine.rooms["bedroom"].state == STATE_RETAINED


# ----------------------------------------------------------------------
# Two-person counterexamples
# ----------------------------------------------------------------------


def test_one_of_two_leaving_empties_the_room_and_the_sleeper_restores_it(
    engine: OccupancyEngine,
) -> None:
    # The accepted error: two people are in the bedroom, one leaves, the other
    # lies still. The trail belongs to the leaver, so the room reads empty --
    # until the sleeper next moves.
    occupy(engine, "bedroom", T0)
    engine.apply(T0 + 3, {"bedroom", "corridor"})
    engine.apply(T0 + 5, {"corridor"})
    engine.apply(T0 + 12, set())
    engine.tick(T0 + 5 + HOLD)
    assert engine.rooms["bedroom"].state == STATE_VACANT

    occupy(engine, "bedroom", T0 + 2000)
    assert engine.rooms["bedroom"].state == STATE_OCCUPIED


def test_a_neighbour_pass_that_is_not_a_trail_keeps_both_rooms(
    engine: OccupancyEngine,
) -> None:
    # Somebody walks the corridor while somebody else is settled in the
    # bedroom, but the bedroom fell silent long before the corridor fired.
    occupy(engine, "bedroom", T0)
    quiet(engine, T0 + 5)
    engine.tick(T0 + 5 + HOLD)
    occupy(engine, "corridor", T0 + 600)
    quiet(engine, T0 + 605)
    engine.tick(T0 + 605 + TRANSITION_HOLD)
    assert engine.rooms["bedroom"].state == STATE_RETAINED
    assert engine.rooms["corridor"].state == STATE_VACANT


# ----------------------------------------------------------------------
# Outdoor areas
# ----------------------------------------------------------------------


def test_outdoor_areas_mirror_their_sensors(engine: OccupancyEngine) -> None:
    occupy(engine, "yard", T0)
    assert engine.rooms["yard"].state == STATE_OCCUPIED
    quiet(engine, T0 + 5)
    assert engine.rooms["yard"].state == STATE_VACANT


def test_outdoor_activity_never_manufactures_an_indoor_departure(
    engine: OccupancyEngine,
) -> None:
    occupy(engine, "bedroom", T0)
    quiet(engine, T0 + 5)
    occupy(engine, "yard", T0 + 8)
    engine.tick(T0 + 5 + HOLD)
    assert engine.rooms["bedroom"].state == STATE_RETAINED


# ----------------------------------------------------------------------
# Faults
# ----------------------------------------------------------------------


def test_unavailable_inputs_publish_unknown_after_the_grace(
    engine: OccupancyEngine,
) -> None:
    occupy(engine, "living", T0)
    engine.apply(T0 + 5, set(), {"living"})
    assert engine.published_state("living", T0 + 5) == STATE_PENDING
    grace = engine.unavailable_grace
    assert engine.published_state("living", T0 + 5 + grace - 1) == STATE_PENDING
    assert engine.published_state("living", T0 + 5 + grace) == STATE_UNKNOWN
    assert engine.is_known("living", T0 + 5 + grace) is False


def test_unavailable_inputs_never_read_as_vacant(engine: OccupancyEngine) -> None:
    occupy(engine, "living", T0)
    engine.apply(T0 + 5, set(), {"living"})
    engine.apply(T0 + 10 * 3600, set(), {"living"})
    assert engine.rooms["living"].state != STATE_VACANT
    assert engine.published_state("living", T0 + 10 * 3600) == STATE_UNKNOWN


def test_recovery_resolves_the_deadline_that_expired_while_blind(
    engine: OccupancyEngine,
) -> None:
    occupy(engine, "living", T0)
    engine.apply(T0 + 5, set(), {"living"})
    engine.apply(T0 + 3600, set())
    assert engine.rooms["living"].state == STATE_RETAINED
    assert engine.is_known("living", T0 + 3600) is True


def test_a_short_transport_blip_changes_nothing(engine: OccupancyEngine) -> None:
    # All indoor entities went unavailable for 8 s once in production.
    occupy(engine, "bedroom", T0)
    engine.apply(T0 + 2, set(), {"bedroom"})
    engine.apply(T0 + 10, {"bedroom"})
    assert engine.rooms["bedroom"].state == STATE_OCCUPIED
    assert engine.is_known("bedroom", T0 + 10) is True


def test_an_out_of_order_event_cannot_rewind_a_decision(
    engine: OccupancyEngine,
) -> None:
    occupy(engine, "corridor", T0)
    quiet(engine, T0 + 5)
    engine.tick(T0 + 5 + TRANSITION_HOLD)
    assert engine.rooms["corridor"].state == STATE_VACANT

    # A late duplicate of the original OFF arrives.
    engine.apply(T0 + 5, set())
    assert engine.rooms["corridor"].state == STATE_VACANT


# ----------------------------------------------------------------------
# Manual clear
# ----------------------------------------------------------------------


def test_manual_clear_empties_a_retained_room(engine: OccupancyEngine) -> None:
    occupy(engine, "bedroom", T0)
    quiet(engine, T0 + 5)
    engine.tick(T0 + 5 + HOLD)
    assert engine.force_vacant(["bedroom"], T0 + 200, "manual_button") == ["bedroom"]
    assert engine.rooms["bedroom"].state == STATE_VACANT


def test_manual_clear_refuses_a_room_with_live_motion(engine: OccupancyEngine) -> None:
    occupy(engine, "bedroom", T0)
    assert engine.force_vacant(["bedroom"], T0 + 1, "manual_button") == []
    assert engine.rooms["bedroom"].state == STATE_OCCUPIED


# ----------------------------------------------------------------------
# Restart
# ----------------------------------------------------------------------


def _reloaded(engine: OccupancyEngine, now: float, active=(), unavailable=()):
    """Persist an engine and bring a fresh one back up at ``now``."""
    stored = engine.snapshot()
    restarted = OccupancyEngine(CONFIG, clock=lambda: now)
    rejected = restarted.restore(stored, now, set(active), set(unavailable))
    return restarted, rejected


def test_restart_keeps_a_retained_room_and_its_original_ceiling(
    engine: OccupancyEngine,
) -> None:
    occupy(engine, "bedroom", T0)
    quiet(engine, T0 + 5)
    engine.tick(T0 + 5 + HOLD)

    restarted, _ = _reloaded(engine, T0 + 3600)
    room = restarted.rooms["bedroom"]
    assert room.state == STATE_RETAINED
    assert room.deadline == pytest.approx(T0 + 5 + SLEEPING_CEILING)


def test_restart_never_grants_a_fresh_hold(engine: OccupancyEngine) -> None:
    occupy(engine, "bedroom", T0)
    quiet(engine, T0 + 5)
    # The hold had 30 s left when Home Assistant went down; it comes back long
    # after the deadline and must resolve at once, not start over.
    restarted, _ = _reloaded(engine, T0 + 5 + HOLD + 600)
    assert restarted.rooms["bedroom"].state == STATE_RETAINED


def test_restart_inside_the_hold_keeps_the_original_deadline(
    engine: OccupancyEngine,
) -> None:
    occupy(engine, "bedroom", T0)
    quiet(engine, T0 + 5)
    restarted, _ = _reloaded(engine, T0 + 20)
    room = restarted.rooms["bedroom"]
    assert room.state == STATE_PENDING
    assert room.deadline == pytest.approx(T0 + 5 + HOLD)


def test_restart_past_the_ceiling_releases_the_room(engine: OccupancyEngine) -> None:
    occupy(engine, "bedroom", T0)
    quiet(engine, T0 + 5)
    engine.tick(T0 + 5 + HOLD)
    restarted, _ = _reloaded(engine, T0 + SLEEPING_CEILING + 60)
    assert restarted.rooms["bedroom"].state == STATE_VACANT


def test_restart_while_occupied_resumes_a_hold_from_the_last_evidence(
    engine: OccupancyEngine,
) -> None:
    occupy(engine, "bedroom", T0)
    restarted, _ = _reloaded(engine, T0 + 30)
    room = restarted.rooms["bedroom"]
    assert room.state == STATE_PENDING
    assert room.deadline == pytest.approx(T0 + HOLD)


def test_restart_with_live_motion_reoccupies_the_room(
    engine: OccupancyEngine,
) -> None:
    occupy(engine, "bedroom", T0)
    quiet(engine, T0 + 5)
    engine.tick(T0 + 5 + HOLD)
    restarted, _ = _reloaded(engine, T0 + 3600, active={"bedroom"})
    assert restarted.rooms["bedroom"].state == STATE_OCCUPIED


def test_restart_with_unavailable_inputs_publishes_unknown() -> None:
    engine = OccupancyEngine(CONFIG, clock=lambda: T0)
    occupy(engine, "bedroom", T0)
    quiet(engine, T0 + 5)
    now = T0 + 3600
    restarted, _ = _reloaded(engine, now, unavailable={"bedroom"})
    assert restarted.published_state("bedroom", now + 3600) == STATE_UNKNOWN


@pytest.mark.parametrize(
    "stored",
    [
        {"bedroom": "not-a-dict"},
        {"bedroom": {"state": "possible"}},
        {"bedroom": {"state": None}},
        {"nonexistent_room": {"state": STATE_RETAINED}},
        {"yard": {"state": STATE_RETAINED}},
    ],
)
def test_corrupt_stored_rooms_are_rejected_not_trusted(stored: dict) -> None:
    engine = OccupancyEngine(CONFIG, clock=lambda: T0)
    rejected = engine.restore(stored, T0, set())
    assert rejected == list(stored)
    assert all(room.state == STATE_VACANT for room in engine.rooms.values())


def test_corrupt_timestamps_do_not_poison_a_restored_room() -> None:
    engine = OccupancyEngine(CONFIG, clock=lambda: T0)
    engine.restore(
        {
            "bedroom": {
                "state": STATE_RETAINED,
                "state_since": "yesterday",
                "last_own_off": float("inf"),
                "deadline": -5,
                "confirmed": True,
            }
        },
        T0,
        set(),
    )
    room = engine.rooms["bedroom"]
    assert room.state_since == T0
    assert room.last_own_off is None
    # With no usable deadline the room falls back to a hold anchored on the
    # evidence it has, and resolves from there.
    assert room.deadline is not None


def test_an_empty_store_leaves_every_room_vacant() -> None:
    engine = OccupancyEngine(CONFIG, clock=lambda: T0)
    assert engine.restore({}, T0, set()) == []
    assert all(room.state == STATE_VACANT for room in engine.rooms.values())


def test_snapshot_covers_indoor_rooms_only(engine: OccupancyEngine) -> None:
    occupy(engine, "bedroom", T0)
    occupy(engine, "yard", T0 + 1)
    stored = engine.snapshot()
    assert "yard" not in stored
    assert set(stored) == {"bedroom", "corridor", "ensuite", "living", "utility"}
