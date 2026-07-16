MAX_HISTORY_LENGTH = 100
ACTIVITY_HOLD_SECONDS = 120

MOTION_SENSOR_TYPES = {"motion", "camera_motion", "camera_person"}
MAGNETIC_SENSOR_TYPES = {"door", "garage_door", "window", "magnetic"}


def normalize_area_ids(raw_value) -> list[str]:
    """Normalize area config value to a list of area ID strings."""
    if isinstance(raw_value, str):
        return [raw_value]
    if isinstance(raw_value, list):
        return [entry for entry in raw_value if isinstance(entry, str)]
    return []
