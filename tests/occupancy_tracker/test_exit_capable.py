import pytest
from custom_components.occupancy_tracker.helpers.anomaly_detector import AnomalyDetector
from custom_components.occupancy_tracker.helpers.area_state import AreaState


def _set_occupancy(area: AreaState, count: int) -> None:
    """Set area occupancy by adding test claims."""
    area.claims.clear()
    for i in range(count):
        area.claims.add(f"_test_{i}")


@pytest.fixture
def config_with_exit_area():
    return {
        "areas": {
            "frontyard": {"name": "Front Yard", "exit_capable": True, "indoors": False},
            "living_room": {"name": "Living Room"},
        },
        "adjacency": {
            "frontyard": ["living_room"],
            "living_room": ["frontyard"],
        },
        "sensors": {},
    }


@pytest.fixture
def anomaly_detector(config_with_exit_area):
    return AnomalyDetector(config_with_exit_area)


def test_exit_capable_area_timeout_warns_without_clearing(
    anomaly_detector: AnomalyDetector,
):
    """Timeout diagnostics never mutate occupancy."""
    start_time = 1000.0

    # Create areas
    frontyard = AreaState(
        "frontyard",
        {"name": "Front Yard", "exit_capable": True, "indoors": False},
    )
    living_room = AreaState("living_room", {"name": "Living Room"})

    areas = {
        "frontyard": frontyard,
        "living_room": living_room,
    }

    # Simulate person entering frontyard
    frontyard.record_entry(start_time)
    frontyard.record_motion(start_time)
    assert frontyard.occupancy == 1

    # After 4 minutes (240s) - should still be occupied
    anomaly_detector.check_timeouts(areas, start_time + 240)
    assert frontyard.occupancy == 1

    # After 5 minutes + 1 second (301s), uncertainty is reported.
    anomaly_detector.check_timeouts(areas, start_time + 301)
    assert frontyard.occupancy == 1

    # Check that a warning was created
    warnings = anomaly_detector.get_warnings(active_only=True)
    assert len(warnings) == 1
    assert warnings[0].type == "exit_area_stale"
    assert "frontyard" in warnings[0].message


def test_regular_area_no_auto_clear(anomaly_detector: AnomalyDetector):
    """Test that regular areas do NOT auto-clear after 5 minutes."""
    start_time = 1000.0

    # Create areas
    frontyard = AreaState("frontyard", {"name": "Front Yard", "exit_capable": True})
    living_room = AreaState("living_room", {"name": "Living Room"})

    areas = {
        "frontyard": frontyard,
        "living_room": living_room,
    }

    # Simulate person in living room
    living_room.record_entry(start_time)
    living_room.record_motion(start_time)
    assert living_room.occupancy == 1

    # After 5 minutes + 1 second - should still be occupied
    anomaly_detector.check_timeouts(areas, start_time + 301)
    assert living_room.occupancy == 1

    # No outdoor-staleness warnings
    warnings = [
        w for w in anomaly_detector.get_warnings() if w.type == "exit_area_stale"
    ]
    assert len(warnings) == 0


def test_exit_capable_area_with_recent_motion(anomaly_detector: AnomalyDetector):
    """Test that exit-capable areas with recent motion don't auto-clear."""
    start_time = 1000.0

    frontyard = AreaState("frontyard", {"name": "Front Yard", "exit_capable": True})
    areas = {"frontyard": frontyard}

    # Simulate person entering frontyard
    frontyard.record_entry(start_time)
    frontyard.record_motion(start_time)

    # Motion after 3 minutes
    frontyard.record_motion(start_time + 180)

    # Check at 6 minutes (but only 3 minutes since last motion)
    anomaly_detector.check_timeouts(areas, start_time + 360)
    assert frontyard.occupancy == 1  # Still occupied


def test_exit_capable_zero_occupancy_no_clear(anomaly_detector: AnomalyDetector):
    """Test that exit-capable areas with zero occupancy don't trigger warnings."""
    start_time = 1000.0

    frontyard = AreaState("frontyard", {"name": "Front Yard", "exit_capable": True})
    areas = {"frontyard": frontyard}

    # No occupancy
    assert frontyard.occupancy == 0

    # Check timeouts - should not trigger any warnings
    anomaly_detector.check_timeouts(areas, start_time + 1000)

    warnings = anomaly_detector.get_warnings()
    assert len(warnings) == 0
