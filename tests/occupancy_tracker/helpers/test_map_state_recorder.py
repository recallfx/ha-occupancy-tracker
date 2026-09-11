"""Tests for the MapStateRecorder helper."""

import time

from custom_components.occupancy_tracker.helpers.area_state import AreaState
from custom_components.occupancy_tracker.helpers.map_state_recorder import (
    MapSnapshot,
    MapStateRecorder,
)
from custom_components.occupancy_tracker.helpers.sensor_state import SensorState


def _build_state_fixture():
    base_time = time.time()
    area = AreaState("living_room", {"exit_capable": True})
    area.record_motion(base_time - 5)
    area.record_entry(base_time - 5)

    sensor = SensorState(
        "binary_sensor.motion_living_room",
        {"area": "living_room", "type": "motion"},
        base_time - 10,
    )
    sensor.current_state = False
    sensor.last_changed = base_time - 10

    areas = {area.id: area}
    sensors = {sensor.id: sensor}
    return base_time, areas, sensors


def test_record_sensor_event_snapshot_includes_state():
    """Sensor-triggered snapshots should capture area and sensor state."""
    base_time, areas, sensors = _build_state_fixture()
    recorder = MapStateRecorder(max_snapshots=5, tick_interval=60)

    snapshot = recorder.record_sensor_event(
        base_time,
        "binary_sensor.motion_living_room",
        True,
        areas,
        sensors,
    )

    assert isinstance(snapshot, MapSnapshot)
    assert snapshot.event_type == "sensor"
    assert snapshot.description == "sensor:binary_sensor.motion_living_room:on"
    assert snapshot.areas["living_room"]["occupancy"] == areas["living_room"].occupancy
    assert snapshot.sensors["binary_sensor.motion_living_room"]["state"] is False


def test_record_clear_event_is_replayable():
    """Explicit occupancy cleanup must be represented in event history."""
    base_time, areas, sensors = _build_state_fixture()
    recorder = MapStateRecorder()

    snapshot = recorder.record_clear_event(base_time, ["living_room"], areas, sensors)

    assert snapshot.event_type == "clear"
    assert snapshot.description == "clear:living_room"


def test_record_occupancy_restore_is_replayable():
    """Restored rooms come from storage, so history has to carry them itself."""
    base_time, areas, sensors = _build_state_fixture()
    recorder = MapStateRecorder()
    rooms = {"living_room": {"state": "retained", "deadline": base_time + 3600}}

    snapshot = recorder.record_occupancy_restore(base_time, rooms, areas, sensors)

    assert snapshot.event_type == "restore"
    assert snapshot.description == "restore"
    assert snapshot.rooms == rooms
    assert [s.event_type for s in recorder.get_history()] == ["restore"]


def test_tick_snapshots_respect_interval():
    """Tick snapshots should be skipped until the configured interval expires."""
    base_time, areas, sensors = _build_state_fixture()
    recorder = MapStateRecorder(max_snapshots=5, tick_interval=120)

    # First tick when no history should always record
    initial = recorder.maybe_record_tick(base_time, areas, sensors)
    assert initial is not None
    assert initial.event_type == "tick"

    recorder.record_sensor_event(
        base_time + 10,
        "binary_sensor.motion_living_room",
        True,
        areas,
        sensors,
    )

    # Tick before interval should be ignored because event snapshot was recent
    skipped = recorder.maybe_record_tick(base_time + 50, areas, sensors)
    assert skipped is None

    # Once interval passes without new events we should capture a periodic tick
    periodic = recorder.maybe_record_tick(base_time + 200, areas, sensors)
    assert periodic is not None
    assert periodic.event_type == "tick"


def test_max_snapshot_history_respected():
    """Recorder should drop oldest entries once max length is exceeded."""
    base_time, areas, sensors = _build_state_fixture()
    recorder = MapStateRecorder(max_snapshots=3, tick_interval=60)

    for offset in range(5):
        recorder.record_sensor_event(
            base_time + offset,
            "binary_sensor.motion_living_room",
            bool(offset % 2),
            areas,
            sensors,
        )

    history = recorder.get_history()
    assert len(history) == 3
    # Ensure we kept the newest timestamps
    assert history[0].timestamp == base_time + 2
    assert history[-1].timestamp == base_time + 4


def test_merge_with_config_rehydrates_static_metadata():
    """Merging snapshots with config should restore static fields when needed."""
    base_time, areas, sensors = _build_state_fixture()
    recorder = MapStateRecorder(max_snapshots=5, tick_interval=60)

    snapshot = recorder.record_sensor_event(
        base_time,
        "binary_sensor.motion_living_room",
        True,
        areas,
        sensors,
    )

    assert "exit_capable" not in snapshot.areas["living_room"]
    assert "type" not in snapshot.sensors["binary_sensor.motion_living_room"]

    config = {
        "areas": {"living_room": {"exit_capable": True, "label": "Living"}},
        "sensors": {
            "binary_sensor.motion_living_room": {
                "type": "motion",
                "area": "living_room",
            }
        },
    }

    merged = MapStateRecorder.merge_with_config(snapshot, config)

    assert merged.areas["living_room"]["exit_capable"] is True
    assert (
        merged.areas["living_room"]["occupancy"]
        == snapshot.areas["living_room"]["occupancy"]
    )
    assert merged.sensors["binary_sensor.motion_living_room"]["type"] == "motion"
    assert (
        merged.sensors["binary_sensor.motion_living_room"]["state"]
        == snapshot.sensors["binary_sensor.motion_living_room"]["state"]
    )
