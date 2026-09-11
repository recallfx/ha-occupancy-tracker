"""Named room behavior profiles.

Profiles carry the policy numbers of the occupancy state machine: how long a
room is held after its own sensors fall silent, and how long a room with no
observed departure may stay retained before the ceiling releases it. They also
tune convenience signals and diagnostics.
"""

from __future__ import annotations

from dataclasses import dataclass

from .types import AreaConfig


@dataclass(frozen=True)
class RoomProfile:
    """Time behavior for one class of room."""

    name: str
    hold_seconds: int
    retention_ceiling_seconds: int
    activity_hold_seconds: int
    freshness_scale: float
    phantom_inactivity_seconds: int
    extended_occupancy_seconds: int
    neighbor_activity_seconds: int
    contact_evidence_seconds: int


ROOM_PROFILES: dict[str, RoomProfile] = {
    "transition": RoomProfile(
        name="transition",
        hold_seconds=30,
        retention_ceiling_seconds=0,
        activity_hold_seconds=20,
        freshness_scale=0.05,
        phantom_inactivity_seconds=5 * 60,
        extended_occupancy_seconds=30 * 60,
        neighbor_activity_seconds=2 * 60,
        contact_evidence_seconds=5 * 60,
    ),
    "default": RoomProfile(
        name="default",
        hold_seconds=90,
        retention_ceiling_seconds=2 * 3600,
        activity_hold_seconds=2 * 60,
        freshness_scale=1.0,
        phantom_inactivity_seconds=30 * 60,
        extended_occupancy_seconds=12 * 3600,
        neighbor_activity_seconds=30 * 60,
        contact_evidence_seconds=30 * 60,
    ),
    "living": RoomProfile(
        name="living",
        hold_seconds=90,
        retention_ceiling_seconds=4 * 3600,
        activity_hold_seconds=2 * 60,
        freshness_scale=1.0,
        phantom_inactivity_seconds=30 * 60,
        extended_occupancy_seconds=12 * 3600,
        neighbor_activity_seconds=30 * 60,
        contact_evidence_seconds=30 * 60,
    ),
    "sleeping": RoomProfile(
        name="sleeping",
        hold_seconds=90,
        retention_ceiling_seconds=12 * 3600,
        activity_hold_seconds=15 * 60,
        freshness_scale=6.0,
        phantom_inactivity_seconds=12 * 3600,
        extended_occupancy_seconds=24 * 3600,
        neighbor_activity_seconds=60 * 60,
        contact_evidence_seconds=60 * 60,
    ),
}


def resolve_room_profile(area_config: AreaConfig) -> RoomProfile:
    """Return the explicit profile or the transition-compatible default."""
    requested = area_config.get("profile")
    if requested is None and area_config.get("transition", False):
        requested = "transition"
    return ROOM_PROFILES.get(requested or "default", ROOM_PROFILES["default"])
