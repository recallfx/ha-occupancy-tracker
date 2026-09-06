"""Tests for concise operational logging and the structured audit trail."""

import json
import logging
from unittest.mock import patch

from custom_components.occupancy_tracker import _setup_file_logging
from custom_components.occupancy_tracker.coordinator import OccupancyCoordinator
from custom_components.occupancy_tracker.helpers.anomaly_detector import AnomalyDetector
from custom_components.occupancy_tracker.helpers.audit import (
    AUDIT_LOGGER_NAME,
    audit_event,
)
from custom_components.occupancy_tracker.helpers.room_profiles import ROOM_PROFILES


def _config():
    return {
        "areas": {"corridor": {"profile": "transition"}},
        "adjacency": {},
        "sensors": {
            "binary_sensor.corridor_motion": {
                "area": "corridor",
                "type": "motion",
            }
        },
    }


def test_audit_event_is_one_stable_json_object():
    with patch("custom_components.occupancy_tracker.helpers.audit._LOGGER.info") as log:
        audit_event(
            "sensor_event",
            timestamp=123.5,
            sensor_id="binary_sensor.corridor_motion",
            decision="accepted_transition",
        )

    payload = json.loads(log.call_args.args[0])
    assert payload == {
        "decision": "accepted_transition",
        "event": "sensor_event",
        "sensor_id": "binary_sensor.corridor_motion",
        "timestamp": 123.5,
    }


def test_file_logging_is_idempotent_and_separates_audit(tmp_path):
    _setup_file_logging(str(tmp_path))
    _setup_file_logging(str(tmp_path))

    paths = {
        "operational": tmp_path / "occupancy_tracker.log",
        "raw": tmp_path / "occupancy_tracker_raw.csv",
        "audit": tmp_path / "occupancy_tracker_audit.jsonl",
    }
    loggers = {
        "operational": logging.getLogger("custom_components.occupancy_tracker"),
        "raw": logging.getLogger("custom_components.occupancy_tracker.raw"),
        "audit": logging.getLogger(AUDIT_LOGGER_NAME),
    }

    try:
        for name, path in paths.items():
            matching = [
                handler
                for handler in loggers[name].handlers
                if getattr(handler, "baseFilename", None) == str(path)
            ]
            assert len(matching) == 1
        assert loggers["operational"].level == logging.INFO
        assert loggers["raw"].propagate is False
        assert loggers["audit"].propagate is False
    finally:
        for logger in loggers.values():
            for handler in list(logger.handlers):
                if str(tmp_path) in str(getattr(handler, "baseFilename", "")):
                    logger.removeHandler(handler)
                    handler.close()


async def test_sensor_audit_records_source_and_receipt_times(hass):
    coordinator = OccupancyCoordinator(hass, _config())

    with patch("custom_components.occupancy_tracker.coordinator.audit_event") as audit:
        coordinator.process_sensor_event(
            "binary_sensor.corridor_motion",
            True,
            timestamp=100.0,
            received_timestamp=105.0,
        )

    event = audit.call_args.kwargs
    assert event["timestamp"] == 105.0
    assert event["source_timestamp"] == 100.0
    assert event["decision"] == "accepted_transition"
    assert event["occupancy_changes"] == {"corridor": {"from": 0, "to": 1}}
    assert event["room_transitions"] == {
        "corridor": {"from": "vacant", "to": "occupied"}
    }


async def test_old_startup_on_state_is_immediately_untrusted_as_stuck(hass):
    coordinator = OccupancyCoordinator(hass, _config())
    received = 100.0 + 25 * 3600

    coordinator.process_sensor_event(
        "binary_sensor.corridor_motion",
        True,
        timestamp=100.0,
        received_timestamp=received,
    )

    sensor = coordinator.sensors["binary_sensor.corridor_motion"]
    assert sensor.is_stuck is True
    assert sensor.is_reliable is False
    # An untrusted sensor is not live motion, so the room holds instead.
    assert coordinator.get_occupancy_evidence("corridor") == "pending"
    # A corridor never retains, so the hold is all the room gets.
    coordinator.check_timeouts(received + ROOM_PROFILES["transition"].hold_seconds)
    assert coordinator.get_occupancy_evidence("corridor") == "vacant"


async def test_clear_audit_records_actor_and_active_refusal(hass):
    coordinator = OccupancyCoordinator(hass, _config())
    coordinator.process_sensor_event(
        "binary_sensor.corridor_motion", True, timestamp=100.0
    )

    with patch("custom_components.occupancy_tracker.coordinator.audit_event") as audit:
        cleared = coordinator.clear_stale_occupancy(
            ["corridor"], actor="user:abc", reason="manual_service"
        )

    assert cleared == []
    event = audit.call_args.kwargs
    assert event["actor"] == "user:abc"
    assert event["requested"] == ["corridor"]
    assert event["cleared"] == []
    assert event["refused_active"] == ["corridor"]


async def test_out_of_order_event_is_audited_and_cannot_rewind_state(hass):
    config = _config()
    config["areas"]["corridor"]["indoors"] = False
    coordinator = OccupancyCoordinator(hass, config)
    coordinator.process_sensor_event(
        "binary_sensor.corridor_motion", True, timestamp=200.0
    )

    with patch("custom_components.occupancy_tracker.coordinator.audit_event") as audit:
        coordinator.process_sensor_event(
            "binary_sensor.corridor_motion", False, timestamp=100.0
        )

    assert coordinator.sensors["binary_sensor.corridor_motion"].current_state is True
    assert coordinator.get_occupancy("corridor") == 1
    assert audit.call_args.kwargs["decision"] == "ignored_out_of_order"
    assert audit.call_args.kwargs["latest_source_timestamp"] == 200.0


async def test_far_future_source_time_is_ignored_without_poisoning_state(hass):
    coordinator = OccupancyCoordinator(hass, _config())

    with patch("custom_components.occupancy_tracker.coordinator.audit_event") as audit:
        coordinator.process_sensor_event(
            "binary_sensor.corridor_motion",
            True,
            timestamp=10_000.0,
            received_timestamp=100.0,
        )

    sensor = coordinator.sensors["binary_sensor.corridor_motion"]
    assert sensor.current_state is False
    assert sensor.last_source_timestamp == 0
    assert audit.call_args.kwargs["decision"] == "ignored_invalid_timestamp"


def test_warning_open_and_manual_resolution_are_audited():
    detector = AnomalyDetector(_config())

    with patch(
        "custom_components.occupancy_tracker.helpers.anomaly_detector.audit_event"
    ) as audit:
        warning = detector._create_warning(
            "unexpected_motion",
            "Unexpected motion in corridor",
            area="corridor",
            timestamp=100.0,
        )
        assert detector.resolve_warning(warning.id) is True

    assert audit.call_args_list[0].args == ("warning_opened",)
    assert audit.call_args_list[1].args == ("warning_resolved",)
    assert audit.call_args_list[1].kwargs["reason"] == "manual"


def test_resolved_warning_history_is_bounded():
    detector = AnomalyDetector(_config())

    with (
        patch(
            "custom_components.occupancy_tracker.helpers.anomaly_detector.audit_event"
        ),
        patch(
            "custom_components.occupancy_tracker.helpers.anomaly_detector.logger.warning"
        ),
    ):
        for index in range(detector.MAX_WARNING_HISTORY + 5):
            warning = detector._create_warning(
                "test", f"warning {index}", timestamp=float(index)
            )
            warning.resolve()

    assert len(detector.get_warnings(active_only=False)) == detector.MAX_WARNING_HISTORY
