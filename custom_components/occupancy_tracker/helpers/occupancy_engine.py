"""Exit-gated per-room occupancy state machine.

The engine owns every occupancy decision. It is a pure function of the events
it is given plus an injected clock, so the same sequence always produces the
same states and the whole machine is testable without Home Assistant.

A room moves VACANT -> OCCUPIED while its own motion inputs are active. When
they fall silent the room enters PENDING with an explicit deadline. At that
deadline the room is released only if a departure trail was observed -- an
activation of one of its exits, or a pulse on one of its own door/window
contacts, timed so that it could plausibly be the person leaving. With no
trail the room becomes RETAINED until its own motion returns or a policy
ceiling expires. A room whose motion inputs are all unavailable publishes
unknown rather than a stale answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Callable, Dict, Iterable, Mapping

from .constants import (
    SPILL_WINDOW_SECONDS,
    TRAIL_WINDOW_SECONDS,
    UNAVAILABLE_GRACE_SECONDS,
)
from .room_profiles import RoomProfile, resolve_room_profile
from .types import OccupancyTrackerConfig

STATE_VACANT = "vacant"
STATE_OCCUPIED = "occupied"
STATE_PENDING = "pending"
STATE_RETAINED = "retained"
STATE_UNKNOWN = "unknown"

#: States in which the published binary sensor is ON.
OCCUPIED_STATES = frozenset({STATE_OCCUPIED, STATE_PENDING, STATE_RETAINED})


@dataclass
class RoomRuntime:
    """Durable decision state for one room."""

    state: str = STATE_VACANT
    state_since: float = 0.0
    last_own_on: float | None = None
    last_own_off: float | None = None
    confirmed: bool = False
    deadline: float | None = None
    reason: str = "init"
    # Earliest exit activation seen since this room's own last activation.
    # Storing the earliest one keeps a later, out-of-window activation from
    # hiding a trail that did happen.
    first_exit_edge: float | None = None
    unavailable_since: float | None = None
    # A spill-timed activation of a room that is already held says nothing
    # about whoever is inside, so it is ignored until its own OFF edge. Not
    # persisted: after a restart the live sensor state decides afresh.
    spill_ignored: bool = False

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON-serialisable form used for persistence."""
        return {
            "state": self.state,
            "state_since": self.state_since,
            "last_own_on": self.last_own_on,
            "last_own_off": self.last_own_off,
            "confirmed": self.confirmed,
            "deadline": self.deadline,
            "reason": self.reason,
            "first_exit_edge": self.first_exit_edge,
        }


def _finite(value: Any, *, limit: float) -> float | None:
    """Return a usable wall-clock number, or None for anything suspect."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return None
    if number <= 0 or number > limit:
        return None
    return number


@dataclass
class _AreaTopology:
    """Static per-area facts derived from configuration."""

    profile: RoomProfile
    is_indoors: bool
    exits: frozenset[str] = frozenset()
    has_motion_inputs: bool = False
    rooms_exited_by: frozenset[str] = field(default_factory=frozenset)


class OccupancyEngine:
    """Decide occupancy for every configured area."""

    def __init__(
        self,
        config: OccupancyTrackerConfig,
        *,
        clock: Callable[[], float] = time.time,
        spill_window: float = SPILL_WINDOW_SECONDS,
        trail_window: float = TRAIL_WINDOW_SECONDS,
        unavailable_grace: float = UNAVAILABLE_GRACE_SECONDS,
    ) -> None:
        self.clock = clock
        self.spill_window = spill_window
        self.trail_window = trail_window
        self.unavailable_grace = unavailable_grace
        self.topology = self._build_topology(config)
        self.rooms: Dict[str, RoomRuntime] = {
            area_id: RoomRuntime() for area_id in self.topology
        }
        self._active: set[str] = set()
        self._unavailable: set[str] = set()
        self._last_on_edge: Dict[str, float] = {}
        self._last_timestamp: float = 0.0

    # ------------------------------------------------------------------
    # Topology
    # ------------------------------------------------------------------

    @staticmethod
    def _symmetric_adjacency(
        config: OccupancyTrackerConfig,
    ) -> Dict[str, set[str]]:
        """Return adjacency as a symmetric map, as the YAML is one-sided."""
        adjacency: Dict[str, set[str]] = {}
        raw = config.get("adjacency", {}) or {}
        for area_id, neighbors in raw.items():
            adjacency.setdefault(area_id, set())
            for neighbor in neighbors:
                adjacency[area_id].add(neighbor)
                adjacency.setdefault(neighbor, set()).add(area_id)
        return adjacency

    def _build_topology(
        self, config: OccupancyTrackerConfig
    ) -> Dict[str, _AreaTopology]:
        """Derive per-area profiles, exits and contact ownership."""
        areas = config.get("areas", {}) or {}
        adjacency = self._symmetric_adjacency(config)
        indoors = {
            area_id: bool(area_config.get("indoors", True))
            for area_id, area_config in areas.items()
        }

        def indoor_neighbors(area_id: str) -> set[str]:
            return {
                neighbor
                for neighbor in adjacency.get(area_id, set())
                if indoors.get(neighbor, False)
            }

        topology: Dict[str, _AreaTopology] = {}
        for area_id, area_config in areas.items():
            candidates = indoor_neighbors(area_id)
            # A neighbour whose only indoor connection is this room is a dead
            # end inside it -- an ensuite or a walk-in wardrobe. Walking into
            # one is not leaving, so it is never a departure trail.
            exits = {
                neighbor
                for neighbor in candidates
                if indoor_neighbors(neighbor) - {area_id}
            }
            topology[area_id] = _AreaTopology(
                profile=resolve_room_profile(area_config),
                is_indoors=indoors.get(area_id, True),
                exits=frozenset(exits),
            )

        for area_id, area in topology.items():
            area.rooms_exited_by = frozenset(
                other_id
                for other_id, other in topology.items()
                if area_id in other.exits
            )

        motion_areas: set[str] = set()
        for sensor_config in (config.get("sensors", {}) or {}).values():
            if sensor_config.get("type", "motion") not in {
                "motion",
                "camera_motion",
                "camera_person",
            }:
                continue
            raw_area = sensor_config.get("area")
            sensor_areas = [raw_area] if isinstance(raw_area, str) else (raw_area or [])
            motion_areas.update(
                area_id for area_id in sensor_areas if isinstance(area_id, str)
            )
        for area_id, area in topology.items():
            area.has_motion_inputs = area_id in motion_areas

        return topology

    def exits_for(self, area_id: str) -> frozenset[str]:
        """Return the areas whose activation can release ``area_id``."""
        area = self.topology.get(area_id)
        return area.exits if area else frozenset()

    def profile_for(self, area_id: str) -> RoomProfile | None:
        """Return the resolved profile for an area."""
        area = self.topology.get(area_id)
        return area.profile if area else None

    # ------------------------------------------------------------------
    # Public state
    # ------------------------------------------------------------------

    def published_state(self, area_id: str, now: float | None = None) -> str:
        """Return the state to publish, including the unknown overlay."""
        room = self.rooms.get(area_id)
        if room is None:
            return STATE_VACANT
        if self._is_unknown(room, self.clock() if now is None else now):
            return STATE_UNKNOWN
        return room.state

    def is_occupied(self, area_id: str) -> bool:
        """Return whether the area currently counts as occupied."""
        room = self.rooms.get(area_id)
        return bool(room and room.state in OCCUPIED_STATES)

    def is_known(self, area_id: str, now: float | None = None) -> bool:
        """Return whether the area's state rests on usable evidence."""
        room = self.rooms.get(area_id)
        if room is None:
            return True
        return not self._is_unknown(room, self.clock() if now is None else now)

    def _is_unknown(self, room: RoomRuntime, now: float) -> bool:
        if room.unavailable_since is None:
            return False
        return (now - room.unavailable_since) >= self.unavailable_grace

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def record_contact(self, area_ids: Iterable[str], timestamp: float) -> None:
        """Record a door/window pulse as possible departure evidence.

        The contacts are all on the house envelope, so a pulse on a room's own
        contact is a boundary event on a way out of that room. It is treated
        exactly like an exit activation: it only matters if it lands in the
        trail window after the room's own activity stopped.
        """
        timestamp = self._monotonic(timestamp)
        for area_id in area_ids:
            area = self.topology.get(area_id)
            if area is None or not area.is_indoors:
                continue
            self._note_exit_edge(area_id, timestamp)

    def apply(
        self,
        timestamp: float,
        active_areas: Iterable[str],
        unavailable_areas: Iterable[str] = (),
    ) -> dict[str, tuple[str, str]]:
        """Advance every room to ``timestamp`` given current sensor evidence.

        ``active_areas`` is the set of areas with at least one trusted, active
        motion input; ``unavailable_areas`` the set whose motion inputs are all
        unavailable. Returns the rooms whose state changed, mapped to
        ``(previous, current)``.
        """
        timestamp = self._monotonic(timestamp)
        active = {area_id for area_id in active_areas if area_id in self.topology}
        unavailable = {
            area_id for area_id in unavailable_areas if area_id in self.topology
        }

        previous_active = self._active
        previous_on_edges = dict(self._last_on_edge)
        turned_on = active - previous_active
        turned_off = previous_active - active
        # A sensor that went unavailable while ON and comes back still ON did
        # not produce a new edge: the room resumes rather than re-enters.
        resuming = {
            area_id for area_id in turned_on if self._is_resuming(self.rooms[area_id])
        }
        edges = turned_on - resuming

        before = {area_id: room.state for area_id, room in self.rooms.items()}

        for area_id in sorted(edges):
            self._last_on_edge[area_id] = timestamp
        self._active = active
        self._unavailable = unavailable

        # Exit activations are recorded before rooms are advanced so a trail
        # observed in this same event is visible when a deadline fires.
        for area_id in sorted(edges):
            for room_id in self.topology[area_id].rooms_exited_by:
                self._note_exit_edge(room_id, timestamp)

        for area_id in sorted(self.topology):
            self._advance_room(
                area_id,
                timestamp,
                active=area_id in active,
                turned_on=area_id in turned_on,
                turned_off=area_id in turned_off,
                resuming=area_id in resuming,
                unavailable=area_id in unavailable,
                previous_active=previous_active,
                previous_on_edges=previous_on_edges,
            )

        return {
            area_id: (before[area_id], room.state)
            for area_id, room in self.rooms.items()
            if before[area_id] != room.state
        }

    def tick(self, timestamp: float | None = None) -> dict[str, tuple[str, str]]:
        """Advance deadlines with no change in sensor evidence."""
        now = self.clock() if timestamp is None else timestamp
        return self.apply(now, self._active, self._unavailable)

    def force_vacant(
        self, area_ids: Iterable[str], timestamp: float, reason: str
    ) -> list[str]:
        """Clear rooms on an explicit human assertion that they are empty."""
        timestamp = self._monotonic(timestamp)
        cleared: list[str] = []
        for area_id in sorted(set(area_ids)):
            room = self.rooms.get(area_id)
            area = self.topology.get(area_id)
            if room is None or area is None or not area.is_indoors:
                continue
            if area_id in self._active:
                # Live positive evidence outranks a stale manual assertion.
                continue
            if room.state == STATE_VACANT and room.unavailable_since is None:
                continue
            room.unavailable_since = None
            self._set_state(room, STATE_VACANT, timestamp, reason)
            cleared.append(area_id)
        return cleared

    # ------------------------------------------------------------------
    # Per-room transitions
    # ------------------------------------------------------------------

    def _advance_room(
        self,
        area_id: str,
        timestamp: float,
        *,
        active: bool,
        turned_on: bool,
        turned_off: bool,
        resuming: bool,
        unavailable: bool,
        previous_active: set[str],
        previous_on_edges: Mapping[str, float],
    ) -> None:
        area = self.topology[area_id]
        room = self.rooms[area_id]

        if unavailable and not active:
            if room.unavailable_since is None:
                room.unavailable_since = timestamp
        else:
            room.unavailable_since = None

        if not area.is_indoors:
            # Outdoor areas mirror their sensors. Outdoor activity must never
            # manufacture an indoor departure, and outdoor rooms never retain.
            target = STATE_OCCUPIED if active else STATE_VACANT
            if room.state != target:
                self._set_state(
                    room,
                    target,
                    timestamp,
                    "outdoor_active" if active else "outdoor_clear",
                )
            if turned_on and not resuming:
                room.last_own_on = timestamp
            if turned_off:
                room.last_own_off = timestamp
            return

        if turned_on:
            if resuming:
                # The evidence never stopped; only the transport did. Keep the
                # activation time and any trail already observed.
                room.deadline = None
                self._set_state(room, STATE_OCCUPIED, timestamp, "own_motion_resumed")
                return
            spilled = self._is_spill(
                area_id, timestamp, previous_active, previous_on_edges
            )
            if spilled and room.state in (STATE_PENDING, STATE_RETAINED):
                # Rule 2: a spill-timed activation is not evidence of a person.
                # For a room already held it must neither confirm the entry nor
                # erase the trail that was observed, so the decision the room
                # was already heading for stands. Its OFF edge is ignored too.
                room.spill_ignored = True
                return
            self._enter_occupied(room, timestamp, spilled)
            return

        if turned_off:
            if room.spill_ignored:
                room.spill_ignored = False
                return
            room.last_own_off = timestamp
            room.deadline = timestamp + area.profile.hold_seconds
            self._set_state(room, STATE_PENDING, timestamp, "hold")
            return

        if active:
            # Still ON with no edge: nothing to decide.
            return

        if room.unavailable_since is not None:
            # No usable evidence: freeze the decision rather than let a
            # deadline expire on silence we cannot see behind.
            return

        self._evaluate_deadline(area_id, room, timestamp)

    @staticmethod
    def _is_resuming(room: RoomRuntime) -> bool:
        """Return whether an ON edge is a sensor coming back from unavailable.

        When a room's inputs go unavailable while ON, the room leaves the
        active set and its OFF is recorded at that same instant. An ON edge
        arriving while that marker is still in place is the same activation
        reporting again, not a person arriving.
        """
        return (
            room.unavailable_since is not None
            and room.last_own_off == room.unavailable_since
        )

    def _enter_occupied(
        self, room: RoomRuntime, timestamp: float, spilled: bool
    ) -> None:
        """Handle this room's own motion turning ON.

        Rule 2 of the design says a further own activation confirms a spilled
        entry "after the neighbour has gone quiet". This is deliberately looser:
        it confirms once the activation is more than the spill window past the
        neighbour's own activation, even while the neighbour is still ON. The
        detectors hold ON for about five seconds after the last motion they see,
        so waiting for the neighbour to go quiet would refuse to confirm the
        ordinary case of somebody walking out of the corridor into a room. What
        distinguishes spill is its lag, measured at 0.4 to 0.6 seconds, not
        whether the neighbour is still reporting.
        """
        if not spilled:
            # An activation that cannot be detector spill is a person.
            room.confirmed = True
        elif room.state == STATE_VACANT:
            room.confirmed = False

        room.last_own_on = timestamp
        room.first_exit_edge = None
        room.deadline = None
        self._set_state(
            room,
            STATE_OCCUPIED,
            timestamp,
            "own_motion_spill" if spilled else "own_motion",
        )

    def _is_spill(
        self,
        area_id: str,
        timestamp: float,
        previous_active: set[str],
        previous_on_edges: Mapping[str, float],
    ) -> bool:
        """Return whether this activation looks like an exit neighbour's spill."""
        for neighbor in self.topology[area_id].exits:
            if neighbor not in previous_active:
                continue
            neighbor_on = previous_on_edges.get(neighbor)
            if neighbor_on is None:
                continue
            if 0 <= timestamp - neighbor_on <= self.spill_window:
                return True
        return False

    def _note_exit_edge(self, area_id: str, timestamp: float) -> None:
        """Remember the first exit activation since this room's own activity.

        Rule 4 qualifies the trail only by timing, so any exit activation in the
        window counts. Detector overlap runs both ways, and a replay over three
        months of recorded events shows the cost: 60% of bedroom_1's trail
        releases fire on a corridor_2 edge whose lag from bedroom_1's own edge
        has a median of 0.39 s, which is spill rather than someone walking out.
        Qualifying the trail the way rule 2 qualifies entry needs a threshold of
        its own -- the lag distributions separate near 0.75 s, not at the 2 s
        spill window -- so it is left as a measured follow-up rather than a
        silent change to the rule.
        """
        room = self.rooms.get(area_id)
        if room is None or room.last_own_on is None:
            return
        if timestamp <= room.last_own_on:
            return
        if room.state == STATE_RETAINED:
            # A later exit activation never clears a retained room.
            return
        if room.first_exit_edge is None:
            room.first_exit_edge = timestamp

    def _has_trail(self, room: RoomRuntime) -> bool:
        if room.first_exit_edge is None or room.last_own_off is None:
            return False
        return room.first_exit_edge <= room.last_own_off + self.trail_window

    def _evaluate_deadline(
        self, area_id: str, room: RoomRuntime, timestamp: float
    ) -> None:
        """Resolve a hold or a retention ceiling that has come due."""
        if room.deadline is None or timestamp < room.deadline:
            return

        profile = self.topology[area_id].profile

        if room.state == STATE_PENDING:
            if self._has_trail(room):
                self._set_state(room, STATE_VACANT, timestamp, "departure_trail")
            elif not room.confirmed:
                self._set_state(room, STATE_VACANT, timestamp, "unconfirmed_entry")
            elif profile.retention_ceiling_seconds <= 0:
                self._set_state(room, STATE_VACANT, timestamp, "transition_room")
            else:
                anchor = (
                    room.last_own_off if room.last_own_off is not None else timestamp
                )
                room.deadline = anchor + profile.retention_ceiling_seconds
                self._set_state(room, STATE_RETAINED, timestamp, "no_exit_trail")
                # The ceiling may already have passed on a long restart gap.
                if timestamp >= room.deadline:
                    self._set_state(room, STATE_VACANT, timestamp, "retention_ceiling")
            return

        if room.state == STATE_RETAINED:
            self._set_state(room, STATE_VACANT, timestamp, "retention_ceiling")

    def _set_state(
        self, room: RoomRuntime, state: str, timestamp: float, reason: str
    ) -> None:
        if room.state != state:
            room.state = state
            room.state_since = timestamp
        room.reason = reason
        if state in (STATE_VACANT, STATE_OCCUPIED):
            room.deadline = None
        if state == STATE_VACANT:
            room.confirmed = False
            room.first_exit_edge = None

    def _monotonic(self, timestamp: float) -> float:
        """Clamp time forward so a late or reordered event cannot rewind."""
        if timestamp < self._last_timestamp:
            timestamp = self._last_timestamp
        self._last_timestamp = timestamp
        return timestamp

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Return the durable per-room decision state."""
        return {
            area_id: room.as_dict()
            for area_id, room in sorted(self.rooms.items())
            if self.topology[area_id].is_indoors
        }

    def restore(
        self,
        stored_rooms: Mapping[str, Any],
        now: float,
        active_areas: Iterable[str],
        unavailable_areas: Iterable[str] = (),
        *,
        evaluate: bool = True,
    ) -> list[str]:
        """Seed from persisted state, then re-evaluate everything against now.

        A restart never grants a fresh hold: a stored deadline keeps its
        original wall clock, and a room stored as occupied but no longer
        active resumes the hold that its last evidence had earned. Anything
        already expired is released in the same pass.

        With ``evaluate=False`` the pass seeds the rooms and stops. Home
        Assistant startup needs that: no sensor baseline has been read yet, so
        both sets are still empty and a stored room whose inputs are dead
        would be judged available and inactive. The caller runs the first
        evaluation after the baseline pass, with the live sets.
        """
        limit = now + 86400
        rejected: list[str] = []
        # The stored rooms never saw this engine's edges. Seed as a cold engine
        # would, so the pass below derives every edge from the live sets.
        self._active = set()
        self._last_on_edge = {}
        for area_id, raw in (stored_rooms or {}).items():
            area = self.topology.get(area_id)
            room = self.rooms.get(area_id)
            if area is None or room is None or not area.is_indoors:
                rejected.append(area_id)
                continue
            if not isinstance(raw, dict):
                rejected.append(area_id)
                continue
            state = raw.get("state")
            if state not in (
                STATE_VACANT,
                STATE_OCCUPIED,
                STATE_PENDING,
                STATE_RETAINED,
            ):
                rejected.append(area_id)
                continue
            room.state = state
            room.state_since = _finite(raw.get("state_since"), limit=limit) or now
            room.last_own_on = _finite(raw.get("last_own_on"), limit=limit)
            room.last_own_off = _finite(raw.get("last_own_off"), limit=limit)
            room.confirmed = bool(raw.get("confirmed"))
            room.deadline = _finite(raw.get("deadline"), limit=limit)
            room.first_exit_edge = _finite(raw.get("first_exit_edge"), limit=limit)
            room.reason = "restored"

            if state == STATE_OCCUPIED:
                # Motion that is no longer live cannot keep a room occupied. The
                # last evidence is the activation that was live at shutdown;
                # any stored OFF belongs to an earlier episode.
                anchor = room.last_own_on or room.state_since
                room.last_own_off = anchor
                room.state = STATE_PENDING
                room.deadline = anchor + area.profile.hold_seconds
            elif state in (STATE_PENDING, STATE_RETAINED) and room.deadline is None:
                anchor = room.last_own_off or room.state_since
                room.deadline = anchor + area.profile.hold_seconds

        if evaluate:
            self.apply(now, active_areas, unavailable_areas)
        # Home Assistant replays its startup baselines after this, carrying the
        # sensors' real last-changed times. Those are older than now and must
        # not be clamped forward, or genuine entries look like detector spill.
        self._last_timestamp = 0.0
        return rejected

    def reset(self) -> None:
        """Return every room to its initial, evidence-free state."""
        for room in self.rooms.values():
            room.state = STATE_VACANT
            room.state_since = 0.0
            room.last_own_on = None
            room.last_own_off = None
            room.confirmed = False
            room.deadline = None
            room.reason = "reset"
            room.first_exit_edge = None
            room.unavailable_since = None
            room.spill_ignored = False
        self._active = set()
        self._unavailable = set()
        self._last_on_edge = {}
        self._last_timestamp = 0.0
