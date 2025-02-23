"""Custom component for tracking occupancy across multiple areas using sensor fusion."""

import logging
import time

from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_track_state_change,
    async_track_time_interval,
)

from custom_components.occupancy_tracker.occupancy_system import OccupancySystem

_LOGGER = logging.getLogger(__name__)
DOMAIN = "occupancy_tracker"


async def async_setup(hass, config):
    """Set up the Occupancy Tracker integration."""
    conf = config.get(DOMAIN)
    if conf is None:
        _LOGGER.error("No configuration found for occupancy_tracker")
        return False

    # Build the occupancy configuration directly from HA's configuration.
    occupancy_config = {
        "areas": conf.get("areas", {}),
        "adjacency": conf.get("adjacency", {}),
        "sensors": conf.get("sensors", {}),
    }
    long_detect_threshold = conf.get("long_detect_threshold", 300)
    short_threshold = conf.get("short_threshold", 5)

    # Create the occupancy system instance.
    occupancy_system = OccupancySystem(
        occupancy_config,
        long_detect_threshold=long_detect_threshold,
        short_threshold=short_threshold,
    )

    # If a sensor_mapping is not provided, assume the sensor names are the HA entity IDs.
    sensor_mapping = conf.get("sensor_mapping")
    if not sensor_mapping:
        sensor_mapping = {
            sensor_name: sensor_name
            for sensor_name in occupancy_config.get("sensors", {})
        }

    hass.data[DOMAIN] = {
        "occupancy_system": occupancy_system,
        "sensor_mapping": sensor_mapping,
    }

    async def state_change_listener(entity_id, old_state, new_state):
        # Look up which occupancy system sensor corresponds to this entity.
        for occ_sensor, ha_entity in sensor_mapping.items():
            if ha_entity == entity_id:
                # Interpret HA state: 'on' becomes True; anything else is False.
                sensor_state = new_state.state.lower() == "on" if new_state else False
                timestamp = time.time()
                occupancy_system.handle_event(
                    occ_sensor, sensor_state, timestamp=timestamp
                )
                async_dispatcher_send(hass, f"{DOMAIN}_update")
                break

    # Set up state listeners for each HA sensor.
    if sensor_mapping:
        entity_ids = list(sensor_mapping.values())
        async_track_state_change(hass, entity_ids, state_change_listener)

    async def periodic_update(now):
        async_dispatcher_send(hass, f"{DOMAIN}_update")

    async_track_time_interval(hass, periodic_update, 30)

    _LOGGER.info("Occupancy Tracker integration set up successfully")
    return True
