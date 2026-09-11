MAX_HISTORY_LENGTH = 100
ACTIVITY_HOLD_SECONDS = 120

MOTION_SENSOR_TYPES = {"motion", "camera_motion", "camera_person"}
MAGNETIC_SENSOR_TYPES = {"door", "garage_door", "window", "magnetic"}

# An own activation that begins within this window of an exit neighbour's
# activation, while that neighbour is still ON, is detector spill rather than
# a person walking in. Measured lag of spilled edges is 0.5-0.6 s.
SPILL_WINDOW_SECONDS = 2.0

# How long after a room's own sensors fall silent an exit activation still
# counts as the departure trail of the person who was in the room. Measured
# behaviour is insensitive between 15 s and 60 s.
TRAIL_WINDOW_SECONDS = 30.0

# A room whose motion inputs are all unavailable for longer than this
# publishes unknown rather than a silently stale state.
UNAVAILABLE_GRACE_SECONDS = 60.0


def normalize_area_ids(raw_value) -> list[str]:
    """Normalize area config value to a list of area ID strings."""
    if isinstance(raw_value, str):
        return [raw_value]
    if isinstance(raw_value, list):
        return [entry for entry in raw_value if isinstance(entry, str)]
    return []
