"""Tests for occupancy evidence diagnostics."""

from unittest.mock import Mock

from homeassistant.core import HomeAssistant

from custom_components.occupancy_tracker.coordinator import OccupancyCoordinator
from custom_components.occupancy_tracker.helpers.room_profiles import ROOM_PROFILES


def _coordinator():
    return OccupancyCoordinator(
        Mock(spec=HomeAssistant),
        {
            "areas": {"study": {"name": "Study", "indoors": True}},
            "adjacency": {},
            "sensors": {
                "binary_sensor.study_motion": {
                    "area": "study",
                    "type": "motion",
                }
            },
        },
        store=Mock(),
    )


def test_area_status_exposes_a_held_room():
    coordinator = _coordinator()
    coordinator.process_sensor_event("binary_sensor.study_motion", True, 1000.0)
    coordinator.process_sensor_event("binary_sensor.study_motion", False, 1010.0)

    status = coordinator.get_area_status("study")

    assert status["evidence_state"] == "pending"
    assert status["reason"] == "hold"
    assert status["deadline"] == 1010.0 + ROOM_PROFILES["default"].hold_seconds
    assert status["active_sensors"] == []
    assert status["last_positive_evidence"] == 1000.0
    assert status["stale_since"] == 1010.0


def test_system_status_summarizes_evidence_states():
    coordinator = _coordinator()
    coordinator.process_sensor_event("binary_sensor.study_motion", True, 1000.0)
    coordinator.process_sensor_event("binary_sensor.study_motion", False, 1010.0)

    status = coordinator.get_system_status()

    assert status["area_evidence"] == {"study": "pending"}
    assert status["evidence_counts"] == {
        "occupied": 0,
        "pending": 1,
        "retained": 0,
        "vacant": 0,
        "unknown": 0,
    }


def test_motion_diagnostics_supports_multi_area_sensors():
    coordinator = OccupancyCoordinator(
        Mock(spec=HomeAssistant),
        {
            "areas": {
                "entrance": {"indoors": True},
                "frontyard": {"indoors": False},
            },
            "adjacency": {},
            "sensors": {
                "binary_sensor.front_door": {
                    "area": ["entrance", "frontyard"],
                    "type": "door",
                }
            },
        },
        store=Mock(),
    )

    status = coordinator.diagnose_motion_issues("binary_sensor.front_door")

    assert status["binary_sensor.front_door"]["area_exists"] is True
