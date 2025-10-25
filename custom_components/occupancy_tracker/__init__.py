"""The occupancy_tracker integration."""

import logging
import time

import voluptuous as vol

from homeassistant.core import Event, HomeAssistant
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.discovery import async_load_platform
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_state_change_event

from .components.types import OccupancyTrackerConfig
from .occupancy_tracker import OccupancyTracker

_LOGGER = logging.getLogger(__name__)
DOMAIN = "occupancy_tracker"

# Schema for individual sensor configuration
SENSOR_SCHEMA = vol.Schema(
    {
        vol.Required("area"): vol.Any(cv.string, [cv.string]),
        vol.Optional("type", default="motion"): cv.string,
    }
)

# Schema for individual area configuration
AREA_SCHEMA = vol.Schema(
    {
        vol.Required("name"): cv.string,
        vol.Optional("exit_capable", default=False): cv.boolean,
    }
)

# Main configuration schema
CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required("areas"): vol.Schema({cv.string: AREA_SCHEMA}),
                vol.Required("adjacency"): vol.Schema({cv.string: [cv.string]}),
                vol.Required("sensors"): vol.Schema({cv.entity_id: SENSOR_SCHEMA}),
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
            # Handle sensor unavailability
            if new_state is None or new_state.state in ["unavailable", "unknown"]:
                _LOGGER.warning(
                    f"Sensor {entity_id} is unavailable or in unknown state, skipping event"
                )
                return

            # Interpret HA state: 'on' becomes True; any other value is False
            sensor_state = new_state.state.lower() == "on"
            timestamp = time.time()
            occupancy_tracker.process_sensor_event(
                entity_id, sensor_state, timestamp=timestamp
            )
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
