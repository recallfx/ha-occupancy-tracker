"""The occupancy_tracker integration."""

import logging
import logging.handlers
import os
import time

import voluptuous as vol

from homeassistant.core import Event, HomeAssistant
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.discovery import async_load_platform
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from datetime import timedelta

from .const import DOMAIN
from .helpers.constants import MOTION_SENSOR_TYPES
from .helpers.types import OccupancyTrackerConfig
from .coordinator import OccupancyCoordinator

_LOGGER = logging.getLogger("occupancy_tracker")
_RAW_LOGGER = logging.getLogger("occupancy_tracker.raw")

# All loggers used by this integration
_INTEGRATION_LOGGERS = [
    "occupancy_tracker",
    "resolver",
    "coordinator",
    "anomaly_detector",
]


def _setup_file_logging(config_dir: str) -> None:
    """Set up a dedicated log file for the occupancy tracker integration."""
    log_path = os.path.join(config_dir, "occupancy_tracker.log")
    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=3
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    )
    for name in _INTEGRATION_LOGGERS:
        logger = logging.getLogger(name)
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)

    # Raw sensor event log — clean CSV for replay testing (going forward)
    raw_path = os.path.join(config_dir, "occupancy_tracker_raw.csv")
    raw_handler = logging.handlers.RotatingFileHandler(
        raw_path, maxBytes=10 * 1024 * 1024, backupCount=3
    )
    raw_handler.setFormatter(logging.Formatter("%(message)s"))
    raw_logger = logging.getLogger("occupancy_tracker.raw")
    raw_logger.addHandler(raw_handler)
    raw_logger.setLevel(logging.DEBUG)
    raw_logger.propagate = False


# Schema for individual sensor configuration
def _export_sensor_history(config_dir: str, config: dict) -> None:
    """Export sensor history from recorder DB for replay testing."""
    import sqlite3

    db_path = os.path.join(config_dir, "home-assistant_v2.db")
    out_path = os.path.join(config_dir, "sensor_history_24h.csv")

    if not os.path.exists(db_path):
        return

    sensor_ids = list(config.get("sensors", {}).keys())
    if not sensor_ids:
        return

    try:
        db = sqlite3.connect(db_path)
        placeholders = ",".join("?" for _ in sensor_ids)
        cutoff = time.time() - 86400
        rows = db.execute(
            f"""
            SELECT sm.entity_id, s.state, s.last_updated_ts
            FROM states s
            JOIN states_meta sm ON s.metadata_id = sm.metadata_id
            WHERE sm.entity_id IN ({placeholders})
            AND s.state IN ('on', 'off')
            AND s.last_updated_ts > ?
            ORDER BY s.last_updated_ts
            """,
            (*sensor_ids, cutoff),
        ).fetchall()
        db.close()

        with open(out_path, "w") as f:
            f.write("timestamp,entity_id,state\n")
            for entity_id, state, ts in rows:
                f.write(f"{ts},{entity_id},{state}\n")

        _LOGGER.info(f"Exported {len(rows)} sensor events to {out_path}")
    except Exception as e:
        _LOGGER.warning(f"Could not export sensor history: {e}")


SENSOR_SCHEMA = vol.Schema(
    {
        vol.Required("area"): vol.Any(cv.string, [cv.string]),
        vol.Optional("type", default="motion"): cv.string,
        vol.Optional("between_areas"): vol.All([cv.string]),
    },
    extra=vol.ALLOW_EXTRA,
)

# Schema for individual area configuration
AREA_SCHEMA = vol.Schema(
    {
        vol.Optional("name"): cv.string,
        vol.Optional("indoors", default=True): cv.boolean,
        vol.Optional("exit_capable", default=False): cv.boolean,
    },
    extra=vol.ALLOW_EXTRA,
)

# Schema for open-plan group configuration
OPEN_PLAN_GROUP_SCHEMA = vol.Schema(
    {
        vol.Required("areas"): vol.All([cv.string]),
    },
    extra=vol.ALLOW_EXTRA,
)

# Main configuration schema
CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required("areas"): vol.Schema({cv.string: AREA_SCHEMA}),
                vol.Required("adjacency"): vol.Schema({cv.string: [cv.string]}),
                vol.Required("sensors"): vol.Schema({cv.entity_id: SENSOR_SCHEMA}),
                vol.Optional("open_plan_groups", default={}): vol.Schema(
                    {cv.string: OPEN_PLAN_GROUP_SCHEMA}
                ),
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Occupancy Tracker integration from YAML configuration."""
    conf = config.get(DOMAIN)
    if conf is None:
        _LOGGER.error("No configuration found for occupancy_tracker")
        return False

    # Build the occupancy system configuration from HA's YAML configuration.
    occupancy_config: OccupancyTrackerConfig = {
        "areas": conf.get("areas", {}),
        "adjacency": conf.get("adjacency", {}),
        "sensors": conf.get("sensors", {}),
        "open_plan_groups": conf.get("open_plan_groups", {}),
    }

    # Set up dedicated log file
    _setup_file_logging(hass.config.config_dir)

    # One-shot: export sensor history from recorder DB for replay testing
    _export_sensor_history(hass.config.config_dir, occupancy_config)

    # Validate configuration
    if not _validate_config(occupancy_config):
        return False

    # Create the coordinator instance.
    coordinator = OccupancyCoordinator(hass, occupancy_config)

    # Store the coordinator
    hass.data[DOMAIN] = {"coordinator": coordinator}

    async def state_change_listener(event: Event) -> None:
        """Handle state changes for sensors."""
        # Since sensor names are assumed to be the actual HA entity IDs,
        # check if the changed entity is one of our sensors.
        entity_id = event.data.get("entity_id")
        new_state = event.data.get("new_state")

        sensors = occupancy_config.get("sensors", {})
        if entity_id in sensors:
            timestamp = time.time()

            # Handle sensor unavailability
            if new_state is None or new_state.state in ["unavailable", "unknown"]:
                _LOGGER.warning(
                    f"Sensor {entity_id} is unavailable or in unknown state"
                )
                coordinator.invalidate_sensor_state(entity_id, timestamp=timestamp)
                return

            # Interpret HA state: 'on' becomes True; any other value is False
            sensor_state = new_state.state.lower() == "on"

            # Log raw sensor event for replay testing
            _RAW_LOGGER.debug(
                f"{timestamp},{entity_id},{'on' if sensor_state else 'off'}"
            )

            # Process event through coordinator
            coordinator.process_sensor_event(
                entity_id, sensor_state, timestamp=timestamp
            )

    # Set up state listeners for each sensor entity defined in the occupancy config.
    sensor_entities = list(occupancy_config.get("sensors", {}).keys())
    if sensor_entities:
        async_track_state_change_event(hass, sensor_entities, state_change_listener)

        # Restore states already present when the integration starts. Motion ON
        # is live occupancy evidence; other states are only cached baselines.
        timestamp = time.time()
        for entity_id in sensor_entities:
            state = hass.states.get(entity_id)
            sensor_type = occupancy_config["sensors"][entity_id].get("type", "")
            if state is None or state.state in ["unavailable", "unknown"]:
                coordinator.invalidate_sensor_state(entity_id, timestamp)
            elif state.state == "on" and sensor_type in MOTION_SENSOR_TYPES:
                coordinator.process_sensor_event(entity_id, True, timestamp=timestamp)
            else:
                coordinator.seed_sensor_state(
                    entity_id,
                    state.state.lower() == "on",
                    timestamp,
                )

    # Set up periodic check for timeouts (every 60 seconds)
    # Note: The coordinator also has a 5-second update_interval for consistency checks.
    # This 60-second check is specifically for longer-term anomalies.
    async def interval_listener(now) -> None:
        """Handle periodic checks."""
        coordinator.check_timeouts(timestamp=now.timestamp())

    remove_interval = async_track_time_interval(
        hass, interval_listener, timedelta(seconds=60)
    )
    hass.data[DOMAIN]["remove_update_listener"] = remove_interval

    # Ensure timer is cleaned up when HA stops
    from homeassistant.const import EVENT_HOMEASSISTANT_STOP

    async def _cleanup_timer(event):
        remove_interval()
        # Also stop the coordinator's periodic updates
        await coordinator.async_shutdown()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _cleanup_timer)

    # Set up platforms
    await async_load_platform(hass, "binary_sensor", DOMAIN, {}, config)
    await async_load_platform(hass, "sensor", DOMAIN, {}, config)
    await async_load_platform(hass, "button", DOMAIN, {}, config)

    _LOGGER.info("Occupancy Tracker integration set up successfully")
    return True


def _validate_config(config: OccupancyTrackerConfig) -> bool:
    """Validate that all referenced areas exist."""
    areas = config.get("areas", {})
    adjacency = config.get("adjacency", {})
    sensors = config.get("sensors", {})

    # Validate adjacency
    for area_id, adjacent_areas in adjacency.items():
        if area_id not in areas:
            _LOGGER.error(f"Adjacency config references unknown area: {area_id}")
            return False
        for adj_id in adjacent_areas:
            if adj_id not in areas:
                _LOGGER.error(
                    f"Adjacency config for {area_id} references unknown area: {adj_id}"
                )
                return False

    # Validate sensors
    for sensor_id, sensor_config in sensors.items():
        area_config = sensor_config.get("area")
        if area_config:
            area_ids = area_config if isinstance(area_config, list) else [area_config]
            for area_id in area_ids:
                if area_id not in areas:
                    _LOGGER.error(
                        f"Sensor {sensor_id} references unknown area: {area_id}"
                    )
                    return False

        # Validate between_areas for magnetic sensors
        if sensor_config.get("type") == "magnetic":
            between = sensor_config.get("between_areas")
            if between:
                for area_id in between:
                    if area_id not in areas:
                        _LOGGER.error(
                            f"Sensor {sensor_id} between_areas references unknown area: {area_id}"
                        )
                        return False

    return True
