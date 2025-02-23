"""Button platform for resetting anomaly detection."""
from homeassistant.components.button import ButtonEntity
from . import DOMAIN


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up the Occupancy Tracker button platform."""
    occupancy_system = hass.data[DOMAIN]["occupancy_system"]
    async_add_entities([ResetAnomaliesButton(occupancy_system)], True)


class ResetAnomaliesButton(ButtonEntity):
    """Button entity to reset anomaly detection."""

    def __init__(self, occupancy_system):
        self._occupancy_system = occupancy_system
        self._attr_name = "Reset Anomalies"
        self._attr_unique_id = "reset_anomalies_button"

    async def async_press(self) -> None:
        """Handle the button press - reset anomalies."""
        self._occupancy_system.reset_anomalies()