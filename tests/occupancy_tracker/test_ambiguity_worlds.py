"""Paired-worlds evaluation of the exit-gated occupancy engine.

Some occupancy questions cannot be answered from the sensors this house has.
`ambiguity_cases.json` in the data bundle pairs one observation sequence with
two physical worlds that produce exactly that sequence, so no algorithm can
tell those worlds apart. This file replays each sequence through the engine on
a virtual clock, scores the published track against both worlds, and asserts
only what the evidence supports.

The metric is the agreement fraction: the share of one-second samples over the
case duration where the published answer, occupied or vacant, equals the
world's true answer.

Every case names a reference world, the world the documented design commits
to. The engine has to agree with the reference world for at least `BAR` of the
samples in every case. A constant-ON and a constant-OFF implementation are
scored beside it. Each of them fails the bar, and they fail on disjoint cases,
so a bar that any trivial implementation clears cannot hide here.

Where the worlds fork on something the sensors cannot resolve, this file does
not assert that the engine picks the true world. For
`last_person_or_one_of_two_leaves` and `sleep_or_empty` it asserts the
accepted error that section 1 of `output/occupancy-handoff-2026-09-06.md`
states: two people enter, one leaves, one sleeps, so the room is treated as
empty until the sleeper next triggers the detector, and then it is occupied
again immediately. Both directions of that error are bounded, by the hold on
one side and by the profile retention ceiling on the other.

The engine is built from the production `config.yaml`, so the topology,
profiles and exits are the real ones.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Iterable, Sequence

import pytest
import yaml

from custom_components.occupancy_tracker.helpers.occupancy_engine import (
    OCCUPIED_STATES,
    STATE_OCCUPIED,
    STATE_RETAINED,
    STATE_VACANT,
    OccupancyEngine,
)
from custom_components.occupancy_tracker.helpers.room_profiles import ROOM_PROFILES

T0 = 1_000_000.0

#: The room every ambiguity case observes and scores.
SCORED_ROOM = "main_bedroom"

#: Minimum agreement with a case's reference world.
BAR = 0.95

SLEEPING_HOLD = ROOM_PROFILES["sleeping"].hold_seconds
SLEEPING_CEILING = ROOM_PROFILES["sleeping"].retention_ceiling_seconds

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"
PRODUCTION_CONFIG = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))

#: Published copy of the ambiguity cases, kept beside the analysis bundle.
PUBLISHED_CASES_PATH = (
    Path(__file__).resolve().parents[3]
    / "output"
    / "occupancy-data-analysis"
    / "ambiguity_cases.json"
)

# The five cases from `ambiguity_cases.json`, embedded so the suite runs
# without the analysis bundle. `test_embedded_cases_match_the_published_data`
# keeps the copy honest whenever the bundle is present. An observation is
# `[offset_seconds, area, "on" | "off"]`; a world lists the `[start, end]`
# intervals during which the room really is occupied in that world.
AMBIGUITY_CASES = [
    {
        "id": "last_person_or_one_of_two_leaves",
        "observations": [
            [0, "main_bedroom", "on"],
            [5, "main_bedroom", "off"],
            [15, "corridor_2", "on"],
            [20, "corridor_2", "off"],
        ],
        "duration": 3600,
        "worlds": {
            "last_person_leaves": [[0, 15]],
            "one_leaves_other_remains_still": [[0, 3600]],
        },
    },
    {
        "id": "sleep_or_empty",
        "observations": [
            [0, "main_bedroom", "on"],
            [5, "main_bedroom", "off"],
        ],
        "duration": 28800,
        "worlds": {
            "went_to_sleep": [[0, 28800]],
            "left_without_further_detection": [[0, 5]],
        },
    },
    {
        "id": "isolated_false_positive_or_real_arrival",
        "observations": [
            [0, "main_bedroom", "on"],
            [5, "main_bedroom", "off"],
        ],
        "duration": 600,
        "worlds": {
            "empty_false_trigger": [],
            "quiet_real_arrival": [[0, 600]],
        },
    },
    {
        "id": "missed_arrival_or_empty",
        "observations": [],
        "duration": 600,
        "worlds": {
            "empty": [],
            "arrival_on_lost": [[0, 600]],
        },
    },
    {
        "id": "missed_off_or_continuous_positive",
        "observations": [[0, "main_bedroom", "on"]],
        "duration": 600,
        "worlds": {
            "left_off_lost": [[0, 5]],
            "continuous_presence": [[0, 600]],
        },
    },
]

CASES = {case["id"]: case for case in AMBIGUITY_CASES}
CASE_IDS = [case["id"] for case in AMBIGUITY_CASES]

#: The world each case's documented design commits to.
#:
#: For `missed_arrival_or_empty` and `missed_off_or_continuous_positive` the
#: reference world is the one the rules in section 6 of the handoff require:
#: no evidence never manufactures occupancy, and a healthy input that stays ON
#: never expires. For the other three the reference world is the one the rules
#: happen to land on, and the counterpart world is a documented accepted error
#: rather than a defect.
REFERENCE_WORLDS = {
    "last_person_or_one_of_two_leaves": "last_person_leaves",
    "sleep_or_empty": "went_to_sleep",
    "isolated_false_positive_or_real_arrival": "quiet_real_arrival",
    "missed_arrival_or_empty": "empty",
    "missed_off_or_continuous_positive": "continuous_presence",
}

#: One `[offset_seconds, area, "on" | "off"]` entry of a case.
Observation = Sequence
#: One occupied or vacant answer per sample offset.
Track = list[bool]


# ----------------------------------------------------------------------
# Replay and scoring
# ----------------------------------------------------------------------


def samples_for(case: dict, horizon: int | None = None) -> list[int]:
    """Return the one-second sample offsets scored for a case."""
    return list(range(0, case["duration"] if horizon is None else horizon))


def world_track(case: dict, world: str, samples: Iterable[int]) -> Track:
    """Return the true occupied track of one world at each sample."""
    intervals = case["worlds"][world]
    return [
        any(start <= sample < end for start, end in intervals) for sample in samples
    ]


def engine_track(
    case: dict,
    samples: Sequence[int],
    extra: Iterable[Observation] = (),
) -> Track:
    """Replay a case through the engine and return its published track.

    The clock only moves because this function moves it, so vacancy has to
    happen with no further sensor events. `extra` appends observations beyond
    the ones the case supplies, which is how the self-healing checks add the
    sleeper's next detector trip.
    """
    engine = OccupancyEngine(PRODUCTION_CONFIG, clock=lambda: T0)
    observations = sorted([*case["observations"], *extra], key=lambda entry: entry[0])
    active: set[str] = set()
    pending = 0
    track: Track = []
    for sample in samples:
        while pending < len(observations) and observations[pending][0] <= sample:
            offset, area, state = observations[pending]
            if state == "on":
                active.add(area)
            else:
                active.discard(area)
            engine.apply(T0 + offset, set(active))
            pending += 1
        engine.tick(T0 + sample)
        track.append(
            engine.published_state(SCORED_ROOM, T0 + sample) in OCCUPIED_STATES
        )
    return track


def constant_on_track(
    case: dict, samples: Sequence[int], extra: Iterable[Observation] = ()
) -> Track:
    """Return the track of an implementation that always reports occupied."""
    return [True] * len(samples)


def constant_off_track(
    case: dict, samples: Sequence[int], extra: Iterable[Observation] = ()
) -> Track:
    """Return the track of an implementation that always reports vacant."""
    return [False] * len(samples)


IMPLEMENTATIONS: dict[str, Callable[..., Track]] = {
    "engine": engine_track,
    "constant_on": constant_on_track,
    "constant_off": constant_off_track,
}


def agreement(predicted: Track, truth: Track) -> float:
    """Return the share of samples where the two tracks agree."""
    assert len(predicted) == len(truth)
    matches = sum(1 for left, right in zip(predicted, truth) if left == right)
    return matches / len(truth)


def score(implementation: str, case_id: str, world: str) -> float:
    """Return one implementation's agreement with one world of one case."""
    case = CASES[case_id]
    samples = samples_for(case)
    predicted = IMPLEMENTATIONS[implementation](case, samples)
    return agreement(predicted, world_track(case, world, samples))


def reference_score(implementation: str, case_id: str) -> float:
    """Return the agreement with the world the case's design commits to."""
    return score(implementation, case_id, REFERENCE_WORLDS[case_id])


def cases_below_the_bar(implementation: str) -> list[str]:
    """Return the cases where an implementation misses its reference world."""
    return [
        case_id
        for case_id in CASE_IDS
        if reference_score(implementation, case_id) < BAR
    ]


def occupied_runs(track: Track, samples: Sequence[int]) -> list[tuple[int, int]]:
    """Return the `[start, end)` sample offsets of each occupied run."""
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for sample, occupied in zip(samples, track):
        if occupied and start is None:
            start = sample
        elif not occupied and start is not None:
            runs.append((start, sample))
            start = None
    if start is not None:
        runs.append((start, samples[-1] + 1))
    return runs


# ----------------------------------------------------------------------
# The data and the fixture the cases run against
# ----------------------------------------------------------------------


def test_embedded_cases_match_the_published_data() -> None:
    """Keep the embedded copy identical to the published ambiguity cases."""
    if not PUBLISHED_CASES_PATH.exists():
        pytest.skip(f"analysis bundle not present at {PUBLISHED_CASES_PATH}")
    published = json.loads(PUBLISHED_CASES_PATH.read_text(encoding="utf-8"))
    assert published == AMBIGUITY_CASES


def test_every_case_names_a_reference_world() -> None:
    """Every case has to say which world the engine is held to."""
    assert set(REFERENCE_WORLDS) == set(CASE_IDS)
    for case_id, world in REFERENCE_WORLDS.items():
        assert world in CASES[case_id]["worlds"]


def test_cases_only_reference_production_areas() -> None:
    """The cases have to name areas the real house config defines."""
    areas = set(PRODUCTION_CONFIG["areas"])
    assert SCORED_ROOM in areas
    for case in AMBIGUITY_CASES:
        for _offset, area, _state in case["observations"]:
            assert area in areas, case["id"]


def test_the_scored_rooms_exits_come_from_the_production_topology() -> None:
    """The main bedroom leaves only through corridor 2 in this house.

    The ensuite and the walk-in wardrobe are dead ends inside the suite, so
    walking into either is not a departure. The cases turn on that fact, so
    assert it rather than assume it.
    """
    engine = OccupancyEngine(PRODUCTION_CONFIG, clock=lambda: T0)
    assert engine.exits_for(SCORED_ROOM) == frozenset({"corridor_2"})
    assert engine.profile_for(SCORED_ROOM) is ROOM_PROFILES["sleeping"]


# ----------------------------------------------------------------------
# The bar, and the trivial implementations that must fail it
# ----------------------------------------------------------------------


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_the_engine_tracks_the_reference_world_of_every_case(case_id: str) -> None:
    """The engine has to follow the world its own documented rules commit to."""
    assert reference_score("engine", case_id) >= BAR


def test_a_constant_on_implementation_fails_the_bar() -> None:
    """An implementation that always reports occupied cannot clear the bar.

    It cannot release the main bedroom after the departure trail, and it
    invents occupancy for a room that produced no events at all.
    """
    assert cases_below_the_bar("constant_on") == [
        "last_person_or_one_of_two_leaves",
        "missed_arrival_or_empty",
    ]


def test_a_constant_off_implementation_fails_the_bar() -> None:
    """An implementation that always reports vacant cannot clear the bar.

    It reports an empty room while the detector is still ON, and it drops the
    hold and the retention that the exit gating is built on.
    """
    assert cases_below_the_bar("constant_off") == [
        "sleep_or_empty",
        "isolated_false_positive_or_real_arrival",
        "missed_off_or_continuous_positive",
    ]


def test_the_engine_clears_a_bar_no_constant_answer_clears() -> None:
    """Each case rules out one of the two constant implementations.

    The two constants fail on disjoint cases that together cover the suite, so
    the bar cannot be met by answering the same thing every time.
    """
    assert cases_below_the_bar("engine") == []
    constant_on = set(cases_below_the_bar("constant_on"))
    constant_off = set(cases_below_the_bar("constant_off"))
    assert constant_on & constant_off == set()
    assert constant_on | constant_off == set(CASE_IDS)


@pytest.mark.parametrize("case_id", CASE_IDS)
@pytest.mark.parametrize("implementation", sorted(IMPLEMENTATIONS))
def test_no_implementation_clears_the_bar_in_both_worlds(
    implementation: str, case_id: str
) -> None:
    """No published track can be right in both worlds of a case.

    The paired worlds produce identical sensor input and disagree over almost
    the whole window, so every implementation, the engine included, is wrong in
    one of them. That is the non-identifiability the cases exist to show, and
    it is why the cases below assert bounded behaviour instead of world
    selection.
    """
    scores = [
        score(implementation, case_id, world) for world in CASES[case_id]["worlds"]
    ]
    assert min(scores) < BAR


# ----------------------------------------------------------------------
# Undecidable cases: bounded, documented commitments
# ----------------------------------------------------------------------


def test_one_of_two_leaving_commits_to_the_departure() -> None:
    """Ambiguous: one person left, or one of two left and the other lay still.

    The main bedroom fires, falls silent, and corridor 2 fires ten seconds
    later. Whether the corridor pass was the last person leaving or one of two
    people leaving, the sensors produce exactly these events.

    The engine is held to the commitment, not to the truth: the trail releases
    the room one hold after its own last OFF, which is right in the
    `last_person_leaves` world and is the accepted error of section 1 in the
    other.
    """
    case = CASES["last_person_or_one_of_two_leaves"]
    samples = samples_for(case)
    track = engine_track(case, samples)

    release = 5 + SLEEPING_HOLD
    assert occupied_runs(track, samples) == [(0, release)]
    assert reference_score("engine", case["id"]) >= BAR

    # The other world costs the rest of the hour as false vacancy. That is the
    # accepted error, so record its size rather than assert it away.
    truth = world_track(case, "one_leaves_other_remains_still", samples)
    false_vacant = sum(1 for a, b in zip(track, truth) if b and not a)
    assert false_vacant == case["duration"] - release


def test_one_of_two_leaving_self_heals_on_the_next_own_motion() -> None:
    """The accepted error ends the moment the sleeper trips the detector.

    Section 1: the room is treated as empty until the sleeper next triggers
    the PIR, then re-occupied immediately. The false vacancy is therefore
    bounded by the gap between the release and that next trip, and nothing
    else has to happen for the room to recover.
    """
    case = CASES["last_person_or_one_of_two_leaves"]
    samples = samples_for(case)
    stirs_at = 2000
    track = engine_track(
        case,
        samples,
        extra=[[stirs_at, SCORED_ROOM, "on"], [stirs_at + 5, SCORED_ROOM, "off"]],
    )

    release = 5 + SLEEPING_HOLD
    # Occupied again in the same second the detector fires, and it stays
    # occupied for the rest of the hour.
    assert occupied_runs(track, samples) == [
        (0, release),
        (stirs_at, case["duration"]),
    ]

    truth = world_track(case, "one_leaves_other_remains_still", samples)
    false_vacant = sum(1 for a, b in zip(track, truth) if b and not a)
    assert false_vacant == stirs_at - release


def test_sleep_or_empty_commits_to_the_sleeper() -> None:
    """Ambiguous: someone went to sleep, or someone left unseen.

    The main bedroom fires once and then stays silent for eight hours. A
    sleeper and an empty room whose exit went unrecorded give the same events.

    The engine is held to the documented commitment: no exit fired after the
    room's own last activation, so nobody could have left, and the room holds.
    That tracks `went_to_sleep` exactly and is wrong all night in
    `left_without_further_detection`.
    """
    case = CASES["sleep_or_empty"]
    samples = samples_for(case)
    track = engine_track(case, samples)

    assert occupied_runs(track, samples) == [(0, case["duration"])]
    assert reference_score("engine", case["id"]) == pytest.approx(1.0)
    assert score("engine", case["id"], "left_without_further_detection") < 1 - BAR


def test_sleep_or_empty_error_is_bounded_by_the_retention_ceiling() -> None:
    """Holding an empty room costs at most one hold plus one ceiling.

    The ceiling is the policy bound on a missed exit. Replay past the eight
    hours the case covers and the sleeping ceiling releases the room, so the
    false occupancy in the `left_without_further_detection` world is finite
    and known in advance.
    """
    case = CASES["sleep_or_empty"]
    horizon = 5 + SLEEPING_CEILING + 120
    samples = samples_for(case, horizon)
    track = engine_track(case, samples)

    assert occupied_runs(track, samples) == [(0, 5 + SLEEPING_CEILING)]

    engine = OccupancyEngine(PRODUCTION_CONFIG, clock=lambda: T0)
    engine.apply(T0, {SCORED_ROOM})
    engine.apply(T0 + 5, set())
    engine.tick(T0 + 5 + SLEEPING_HOLD)
    assert engine.rooms[SCORED_ROOM].state == STATE_RETAINED
    engine.tick(T0 + 5 + SLEEPING_CEILING)
    assert engine.rooms[SCORED_ROOM].state == STATE_VACANT
    assert engine.rooms[SCORED_ROOM].reason == "retention_ceiling"

    # Self-healing runs in this direction too: own motion after the ceiling
    # puts the room straight back to occupied.
    engine.apply(T0 + 5 + SLEEPING_CEILING + 60, {SCORED_ROOM})
    assert engine.rooms[SCORED_ROOM].state == STATE_OCCUPIED


def test_an_isolated_pulse_is_trusted_as_an_arrival() -> None:
    """Ambiguous: a real arrival that then kept still, or a false trigger.

    A single five-second pulse is compatible with both. Section 6 rule 1 makes
    the commitment: trusted motion means the room is occupied. The engine
    tracks `quiet_real_arrival` exactly, and the cost in
    `empty_false_trigger` is bounded by the same hold plus ceiling as a
    missed exit.
    """
    case = CASES["isolated_false_positive_or_real_arrival"]
    samples = samples_for(case)
    track = engine_track(case, samples)

    assert occupied_runs(track, samples) == [(0, case["duration"])]
    assert reference_score("engine", case["id"]) == pytest.approx(1.0)

    bounded = samples_for(case, 5 + SLEEPING_CEILING + 120)
    assert occupied_runs(engine_track(case, bounded), bounded) == [
        (0, 5 + SLEEPING_CEILING)
    ]


# ----------------------------------------------------------------------
# Cases the engine has to get right
# ----------------------------------------------------------------------


def test_missed_arrival_never_manufactures_occupancy() -> None:
    """Ambiguous: an empty room, or an arrival whose ON event was lost.

    Nothing at all is observed for ten minutes. The engine has to report
    vacant, because no rule in the design creates occupancy without evidence.
    A constant-ON implementation fails exactly here.
    """
    case = CASES["missed_arrival_or_empty"]
    samples = samples_for(case)
    track = engine_track(case, samples)

    assert occupied_runs(track, samples) == []
    assert reference_score("engine", case["id"]) == pytest.approx(1.0)
    assert reference_score("constant_on", case["id"]) == pytest.approx(0.0)

    engine = OccupancyEngine(PRODUCTION_CONFIG, clock=lambda: T0)
    engine.tick(T0 + case["duration"])
    assert engine.published_state(SCORED_ROOM, T0 + case["duration"]) == STATE_VACANT


def test_a_sustained_on_input_stays_occupied() -> None:
    """Ambiguous: continuous presence, or a departure whose OFF event was lost.

    The main bedroom detector goes ON and never reports OFF. The engine has to
    keep the room occupied: section 6 rule 6 forbids expiring a healthy input
    that is still ON to satisfy a ceiling. A constant-OFF implementation fails
    exactly here.
    """
    case = CASES["missed_off_or_continuous_positive"]
    samples = samples_for(case)
    track = engine_track(case, samples)

    assert occupied_runs(track, samples) == [(0, case["duration"])]
    assert reference_score("engine", case["id"]) == pytest.approx(1.0)
    assert reference_score("constant_off", case["id"]) == pytest.approx(0.0)

    # Well past the sleeping ceiling, the live input still wins.
    long_run = samples_for(case, SLEEPING_CEILING + 600)
    assert occupied_runs(engine_track(case, long_run), long_run) == [
        (0, SLEEPING_CEILING + 600)
    ]
    engine = OccupancyEngine(PRODUCTION_CONFIG, clock=lambda: T0)
    engine.apply(T0, {SCORED_ROOM})
    engine.apply(T0 + SLEEPING_CEILING + 600, {SCORED_ROOM})
    assert engine.rooms[SCORED_ROOM].state == STATE_OCCUPIED


# ----------------------------------------------------------------------
# The full scoreboard
# ----------------------------------------------------------------------


def test_the_scoreboard_is_stable() -> None:
    """Pin every score, so a change in behaviour has to be read and accepted.

    Values are agreement fractions rounded to four places, keyed by case and
    world, with the reference world marked in `REFERENCE_WORLDS`.
    """
    scoreboard = {
        case_id: {
            world: {
                name: round(score(name, case_id, world), 4)
                for name in sorted(IMPLEMENTATIONS)
            }
            for world in CASES[case_id]["worlds"]
        }
        for case_id in CASE_IDS
    }
    assert scoreboard == {
        "last_person_or_one_of_two_leaves": {
            "last_person_leaves": {
                "constant_off": 0.9958,
                "constant_on": 0.0042,
                "engine": 0.9778,
            },
            "one_leaves_other_remains_still": {
                "constant_off": 0.0,
                "constant_on": 1.0,
                "engine": 0.0264,
            },
        },
        "sleep_or_empty": {
            "went_to_sleep": {
                "constant_off": 0.0,
                "constant_on": 1.0,
                "engine": 1.0,
            },
            "left_without_further_detection": {
                "constant_off": 0.9998,
                "constant_on": 0.0002,
                "engine": 0.0002,
            },
        },
        "isolated_false_positive_or_real_arrival": {
            "empty_false_trigger": {
                "constant_off": 1.0,
                "constant_on": 0.0,
                "engine": 0.0,
            },
            "quiet_real_arrival": {
                "constant_off": 0.0,
                "constant_on": 1.0,
                "engine": 1.0,
            },
        },
        "missed_arrival_or_empty": {
            "empty": {"constant_off": 1.0, "constant_on": 0.0, "engine": 1.0},
            "arrival_on_lost": {
                "constant_off": 0.0,
                "constant_on": 1.0,
                "engine": 0.0,
            },
        },
        "missed_off_or_continuous_positive": {
            "left_off_lost": {
                "constant_off": 0.9917,
                "constant_on": 0.0083,
                "engine": 0.0083,
            },
            "continuous_presence": {
                "constant_off": 0.0,
                "constant_on": 1.0,
                "engine": 1.0,
            },
        },
    }
