"""The occupancy_tracker integration."""

import logging
import time

from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers.discovery import async_load_platform
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_state_change_event

from .occupancy_tracker import OccupancyTracker

_LOGGER = logging.getLogger(__name__)
DOMAIN = "occupancy_tracker"


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Occupancy Tracker integration from YAML configuration."""
    conf = config.get(DOMAIN)
    if conf is None:
        _LOGGER.error("No configuration found for occupancy_tracker")
        return False

    # Build the occupancy system configuration from HA's YAML configuration.
    occupancy_config = {
        "areas": conf.get("areas", {}),
        "adjacency": conf.get("adjacency", {}),
        "sensors": conf.get("sensors", {}),
    }

    # Create the occupancy system instance.
    occupancy_tracker = OccupancyTracker(
        occupancy_config,
    )

    hass.data[DOMAIN] = {"occupancy_tracker": occupancy_tracker}

    async def state_change_listener(event: Event) -> None:
        """Handle state changes for sensors."""
        # Since sensor names are assumed to be the actual HA entity IDs,
        # check if the changed entity is one of our sensors.
        entity_id = event.data.get("entity_id")
        new_state = event.data.get("new_state")

        sensors = occupancy_config.get("sensors", {})
        if entity_id in sensors:
            # Interpret HA state: 'on' becomes True; any other value is False
            sensor_state = new_state.state.lower() == "on" if new_state else False
            timestamp = time.time()
            occupancy_tracker.process_sensor_event(entity_id, sensor_state, timestamp=timestamp)
            async_dispatcher_send(hass, f"{DOMAIN}_update")

    # Set up state listeners for each sensor entity defined in the occupancy config.
    sensor_entities = list(occupancy_config.get("sensors", {}).keys())
    if sensor_entities:
        async_track_state_change_event(hass, sensor_entities, state_change_listener)

    # Set up the sensor platform
    await async_load_platform(hass, "sensor", DOMAIN, {}, config)
    
    # Set up the button platform
    await async_load_platform(hass, "button", DOMAIN, {}, config)

    _LOGGER.info("Occupancy Tracker integration set up successfully")
    return True
