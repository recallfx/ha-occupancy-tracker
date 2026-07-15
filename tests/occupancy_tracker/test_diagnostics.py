"""Tests for occupancy evidence diagnostics."""

from unittest.mock import Mock

from homeassistant.core import HomeAssistant

from custom_components.occupancy_tracker.coordinator import OccupancyCoordinator


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


def test_area_status_exposes_stale_evidence():
    coordinator = _coordinator()
    area = coordinator.areas["study"]
    area.occupied = True
    area.record_motion(1000.0)
    area.stale_since = 1010.0
    coordinator.occupancy_resolver.indoor_latched.add("study")

    status = coordinator.get_area_status("study")

    assert status["evidence_state"] == "stale"
    assert status["active_sensors"] == []
    assert status["last_positive_evidence"] == 1000.0
    assert status["stale_since"] == 1010.0


def test_system_status_summarizes_evidence_states():
    coordinator = _coordinator()
    coordinator.areas["study"].occupied = True
    coordinator.occupancy_resolver.indoor_latched.add("study")

    status = coordinator.get_system_status()

    assert status["area_evidence"] == {"study": "stale"}
    assert status["evidence_counts"] == {
        "active": 0,
        "stale": 1,
        "inferred": 0,
        "vacant": 0,
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
